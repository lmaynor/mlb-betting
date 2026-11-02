"""The SERVING NBA engine (PR #147 + walk-forward follow-ups) against HISTORICAL BettingPros consensus lines.

    PYTHONPATH=. python -m scripts.nba_engine_vs_market --bp-dir <dir with {slug}/{date}.parquet> --master <player_boxscores_master.csv> \
        --work-dir <scratch> [--sample-every N] [--workers 8] [--out-md tables.md] [--out-json res.json]

Nothing scored the serving engine against real book lines before this: nba/props/engine_eval.py replays it against the BOX
SCORE (accuracy only) and the 2026-10-23 price test used the TRAINED research model. This prices every historical consensus
prop (mlb_core.odds.bettingpros_loader: >= 3 two-sided real books, de-vigged median, one modal line per (date, player, stat),
fail-closed identity join) with the production functions `ev.daily_projections._nba_projection` / `GameLogs.context` /
`p_over` AS OF the game, using only master rows dated strictly before it (league context rebuilt monthly from rows before the
month, like engine_eval), and scores the probabilities against the market's on the SAME rows.

Variants priced on every prop (all paired on the same rows):
  old / old_noabs          the engine before PR #147 (independent EWM per stat, flat 1 + 0.04 * absences boost; engine_eval `old`)
  ship / ship_noabs        the engine as SHIPPED (`DP.NBA_FEATURES` read at run time) with opponent information present
  ship_live / ship_live_noabs
                           the same engine with NO opponent column, which is what production feeds it today: the live master
                           loader (cockpit_form._load_nba) did not set `_opp` before the 2026-11-02 fix (the master has no opp_abbr),
                           so the opponent factor and the team-makes assist factor were inert on the real board. engine_eval derives `_opp`.
  *_noabs                  NO absence information (what a pricer without the injury report sees).
  (no suffix)              ORACLE absences: every rotation player (L10 >= 12 mpg) the box score lists as not playing is Out with
                           probability 1, a leak of the very game being priced.

Why the absence choice matters (state it before reading any number): the market quote is the LAST snapshot of the day, after
injury news, so it prices the known absences. `*_noabs` therefore under-informs the engine relative to a live board that has the
injury report; oracle absences (which include late scratches and DNP-CDs the books may not have priced) over-inform it. The live
engine sits between the two. Identity is by player id through PlayerResolver over the players who played that UTC date; the
master's `date` is the UTC tip date, which is also what BettingPros' `scheduled` converts to (95.9% exact-name match vs 32.9% for the
ET day), so the join is on the UTC date of `scheduled`. Props whose player did not play are void at the book and are not scored
(the same conditioning as the 2026-10-23 test).

The consensus is one last snapshot per day: there is NO opening/closing series, so CLV is not computable and the verdict can be at
most UNDETERMINED / NO_EDGE. Best-price ROI is mostly a best-quote artifact (2026-10-23 study); the no-model controls are always
printed beside every ROI.
"""
from __future__ import annotations

import argparse
import dataclasses
import glob
import hashlib
import json
import logging
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
from bisect import bisect_left
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev import daily_projections as DP  # noqa: E402
from mlb_core.data.player_names import PlayerResolver, norm_name  # noqa: E402
from mlb_core.odds import bettingpros_loader as L  # noqa: E402
from mlb_core.odds import bettingpros_props as bpp  # noqa: E402
from mlb_core.odds import model_vs_market as MVM  # noqa: E402
from nba.props import engine_eval as EE  # noqa: E402
from nba.props import team_context as TC  # noqa: E402
from nhl.props import market as M  # noqa: E402

log = logging.getLogger("scripts.nba_engine_vs_market")

STAT_MAP = {"pts": "points", "reb": "rebounds", "ast": "assists", "threes": "threes", "pra": "pra"}   # BettingPros label -> engine stat
SLUG = {k: v for k, v in bpp.SPORTS["NBA"].items() if k in STAT_MAP}
BP_TEAM_FIX = {"PHO": "PHX", "NOR": "NOP", "UTH": "UTA"}        # BettingPros tricodes DP.canon_team cannot resolve
GRACE_MIN = L.DEFAULT_GRACE_MINUTES["NBA"]
CLIP = (0.005, 0.995)
THRESHOLDS = (0.02, 0.03, 0.04, 0.05)
SEL_THRS = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08)
N_BOOT = 600
VARIANTS = {   # name -> (kind, uses oracle absences, opponent info present)
    "old": ("old", True, True), "old_noabs": ("old", False, True),
    "ship": ("new", True, True), "ship_noabs": ("new", False, True),
    "ship_live": ("new", True, False), "ship_live_noabs": ("new", False, False),
}
TUNE_SEASON = 2023          # nba/props/engine_eval.py chose the shipped constants on this season (season_year; 2023 = 2023-24)


# ----------------------------------------------------------------------------------------------------------------- data
def canon(x) -> str | None:
    return DP.canon_team("nba", x)


def bp_team(x) -> str | None:
    s = str(x).strip().upper() if x is not None else ""
    return canon(BP_TEAM_FIX.get(s, s))


