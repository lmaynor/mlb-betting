"""Walk-forward replay of the NBA SERVING projection engine (ev.daily_projections) against what happened.

    python -m nba.props.engine_eval --master PLAYER_BOXSCORES_MASTER.csv --phase tune --seasons 2023 --out tune.md
    python -m nba.props.engine_eval --master PLAYER_BOXSCORES_MASTER.csv --phase test --seasons 2024,2025 --out test.md

What it is. The picks board runs `ev.daily_projections`, not the trained research models, so nothing measured THAT code. This
replays its own functions (`project_mu`, `GameLogs.context`, `_nba_projection`, `p_over`) for every rotation player-game of a
season, using only rows strictly before that game's date, and scores the projections against the box score.

What it measures: ACCURACY, not edge. The box-score master has no prop lines, so there is no CLV / ROI here. Per stat and
variant: MAE, bias, mean log score of the projection's own distribution at the outcome, and the Brier score of P(over) at a
PROXY line (floor(L10 mean) + 0.5 -- not a book line). Variants are compared PAIRED against the same rows, with a
player-clustered standard error.

Variants: `old` (the engine before PR #147: independent EWM per stat, flat 1 + 0.04 * absences boost), `hier` (PR #147),
and `hier` + one feature each (positional DvP, missed-shot-pool rebounds, team-makes assists, zero-inflated steals/blocks).
Feature parameters are SELECTED on the --phase tune season and only then scored on the --phase test seasons; a feature is
adopted only if it beats `hier` there by ADOPT_Z standard errors (Bonferroni over the stats scored) AND MAE agrees.

Honest limits, repeated in the report:
  * ABSENCES ARE ORACLE: a rotation player (L10 >= 12 mpg) listed in the box score as not playing is treated as Out with
    probability 1. The live board only has the injury report (Questionable players are probabilities), so any absence-driven
    gain here is an UPPER BOUND. `old` gets the SAME oracle absences (counted rotation-only), which favours `old`.
  * NO SPREADS: the master has none, so the blowout trim and the overtime term are not exercised (spread = None).
  * League context (opponent factors, dispersion) is rebuilt each calendar month from rows before that month.
  * The eligible population is the board's: average minutes over the last 10 games >= 20 (ROTATION_MIN_USAGE).
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from bisect import bisect_left
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy.stats import nbinom, norm

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf
from nba.props import hierarchical as H
from nba.props import team_context as TC

log = logging.getLogger("nba.props.engine_eval")

STATS = ("points", "rebounds", "assists", "threes", "steals", "blocks", "pr", "pa", "ra", "pra")
COMBO_PARTS = {"pr": ("points", "rebounds"), "pa": ("points", "assists"), "ra": ("rebounds", "assists"),
               "pra": ("points", "rebounds", "assists")}
ELIG_MIN_MPG = 20.0
HISTORY_SEASONS = 2                      # current + previous season, like the production loader
ADOPT_Z = float(norm.ppf(1 - 0.05 / (2 * len(STATS))))
GRID = {"dvp": [75.0, 150.0, 300.0], "pool": [0.5, 1.0], "shot": [0.5, 1.0], "eta": [0.15, 0.25, 0.4, 0.6],
        "trkast": [0.5, 1.0], "trkreb": [0.5, 1.0]}
# PR #147 exactly as specified (spec-strength boost, no extra factors): an empty switch set would now mean the SHIPPED defaults
HIER_SW = {"eta_scale": 1.0, "eta_off": (), "shot_gamma": 0.0, "pool_gamma": 0.0, "dvp_k": None, "zinb": False,
           "trk_ast": 0.0, "trk_reb": 0.0}
SHIP = "eta_0.4+shot_0.5"
ETA_OFF = ("steals", "blocks")           # the tune season showed boosts on these hurt even with oracle absences
FEATURE_STATS = {"trkast": ("assists", "pa", "ra", "pra"), "trkreb": ("rebounds", "pr", "ra", "pra"),
                 "eta": STATS, "dvp": STATS, "pool": ("rebounds", "pr", "ra", "pra"), "shot": ("assists", "pa", "ra", "pra"),
                 "zinb": ("steals", "blocks")}


# ------------------------------------------------------------------------------------------------------ loading
def load_master(path: str) -> tuple:
    """Box-score master CSV -> (cockpit-shaped index {norm name: [row dicts]}, dnp {(team, date): {norm name}})."""
    df = pd.read_csv(path, low_memory=False)
    df = df[~df["season_type"].astype(str).str.lower().eq("preseason")].copy()
    df["_date"] = df["date"].astype(str).str[:10]
    played = df["played"].astype(str).str.lower().isin(["true", "1", "1.0"])
    teams_of = df.groupby("game_id")["team_abbr"].agg(lambda s: sorted(set(s)))
    opp = {}
    for gid, ts in teams_of.items():
        if len(ts) == 2:
            opp[(gid, ts[0])], opp[(gid, ts[1])] = ts[1], ts[0]
    dnp: dict = {}
    for team, d, name in df.loc[~played, ["team_abbr", "_date", "name"]].itertuples(index=False):
        dnp.setdefault((team, d), set()).add(cf.norm_name(name))
    df = df[played].copy()
    df["minutes"] = df["minutes"].map(cf._minutes)
    df = df[df["minutes"].fillna(0) > 0].copy()
    df["_sort"] = df["_date"]
    df["_opp"] = [opp.get((g, t)) for g, t in zip(df["game_id"], df["team_abbr"])]
    df = df.sort_values("_date", kind="stable")
    return cf._index(df, "name", None), dnp


class ReplayLogs(DP.GameLogs):
    """GameLogs whose league context comes from a truncated index, whose team-game table is the shared full one (every
    lookup in it is strictly prior to the day asked about) and whose role boosts are injected per team and day."""

    def __init__(self, idx, team_games, tracking=None, **kw):
        super().__init__("nba", idx, **kw)
        self._tg = team_games
        self.nba_tracking = tracking
        self.boosts: dict = {}

    def nba_team_games(self):
        return self._tg

    def nba_role_boosts(self, team, as_of):
        return self.boosts.get(team, {})


# --------------------------------------------------------------------------------------------------- scoring
def _logp(res: dict, y: float) -> float:
    fam, mu = res["family"], res["mu"]
    if mu <= 0:
        return math.log(1e-9) if y > 0 else 0.0
    if fam == "normal":
        sd = max(res["sd"], 1e-6)
        hi = norm.cdf((y + 0.5 - mu) / sd)
        lo = 0.0 if y <= 0 else norm.cdf((y - 0.5 - mu) / sd)
        return math.log(max(hi - lo, 1e-9))
    n = 1.0 / res["alpha"]
    zi = res.get("zi", 0.0)
    if zi > 0:
        pmf = nbinom.pmf(y, n, n / (n + mu / (1.0 - zi)))
        p = (zi if y == 0 else 0.0) + (1.0 - zi) * pmf
    else:
        p = nbinom.pmf(y, n, n / (n + mu))
    return math.log(max(float(p), 1e-9))


def _p_over(res: dict, line: float, ctx) -> float:
    return DP.p_over(res["family"], res["mu"], line, ctx, sd=res.get("sd"), alpha=res.get("alpha"),
                     zi=res.get("zi", 0.0)) or 0.0


def old_projection(stat: str, prior: list, ctx, opp: str, wt: float) -> dict | None:
    """The engine as it was before PR #147: EWM + usage + opponent factor on the stat's own history (combos on the SUMMED
    history), flat boost 1 + 0.04 * min(absence weight, 4) capped at 1.20, league-wide spread."""
    sdef = cf.NBA_STATS[stat]
    hl, ewm_w, _ = DP.stat_params("nba", stat)
    proj = DP.project_mu(DP._hist(prior, sdef), hl, ctx.opp_factor.get(opp, 1.0), ewm_w)
    if proj is None:
        return None
    mu = proj["mu"] * (min(1.20, 1.0 + 0.04 * min(wt, 4.0)) if wt > 0 else 1.0)
    fam = DP.PROJECT_STATS["nba"][stat]
    if fam == "negbin":
        return {"family": "negbin", "mu": mu, "alpha": ctx.alpha, "sd": math.sqrt(mu + ctx.alpha * mu * mu), "zi": 0.0}
    return {"family": "normal", "mu": mu, "sd": max(ctx.cv * mu, DP.MIN_SD_FRAC * mu, 1e-6), "alpha": None, "zi": 0.0}


# --------------------------------------------------------------------------------------------------- replay
def _actual(row: dict, stat: str) -> float | None:
    v = cf._sum_cols(row, cf.NBA_STATS[stat][0])
    return v


def variant_table(phase: str, chosen: dict | None) -> dict:
    """name -> (kind, feature switches, stats it is scored on). tune: the whole grid; test: only the chosen parameter."""
    v = {"old": ("old", {}, STATS), "old_noabs": ("old", {}, STATS), "hier": ("new", dict(HIER_SW), STATS),
         "hier_noabs": ("new", dict(HIER_SW), STATS)}
    if phase == "tune":
        for k in GRID["dvp"]:
            v[f"dvp_{k:g}"] = ("new", {"dvp_k": k}, FEATURE_STATS["dvp"])
        for g in GRID["pool"]:
            v[f"pool_{g:g}"] = ("new", {"pool_gamma": g}, FEATURE_STATS["pool"])
        for g in GRID["shot"]:
            v[f"shot_{g:g}"] = ("new", {"shot_gamma": g}, FEATURE_STATS["shot"])
        v["zinb"] = ("new", {"zinb": True}, FEATURE_STATS["zinb"])
        for k in GRID["eta"]:
            v[f"eta_{k:g}"] = ("new", {"eta_scale": k, "eta_off": ETA_OFF}, FEATURE_STATS["eta"])
    for name, sw in (chosen or {}).items():                     # explicit variants, both phases (see parse_variant)
        base_parts = set(SHIP.split("+"))
        new_parts = [part.split("_")[0] for part in name.split("+") if part not in base_parts]
        stats = sorted({st for fam in new_parts for st in FEATURE_STATS[fam]}, key=STATS.index) if new_parts else STATS
        v[name] = ("new", sw, tuple(stats))
    return v


def parse_variant(name: str) -> dict:
    """'eta_0.4' / 'dvp_150' / 'zinb' / 'eta_0.4+pool_0.5+shot_0.5' -> the NBA_FEATURES switches it stands for."""
    out: dict = {}
    for part in name.split("+"):
        fam, _, val = part.partition("_")
        if fam == "eta":
            out.update(eta_scale=float(val), eta_off=ETA_OFF)
        elif fam == "dvp":
            out["dvp_k"] = float(val)
        elif fam == "pool":
            out["pool_gamma"] = float(val)
        elif fam == "shot":
            out["shot_gamma"] = float(val)
        elif fam == "zinb":
            out["zinb"] = True
        elif fam == "trkast":
            out["trk_ast"] = float(val)
        elif fam == "trkreb":
            out["trk_reb"] = float(val)
        else:
            raise ValueError(f"unknown variant part {part!r}")
    return out


def _boost_key(sw: dict) -> tuple:
    return (float(sw.get("eta_scale", 1.0)), tuple(sw.get("eta_off", ())))


def replay(index: dict, dnp: dict, seasons: list, variants: dict, max_targets: int | None = None,
           tracking=None) -> pd.DataFrame:
    full_dates = {n: [r["_date"] for r in rows] for n, rows in index.items()}
    team_games = TC.TeamGames(index)
    # month blocks of target rows
    targets = []
    for name, rows in index.items():
        for j, r in enumerate(rows):
            if r.get("season_year") in seasons and r.get("_opp") and r.get("team_abbr"):
                targets.append((r["_date"][:7], r["_date"], name, j))
    targets.sort()
    if max_targets:
        targets = targets[:max_targets]
    keys = sorted({_boost_key(sw) for _, sw, _ in variants.values()})
    out = []
    cur_block, logs = None, None
    team_cache: dict = {}
    for block, d, name, j in targets:
        rows = index[name]
        r = rows[j]
        lo = bisect_left([x["season_year"] for x in rows], r["season_year"] - HISTORY_SEASONS + 1)
        prior = rows[lo:j]
        if len(prior) < DP.MIN_GAMES or np.mean([x["minutes"] for x in prior[-10:]]) < ELIG_MIN_MPG:
            continue
        if block != cur_block:
            cur_block = block
            first = f"{block}-01"
            trunc = {n: rs[max(0, bisect_left([x["season_year"] for x in rs], r["season_year"] - HISTORY_SEASONS + 1)):
                           bisect_left(full_dates[n], first)] for n, rs in index.items()}
            logs = ReplayLogs({"all": {n: rs for n, rs in trunc.items() if rs}}, team_games, tracking)
            team_cache = {}
            log.info("block %s: context from %d players", block, len(logs.idx["all"]))
        team, opp = r["team_abbr"], r["_opp"]
        key = (team, d)
        if key not in team_cache:
            team_cache[key] = _team_absences(index, full_dates, team, d, dnp, keys)
        absences, boosts_by_key = team_cache[key]
        wt_old = sum(w for p, w in absences.items() if p != name)
        start = datetime.fromisoformat(f"{d}T12:00:00+00:00")
        gk = f"nba:{d}:{opp}@{team}"
        l10 = {s: float(np.mean([v for v in (_actual(x, s) for x in prior[-10:]) if v is not None] or [0.0]))
               for s in STATS}
        for vname, (kind, sw, stats) in variants.items():
            use_abs = not vname.endswith("_noabs")
            for stat in stats:
                y = _actual(r, stat)
                if y is None:
                    continue
                ctx = logs.context(stat)
                if kind == "old":
                    res = old_projection(stat, prior, ctx, opp, wt_old if use_abs else 0.0)
                else:
                    logs.boosts = {team: boosts_by_key[_boost_key(sw)]} if use_abs else {}
                    with DP.nba_features(**sw):
                        res = DP._nba_projection(stat, name, prior, logs, team, opp, gk, start)
                if res is None:
                    continue
                line = math.floor(l10[stat]) + 0.5
                po = _p_over(res, line, ctx)
                out.append((vname, stat, name, d, r["season_year"], float(y), res["mu"], _logp(res, y), po,
                            float(y > line), abs(res["mu"] - y)))
    return pd.DataFrame(out, columns=["variant", "stat", "player", "date", "season", "y", "mu", "logp", "p_over",
                                      "over", "abs_err"])


def _team_absences(index, full_dates, team, d, dnp, keys=((1.0, ()),)) -> tuple:
    """(oracle absences {player: 1.0}, role boosts) for one team and day. Absent = a rotation regular (L10 >= 12 mpg, played
    for the team within 45 days) the box score lists as not playing."""
    listed = dnp.get((team, d), set())
    if not listed:
        return {}, {k: {} for k in keys}
    team_rows = {}
    for n, rows in index.items():
        k = bisect_left(full_dates[n], d)
        if k < 3 or rows[k - 1].get("team_abbr") != team:
            continue
        last = datetime.fromisoformat(rows[k - 1]["_date"])
        if (datetime.fromisoformat(d) - last).days > 45:
            continue
        team_rows[n] = rows[max(0, k - 30):k]
    res = H.compute_vacated_shares(team_rows, {p: 1.0 for p in listed})
    absent = {p: 1.0 for p in listed if p in res["rotation"]}
    return absent, {k: (H.compute_vacated_shares(team_rows, absent, eta_scale=k[0], eta_off=k[1])["boost"] if absent else {})
                    for k in keys}


# ------------------------------------------------------------------------------------------------ summarising
def _cluster_se(delta: pd.Series, cluster: pd.Series) -> float:
    g = delta.groupby(cluster.values)
    n = len(delta)
    if n < 3 or g.ngroups < 2:
        return float("nan")
    dm = float(delta.mean())
    s = (g.sum() - dm * g.size()).pow(2).sum()
    return math.sqrt(s * g.ngroups / (g.ngroups - 1)) / n


def compare(df: pd.DataFrame, base: str, other: str, stat: str) -> dict | None:
    a = df[(df.variant == base) & (df.stat == stat)].set_index(["player", "date"])
    b = df[(df.variant == other) & (df.stat == stat)].set_index(["player", "date"])
    a, b = a[~a.index.duplicated(keep=False)], b[~b.index.duplicated(keep=False)]     # namesake collisions: drop
    idx = a.index.intersection(b.index)
    if len(idx) < 50:
        return None
    a, b = a.loc[idx], b.loc[idx]
    d_ls, d_mae = b["logp"] - a["logp"], a["abs_err"] - b["abs_err"]      # positive = `other` better
    d_br = (a["p_over"] - a["over"]) ** 2 - (b["p_over"] - b["over"]) ** 2
    cl = pd.Series(idx.get_level_values(0))
    se = _cluster_se(d_ls.reset_index(drop=True), cl)
    return {"n": len(idx), "d_logscore": float(d_ls.mean()), "se_logscore": se,
            "z_logscore": float(d_ls.mean() / se) if se and se == se and se > 0 else float("nan"),
            "d_mae": float(d_mae.mean()), "d_brier": float(d_br.mean())}


def levels(df: pd.DataFrame) -> pd.DataFrame:
    g = df.assign(sq=(df.mu - df.y) ** 2, bias=df.mu - df.y, br=(df.p_over - df.over) ** 2).groupby(["stat", "variant"])
    return g.agg(n=("y", "size"), mae=("abs_err", "mean"), bias=("bias", "mean"), logscore=("logp", "mean"),
                 brier=("br", "mean")).reset_index()


def report(df: pd.DataFrame, phase: str, base: str = "hier") -> str:
    lv = levels(df)
    lines = [f"# NBA engine walk-forward ({phase})", "",
             f"Rows scored: {len(df)}; seasons {sorted(df.season.unique())}. ACCURACY only (no prop lines). "
             "Absences are ORACLE (upper bound); no spreads (blowout / OT terms not exercised); Brier uses a proxy line.", "",
             "## Levels (mean over rotation player-games)", "", "| stat | variant | n | MAE | bias | log score | Brier |",
             "|---|---|---|---|---|---|---|"]
    for _, r in lv.sort_values(["stat", "variant"]).iterrows():
        lines.append(f"| {r.stat} | {r.variant} | {int(r.n)} | {r.mae:.3f} | {r.bias:+.3f} | {r.logscore:.4f} | {r.brier:.4f} |")
    lines += ["", f"## Paired vs `{base}` (positive = variant better; z = log-score gain / player-clustered SE; "
              f"adopt bar z >= {ADOPT_Z:.2f} and MAE gain > 0)", "",
              "| stat | variant | n | d log score | z | d MAE | d Brier |", "|---|---|---|---|---|---|---|"]
    for stat in STATS:
        for v in sorted(set(df.variant) - {base}):
            c = compare(df, base, v, stat)
            if c:
                lines.append(f"| {stat} | {v} | {c['n']} | {c['d_logscore']:+.4f} | {c['z_logscore']:+.2f} | "
                             f"{c['d_mae']:+.4f} | {c['d_brier']:+.5f} |")
    for other in ["hier"] + sorted(v for v in set(df.variant) if v.startswith("eta")):
        lines += ["", f"## Old engine vs `{other}` (positive = `{other}` better)", "",
                  "| stat | n | d log score | z | d MAE | d Brier |", "|---|---|---|---|---|---|"]
        for stat in STATS:
            c = compare(df, "old", other, stat)
            if c:
                lines.append(f"| {stat} | {c['n']} | {c['d_logscore']:+.4f} | {c['z_logscore']:+.2f} | {c['d_mae']:+.4f} | "
                             f"{c['d_brier']:+.5f} |")
    return "\n".join(lines) + "\n"


def choose(df: pd.DataFrame, base: str = "hier") -> dict:
    """Per feature family, the grid point with the best summed paired log-score gain over the family's stats (selection on
    the TUNE season only). -> {variant name: feature switches}; a family whose best grid point has no gain is absent."""
    out = {}
    fams = {"dvp": "dvp_k", "pool": "pool_gamma", "shot": "shot_gamma", "eta": "eta_scale"}
    for fam, key in fams.items():
        best, score = None, 0.0
        for v in sorted(x for x in set(df.variant) if x.startswith(fam + "_")):
            tot = sum((compare(df, base, v, s) or {}).get("d_logscore", 0.0) * (compare(df, base, v, s) or {}).get("n", 0)
                      for s in FEATURE_STATS[fam])
            if tot > score:
                best, score = v, tot
        if best:
            out[best] = {key: float(best.split("_")[1])}
            if fam == "eta":
                out[best]["eta_off"] = ETA_OFF
    if "zinb" in set(df.variant):
        tot = sum((compare(df, base, "zinb", s) or {}).get("d_logscore", 0.0) * (compare(df, base, "zinb", s) or {}).get("n", 0)
                  for s in FEATURE_STATS["zinb"])
        if tot > 0:
            out["zinb"] = {"zinb": True}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--master", required=True)
    ap.add_argument("--phase", choices=("tune", "test"), required=True)
    ap.add_argument("--seasons", required=True, help="comma list of season_year values (2024 = 2024-25)")
    ap.add_argument("--chosen", default="", help="test phase: variant names selected on tune, comma list (e.g. dvp_150,zinb)")
    ap.add_argument("--out")
    ap.add_argument("--rows-out", help="write the per-row scores here (parquet)")
    ap.add_argument("--max-targets", type=int)
    ap.add_argument("--tracking-dir", help="local clone of the community tracking repo (enables the trk_* variants)")
    ap.add_argument("--base", default="hier", help="baseline variant for the paired tables (default hier)")
    ap.add_argument("--only", default="", help="restrict to these variant names (comma list); hier is always kept")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    seasons = [int(s) for s in a.seasons.split(",")]
    chosen = {name: parse_variant(name) for name in a.chosen.split(",") if name}
    index, dnp = load_master(a.master)
    table = variant_table(a.phase, chosen)
    if a.only:
        keep = set(a.only.split(",")) | {"hier"}
        table = {k: v for k, v in table.items() if k in keep}
    tracking = None
    if a.tracking_dir:
        from nba.data import tracking as TR
        tracking = TR.load(range(min(seasons), max(seasons) + 1), a.tracking_dir, source_layout=True)
        log.info("tracking coverage: %s", tracking.coverage())
    df = replay(index, dnp, seasons, table, a.max_targets, tracking)
    if a.rows_out:
        df.to_parquet(a.rows_out)
    text = report(df, a.phase, a.base)
    if a.phase == "tune":
        sel = choose(df, a.base)
        text += "\n## Selected on this season\n\n" + (", ".join(sorted(sel)) or "(none)") + "\n"
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
