"""nba.sim.evaluate -- replay the joint simulator against box scores it has not seen (accuracy only, NO prices).

    python -m nba.sim.evaluate --player-master P.csv --team-master T.csv --params nba/sim/params.json \
        --seasons 2024,2025 --n 4000 --workers 8 --out-dir DIR

For every game of the held-out seasons (structural constants fitted on season_year <= 2023, nba/sim/fit.py) it builds each rotation
player's marginals with the serving engine's OWN functions (nba/sim/adapter.py: history cut strictly before the game, league context rebuilt
monthly, absences ORACLE exactly as in nba/props/engine_eval.py, no spreads), simulates the game and records three kinds of evidence:

  consistency/calibration rows : P(over) of every stat and combo at (a) the proxy line floor(L10 mean) + 0.5 and (b) model-centred lines
                                 floor(mu + z sd) + 0.5, z in {-1, 0, +1}, from the ANALYTIC marginal (combos: today's Normal with the
                                 within-player correlation prior), the STRUCTURAL simulation and the ANCHORED simulation, beside the outcome
  correlation accumulators      : mean product of standardised residuals ((actual - mu) / sd, mu and sd from the analytic engine) for
                                 each pair type, empirical vs simulated, per game so the report can cluster the standard error by game
  pair-event rows               : P(A and B) for SGP-style pairs at model-centred lines from (i) analytic marginals multiplied (independence),
                                 (ii) simulated marginals multiplied, (iii) the structural joint, (iv) the anchored joint, with log loss / Brier
                                 against what happened

What it does NOT do: use a price, a prop line, a spread, or the real injury report (absences are oracle, so a gain that comes from them
is an upper bound -- and the baseline got the same oracle absences).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import multiprocessing as mp
import sys
from bisect import bisect_left
from datetime import datetime

import numpy as np
import pandas as pd

from ev import daily_projections as DP
from mlb_core.data import cockpit_form as cf
from nba.props import engine_eval as EE
from nba.props import team_context as TC
from nba.sim import fit as FIT
from nba.sim.adapter import COMBO_STATS, SIM_STATS, engine_player_input
from nba.sim.joint_sim import GameInput, simulate_game

log = logging.getLogger("nba.sim.evaluate")

ALL = SIM_STATS + COMBO_STATS
ELIG_MPG = 20.0                 # the board's population: last-10 average minutes
MODEL_MPG = 12.0                # the simulator's rotation: modelled players
MIN_PER_SIDE = 3
Z_LINES = (-1.0, 0.0, 1.0)
EPS = 1e-4
ZCLIP = 6.0
# pair types for the correlation table: (label, stat a, stat b, relation)
CORR_TYPES = [
    ("teammates points-points", "points", "points", "team"), ("teammates rebounds-rebounds", "rebounds", "rebounds", "team"),
    ("teammates assists-assists", "assists", "assists", "team"), ("teammates threes-threes", "threes", "threes", "team"),
    ("teammates points-assists (A pts, B ast)", "points", "assists", "team"),
    ("teammates points-rebounds (A pts, B reb)", "points", "rebounds", "team"),
    ("teammates rebounds-assists (A reb, B ast)", "rebounds", "assists", "team"),
    ("opponents points-points", "points", "points", "opp"), ("opponents rebounds-rebounds", "rebounds", "rebounds", "opp"),
    ("opponents assists-assists", "assists", "assists", "opp"), ("opponents points-assists (A pts, B ast)", "points", "assists", "opp"),
    ("same player points-assists", "points", "assists", "self"), ("same player points-rebounds", "points", "rebounds", "self"),
    ("same player rebounds-assists", "rebounds", "assists", "self"), ("same player points-threes", "points", "threes", "self"),
]
TOTAL_TYPES = [("player points vs game total", "points"), ("player rebounds vs game total", "rebounds"),
               ("player assists vs game total", "assists"), ("player points vs own team margin", "points_margin")]
# pair types scored as joint events
PAIR_TYPES = [("teammates points-points", "points", "points", "team"), ("teammates points-assists (A pts, B ast)", "points", "assists", "team"),
              ("opponents points-points", "points", "points", "opp"), ("same player points-assists", "points", "assists", "self"),
              ("same player points-rebounds", "points", "rebounds", "self"), ("same player rebounds-assists", "rebounds", "assists", "self"),
              ("teammates rebounds-rebounds", "rebounds", "rebounds", "team")]

_G: dict = {}                   # worker globals (set before fork)


# ------------------------------------------------------------------------------------------------------ set up
def collect_games(index: dict, seasons: list) -> dict:
    games: dict = {}
    for name, rows in index.items():
        if str(name).startswith("id:"):
            continue
        for j, r in enumerate(rows):
            if r.get("season_year") in seasons and r.get("_opp") and r.get("team_abbr") and r.get("game_id"):
                g = games.setdefault(r["game_id"], {"game_id": r["game_id"], "date": r["_date"], "season": r["season_year"],
                                                    "teams": {}})
                home = str(r.get("is_home")).lower() in ("true", "1", "1.0")
                t = g["teams"].setdefault(r["team_abbr"], {"home": home, "opp": r["_opp"], "players": []})
                t["players"].append((name, j))
    return {k: v for k, v in games.items() if len(v["teams"]) == 2 and sum(t["home"] for t in v["teams"].values()) == 1}


def rating_diffs(player_master: pd.DataFrame, team_master: pd.DataFrame) -> dict:
    """{game_id: home rating - away rating} from the ex-ante team table (EWM net points of each team's EARLIER games)."""
    g = FIT.team_table(team_master, player_master)
    h = g[g.is_home]
    return dict(zip(h.game_id, (h.rating - h.o_rating).astype(float)))


def _actual(row: dict, stat: str) -> float | None:
    return cf._sum_cols(row, cf.NBA_STATS[stat][0])


# ------------------------------------------------------------------------------------------------------ one game
def _lines(marg, l10: float) -> list:
    out = [("l10", math.floor(l10) + 0.5)]
    for z in Z_LINES:
        out.append((f"z{z:+.0f}", math.floor(marg.mu + z * max(marg.sd, 1e-6)) + 0.5))
    return out


def _ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def process_game(game: dict, logs, n: int, seed: int, boosts_cache: dict):
    idx, dnp, full_dates, params = _G["index"], _G["dnp"], _G["full_dates"], _G["params"]
    d = game["date"]
    start = datetime.fromisoformat(f"{d}T12:00:00+00:00")
    teams = game["teams"]
    home = next(t for t, v in teams.items() if v["home"])
    away = next(t for t in teams if t != home)
    keys = ((float(DP.NBA_FEATURES["eta_scale"]), tuple(DP.NBA_FEATURES["eta_off"])),)
    boosts = {}
    for t in (home, away):
        if (t, d) not in boosts_cache:
            boosts_cache[(t, d)] = EE._team_absences(idx, full_dates, t, d, dnp, keys)
        boosts[t] = boosts_cache[(t, d)][1][keys[0]]
    logs.boosts = boosts
    players, meta = [], {}
    team_pts = {t: 0.0 for t in teams}
    for t, v in teams.items():
        for name, j in v["players"]:
            team_pts[t] += float(idx[name][j].get("points") or 0.0)
    seen = set()
    for t, v in teams.items():
        side = "home" if t == home else "away"
        opp = v["opp"]
        gk = f"nba:{d}:{away}@{home}"
        for name, j in v["players"]:
            if name in seen:
                continue
            rows = idx[name]
            r = rows[j]
            lo = _G["lo"](name, r["season_year"])
            prior = rows[lo:j]
            if len(prior) < DP.MIN_GAMES:
                continue
            l10m = float(np.mean([x["minutes"] for x in prior[-10:]]))
            if l10m < MODEL_MPG:
                continue
            got = engine_player_input(name, side, prior, logs, t, opp, gk, start, day=d)
            if got is None:
                continue
            pin, combos = got
            if any(_actual(r, s) is None for s in ("points", "rebounds", "assists")):
                continue
            seen.add(name)
            players.append(pin)
            l10 = {s: float(np.mean([v_ for v_ in (_actual(x, s) for x in prior[-10:]) if v_ is not None] or [0.0])) for s in ALL}
            meta[name] = {"combos": combos, "row": r, "l10": l10, "elig": l10m >= ELIG_MPG, "side": side}
    if sum(p.team == "home" for p in players) < MIN_PER_SIDE or sum(p.team == "away" for p in players) < MIN_PER_SIDE:
        return None
    rd = _G["ratings"].get(game["game_id"])
    pg = params["game"]
    mu_m = None if rd is None or rd != rd else pg["margin_intercept"] + pg["margin_slope"] * rd
    gi = GameInput(game["game_id"], players, mu_margin=mu_m)
    ds = simulate_game(gi, n=n, seed=seed, params=params)
    da = ds.anchored(seed)
    act_T = team_pts[home] + team_pts[away]
    act_M = team_pts[home] - team_pts[away]
    out = {"cal": [], "pair": [], "corr": {}, "tot": {},
           "game": (act_T - ds.meta["mu_total"], act_M - ds.meta["mu_margin"], ds.meta["sd_total"], ds.meta["sd_margin"])}
    # ---- calibration / consistency rows
    for name, m in meta.items():
        if not m["elig"]:
            continue
        r = m["row"]
        for stat in ALL:
            marg = ds.marginals.get((name, stat)) if stat in SIM_STATS else m["combos"].get(stat)
            y = _actual(r, stat)
            if marg is None or y is None:
                continue
            xs, xa = ds.series(name, stat), da.series(name, stat)
            for kind, line in _lines(marg, m["l10"][stat]):
                out["cal"].append((game["game_id"], d, r["season_year"], name, stat, kind, line, float(y > line), marg.p_over(line),
                                   float(np.mean(xs > line)), float(np.mean(xa > line)), marg.mu, marg.sd))
    # ---- correlations (standardised residuals) over ALL modelled players
    cols = [(p.name, s) for p in players for s in SIM_STATS if (p.name, s) in ds.marginals]
    pidx = np.array([ds.players().index(c[0]) for c in cols])
    sidx = np.array([SIM_STATS.index(c[1]) for c in cols])
    side_of = {p.name: p.team for p in players}
    tidx = np.array([0 if side_of[c[0]] == "home" else 1 for c in cols])
    mus = np.array([ds.marginals[c].mu for c in cols])
    sds = np.maximum(np.array([ds.marginals[c].sd for c in cols]), 0.5)
    # a player projected at ~0 of a stat has a tiny sd and one stray assist would be a z of 20+: both sides are clipped alike
    Zs = np.clip((np.column_stack([ds.series(*c) for c in cols]) - mus) / sds, -ZCLIP, ZCLIP)
    zemp = np.clip((np.array([_actual(meta[c[0]]["row"], c[1]) for c in cols], dtype=float) - mus) / sds, -ZCLIP, ZCLIP)
    Cs = Zs.T @ Zs / n
    ms, vs = Zs.mean(axis=0), (Zs ** 2).mean(axis=0)               # E[z], E[z^2] over the draws, per column
    same_p = pidx[:, None] == pidx[None, :]
    same_t = tidx[:, None] == tidx[None, :]
    triu = np.triu(np.ones_like(same_p, dtype=bool), 1)
    for label, sa, sb, rel in CORR_TYPES:
        ia, ib = SIM_STATS.index(sa), SIM_STATS.index(sb)
        mask = (sidx[:, None] == ia) & (sidx[None, :] == ib)
        mask &= {"self": same_p & ~np.eye(len(cols), dtype=bool), "team": same_t & ~same_p, "opp": ~same_t}[rel]
        if ia == ib:
            mask &= triu
        ii, jj = np.where(mask)
        if len(ii):
            a_, b_ = zemp[ii], zemp[jj]
            emp = (float((a_ * b_).sum()), float(a_.sum()), float(b_.sum()), float((a_ ** 2).sum()), float((b_ ** 2).sum()), len(ii))
            sim = (float(Cs[ii, jj].sum()), float(ms[ii].sum()), float(ms[jj].sum()), float(vs[ii].sum()), float(vs[jj].sum()), len(ii))
            out["corr"][label] = (emp, sim)
    sdT = ds.meta["sd_total"]
    zT_s = (ds.series("GAME", "total") - ds.meta["mu_total"]) / sdT
    zT_e = (act_T - ds.meta["mu_total"]) / sdT
    zM_s = (ds.series("GAME", "margin") - ds.meta["mu_margin"]) / ds.meta["sd_margin"]
    zM_e = (act_M - ds.meta["mu_margin"]) / ds.meta["sd_margin"]
    for label, key in TOTAL_TYPES:
        stat = "points" if key == "points_margin" else key
        sel = np.where(sidx == SIM_STATS.index(stat))[0]
        if not len(sel):
            continue
        sgn = np.where(tidx[sel] == 0, 1.0, -1.0) if key == "points_margin" else np.ones(len(sel))
        zb_s = zM_s if key == "points_margin" else zT_s
        zb_e = zM_e if key == "points_margin" else zT_e
        a_ = zemp[sel]
        b_ = sgn * zb_e
        emp = (float((a_ * b_).sum()), float(a_.sum()), float(b_.sum()), float((a_ ** 2).sum()), float((b_ ** 2).sum()), len(sel))
        Zb = zb_s[:, None] * sgn[None, :]                              # (n, |sel|): the game-level z, signed by the player's side
        sim = (float(np.mean(Zs[:, sel] * Zb, axis=0).sum()), float(ms[sel].sum()), float(Zb.mean(axis=0).sum()),
               float(vs[sel].sum()), float((Zb ** 2).mean(axis=0).sum()), len(sel))
        out["tot"][label] = (emp, sim)
    # ---- joint events at model-centred lines (eligible players only)
    el = np.array([meta[c[0]]["elig"] for c in cols])
    lines = np.array([math.floor(mus[k]) + 0.5 for k in range(len(cols))])
    Bs = (np.column_stack([ds.series(*c) for c in cols]) > lines).astype(np.float32)
    Ba = (np.column_stack([da.series(*c) for c in cols]) > lines).astype(np.float32)
    ye = (np.array([_actual(meta[c[0]]["row"], c[1]) for c in cols], dtype=float) > lines).astype(float)
    pan = np.array([ds.marginals[c].p_over(lines[k]) for k, c in enumerate(cols)])
    ps, pa_ = Bs.mean(0), Ba.mean(0)
    for label, sa, sb, rel in PAIR_TYPES:
        ia, ib = SIM_STATS.index(sa), SIM_STATS.index(sb)
        mask = (sidx[:, None] == ia) & (sidx[None, :] == ib) & el[:, None] & el[None, :]
        mask &= {"self": same_p & ~np.eye(len(cols), dtype=bool), "team": same_t & ~same_p, "opp": ~same_t}[rel]
        if ia == ib:
            mask &= triu
        ii, jj = np.where(mask)
        if not len(ii):
            continue
        y = ye[ii] * ye[jj]
        preds = {"indep_analytic": pan[ii] * pan[jj], "indep_sim": ps[ii] * ps[jj],
                 "sim_structural": (Bs[:, ii] * Bs[:, jj]).mean(0), "sim_anchored": (Ba[:, ii] * Ba[:, jj]).mean(0)}
        row = {"game_id": game["game_id"], "date": d, "season": game["season"], "type": label, "n_pairs": len(ii), "y": float(y.sum())}
        for k, p in preds.items():
            row[f"p_{k}"] = float(p.sum())
            row[f"ll_{k}"] = float(_ll(p, y).sum())
            row[f"br_{k}"] = float(((np.clip(p, EPS, 1 - EPS) - y) ** 2).sum())
        out["pair"].append(row)
    # player points / rebounds / assists over vs game total over
    Tline = math.floor(ds.meta["mu_total"]) + 0.5
    BT_s, BT_a = (ds.series("GAME", "total") > Tline).astype(np.float32), (da.series("GAME", "total") > Tline).astype(np.float32)
    yT = float(act_T > Tline)
    for stat in ("points", "rebounds", "assists"):
        sel = np.where((sidx == SIM_STATS.index(stat)) & el)[0]
        if not len(sel):
            continue
        y = ye[sel] * yT
        pT = float(BT_s.mean())
        preds = {"indep_analytic": pan[sel] * pT, "indep_sim": ps[sel] * pT,
                 "sim_structural": (Bs[:, sel] * BT_s[:, None]).mean(0), "sim_anchored": (Ba[:, sel] * BT_a[:, None]).mean(0)}
        row = {"game_id": game["game_id"], "date": d, "season": game["season"], "type": f"player {stat} over x game total over",
               "n_pairs": len(sel), "y": float(y.sum())}
        for k, p in preds.items():
            row[f"p_{k}"] = float(p.sum())
            row[f"ll_{k}"] = float(_ll(p, y).sum())
            row[f"br_{k}"] = float(((np.clip(p, EPS, 1 - EPS) - y) ** 2).sum())
        out["pair"].append(row)
    return out


def process_block(args):
    block, game_list, n, seed = args
    idx, full_dates = _G["index"], _G["full_dates"]
    first = f"{block}-01"
    season = game_list[0]["season"]
    trunc = {}
    for nm, rs in idx.items():
        trunc[nm] = rs[_G["lo"](nm, season):bisect_left(full_dates[nm], first)]
    logs = EE.ReplayLogs({"all": {nm: rs for nm, rs in trunc.items() if rs}}, _G["team_games"], None)
    cache: dict = {}
    res = []
    for g in game_list:
        try:
            o = process_game(g, logs, n, seed, cache)
        except Exception:                              # one bad game must not lose a month; the count is reported
            log.exception("game %s failed", g["game_id"])
            o = None
        if o is not None:
            res.append(o)
    log.info("block %s: %d / %d games simulated", block, len(res), len(game_list))
    return block, res, len(game_list)


# ------------------------------------------------------------------------------------------------------ driver
def run(player_csv: str, team_csv: str, params: dict, seasons: list, n: int, workers: int, seed: int = 11,
        max_games: int | None = None, months: list | None = None) -> dict:
    index, dnp = EE.load_master(player_csv)
    full_dates = {nm: [r["_date"] for r in rows] for nm, rows in index.items()}
    yrs = {nm: [x["season_year"] for x in rows] for nm, rows in index.items()}

    def lo(name, season):
        return bisect_left(yrs[name], season - EE.HISTORY_SEASONS + 1)

    pm = pd.read_csv(player_csv, low_memory=False)
    tm = pd.read_csv(team_csv, low_memory=False)
    _G.update(index=index, dnp=dnp, full_dates=full_dates, params=params, lo=lo, team_games=TC.TeamGames(index),
              ratings=rating_diffs(pm, tm))
    games = collect_games(index, seasons)
    glist = sorted(games.values(), key=lambda g: (g["date"], g["game_id"]))
    if months:
        glist = [g for g in glist if g["date"][:7] in months]
    if max_games:
        glist = glist[:max_games]
    blocks: dict = {}
    for g in glist:
        blocks.setdefault(g["date"][:7], []).append(g)
    jobs = [(b, gl, n, seed) for b, gl in sorted(blocks.items())]
    results = []
    if workers <= 1:
        results = [process_block(j) for j in jobs]
    else:
        with mp.get_context("fork").Pool(workers) as pool:
            results = list(pool.imap_unordered(process_block, jobs))
    cal, pair, corr, tot, game = [], [], [], [], []
    n_games = 0
    for _, res, _ in results:
        for o in res:
            n_games += 1
            cal += o["cal"]
            pair += o["pair"]
            corr.append(o["corr"])
            tot.append(o["tot"])
            game.append(o["game"])
    tried = sum(r[2] for r in results)
    cal_df = pd.DataFrame(cal, columns=["game_id", "date", "season", "player", "stat", "kind", "line", "y", "p_analytic", "p_structural",
                                        "p_anchored", "mu", "sd"])
    return {"cal": cal_df, "pair": pd.DataFrame(pair), "corr": corr, "tot": tot, "game": game, "games": n_games, "games_tried": tried}


# ------------------------------------------------------------------------------------------------------ summaries
def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    b = np.minimum((p * bins).astype(int), bins - 1)
    tot = 0.0
    for k in range(bins):
        s = b == k
        if s.any():
            tot += s.mean() * abs(p[s].mean() - y[s].mean())
    return float(tot)


def cluster_se(delta: np.ndarray, groups: np.ndarray) -> float:
    s = pd.Series(delta).groupby(groups).sum()
    g, nn = len(s), len(delta)
    if g < 2:
        return float("nan")
    dm = delta.mean()
    cnt = pd.Series(np.ones(nn)).groupby(groups).sum()
    return float(math.sqrt(((s - dm * cnt) ** 2).sum() * g / (g - 1)) / nn)


def calibration_table(cal: pd.DataFrame, kinds=("l10", "z-1", "z+0", "z+1")) -> pd.DataFrame:
    rows = []
    for stat in ALL:
        for kg, ks in (("proxy L10 line", ("l10",)), ("model-centred lines", ("z-1", "z+0", "z+1"))):
            d = cal[(cal.stat == stat) & cal.kind.isin(ks)]
            if len(d) < 100:
                continue
            y = d.y.to_numpy()
            r = {"stat": stat, "lines": kg, "n": len(d)}
            ll_a = _ll(d.p_analytic.to_numpy(), y)
            for tag in ("analytic", "structural", "anchored"):
                p = d[f"p_{tag}"].to_numpy()
                ll = _ll(p, y)
                r[f"ll_{tag}"] = float(ll.mean())
                r[f"brier_{tag}"] = float(((np.clip(p, EPS, 1 - EPS) - y) ** 2).mean())
                r[f"ece_{tag}"] = ece(p, y)
                if tag != "analytic":
                    delta = ll_a - ll                                   # positive = the simulation is better
                    r[f"dll_{tag}"] = float(delta.mean())
                    r[f"z_{tag}"] = float(delta.mean() / cluster_se(delta, d.game_id.to_numpy()))
            rows.append(r)
    return pd.DataFrame(rows)


def consistency_table(cal: pd.DataFrame, n: int) -> pd.DataFrame:
    """Simulated vs analytic P(over): signed mean gap, mean |gap|, and the share of rows whose |gap| exceeds 3 Monte-Carlo SEs."""
    rows = []
    for stat in ALL:
        d = cal[cal.stat == stat]
        if d.empty:
            continue
        pa = d.p_analytic.to_numpy()
        se = np.sqrt(np.maximum(pa * (1 - pa), 1e-4) / n)
        r = {"stat": stat, "n": len(d), "mc_se": float(se.mean())}
        for tag in ("structural", "anchored"):
            gap = d[f"p_{tag}"].to_numpy() - pa
            r[f"bias_{tag}"] = float(gap.mean())
            r[f"mad_{tag}"] = float(np.abs(gap).mean())
            r[f"p95_{tag}"] = float(np.quantile(np.abs(gap), 0.95))
            r[f"over3se_{tag}"] = float((np.abs(gap) > 3 * se).mean())
        rows.append(r)
    return pd.DataFrame(rows)


def correlation_table(corr: list, tot: list, boot: int = 300, seed: int = 0) -> pd.DataFrame:
    rows = []
    for label in [c[0] for c in CORR_TYPES] + [c[0] for c in TOTAL_TYPES]:
        src = corr if any(label in c for c in corr) else tot
        g = [c[label] for c in src if label in c]
        if not g:
            continue
        emp = np.array([x[0] for x in g], dtype=float)
        sim = np.array([x[1] for x in g], dtype=float)
        rows.append(_corr_row(label, emp, sim, boot, seed))
    return pd.DataFrame(rows)


def _pearson(m: np.ndarray) -> float:
    """m = per-game sums (sum ab, sum a, sum b, sum a^2, sum b^2, pairs) -> pooled Pearson correlation over all pairs."""
    n = m[:, 5].sum()
    sab, sa, sb, saa, sbb = (m[:, k].sum() / n for k in range(5))
    den = math.sqrt(max(saa - sa * sa, 1e-12) * max(sbb - sb * sb, 1e-12))
    return float((sab - sa * sb) / den)


def _corr_row(label, emp, sim, boot: int, seed: int) -> dict:
    """Pooled Pearson correlation of the residuals over every pair of the type, empirical vs simulated, with a game-bootstrap SE."""
    rng = np.random.default_rng(seed)
    G = len(emp)
    out = {"pair type": label, "pairs": int(emp[:, 5].sum()), "games": G, "empirical": _pearson(emp), "simulated": _pearson(sim)}
    draws_e, draws_s = [], []
    for _ in range(boot):
        ix = rng.integers(0, G, G)
        draws_e.append(_pearson(emp[ix]))
        draws_s.append(_pearson(sim[ix]))
    out["se_empirical"], out["se_simulated"] = float(np.std(draws_e)), float(np.std(draws_s))
    out["gap"] = out["simulated"] - out["empirical"]
    return out


def pair_table(pair: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for typ, d in pair.groupby("type"):
        n = d.n_pairs.sum()
        r = {"pair type": typ, "pairs": int(n), "games": d.game_id.nunique(), "hit rate": d.y.sum() / n}
        for k in ("indep_analytic", "indep_sim", "sim_structural", "sim_anchored"):
            r[f"p_{k}"] = d[f"p_{k}"].sum() / n
            r[f"ll_{k}"] = d[f"ll_{k}"].sum() / n
            r[f"br_{k}"] = d[f"br_{k}"].sum() / n
        for k, base, tag in (("sim_structural", "indep_analytic", ""), ("sim_anchored", "indep_analytic", ""),
                             ("sim_structural", "indep_sim", "dep_")):
            dl = d[f"ll_{base}"] - d[f"ll_{k}"]                         # positive = the simulated joint is better than the product
            resid = dl - (dl.sum() / n) * d.n_pairs
            se = math.sqrt((resid ** 2).sum() * len(d) / max(len(d) - 1, 1)) / n
            r[f"{tag}dll_{k}"] = float(dl.sum() / n)
            r[f"{tag}z_{k}"] = float(dl.sum() / n / se) if se > 0 else float("nan")
        r["lift_realised"] = d.y.sum() / max(d.p_indep_analytic.sum(), 1e-9)
        r["lift_structural"] = d.p_sim_structural.sum() / max(d.p_indep_sim.sum(), 1e-9)
        r["lift_anchored"] = d.p_sim_anchored.sum() / max(d.p_indep_analytic.sum(), 1e-9)
        rows.append(r)
    return pd.DataFrame(rows)


def _md(df: pd.DataFrame, cols: list, fmts: dict | None = None) -> str:
    fmts = fmts or {}
    out = ["| " + " | ".join(c for c, _ in cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for _, key in cols:
            v = r[key]
            f = fmts.get(key)
            cells.append(f.format(v) if (f and isinstance(v, (float, np.floating)) and v == v) else str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def load_results(dirs: list) -> tuple:
    cal = pd.concat([pd.read_parquet(f"{d}/cal.parquet") for d in dirs], ignore_index=True)
    pair = pd.concat([pd.read_parquet(f"{d}/pair.parquet") for d in dirs], ignore_index=True)
    corr, tot, game, n_games, tried, n = [], [], [], 0, 0, None
    for d in dirs:
        with open(f"{d}/corr.json") as fh:
            c = json.load(fh)
        corr += c["corr"]
        tot += c["tot"]
        game += c["game"]
        n_games += c["games"]
        tried += c["games_tried"]
        n = c["n"]
    return cal, pair, corr, tot, game, n_games, tried, n


def report_markdown(dirs: list) -> str:
    """Markdown tables for the analysis document from one or more evaluate output directories (pooled)."""
    cal, pair, corr, tot, game, n_games, tried, n = load_results(dirs)
    g = np.array(game, dtype=float)
    lines = [f"Games simulated: {n_games} of {tried}; draws per game: {n}; seasons {sorted(int(x) for x in cal.season.unique())}; eligible player-games (last-10 "
             f"minutes >= {ELIG_MPG:g}): {cal[['game_id', 'player']].drop_duplicates().shape[0]}.", "",
             f"Game-level input check (actual minus the simulator's input mean): total residual mean {g[:, 0].mean():+.2f}, sd {g[:, 0].std():.2f} "
             f"(input sd {g[:, 2].mean():.2f}); margin residual mean {g[:, 1].mean():+.2f}, sd {g[:, 1].std():.2f} (input sd {g[:, 3].mean():.2f}).", ""]
    cons = consistency_table(cal, n)
    lines += ["### 1. Consistency: simulated vs analytic P(over)", "",
              "Gap = simulated minus analytic P(over) over every (player, stat, line) row (proxy L10 line + model-centred lines at z = -1, 0, +1). "
              "`3 SE` = share of rows whose |gap| exceeds three Monte-Carlo standard errors.", "",
              _md(cons, [("stat", "stat"), ("rows", "n"), ("MC SE", "mc_se"), ("structural bias", "bias_structural"),
                         ("structural mean abs gap", "mad_structural"), ("structural p95 abs gap", "p95_structural"),
                         ("structural > 3 SE", "over3se_structural"), ("anchored mean abs gap", "mad_anchored"),
                         ("anchored > 3 SE", "over3se_anchored")],
                  {k: "{:.4f}" for k in ("mc_se", "bias_structural", "mad_structural", "p95_structural", "over3se_structural",
                                         "mad_anchored", "over3se_anchored")}), ""]
    ct = correlation_table(corr, tot)
    lines += ["### 2. Simulated vs empirical within-game correlation", "",
              "Pooled Pearson correlation of residuals (actual minus the engine's mean, scaled by the engine's sd; clipped at +/-6) over every pair of the "
              "type, rotation players (last-10 minutes >= 12). Empirical: box scores; simulated: the structural draws. SE = game bootstrap.", "",
              _md(ct, [("pair type", "pair type"), ("pairs", "pairs"), ("empirical", "empirical"), ("+/- SE", "se_empirical"),
                       ("simulated", "simulated"), ("gap", "gap")],
                  {k: "{:+.3f}" for k in ("empirical", "se_empirical", "simulated", "gap")}),
              "", f"RMS gap over the {len(ct)} rows: {math.sqrt((ct.gap ** 2).mean()):.3f}.", ""]
    cl = calibration_table(cal)
    lines += ["### 3. Walk-forward calibration (log score, Brier, ECE)", "",
              "Log score is mean negative log-likelihood of `over` at the stated line (lower = better). `d` = analytic minus simulated "
              "(positive = the simulation is better); z uses a game-clustered SE. `analytic` = what the board computes today (components: the engine's "
              "marginal; pra / pr / pa / ra: Normal with moment-matched variance from the shrunk within-player correlation).", "",
              _md(cl, [("stat", "stat"), ("lines", "lines"), ("rows", "n"), ("LL analytic", "ll_analytic"), ("LL structural", "ll_structural"),
                       ("d LL", "dll_structural"), ("z", "z_structural"), ("LL anchored", "ll_anchored"), ("d LL", "dll_anchored"),
                       ("z", "z_anchored"), ("ECE an.", "ece_analytic"), ("ECE str.", "ece_structural"), ("ECE anch.", "ece_anchored")],
                  {k: "{:+.4f}" if k.startswith(("dll", "z_")) else "{:.4f}" for k in cl.columns if k not in ("stat", "lines", "n")}), ""]
    pt = pair_table(pair)
    lines += ["### 4. SGP-style pair events (accuracy of P(A and B), no prices)", "",
              "Each pair is two over-legs at model-centred lines (floor(mu) + 0.5), eligible players only. `d LL` = independence (product of the "
              "analytic marginals) minus the simulated joint, per pair (positive = the simulated joint is better); `dependence only` compares the structural "
              "joint with the product of the structural simulation's OWN marginals, so the marginal shape cancels and only the dependence is scored; lift columns are "
              "sum(joint) / sum(product of that column's own marginals) for the simulations and realised / analytic product for the box scores.", "",
              _md(pt, [("pair type", "pair type"), ("pairs", "pairs"), ("hit rate", "hit rate"), ("p indep (analytic)", "p_indep_analytic"),
                       ("p structural", "p_sim_structural"), ("p anchored", "p_sim_anchored"), ("d LL structural", "dll_sim_structural"),
                       ("z", "z_sim_structural"), ("d LL anchored", "dll_sim_anchored"), ("z", "z_sim_anchored"),
                       ("dependence only: d LL structural vs its own marginals", "dep_dll_sim_structural"), ("z", "dep_z_sim_structural"),
                       ("lift realised", "lift_realised"), ("lift structural", "lift_structural"), ("lift anchored", "lift_anchored")],
                  {k: ("{:+.4f}" if "dll" in k else "{:+.2f}" if "z_" in k else "{:.3f}") for k in pt.columns
                   if k not in ("pair type", "pairs", "games")}), ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--report", nargs="+", metavar="DIR", help="print the markdown tables of finished evaluate output directories and exit")
    ap.add_argument("--player-master")
    ap.add_argument("--team-master")
    ap.add_argument("--params")
    ap.add_argument("--seasons", help="comma list of season_year (2024 = 2024-25)")
    ap.add_argument("--n", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--max-games", type=int)
    ap.add_argument("--months", default="", help="comma list of YYYY-MM to replay (tuning on a subsample)")
    ap.add_argument("--set", action="append", default=[], metavar="a.b.c=VALUE",
                    help="override one params.json entry (JSON value), e.g. player.gamma.points=0.8; repeatable (tuning grid)")
    ap.add_argument("--out-dir")
    a = ap.parse_args(argv)
    if a.report:
        sys.stdout.write(report_markdown(a.report) + "\n")
        return 0
    if not (a.player_master and a.team_master and a.params and a.seasons and a.out_dir):
        ap.error("--player-master --team-master --params --seasons --out-dir are required unless --report is given")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open(a.params) as fh:
        params = json.load(fh)
    for item in a.set:
        path, _, val = item.partition("=")
        node = params
        keys = path.split(".")
        for k in keys[:-1]:
            node = node.setdefault(k, {})
        node[keys[-1]] = json.loads(val)
    res = run(a.player_master, a.team_master, params, [int(s) for s in a.seasons.split(",")], a.n, a.workers, a.seed, a.max_games,
              [m for m in a.months.split(",") if m] or None)
    import os
    os.makedirs(a.out_dir, exist_ok=True)
    res["cal"].to_parquet(f"{a.out_dir}/cal.parquet")
    res["pair"].to_parquet(f"{a.out_dir}/pair.parquet")
    with open(f"{a.out_dir}/corr.json", "w") as fh:
        json.dump({"corr": res["corr"], "tot": res["tot"], "game": res["game"], "params_used": params, "games": res["games"], "games_tried": res["games_tried"], "n": a.n}, fh)
    sys.stdout.write(f"games simulated {res['games']} / {res['games_tried']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