def load_consensus(bp_dir: str, cache: str | None = None) -> pd.DataFrame:
    """The 5-market NBA consensus (grace +10 min on the postgame test) from a LOCAL copy of Odds/bettingpros_nba."""
    if cache and os.path.exists(cache):
        return pd.read_parquet(cache)
    parts = []
    for stat, slug in SLUG.items():
        files = sorted(glob.glob(os.path.join(bp_dir, slug, "*.parquet")))
        if not files:
            continue
        ld = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        ld = ld.drop_duplicates(subset=bpp.DEDUP_KEY, keep="last")
        c = L.consensus_from_long(ld, min_books=3, stat_map={slug: stat}, sport="NBA", grace_minutes=GRACE_MIN)
        log.info("consensus %s: %d quote rows -> %d props", stat, len(ld), len(c))
        parts.append(c)
    cons = pd.concat(parts, ignore_index=True)
    if cache:
        cons.to_parquet(cache, index=False)
    return cons


def load_extras(bp_dir: str, cons: pd.DataFrame, cache: str | None = None) -> pd.DataFrame:
    """Second-best / median American price per side (scripts/nba_bp_price_extras.py; same modal line as the consensus)."""
    if cache and os.path.exists(cache):
        return pd.read_parquet(cache)
    from scripts import nba_bp_price_extras as X
    c = cons[["event_id", "player_id", "stat", "line"]]
    parts = [X.one_market(bp_dir, stat, slug, c) for stat, slug in SLUG.items() if stat in set(cons["stat"])]
    ex = pd.concat(parts, ignore_index=True)
    if cache:
        ex.to_parquet(cache, index=False)
    return ex


