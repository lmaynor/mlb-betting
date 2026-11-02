"""Walk-forward replay of the NHL hierarchical projection engine against the engine it replaced.

    PYTHONPATH=. python -m scripts.nhl_engine_replay --test-seasons 2024,2025 [--date-stride 3] [--oracle-absences]
                                                     [--stats goals,points,...] [--out report.json]

For every player-game in the test seasons that the board would have projected (rotation-level average usage, >= MIN_GAMES
earlier games), the SERVING functions are called as of that game's date -- history, league contexts (alpha / cv /
opponent factors), team shot pace and shrunk rates all cut to games STRICTLY BEFORE the date (`as_of`) -- and scored against
what happened, next to the legacy estimate (generic EWM + usage + opponent factor, plus the old goals / points finishing
regression) on the same rows. Nothing here is edge against a price: there are no historical NHL lines in this path.

Scores, per stat, paired over the same player-games (standard error of the paired mean difference in brackets):
  mae        mean |actual - mean|                       (lower is better)
  bias       mean (actual - mean)
  loglik     mean log P(actual) under the engine's own count distribution (NegBin for counts, Normal / mixture for saves
             with a +-0.5 continuity window): a proper score for the WHOLE distribution, which is what prices over / unders
  dLoglik    new - legacy (positive = the new engine is better)

`--oracle-absences` additionally feeds each game's ACTUAL non-dressers to the absent-teammate redistribution. That is
hindsight (the live engine only knows who is off a roster), so it is an UPPER BOUND on what the mechanism can add.

The data is read through the production loader (mlb_core.data.cockpit_form._load_nhl), from GCS or from a local copy of the
masters (MLB_BASE_DATA=<dir with NHL/...>). Parameters were chosen on --select-seasons only; the report seasons are never
used for selection.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import defaultdict

import numpy as np
from scipy.stats import nbinom, norm

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf
from nhl.props import hierarchical as H

log = logging.getLogger("scripts.nhl_engine_replay")

MIN_N = 200


def season_of(date: str) -> int:
    y, m = int(date[:4]), int(date[5:7])
    return y if m >= 8 else y - 1


def legacy_mu(stat: str, rows: list, sdef: tuple, logs: DP.GameLogs, opp, as_of: str):
    """The estimate this PR replaced: project_mu x opponent factor, plus the old goals / points finishing regression."""
    ctx = logs.context(stat, as_of)
    hl, w, _ = DP.stat_params("nhl", stat)
    pj = DP.project_mu(DP._hist(rows, sdef), hl, ctx.opp_factor.get(opp, 1.0) if opp else 1.0, w)
    if pj is None:
        return None
    mu = pj["mu"]
    if stat in ("goals", "points"):
        rg = [r for r in rows[-15:] if r.get("goals") is not None]
        rs = [r for r in rows[-15:] if r.get("sog") is not None]
        goals, sog = sum(float(r["goals"]) for r in rg), sum(float(r["sog"]) for r in rs)
        if sog >= 15.0:
            fin = (goals + 3.0) / (sog * 0.100 + 3.0)
            reg = (np.clip(1.0 / (0.4 * fin + 0.6), 0.85, 1.15) if stat == "goals"
                   else np.clip(1.0 / (0.6 * fin + 0.4), 0.90, 1.10))
            mu *= float(reg)
    return mu


def nb_loglik(y: float, mu: float, alpha: float) -> float:
    mu = max(mu, 1e-6)
    n = 1.0 / alpha
    return float(nbinom.logpmf(int(round(y)), n, n / (n + mu)))


def normal_loglik(y: float, mu: float, sd: float) -> float:
    p = norm.cdf((y + 0.5 - mu) / sd) - norm.cdf((y - 0.5 - mu) / sd)
    return math.log(max(float(p), 1e-12))


def mixture_loglik(y: float, d: H.SavesMixture) -> float:
    return math.log(max(d.cdf(y + 0.5) - d.cdf(y - 0.5), 1e-12))


def _summ(new: list, old: list) -> dict:
    new, old = np.asarray(new, dtype=float), np.asarray(old, dtype=float)
    d = new - old
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("nan")
    return {"new": round(float(new.mean()), 5), "legacy": round(float(old.mean()), 5),
            "diff": round(float(d.mean()), 5), "se": round(se, 5)}


def replay_stat(logs: DP.GameLogs, stat: str, seasons: set, stride: int, oracle: bool) -> dict | None:
    goalie = stat in cf.NHL_GOALIE_STATS
    table = logs.idx["goalie" if goalie else "skater"]
    sdef = (cf.NHL_GOALIE_STATS if goalie else cf.NHL_SKATER_STATS)[stat]
    thr = DP.ROTATION_MIN_USAGE["nhl"][stat]
    family = DP.PROJECT_STATS["nhl"][stat]
    by_date: dict = defaultdict(list)
    for name, rows in table.items():
        if str(name).startswith("id:"):
            continue
        for i, r in enumerate(rows):
            d = str(r.get("_date") or "")[:10]
            if d and season_of(d) in seasons and i >= DP.MIN_GAMES:
                by_date[d].append((name, i))
    dates = sorted(by_date)[::stride]
    acc = defaultdict(list)
    dressed_by_team: dict = {}
    if oracle and not goalie:
        for name, rows in table.items():
            if str(name).startswith("id:"):
                continue
            for r in rows:
                if (r.get("toi_min") or 0) > 0:
                    dressed_by_team.setdefault((str(r["_date"])[:10], DP.canon_team("nhl", r.get("team_abbr"))),
                                               set()).add(name)
    for d in dates:
        ctx = logs.context(stat, d)
        for name, i in by_date[d]:
            full = table[name]
            rows, row = full[max(0, i - 80):i], full[i]
            use = [g[1] for g in DP._hist(rows, sdef)[-10:] if g[1] is not None]
            if not use or sum(use) / len(use) < thr:
                continue
            if goalie and str(row.get("starter")).strip().lower() not in ("true", "1", "1.0"):
                continue                                  # the board prices probable starters, never a relief appearance
            y = DP.cf._sum_cols(row, sdef[0])
            if y is None:
                continue
            team = DP.canon_team("nhl", row.get("team_abbr"))
            opp = DP.canon_team("nhl", row.get("_opp"))
            absent = ()
            if oracle and not goalie:
                absent = frozenset(p for p, _, rs in logs._players_by_team(logs._eff_as_of(d)).get(team, [])
                                   if p not in dressed_by_team.get((d, team), set()))
            proj, dist, _ = DP.project_nhl(stat, rows, sdef, logs, team, opp, d, None, name, absent)
            old = legacy_mu(stat, rows, sdef, logs, opp, d)
            if proj is None or old is None:
                continue
            mu = proj["mu"]
            if goalie:
                sd_old = max(ctx.cv * old, DP.MIN_SD_FRAC * old, 1e-6)
                ll_new, ll_old = mixture_loglik(y, dist), normal_loglik(y, old, sd_old)
            else:
                ll_new, ll_old = nb_loglik(y, mu, ctx.alpha), nb_loglik(y, old, ctx.alpha)
            acc["ae_new"].append(abs(y - mu))
            acc["ae_old"].append(abs(y - old))
            acc["err_new"].append(y - mu)
            acc["err_old"].append(y - old)
            acc["ll_new"].append(ll_new)
            acc["ll_old"].append(ll_old)
            acc["y"].append(y)
    n = len(acc["y"])
    if n < MIN_N:
        return None
    return {"n": n, "mean_actual": round(float(np.mean(acc["y"])), 4),
            "mae": _summ(acc["ae_new"], acc["ae_old"]),
            "bias": {"new": round(float(np.mean(acc["err_new"])), 4), "legacy": round(float(np.mean(acc["err_old"])), 4)},
            "loglik": _summ(acc["ll_new"], acc["ll_old"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--test-seasons", default="2024,2025", help="season START years to report")
    ap.add_argument("--date-stride", type=int, default=3, help="score every Nth game date (speed)")
    ap.add_argument("--stats", default=",".join(DP.PROJECT_STATS["nhl"]))
    ap.add_argument("--keep-seasons", type=int, default=6, help="seasons the loader keeps (production keeps 2)")
    ap.add_argument("--oracle-absences", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cf.KEEP_SEASONS = a.keep_seasons
    idx = cf._load_nhl()
    if not idx:
        log.error("NHL masters unavailable")
        return 1
    logs = DP.GameLogs("nhl", idx)
    seasons = {int(s) for s in a.test_seasons.split(",")}
    out = {}
    for stat in a.stats.split(","):
        res = replay_stat(logs, stat, seasons, a.date_stride, a.oracle_absences)
        log.info("%s -> %s", stat, res)
        if res:
            out[stat] = res
    payload = {"test_seasons": sorted(seasons), "date_stride": a.date_stride, "oracle_absences": a.oracle_absences,
               "stats": out}
    text = json.dumps(payload, indent=1)
    print(text)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