def build_index(df: pd.DataFrame) -> tuple:
    """Box-score master frame -> (cockpit-shaped index, dnp {(team, date): {norm names listed as not playing}}) with every team
    code folded to the canonical tricode, so opponent factors (keyed canonical by GameLogs._build_context) and the team-game
    table (keyed by the player rows' own codes) agree. engine_eval.load_master keeps the master's raw codes (GS / NO / NY / SA / PHO),
    which the context's canonical keys do not match: its opponent factor is silently 1.0 for those five opponents."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "master.csv")
        df.to_csv(p, index=False)
        index, dnp = EE.load_master(p)
    for rows in index.values():
        for r in rows:
            r["team_abbr"] = canon(r.get("team_abbr")) or r.get("team_abbr")
            r["_opp"] = canon(r.get("_opp")) or r.get("_opp")
    dnp = {(canon(t) or t, d): names for (t, d), names in dnp.items()}
    return index, dnp


def load_master(path: str) -> tuple:
    return build_index(pd.read_csv(path, low_memory=False))


def _strip_opp(index: dict) -> dict:
    """Light copy of the index for TeamGames with the opponent code removed (production's real master has none)."""
    return {n: [{"team_abbr": r.get("team_abbr"), "_date": r.get("_date"), "_sort": r.get("_sort"),
                 "field_goals_made": r.get("field_goals_made"), "field_goals_attempted": r.get("field_goals_attempted"),
                 "free_throws_made": r.get("free_throws_made"), "free_throws_attempted": r.get("free_throws_attempted"),
                 "_opp": None} for r in rows] for n, rows in index.items()}


# ------------------------------------------------------------------------------------------------------------------ join
def join_props(cons: pd.DataFrame, index: dict, dnp: dict, start: str | None = None) -> tuple:
    """Consensus rows -> (props frame with the master row each one resolves to, coverage). Every consensus row is either kept or
    counted under exactly one reason (MVM.coverage raises if the books do not balance). Identity is fail-closed."""
    cons = cons.copy()
    if start:
        cons = cons[cons["game_date"] >= start]
    kept, n_in, dropped = MVM.screen_consensus(cons, STAT_MAP, 3)
    dropped = dict(dropped, no_master_day=0, void_dnp=0, unresolved=0, ambiguous=0, no_master_row=0, team_mismatch=0,
                   namesake_history=0, duplicate=0, no_outcome=0)
    kept = kept.drop(columns=["date"]).assign(utc=pd.to_datetime(kept["scheduled"], utc=True, errors="coerce", format="mixed").dt.strftime("%Y-%m-%d"))
    pos, by_date, pids = {}, {}, {}
    for name, rows in index.items():
        for j, r in enumerate(rows):
            pid = r.get("player_id")
            pos[(pid, r["_date"])] = (name, j)
            pids.setdefault(name, set()).add(pid)
            by_date.setdefault(r["_date"], []).append({"player_id": pid, "full_name": r.get("name"), "team": r.get("team_abbr")})
    dnp_day: dict = {}
    for (t, d), names in dnp.items():
        dnp_day.setdefault(d, set()).update(names)
    resolvers: dict = {}
    out, why = [], []
    for rec in kept.itertuples(index=False):
        d = rec.utc
        rows = by_date.get(d)
        if rows is None:
            why.append("no_master_day")
            out.append(None)
            continue
        rs = resolvers.get(d) or resolvers.setdefault(d, PlayerResolver(rows))
        pid, how = rs.resolve(str(rec.player))
        if pid is None:
            why.append("void_dnp" if norm_name(str(rec.player)) in dnp_day.get(d, ()) else
                       ("ambiguous" if how == "ambiguous" else "unresolved"))
            out.append(None)
            continue
        hit = pos.get((pid, d))
        if hit is None:
            why.append("no_master_row")
            out.append(None)
            continue
        name, j = hit
        r = index[name][j]
        if r["team_abbr"] not in {bp_team(rec.away), bp_team(rec.home)}:
            why.append("team_mismatch")
            out.append(None)
            continue
        if len(pids[name]) > 1:
            why.append("namesake_history")
            out.append(None)
            continue
        why.append(None)
        out.append((name, j, pid, r["season_year"], r["team_abbr"], r["_opp"], EE._actual(r, STAT_MAP[rec.stat])))
    kept = kept.assign(_hit=out, _why=why)
    for k in set(w for w in why if w):
        dropped[k] += int(sum(1 for w in why if w == k))
    ok = kept[kept["_why"].isna()].copy()
    res = pd.DataFrame(ok["_hit"].tolist(), columns=["name_key", "j", "master_pid", "season", "team_m", "opp_m", "y"], index=ok.index)
    ok = pd.concat([ok.drop(columns=["_hit", "_why", "team", "model_stat"]), res], axis=1)
    dup = ok.duplicated(["master_pid", "utc", "stat"], keep=False)
    dropped["duplicate"] = int(dup.sum())
    nan_y = ok["y"].isna() & ~dup
    dropped["no_outcome"] = int(nan_y.sum())
    ok = ok[~dup & ~nan_y]
    props = ok.reset_index(drop=True).rename(columns={"utc": "date", "stat": "bp_stat"})
    props["stat"] = props["bp_stat"].map(STAT_MAP)
    props["over"] = (props["y"].to_numpy(float) > props["line"].to_numpy(float)).astype(float)
    props["push"] = np.isclose(props["y"].to_numpy(float), props["line"].to_numpy(float))
    cov = MVM.coverage(n_in, len(props), dropped)
    return props, cov



# --------------------------------------------------------------------------------------------------------------- replay
class Replayer:
    """Prices props with the serving engine as of each game. Everything is cut to master rows strictly before the game's date."""

    def __init__(self, index: dict, dnp: dict):
        self.index, self.dnp = index, dnp
        self.full_dates = {n: [r["_date"] for r in rows] for n, rows in index.items()}
        self.seasons = {n: [r["season_year"] for r in rows] for n, rows in index.items()}
        self.tg = TC.TeamGames(index)                    # opponent codes present
        self.tg_noopp = TC.TeamGames(_strip_opp(index))  # production: none
        self.switches = dict(DP.NBA_FEATURES)            # the engine as SHIPPED, read now
        self.keys = sorted({EE._boost_key(self.switches)})

    def _logs(self, block: str, season: int) -> tuple:
        first = f"{block}-01"
        lo_season = season - EE.HISTORY_SEASONS + 1
        trunc = {}
        for n, rs in self.index.items():
            a = bisect_left(self.seasons[n], lo_season)
            b = bisect_left(self.full_dates[n], first)
            if b > a:
                trunc[n] = rs[a:b]
        logs = EE.ReplayLogs({"all": trunc}, self.tg)
        logs_live = EE.ReplayLogs({"all": trunc}, self.tg_noopp)
        for stat in ("points", "rebounds", "assists", "threes", "pra"):      # every context the engine can ask for (pra builds on three)
            self._ctx_live(logs, logs_live, stat)
        return logs, logs_live

    @staticmethod
    def _ctx_live(logs, logs_live, stat):
        """League context WITHOUT opponent information: identical dispersion, empty opponent tables (what a master with no `_opp`
        builds)."""
        key = (stat, None)
        if key not in logs_live._ctx:
            c = logs.context(stat)
            logs_live._ctx[key] = dataclasses.replace(c, opp_factor={}, opp_raw={}, opp_cnt={}, nba_opp_pos_raw={}, nba_opp_pos_cnt={})
        return logs_live._ctx[key]

    def price_block(self, block: str, targets: list) -> list:
        """targets: [(name, j, [(prop_idx, stat, line), ...])] all in month `block` -> [(prop_idx, variant, mu, p_over)]."""
        out = []
        logs = logs_live = None
        cur_season = None
        team_cache: dict = {}
        for name, j, props in targets:
            rows = self.index[name]
            r = rows[j]
            if logs is None or r["season_year"] != cur_season:
                cur_season = r["season_year"]
                logs, logs_live = self._logs(block, cur_season)
                team_cache = {}
            lo = bisect_left(self.seasons[name], r["season_year"] - EE.HISTORY_SEASONS + 1)
            prior = rows[lo:j]
            if len(prior) < DP.MIN_GAMES:
                continue
            d, team, opp = r["_date"], r["team_abbr"], r["_opp"]
            key = (team, d)
            if key not in team_cache:
                team_cache[key] = EE._team_absences(self.index, self.full_dates, team, d, self.dnp, self.keys)
            absences, boosts_by_key = team_cache[key]
            wt_old = sum(w for p, w in absences.items() if p != name)
            start = datetime.fromisoformat(f"{d}T12:00:00+00:00")
            gk = f"nba:{d}:{opp}@{team}"
            for vname, (kind, use_abs, with_opp) in VARIANTS.items():
                lg = logs if with_opp else logs_live
                for idx, stat, line in props:
                    ctx = lg.context(stat) if with_opp else self._ctx_live(logs, logs_live, stat)
                    if kind == "old":
                        res = EE.old_projection(stat, prior, ctx, opp, wt_old if use_abs else 0.0)
                    else:
                        lg.boosts = {team: boosts_by_key[EE._boost_key(self.switches)]} if use_abs else {}
                        with DP.nba_features(**self.switches):
                            res = DP._nba_projection(stat, name, prior, lg, team, opp, gk, start)
                    if res is None:
                        continue
                    out.append((idx, vname, float(res["mu"]), float(EE._p_over(res, line, ctx))))
        return out


_R: Replayer | None = None


def _work(item):
    block, targets = item
    t = time.time()
    res = _R.price_block(block, targets)
    return block, res, time.time() - t


def replay_props(props: pd.DataFrame, index: dict, dnp: dict, workers: int = 8, sample_every: int = 1) -> tuple:
    """-> (wide frame: props + mu_<variant> / p_<variant>, replay stats). Sampling is by GAME DATE (every Nth distinct date)."""
    global _R
    dates = sorted(props["date"].unique())
    keep_dates = set(dates[::max(1, sample_every)])
    sel = props[props["date"].isin(keep_dates)]
    stats = {"props_in": int(len(props)), "dates_total": len(dates), "dates_scored": len(keep_dates), "sample_every": sample_every}
    _R = Replayer(index, dnp)
    groups: dict = {}
    for pi, p in sel.iterrows():
        groups.setdefault((p["date"][:7], p["name_key"], int(p["j"])), []).append((int(pi), p["stat"], float(p["line"])))
    blocks: dict = {}
    for (block, name, j), plist in groups.items():
        blocks.setdefault(block, []).append((name, j, plist))
    items = sorted(blocks.items(), key=lambda kv: -len(kv[1]))
    log.info("%d props, %d player-games, %d month blocks, %d workers", len(sel), len(groups), len(items), workers)
    t0 = time.time()
    rows = []
    if workers <= 1:
        for it in items:
            b, res, dt = _work(it)
            rows.extend(res)
            log.info("block %s: %d prices in %.0fs (%.0fs total)", b, len(res), dt, time.time() - t0)
    else:
        with mp.get_context("fork").Pool(workers) as pool:
            for b, res, dt in pool.imap_unordered(_work, items):
                rows.extend(res)
                log.info("block %s: %d prices in %.0fs (%.0fs total)", b, len(res), dt, time.time() - t0)
    long = pd.DataFrame(rows, columns=["idx", "variant", "mu", "p"])
    wide = long.pivot(index="idx", columns="variant", values=["mu", "p"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    out = sel.join(wide, how="left")
    stats["seconds"] = time.time() - t0
    return out, stats


# --------------------------------------------------------------------------------------------------------- analysis
def _ll(y, p):
    p = np.clip(np.asarray(p, dtype=float), *CLIP)
    y = np.asarray(y, dtype=float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def attach_prices(f: pd.DataFrame, extras: pd.DataFrame) -> pd.DataFrame:
    ex = extras.assign(event_id=extras["event_id"].astype("Int64"), player_id=pd.to_numeric(extras["player_id"]).astype("Int64"))
    f = f.assign(event_id=f["event_id"].astype("Int64"), player_id=pd.to_numeric(f["player_id"]).astype("Int64"))
    keep = ["event_id", "player_id", "bp_stat", "line", "second_o_am", "second_u_am", "med_o_am", "med_u_am"]
    ex = ex.rename(columns={"stat": "bp_stat"})[keep]
    f = f.merge(ex, on=["event_id", "player_id", "bp_stat", "line"], how="left")
    f["game_id"] = f["event_id"].astype(str)
    f["p_market"] = f["p_over_cons"].astype(float)
    f["upd_min"] = ((pd.to_datetime(f["updated_max"], utc=True, errors="coerce")
                     - pd.to_datetime(f["scheduled"], utc=True, errors="coerce", format="mixed")).dt.total_seconds() / 60.0)
    f["rotation"] = True
    return f


def scored_frame(f: pd.DataFrame, variants=tuple(VARIANTS)) -> pd.DataFrame:
    """Rows priced by EVERY variant, pushes out (none expected: every BettingPros line is x.5), for a paired comparison."""
    need = [f"p_{v}" for v in variants]
    g = f.dropna(subset=need + ["p_market", "over", "best_over_odds", "best_under_odds"])
    g = g[~g["push"]].copy()
    for v in variants:
        g[f"p_{v}"] = g[f"p_{v}"].clip(*CLIP)
    g["p_market"] = g["p_market"].clip(*CLIP)
    return g.reset_index(drop=True)


def paired(g: pd.DataFrame, a: str, b: str) -> dict:
    """Mean of logloss(a) - logloss(b) per prop (negative = a better) with a date-clustered SE."""
    d = pd.Series(_ll(g["over"], g[a]) - _ll(g["over"], g[b]))
    se = EE._cluster_se(d, g["game_date"].reset_index(drop=True))
    return {"d": float(d.mean()), "se": se, "z": float(d.mean() / se) if se and se == se and se > 0 else float("nan")}


def blend_c(g: pd.DataFrame, col: str) -> tuple:
    if len(g) < 200 or g["game_date"].nunique() < 5:
        return (float("nan"), float("nan"))
    r = M.blend_regression(g["over"], g["p_market"], g[col], g["game_date"])
    return float(r["c"]), float(r["t_c"])


def accuracy_table(g: pd.DataFrame, new: str, old: str, by: list) -> pd.DataFrame:
    rows = []
    for key, h in g.groupby(by):
        key = key if isinstance(key, tuple) else (key,)
        c, t = blend_c(h, f"p_{new}")
        c_old, t_old = blend_c(h, f"p_{old}")
        pn, pm = paired(h, f"p_{new}", f"p_{old}"), paired(h, f"p_{new}", "p_market")
        rows.append(dict(zip(by, key), n=len(h), ll_market=_ll(h["over"], h["p_market"]).mean(), ll_new=_ll(h["over"], h[f"p_{new}"]).mean(),
                         ll_old=_ll(h["over"], h[f"p_{old}"]).mean(), d_new_old=pn["d"], se_new_old=pn["se"], z_new_old=pn["z"],
                         d_new_mkt=pm["d"], se_new_mkt=pm["se"], c_new=c, t_new=t, c_old=c_old, t_old=t_old,
                         over_rate=h["over"].mean(), mean_p_market=h["p_market"].mean(), mean_p_new=h[f"p_{new}"].mean()))
    return pd.DataFrame(rows)


def wf_blend(g: pd.DataFrame, col: str, min_train: int = 500, max_shift: float = MVM.BLEND_MAX_SHIFT) -> pd.DataFrame:
    """p_blend per (stat, season) from (a, b, c) fitted on EARLIER seasons of the same stat only (the first season is never scored)."""
    parts = []
    for stat, h in g.groupby("stat"):
        for s in sorted(h["season"].unique()):
            tr, te = h[h["season"] < s], h[h["season"] == s]
            if len(tr) < min_train or len(te) == 0:
                continue
            fit = M.blend_regression(tr["over"], tr["p_market"], tr[col], tr["game_date"])
            a, b, c = float(fit["a"]), float(fit["b"]), float(fit["c"])
            lm = M.logit(te["p_market"].to_numpy(float))
            ld = M.logit(te[col].to_numpy(float)) - lm
            pb = np.clip(MVM._sig(a + b * lm + c * ld), te["p_market"] - max_shift, te["p_market"] + max_shift)
            parts.append(te.assign(p_blend=np.asarray(pb), fit_c=c, fit_t=float(fit["t_c"])))
    return pd.concat(parts, ignore_index=True) if parts else g.iloc[0:0].assign(p_blend=[], fit_c=[], fit_t=[])


def bets(t: pd.DataFrame, pcol: str, thr: float, px=("best_over_odds", "best_under_odds")) -> pd.DataFrame:
    """Flat 1u on the side whose probability beats its price's implied probability by >= thr (selection and payout at that price)."""
    t = t.dropna(subset=list(px))
    y = t["over"].to_numpy(float)
    oo, ou = t[px[0]].to_numpy(float), t[px[1]].to_numpy(float)
    p = t[pcol].to_numpy(float)
    io, iu = MVM._implied(oo), MVM._implied(ou)
    e_over, e_under = p - io, (1.0 - p) - iu
    take_over = e_over >= e_under
    edge = np.where(take_over, e_over, e_under)
    sel = edge >= thr
    win = np.where(take_over, y == 1.0, y == 0.0)
    prof = np.where(win, np.where(take_over, 1.0 / io, 1.0 / iu) - 1.0, -1.0)
    both = (np.where(y == 1.0, 1.0 / io - 1.0, -1.0) + np.where(y == 0.0, 1.0 / iu - 1.0, -1.0)) / 2.0
    out = t.loc[sel, ["game_date", "season", "stat", "n_books"]].copy()
    out["profit"], out["both"], out["edge"] = prof[sel], both[sel], edge[sel]
    out["side"] = np.where(take_over[sel], "over", "under")
    return out


def fixed_side(t: pd.DataFrame, side: str, px) -> pd.DataFrame:
    t = t.dropna(subset=list(px))
    y = t["over"].to_numpy(float)
    imp = MVM._implied(t[px[0] if side == "over" else px[1]].to_numpy(float))
    win = (y == 1.0) if side == "over" else (y == 0.0)
    return t[["game_date", "season", "stat", "n_books"]].assign(profit=np.where(win, 1.0 / imp - 1.0, -1.0), both=0.0)


def summ(b: pd.DataFrame, n_boot: int = N_BOOT) -> dict:
    if len(b) == 0:
        return {"n": 0, "roi": float("nan"), "lo": float("nan"), "hi": float("nan"), "vs_noskill": float("nan")}
    lo, hi = MVM._cluster_boot_mean(b["profit"].to_numpy(), b["game_date"].astype(str), n_boot)
    return {"n": int(len(b)), "roi": float(b["profit"].mean()), "lo": lo, "hi": hi,
            "vs_noskill": float((b["profit"] - b["both"]).mean())}


def fmt(r: dict, ci: bool = True) -> str:
    if not r["n"]:
        return "n=0"
    s = f"{r['roi']:+.1%}"
    return f"{s} [{r['lo']:+.1%}, {r['hi']:+.1%}] (n={r['n']:,})" if ci else f"{s} (n={r['n']:,})"


PRICES = {"median": ("med_o_am", "med_u_am"), "best": ("best_over_odds", "best_under_odds")}


def wf_threshold(t: pd.DataFrame, pcol: str, px, seasons: list, min_bets: int = 300) -> tuple:
    """Out-of-sample ROI with the threshold for season s chosen on seasons < s only (max ROI over SEL_THRS with >= min_bets bets)."""
    parts, chosen = [], {}
    for s in seasons:
        prior, test = t[t["season"] < s], t[t["season"] == s]
        best, best_roi = None, -9.0
        for thr in SEL_THRS:
            bb = bets(prior, pcol, thr, px)
            if len(bb) >= min_bets and bb["profit"].mean() > best_roi:
                best, best_roi = thr, float(bb["profit"].mean())
        if best is None:
            continue
        chosen[int(s)] = (best, best_roi)
        parts.append(bets(test, pcol, best, px))
    return (pd.concat(parts) if parts else None), chosen


def roi_section(t: pd.DataFrame, label: str, mcol: str, res: dict, P) -> None:
    """ROI tables for one engine column on the walk-forward scored frame `t` (has p_blend from the same column)."""
    seasons = sorted(t["season"].unique())
    for pname, px in PRICES.items():
        P(f"\n#### {label}: flat 1u at the **{pname}-book** price, scored seasons {', '.join(map(str, seasons))}\n")
        P("| edge >= | engine ROI [95% CI] (n) | walk-forward blend ROI [CI] (n) | line shopping, NO model (n) | engine vs betting both sides |")
        P("|---|---|---|---|---|")
        for thr in THRESHOLDS:
            e, bl, m = summ(bets(t, mcol, thr, px)), summ(bets(t, "p_blend", thr, px)), summ(bets(t, "p_market", thr, px), 300)
            res.setdefault(f"{label}|{pname}", {})[thr] = {"engine": e, "blend": bl, "market": m}
            P(f"| {thr:.0%} | {fmt(e)} | {fmt(bl)} | {fmt(m, False)} | {e['vs_noskill']:+.1%} |" if e["n"] else f"| {thr:.0%} | n=0 | | | |")
        ob, ub = summ(fixed_side(t, "over", px), 300), summ(fixed_side(t, "under", px), 300)
        P(f"\nBlind controls at this price: always Over {fmt(ob)}; always Under {fmt(ub)}.")
        res.setdefault(f"{label}|{pname}", {})["always_over"], res[f"{label}|{pname}"]["always_under"] = ob, ub


def render(F: pd.DataFrame, extras_ok: bool, cov: dict, rstats: dict, meta: dict) -> tuple:
    """-> (markdown tables, result dict)."""
    md: list = []
    P = md.append
    res: dict = {"meta": meta, "coverage": cov, "replay": rstats}
    g = scored_frame(F)
    res["n_scored"] = int(len(g))
    seasons = sorted(g["season"].unique())
    P(f"All variants priced {len(g):,} props (of {len(F):,} joined; {rstats['dates_scored']} of {rstats['dates_total']} game dates, "
      f"every {rstats['sample_every']}th); seasons {', '.join(map(str, seasons))} (season = start year; {TUNE_SEASON} is the season engine_eval "
      f"tuned the shipped constants on).\n")

    P("### 1. Log loss per stat and season: engine vs old engine vs market\n")
    P("`d new-old` and `d new-mkt` are mean per-prop log-loss differences (NEGATIVE = the shipped engine is better), SE clustered by game date. "
      "`c` = blend coefficient of the engine on top of the market (y ~ a + b logit(mkt) + c (logit(engine) - logit(mkt)), date-clustered t); "
      "c > 0 means information beyond the market. Both engines are priced on the SAME rows.\n")
    for tag, new, old in (("ORACLE absences (leaks the game being priced)", "ship", "old"),
                          ("NO absence information (pre-news pricer)", "ship_noabs", "old_noabs"),
                          ("As production runs today: no opponent column, ORACLE absences", "ship_live", "old")):
        P(f"\n#### {tag}: `{new}` vs `{old}`\n")
        P("| stat | season | n | LL market | LL new | LL old | d new-old (SE) | d new-mkt (SE) | c new (t) | c old (t) |")
        P("|---|---|---|---|---|---|---|---|---|---|")
        tabs = {}
        for by in (["stat"], ["stat", "season"]):
            tabs[tuple(by)] = accuracy_table(g, new, old, by)
        order = []
        for stat in ["points", "rebounds", "assists", "threes", "pra"]:
            order.append((stat, "all", tabs[("stat",)][tabs[("stat",)].stat == stat]))
            for s in seasons:
                order.append((stat, str(s), tabs[("stat", "season")][(tabs[("stat", "season")].stat == stat) & (tabs[("stat", "season")].season == s)]))
        allrow = accuracy_table(g.assign(all="all"), new, old, ["all"])
        order.append(("ALL", "all", allrow))
        for s in seasons:
            order.append(("ALL", str(s), accuracy_table(g[g.season == s].assign(all="all"), new, old, ["all"])))
        for stat, s, row in order:
            if len(row) == 0:
                continue
            r = row.iloc[0]
            P(f"| {stat} | {s} | {int(r.n):,} | {r.ll_market:.4f} | {r.ll_new:.4f} | {r.ll_old:.4f} | {r.d_new_old:+.4f} ({r.se_new_old:.4f}) | "
              f"{r.d_new_mkt:+.4f} ({r.se_new_mkt:.4f}) | {r.c_new:+.2f} ({r.t_new:+.1f}) | {r.c_old:+.2f} ({r.t_old:+.1f}) |")
        res.setdefault("accuracy", {})[new] = [{"stat": s_, "season": se_, **row.iloc[0].to_dict()} for s_, se_, row in order if len(row)]

    P("\n### 1b. What the missing opponent column cost (paired, same props)\n")
    P("`ship` (opponent codes present, what every replay assumed) minus `ship_live` (no opponent column, what the live loader fed the engine "
      "before the 2026-11-02 loader fix). Negative d = having the opponent information is better.\n")
    P("| stat | absences | n | d LL ship - ship_live (SE) | z |")
    P("|---|---|---|---|---|")
    for stat in ["points", "rebounds", "assists", "threes", "pra", "ALL"]:
        h = g if stat == "ALL" else g[g.stat == stat]
        for lab, a, b in (("oracle", "p_ship", "p_ship_live"), ("none", "p_ship_noabs", "p_ship_live_noabs")):
            r = paired(h, a, b)
            P(f"| {stat} | {lab} | {len(h):,} | {r['d']:+.5f} ({r['se']:.5f}) | {r['z']:+.1f} |")
    P("\n### 1c. Calibration at the market line (engine `ship_noabs`; all scored props)\n")
    P("`bias` = mean engine mu - realised stat; `mu - line` = mean engine mu - market line; P(over) columns are means over the same props "
      "(realised over rate is the target; the market is the benchmark).\n")
    P("| stat | n | mean line | mean mu | mean y | bias | P(over) engine | P(over) market | realised over rate |")
    P("|---|---|---|---|---|---|---|---|---|")
    for stat in ["points", "rebounds", "assists", "threes", "pra"]:
        h = g[g.stat == stat]
        P(f"| {stat} | {len(h):,} | {h['line'].mean():.2f} | {h['mu_ship_noabs'].mean():.2f} | {h['y'].mean():.2f} | "
          f"{(h['mu_ship_noabs'] - h['y']).mean():+.2f} | {h['p_ship_noabs'].mean():.3f} | {h['p_market'].mean():.3f} | {h['over'].mean():.3f} |")

    # --- ROI, walk-forward
    P("\n### 2. Flat-stake ROI (walk-forward blend; engine side when edge >= threshold)\n")
    P("`engine` = the engine's own P(over) (clipped 0.5%-99.5%); `walk-forward blend` = market-anchored blend with (a, b, c) fitted per stat on EARLIER "
      "seasons only (display rule of the live board); `line shopping` = the CONSENSUS probability alone with the same threshold and price (no model: pure "
      "best-quote selection); `vs both sides` = ROI minus betting both sides of the same props at the same prices. CIs: date-clustered bootstrap.\n")
    if not extras_ok:
        P("(median-price columns unavailable: no extras)\n")
    for label, mcol in (("ship_noabs (no absence info)", "p_ship_noabs"), ("ship (oracle absences)", "p_ship"),
                        ("ship_live_noabs (production inputs, no absence info)", "p_ship_live_noabs")):
        t = wf_blend(g, mcol)
        res.setdefault("wf_fit_c", {})[label] = {f"{a}|{int(b)}": float(c) for (a, b), c in t.groupby(["stat", "season"])["fit_c"].first().items()}
        roi_section(t, label, mcol, res, P)
        P("\nFitted blend c (earlier seasons only) per stat and season: " + "; ".join(
            f"{a} {int(b)}: {c:+.2f}" for (a, b), c in t.groupby(["stat", "season"])["fit_c"].first().items()) + "\n")
        # walk-forward threshold on the median price (a threshold picked on earlier seasons only)
        sc = sorted(t["season"].unique())[1:]
        for pname, px in PRICES.items():
            for rule, col in (("engine", mcol), ("blend", "p_blend"), ("line shopping", "p_market")):
                oos, chosen = wf_threshold(t, col, px, sc)
                key = f"wf|{label}|{pname}|{rule}"
                if oos is None:
                    res[key] = None
                    continue
                r = summ(oos)
                res[key] = {"chosen": {k: v[0] for k, v in chosen.items()}, **r}
                P(f"- walk-forward threshold ({pname} price, rule={rule}): chosen per season {{{', '.join(f'{k}: {v[0]:.0%}' for k, v in chosen.items())}}} -> out-of-sample "
                  f"ROI {fmt(r)}, vs both sides {r['vs_noskill']:+.1%}")
        # per stat at 3%
        P(f"\n{label}: per stat / per season, edge >= 3%, median price (engine | blend | line shopping):\n")
        P("| slice | engine ROI (n) | blend ROI (n) | line shopping ROI (n) | always Over / Under (median price) |")
        P("|---|---|---|---|---|")
        px = PRICES["median"]
        slices = [(s, t[t.stat == s]) for s in ["points", "rebounds", "assists", "threes", "pra"]] + [(f"season {int(s)}", t[t.season == s]) for s in sorted(t.season.unique())]
        for lab, h in slices:
            e, bl, m = summ(bets(h, mcol, 0.03, px), 200), summ(bets(h, "p_blend", 0.03, px), 200), summ(bets(h, "p_market", 0.03, px), 200)
            ob, ub = summ(fixed_side(h, "over", px), 100), summ(fixed_side(h, "under", px), 100)
            P(f"| {lab} | {fmt(e)} | {fmt(bl)} | {fmt(m, False)} | {ob['roi']:+.1%} / {ub['roi']:+.1%} |")
    return "\n".join(md) + "\n", res


# --------------------------------------------------------------------------------------------------------- bookkeeping
def fingerprint(master_path: str, bp_dir: str, cons: pd.DataFrame, index: dict) -> dict:
    h = hashlib.sha256()
    with open(master_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)

    def sh(*a):
        try:
            return subprocess.check_output(a, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), text=True).strip()
        except Exception:                                                                        # noqa: BLE001
            return "?"
    files = {slug: len(glob.glob(os.path.join(bp_dir, slug, "*.parquet"))) for slug in SLUG.values()}
    n_rows = sum(len(r) for r in index.values())
    dates = [r["_date"] for rows in index.values() for r in rows]
    return {"git_sha": sh("git", "rev-parse", "HEAD"), "git_dirty_files": len(sh("git", "status", "--short").splitlines()),
            "master_sha256": h.hexdigest()[:16], "master_played_rows": n_rows, "master_date_range": [min(dates), max(dates)],
            "bp_partition_files": files, "consensus_rows": int(len(cons)),
            "consensus_date_range": [str(cons["game_date"].min()), str(cons["game_date"].max())],
            "nba_features": {k: (list(v) if isinstance(v, tuple) else v) for k, v in DP.NBA_FEATURES.items()},
            "python": sys.version.split()[0], "pandas": pd.__version__}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bp-dir", required=True, help="local copy of gs://.../Odds/bettingpros_nba")
    ap.add_argument("--master", required=True, help="local NBA/player_boxscores_master.csv")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sample-every", type=int, default=1, help="score every Nth distinct game date (1 = all)")
    ap.add_argument("--start", default=None, help="first ET game date to score")
    ap.add_argument("--max-dates", type=int, default=None, help="smoke test: only the first N dates")
    ap.add_argument("--reuse-rows", action="store_true", help="skip the replay and re-run the analysis on work-dir/priced.parquet")
    ap.add_argument("--out-md")
    ap.add_argument("--out-json")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(a.work_dir, exist_ok=True)
    cons = load_consensus(a.bp_dir, os.path.join(a.work_dir, "cons.parquet"))
    extras = load_extras(a.bp_dir, cons, os.path.join(a.work_dir, "extras.parquet"))
    index, dnp = load_master(a.master)
    meta = fingerprint(a.master, a.bp_dir, cons, index)
    props, cov = join_props(cons, index, dnp, a.start)
    log.info("joined %d of %d consensus rows; dropped %s", cov["matched"], cov["consensus_rows"], cov["dropped"])
    if a.max_dates:
        props = props[props["date"].isin(sorted(props["date"].unique())[:a.max_dates])].reset_index(drop=True)
    cache = os.path.join(a.work_dir, "priced.parquet")
    if a.reuse_rows and os.path.exists(cache):
        F = pd.read_parquet(cache)
        rstats = json.load(open(os.path.join(a.work_dir, "replay_stats.json")))
    else:
        F, rstats = replay_props(props, index, dnp, a.workers, a.sample_every)
        F.to_parquet(cache, index=False)
        json.dump(rstats, open(os.path.join(a.work_dir, "replay_stats.json"), "w"))
    cov["not_projected"] = int(F[[f"p_{v}" for v in VARIANTS]].isna().any(axis=1).sum())
    F = attach_prices(F, extras)
    md, res = render(F, True, cov, rstats, meta)
    if a.out_md:
        open(a.out_md, "w").write(md)
    if a.out_json:
        json.dump(res, open(a.out_json, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
