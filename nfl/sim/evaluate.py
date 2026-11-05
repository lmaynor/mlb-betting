"""Walk-forward evaluation of the drive simulator against the serving EWM engine and the hierarchical engine.

    # 1. replay rows of the two existing engines (once; cached) -- nfl.props.engine_eval.replay_hierarchical
    # 2. the simulator, one process per season (parameters fitted on pbp of seasons < that season only):
    python -m nfl.sim.evaluate run --season 2024 --base base_rows.parquet --out sim_2024 [--draws 2000] [--weeks 1-4]
    # 3. combine seasons into the report
    python -m nfl.sim.evaluate report --base base_rows.parquet --runs sim_2022 sim_2023 ... --out report.md

Protocol (the repo's engine_eval protocol): per-stat paired log score of the 1-unit probability mass of the realised value
(identical floor for every engine), MAE of the mean, Brier of P(over proxy line) where the proxy line is the L5 mean rounded to
x.5 (NOT a book line), standard errors of the paired differences, and the adoption bar of
docs/solutions/conventions/nfl-hierarchical-engine-adoption.md (pooled z >= 2.81 on log score, Brier z >= 1.645, better in >= 4 of 5
seasons). ACCURACY evidence only: no price is involved anywhere.

The simulator's distribution of a stat is the kernel-smoothed histogram of its simulated draws (Silverman bandwidth, reflected at
zero for non-negative stats); anytime TD is the fraction of draws with a rushing or receiving TD.

Information used for game g of week w: pbp / player logs of games strictly before (season, week); the closing spread / total are
NOT used (a ratings-only simulator); the ACTIVE skill-player list of g (who has a player_stats row) -- the same pre-game
inactive-list information the baselines' row set already conditions on. Nothing of g's outcome enters its simulation.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zlib
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

from nfl.props import env_adjust as EA
from nfl.props import engine_eval as EE
from nfl.sim import engine as E
from nfl.sim import fit as F
from nfl.sim import pbp_prep as PP
from nfl.sim import ratings as RT

FIRST_TRAIN_SEASON = 2018
MASS_FLOOR = EE.MASS_FLOOR
MU_FLOOR = EE.MU_FLOOR
SIM_STAT = {"pass_attempts": E.S_ATT, "pass_completions": E.S_CMP, "pass_yds": E.S_PYD, "pass_tds": E.S_PTD,
            "interceptions": E.S_INT, "rush_attempts": E.S_CAR, "rush_yds": E.S_RYD, "receptions": E.S_REC, "rec_yds": E.S_RECYD}
YARD_STATS = ("pass_yds", "rush_yds", "rec_yds")


# -- distribution of one stat from its simulated draws ------------------------------------------------------------------
class DrawDist:
    """Kernel-smoothed distribution of an integer-valued stat from Monte Carlo draws."""

    def __init__(self, draws: np.ndarray, stat: str):
        d = np.asarray(draws, dtype=float)
        self.n = len(d)
        self.stat = stat
        self.mean = float(d.mean())
        self.anytime = stat == "anytime_td"
        if self.anytime:
            self.p1 = float((d > 0).mean())
            return
        self.nonneg = stat != "rush_yds"
        sd = float(d.std())
        q75, q25 = np.percentile(d, [75, 25])
        spread = min(sd, (q75 - q25) / 1.34) if q75 > q25 else sd
        floor = 1.0 if stat in YARD_STATS else 0.25
        self.h = max(0.9 * spread * self.n ** -0.2, floor)
        v, c = np.unique(d, return_counts=True)
        if self.nonneg:
            v = np.concatenate([v, -1.0 - v])
            c = np.concatenate([c, c])
        self.v, self.c = v, c.astype(float) / self.n

    def mass(self, y: float) -> float:
        if self.anytime:
            p = min(max(self.p1, 1.0 - math.exp(-MU_FLOOR)), 1.0 - 1e-9)
            return p if y >= 1 else 1.0 - p
        m = float(np.sum(self.c * (norm.cdf((y + 0.5 - self.v) / self.h) - norm.cdf((y - 0.5 - self.v) / self.h))))
        return max(m, MASS_FLOOR)

    def sf(self, line: float) -> float:
        """P(Y > line) for an integer-valued Y (same convention as the baseline: 1 - cdf(floor(line)))."""
        if self.anytime:
            return self.p1
        x = math.floor(line)
        return float(np.sum(self.c * (1.0 - norm.cdf((x + 0.5 - self.v) / self.h))))


# -- context for one evaluated season -----------------------------------------------------------------------------------
class Context:
    RECENT_GAMES = 6
    OUT_STATUS = ("Out", "Doubtful")

    def __init__(self, season: int, logs: pd.DataFrame, sched: pd.DataFrame, calibrate: bool = True, quiet: bool = False,
                 active_mode: str = "asof", injuries: Optional[pd.DataFrame] = None, pbp_loader=None):
        """active_mode 'asof' (default, no lookahead): a team's active skill players are those who appeared for it in its last
        RECENT_GAMES games, not on their latest team elsewhere, and not reported Out / Doubtful for the game's week.
        'oracle': everyone with a player_stats row in the game itself (uses who produced a stat: a LOOKAHEAD, kept only as a
        sensitivity to show how much it would flatter the simulator)."""
        self.season = season
        self.active_mode = active_mode
        loader = pbp_loader or PP.load_pbp
        train = PP.prep(loader(range(FIRST_TRAIN_SEASON, season)))
        self.P = F.fit_params(train, seasons=tuple(range(FIRST_TRAIN_SEASON, season)))
        allp = PP.prep(loader(range(FIRST_TRAIN_SEASON, season + 1)))
        self.ratings = RT.build_ratings(allp.plays, self.P, k_seasons=list(range(FIRST_TRAIN_SEASON, season)))
        if calibrate:
            self.P.pace_scale = calibrate_pace(self.P, train.plays[train.plays["season"] == season - 1])
        self.targets = RT.league_targets(allp.plays, allp.games)
        self._cal = {}
        self.store = RT.UsageStore(logs, prior_before=season * 100)
        logs = logs.copy()
        logs["_team"] = logs["team"].map(EA.canon)
        self.logs = logs[logs["position"].isin(RT.SKILL)]
        self.by_game = {k: g for k, g in self.logs.groupby(["game_id", "_team"], sort=False)}
        self.team_games = {tm: sorted(set(zip(g["_sort"], g["game_id"]))) for tm, g in self.logs.groupby("_team")}
        self.last_team = {pid: (g["_sort"].to_numpy(), g["_team"].to_numpy()) for pid, g in
                          self.logs.sort_values("_sort", kind="stable").groupby("player_id")}
        self.out = set()
        if injuries is not None and len(injuries):
            inj = injuries[injuries["report_status"].isin(self.OUT_STATUS)]
            self.out = set(zip(inj["season"].astype(int), inj["week"].astype(int), inj["team"].map(EA.canon), inj["gsis_id"]))
        self.out_by_tw = {}
        for (sn, wk, tm, pid) in self.out:
            self.out_by_tw.setdefault((sn, wk, tm), set()).add(pid)
        s = sched[(sched["season"] == season) & (sched["game_type"] == "REG")].copy()
        s["home"] = s["home_team"].map(EA.canon)
        s["away"] = s["away_team"].map(EA.canon)
        self.games = s.sort_values(["week", "game_id"]).reset_index(drop=True)
        self.truth = allp.games[allp.games["season"] == season].set_index("game_id")
        if not quiet:
            print(f"season {season}: params from {FIRST_TRAIN_SEASON}-{season - 1} ({self.P.n_plays} plays), pace_scale "
                  f"{self.P.pace_scale:.3f}, k_off {{ {', '.join(f'{k}:{v:.0f}' for k, v in self.ratings.k_off.items())} }}")

    USAGE_GAMES = 10

    def week_calibration(self, week: int) -> tuple:
        """(yards multiplier, clock multiplier) that make a league-average matchup reproduce the points and plays per team-game
        of the league AS OF this week (results already played; decayed like the team states). Tracks a league that scores more
        or runs faster than the training seasons. Four fixed-point iterations of an average-team simulation."""
        key = int(week)
        if key in self._cal:
            return self._cal[key]
        tgt = self.targets.get((self.season, key))
        eff, pace = 1.0, self.P.pace_scale
        if tgt is not None:
            off = self.ratings.league_adjust(self.season, key)
            for _ in range(4):
                a, b = neutral_team("A"), neutral_team("B")
                for tm in (a, b):
                    tm.off, tm.eff = dict(off), eff
                R = E.simulate(self.P, (a, b), n=1500, seed=17, pace_scale=pace)
                pts = float(R.pts.astype(float).mean())
                plays = float(R.team[:, :, E.TS_PASSPL].astype(float).mean() + R.team[:, :, E.TS_RUSHPL].astype(float).mean())
                eff = float(min(max(eff * (tgt[0] / pts) ** 0.5, 0.8), 1.3))
                pace = float(min(max(pace * plays / tgt[1], 0.85), 1.2))
        self._cal[key] = (eff, pace)
        return self._cal[key]

    def window(self, team: str, t: int, g) -> list:
        """The team's last USAGE_GAMES games before `t`, most recent first, with the players reported Out / Doubtful that week."""
        tg = [(srt, gid) for srt, gid in self.team_games.get(team, []) if srt < t][-self.USAGE_GAMES:][::-1]
        return [(srt, self.out_by_tw.get((int(srt) // 100, int(srt) % 100, team), frozenset())) for srt, _ in tg]

    def active_rows(self, g, team: str, t: int) -> Optional[pd.DataFrame]:
        if self.active_mode == "oracle":
            return self.by_game.get((g.game_id, team))
        tg = [gid for srt, gid in self.team_games.get(team, []) if srt < t][-self.RECENT_GAMES:]
        if not tg:
            return None
        frames = [self.by_game[(gid, team)] for gid in tg if (gid, team) in self.by_game]
        if not frames:
            return None
        rec = pd.concat(frames).sort_values("_sort", kind="stable").drop_duplicates("player_id", keep="last")
        mask = []
        for r in rec.itertuples(index=False):
            ok = (int(g.season), int(g.week), team, r.player_id) not in self.out
            if ok:
                srt, tms = self.last_team[r.player_id]
                i = int(np.searchsorted(srt, t, side="left")) - 1
                ok = i >= 0 and tms[i] == team
            mask.append(ok)
        return rec[np.array(mask, dtype=bool)]

    def specs(self, g) -> Optional[tuple]:
        t = int(g.season) * 100 + int(g.week)
        out = []
        for team, opp in ((g.home, g.away), (g.away, g.home)):
            rows = self.active_rows(g, team, t)
            if rows is None or not len(rows):
                return None
            off, deff = self.ratings.adjustments(g.game_id, team, int(g.season), int(g.week))
            players = RT.build_players(rows, self.store, t, window=self.window(team, t, g))
            if not players:
                return None
            out.append(E.TeamSpec(team, off=off, deff=deff, players=players, eff=self.week_calibration(int(g.week))[0]))
        return tuple(out)


def neutral_team(name: str) -> "E.TeamSpec":
    pl = [E.PlayerSpec("qb", "QB", "QB", att_w=1.0, car_w=0.06),
          E.PlayerSpec("rb1", "RB1", "RB", car_w=0.55, tgt_w=0.10), E.PlayerSpec("rb2", "RB2", "RB", car_w=0.25, tgt_w=0.04),
          E.PlayerSpec("wr1", "WR1", "WR", tgt_w=0.26, car_w=0.01), E.PlayerSpec("wr2", "WR2", "WR", tgt_w=0.20),
          E.PlayerSpec("wr3", "WR3", "WR", tgt_w=0.15), E.PlayerSpec("te1", "TE1", "TE", tgt_w=0.18),
          E.PlayerSpec("te2", "TE2", "TE", tgt_w=0.07)]
    return E.TeamSpec(name, players=pl)


def calibrate_pace(P: "F.SimParams", last_season_plays: pd.DataFrame, n: int = 3000) -> float:
    """One-step global clock multiplier: simulated plays per team-game over the plays per team-game of the last TRAINING
    season (a ratio > 1 means the simulated game runs too many plays and its clock must be slowed)."""
    games = last_season_plays["game_id"].nunique()
    emp = len(last_season_plays) / max(2 * games, 1)
    R = E.simulate(P, (neutral_team("A"), neutral_team("B")), n=n, seed=11)
    sim = float(R.team[:, :, E.TS_PASSPL].astype(float).mean() + R.team[:, :, E.TS_RUSHPL].astype(float).mean())
    return float(min(max(sim / emp, 0.9), 1.15))


def game_seed(game_id: str, base: int = 0) -> int:
    return (zlib.crc32(game_id.encode()) ^ (base * 2654435761)) & 0x7FFFFFFF


# -- one season ---------------------------------------------------------------------------------------------------------
def _load_injuries(season: int) -> Optional[pd.DataFrame]:
    try:
        from nfl.data.read import load
        return load("injuries", seasons=[season - 1, season], strict=False, columns=["season", "week", "team", "gsis_id", "report_status"])
    except Exception:                                                                    # noqa: BLE001 - no injuries mirror: no exclusion
        return None


def parse_weeks(spec: Optional[str]) -> Optional[range]:
    if not spec:
        return None
    lo, _, hi = spec.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def run_season(season: int, base: pd.DataFrame, n_draws: int = 2000, weeks: Optional[range] = None, ctx: Optional[Context] = None,
               seed: int = 0, verbose: bool = True, active_mode: str = "asof", anchor: str = "none") -> tuple:
    """-> (rows, games, margins): per-(player-game, stat) simulator scores on the baseline's rows, per-game team / correlation
    diagnostics, and the aggregate simulated margin histogram."""
    from nfl.data.read import load
    if ctx is None:
        logs = EE.load_player_logs(range(FIRST_TRAIN_SEASON, season + 1))
        ctx = Context(season, logs, load("schedules"), active_mode=active_mode, injuries=_load_injuries(season))
    b = base[base["season"] == season]
    brow = {k: g for k, g in b.groupby("game_id", sort=False)}
    rows, gdiag = [], []
    margin_hist = np.zeros(121)
    for g in ctx.games.itertuples(index=False):
        if weeks is not None and int(g.week) not in weeks:
            continue
        if pd.isna(getattr(g, "result", np.nan)):
            continue
        sp = ctx.specs(g)
        if sp is None:
            continue
        pace = ctx.week_calibration(int(g.week))[1]
        if anchor == "lines":
            sp = anchor_to_lines(ctx, sp, g, pace)
        R = E.simulate(ctx.P, sp, n=n_draws, seed=game_seed(g.game_id, seed), pace_scale=pace)
        pts = R.pts.astype(float)
        margin = pts[:, 0] - pts[:, 1]
        total = pts.sum(1)
        mi = np.clip(np.round(margin).astype(int) + 60, 0, 120)
        margin_hist += np.bincount(mi, minlength=121)
        # --- player rows
        sub = brow.get(g.game_id)
        pos = {}
        for side, tm in enumerate(sp):
            for j, p in enumerate(tm.players):
                pos[p.pid] = (side, j)
        if sub is not None:
            for r in sub.itertuples(index=False):
                loc = pos.get(r.player_id)
                if loc is None:
                    continue
                side, j = loc
                if r.stat == "anytime_td":
                    d = (R.stats[:, side, j, E.S_RTD].astype(int) + R.stats[:, side, j, E.S_RECTD].astype(int))
                elif r.stat in SIM_STAT:
                    d = R.stats[:, side, j, SIM_STAT[r.stat]]
                else:
                    continue
                dist = DrawDist(d, r.stat)
                y = float(r.y)
                if r.stat == "anytime_td":
                    ls = math.log(dist.mass(1.0 if y >= 1 else 0.0))
                else:
                    ls = math.log(dist.mass(round(y)))
                rows.append({"game_id": g.game_id, "player_id": r.player_id, "stat": r.stat, "season": season, "week": int(g.week),
                             "player": r.player, "ls_sim": ls, "p_sim": dist.sf(r.line), "mu_sim": dist.mean})
        gdiag.append(_game_diag(g, sp, R, ctx))
        if verbose and len(gdiag) % 25 == 0:
            print(f"  season {season}: {len(gdiag)} games simulated, {len(rows)} rows", flush=True)
    return pd.DataFrame(rows), pd.DataFrame(gdiag), margin_hist


def anchor_to_lines(ctx: "Context", sp: tuple, g, pace: float, iters: int = 3, n: int = 800) -> tuple:
    """Scale each offence's yards (`TeamSpec.eff`) until the simulated mean points match the closing-line implied team totals
    (total / 2 +- spread / 2; nflverse spread_line > 0 = home favoured). Uses PRE-GAME closing numbers only. Without both lines
    the specs are returned unchanged."""
    sl, tl = getattr(g, "spread_line", np.nan), getattr(g, "total_line", np.nan)
    if not (sl == sl and tl == tl):
        return sp
    imp = (tl / 2.0 + sl / 2.0, tl / 2.0 - sl / 2.0)
    effs = [sp[0].eff, sp[1].eff]
    for _ in range(iters):
        specs = tuple(E.TeamSpec(t.name, off=t.off, deff=t.deff, players=t.players, eff=effs[i]) for i, t in enumerate(sp))
        R = E.simulate(ctx.P, specs, n=n, seed=5, pace_scale=pace)
        m = R.pts.astype(float).mean(0)
        effs = [float(min(max(effs[i] * (imp[i] / max(m[i], 1.0)) ** 0.5, 0.7), 1.4)) for i in range(2)]
    return tuple(E.TeamSpec(t.name, off=t.off, deff=t.deff, players=t.players, eff=effs[i]) for i, t in enumerate(sp))


def _game_diag(g, sp: tuple, R: "E.SimResult", ctx: Context) -> dict:
    pts = R.pts.astype(float)
    margin, total = pts[:, 0] - pts[:, 1], pts.sum(1)
    d = {"game_id": g.game_id, "season": int(g.season), "week": int(g.week), "home": g.home, "away": g.away,
         "sim_total": float(total.mean()), "sim_total_sd": float(total.std()), "sim_margin": float(margin.mean()),
         "sim_margin_sd": float(margin.std()), "corr_total_margin": _corr(total, margin),
         "spread_line": float(getattr(g, "spread_line", np.nan)), "total_line": float(getattr(g, "total_line", np.nan)),
         "result": float(g.result), "actual_total": float(g.home_score + g.away_score)}
    lines = d["spread_line"]
    if lines == lines:
        d["p_home_cover"] = float((margin > lines).mean() + 0.5 * (margin == lines).mean())
        d["home_covered"] = float(g.result > lines) + 0.5 * float(g.result == lines)
    if d["total_line"] == d["total_line"]:
        d["p_over"] = float((total > d["total_line"]).mean() + 0.5 * (total == d["total_line"]).mean())
        d["went_over"] = float(d["actual_total"] > d["total_line"]) + 0.5 * float(d["actual_total"] == d["total_line"])
    # team-level pass vs rush attempts and the QB / top-receiver pair
    for side, tm in enumerate(sp):
        att = R.stats[:, side, :, E.S_ATT].astype(float).sum(1)
        car = R.stats[:, side, :, E.S_CAR].astype(float).sum(1)
        d[f"corr_att_car_{side}"] = _corr(att, car)
        d[f"sim_att_{side}"], d[f"sim_att_sd_{side}"] = float(att.mean()), float(att.std())
        d[f"sim_car_{side}"], d[f"sim_car_sd_{side}"] = float(car.mean()), float(car.std())
        qb = max(range(len(tm.players)), key=lambda j: tm.players[j].att_w)
        rec = max((j for j, p in enumerate(tm.players) if p.pos in ("WR", "TE")), key=lambda j: tm.players[j].tgt_w, default=None)
        if tm.players[qb].att_w > 0 and rec is not None:
            py = R.stats[:, side, qb, E.S_PYD].astype(float)
            ry = R.stats[:, side, rec, E.S_RECYD].astype(float)
            d[f"corr_qb_rec_{side}"] = _corr(py, ry)
            d[f"qb_pid_{side}"], d[f"rec_pid_{side}"] = tm.players[qb].pid, tm.players[rec].pid
            d[f"sim_qb_yds_{side}"], d[f"sim_qb_yds_sd_{side}"] = float(py.mean()), float(py.std())
            d[f"sim_rec_yds_{side}"], d[f"sim_rec_yds_sd_{side}"] = float(ry.mean()), float(ry.std())
            lg = ctx.logs
            for pid_key, col, name in ((f"qb_pid_{side}", "passing_yards", "act_qb_yds"), (f"rec_pid_{side}", "receiving_yards", "act_rec_yds")):
                row = lg[(lg["game_id"] == g.game_id) & (lg["player_id"] == d[pid_key])]
                d[f"{name}_{side}"] = float(pd.to_numeric(row[col], errors="coerce").sum()) if len(row) else np.nan
        tg = ctx.logs[(ctx.logs["game_id"] == g.game_id) & (ctx.logs["_team"] == tm.name)]
        d[f"act_att_{side}"] = float(pd.to_numeric(tg["attempts"], errors="coerce").sum())
        d[f"act_car_{side}"] = float(pd.to_numeric(tg["carries"], errors="coerce").sum())
    return d


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


# -- reporting ----------------------------------------------------------------------------------------------------------
def _se(x: np.ndarray) -> float:
    return float(np.std(x, ddof=1) / math.sqrt(len(x))) if len(x) > 1 else float("nan")


def merge_rows(base: pd.DataFrame, sim: pd.DataFrame) -> pd.DataFrame:
    """The baseline replay rows with the simulator's columns attached (inner join: rows both engines scored)."""
    key = ["game_id", "player_id", "stat"]
    m = base.merge(sim[key + ["ls_sim", "p_sim", "mu_sim"]], on=key, how="inner")
    return m.drop_duplicates(key)


def compare(res: pd.DataFrame, cand: str, ref: str, seasons: list) -> dict:
    """Paired candidate-minus-reference per stat: `cand` / `ref` in {'sim', 'hier', 'base'}."""
    out = {}
    r = res[res["season"].isin(seasons)]
    for stat in EE.HIER_STATS:
        d = r[r["stat"] == stat]
        if len(d) < EE.MIN_ROWS:
            out[stat] = {"n": int(len(d)), "verdict": "INSUFFICIENT"}
            continue
        over = (d["y"] > d["line"]).astype(float).to_numpy()
        dls = (d[f"ls_{cand}"] - d[f"ls_{ref}"]).to_numpy()
        dbr = ((d[f"p_{cand}"] - over) ** 2 - (d[f"p_{ref}"] - over) ** 2).to_numpy()
        dae = (np.abs(d["y"] - d[f"mu_{cand}"]) - np.abs(d["y"] - d[f"mu_{ref}"])).to_numpy()
        per = {int(s): float((g[f"ls_{cand}"] - g[f"ls_{ref}"]).mean()) for s, g in d.groupby("season")}
        z = float(dls.mean() / _se(dls)) if _se(dls) > 0 else float("nan")
        zb = float(-dbr.mean() / _se(dbr)) if _se(dbr) > 0 else float("nan")
        better = sum(1 for v in per.values() if v > 0)
        need = 4 if len(per) >= 5 else len(per)
        verdict = "ADOPT" if (z >= EE.ADOPT_Z_HIER and zb >= EE.ADOPT_Z_BRIER and better >= need) else (
            "WORSE" if z <= -EE.ADOPT_Z_HIER else "NO CLEAR GAIN")
        out[stat] = {"n": int(len(d)), "d_logscore": float(dls.mean()), "se": _se(dls), "z": z, "d_brier": float(dbr.mean()),
                     "d_brier_se": _se(dbr), "z_brier": zb, "d_mae": float(dae.mean()), "d_mae_se": _se(dae),
                     "mae_cand": float(np.abs(d["y"] - d[f"mu_{cand}"]).mean()), "mae_ref": float(np.abs(d["y"] - d[f"mu_{ref}"]).mean()),
                     "per_season": per, "seasons_better": better, "verdict": verdict}
    return out


def render_compare(cmp: dict, title: str) -> str:
    L = [f"#### {title}", "", "| stat | n | d log score (se) | z | d Brier (se) | Brier z | MAE ref -> cand (d, se) | seasons better | verdict |",
         "|---|---:|---:|---:|---:|---:|---|---|---|"]
    for stat in EE.HIER_STATS:
        c = cmp.get(stat, {})
        if "d_logscore" not in c:
            L.append(f"| {stat} | {c.get('n', 0)} | | | | | | | {c.get('verdict', 'n/a')} |")
            continue
        L.append(f"| {stat} | {c['n']:,} | {c['d_logscore']:+.4f} ({c['se']:.4f}) | {c['z']:+.1f} | {c['d_brier']:+.4f} ({c['d_brier_se']:.4f}) | "
                 f"{c['z_brier']:+.1f} | {c['mae_ref']:.3f} -> {c['mae_cand']:.3f} ({c['d_mae']:+.3f}, {c['d_mae_se']:.3f}) | "
                 f"{c['seasons_better']}/{len(c['per_season'])} | {c['verdict']} |")
    return "\n".join(L) + "\n"


def pooled_mae_table(res: pd.DataFrame, seasons: list) -> str:
    r = res[res["season"].isin(seasons)]
    L = ["| stat | n | MAE serving | MAE hierarchical | MAE simulator | mean y | mean serving | mean hier | mean sim |", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for stat in EE.HIER_STATS:
        d = r[r["stat"] == stat]
        if len(d) < EE.MIN_ROWS:
            continue
        y = d["y"]
        L.append(f"| {stat} | {len(d):,} | {np.abs(y - d['mu_base']).mean():.3f} | {np.abs(y - d['mu_hier']).mean():.3f} | "
                 f"{np.abs(y - d['mu_sim']).mean():.3f} | {y.mean():.3f} | {d['mu_base'].mean():.3f} | {d['mu_hier'].mean():.3f} | {d['mu_sim'].mean():.3f} |")
    return "\n".join(L) + "\n"


def correlation_report(gd: pd.DataFrame) -> str:
    """Simulated within-game dependence vs the empirical dependence of standardised residuals."""
    L = ["| pair | simulated within-game corr (mean over games) | empirical corr of standardised residuals | n |", "|---|---:|---:|---:|"]

    def emp(a, b, ma, sa, mb, sb):
        za, zb = (a - ma) / sa, (b - mb) / sb
        ok = np.isfinite(za) & np.isfinite(zb) & (sa > 0) & (sb > 0)
        return (float(np.corrcoef(za[ok], zb[ok])[0, 1]), int(ok.sum())) if ok.sum() > 30 else (float("nan"), int(ok.sum()))
    # QB passing yards vs top receiver's receiving yards
    sc, ec_a = [], []
    for side in (0, 1):
        if f"corr_qb_rec_{side}" in gd:
            sc.append(gd[f"corr_qb_rec_{side}"])
    sim_qr = float(pd.concat(sc).mean()) if sc else float("nan")
    za = np.concatenate([((gd[f"act_qb_yds_{s}"] - gd[f"sim_qb_yds_{s}"]) / gd[f"sim_qb_yds_sd_{s}"]).to_numpy() for s in (0, 1) if f"act_qb_yds_{s}" in gd])
    zb = np.concatenate([((gd[f"act_rec_yds_{s}"] - gd[f"sim_rec_yds_{s}"]) / gd[f"sim_rec_yds_sd_{s}"]).to_numpy() for s in (0, 1) if f"act_rec_yds_{s}" in gd])
    ok = np.isfinite(za) & np.isfinite(zb)
    e_qr = float(np.corrcoef(za[ok], zb[ok])[0, 1]) if ok.sum() > 30 else float("nan")
    L.append(f"| QB passing yds vs his top receiver's receiving yds | {sim_qr:+.3f} | {e_qr:+.3f} | {int(ok.sum())} |")
    # team pass attempts vs rush attempts
    sim_ar = float(pd.concat([gd[f"corr_att_car_{s}"] for s in (0, 1)]).mean())
    za = np.concatenate([((gd[f"act_att_{s}"] - gd[f"sim_att_{s}"]) / gd[f"sim_att_sd_{s}"]).to_numpy() for s in (0, 1)])
    zb = np.concatenate([((gd[f"act_car_{s}"] - gd[f"sim_car_{s}"]) / gd[f"sim_car_sd_{s}"]).to_numpy() for s in (0, 1)])
    ok = np.isfinite(za) & np.isfinite(zb)
    L.append(f"| team pass attempts vs rush attempts | {sim_ar:+.3f} | {float(np.corrcoef(za[ok], zb[ok])[0, 1]):+.3f} | {int(ok.sum())} |")
    # total vs margin
    sim_tm = float(gd["corr_total_margin"].mean())
    zt = (gd["actual_total"] - gd["sim_total"]) / gd["sim_total_sd"]
    zm = (gd["result"] - gd["sim_margin"]) / gd["sim_margin_sd"]
    L.append(f"| game total vs margin (home - away) | {sim_tm:+.3f} | {float(np.corrcoef(zt, zm)[0, 1]):+.3f} | {len(gd)} |")
    return "\n".join(L) + "\n"


def team_accuracy(gd: pd.DataFrame) -> str:
    g = gd.dropna(subset=["spread_line", "total_line"])
    mt = float(np.abs(g["actual_total"] - g["sim_total"]).mean())
    mm = float(np.abs(g["result"] - g["sim_margin"]).mean())
    lt = float(np.abs(g["actual_total"] - g["total_line"]).mean())
    lm = float(np.abs(g["result"] - g["spread_line"]).mean())
    bs = float(((g["p_home_cover"] - g["home_covered"]) ** 2).mean())
    b0 = float(((0.5 - g["home_covered"]) ** 2).mean())
    bo = float(((g["p_over"] - g["went_over"]) ** 2).mean())
    L = ["| quantity | simulator | closing line (context only) |", "|---|---:|---:|",
         f"| MAE of game total | {mt:.2f} | {lt:.2f} |", f"| MAE of home margin | {mm:.2f} | {lm:.2f} |",
         f"| Brier of P(home covers) | {bs:.4f} | coin flip {b0:.4f} |", f"| Brier of P(over) | {bo:.4f} | coin flip "
         f"{float(((0.5 - g['went_over']) ** 2).mean()):.4f} |",
         f"| mean simulated total / actual / line | {g['sim_total'].mean():.2f} | {g['actual_total'].mean():.2f} / {g['total_line'].mean():.2f} |",
         f"| mean within-game sd of simulated total / sd of actual total | {g['sim_total_sd'].mean():.2f} | {g['actual_total'].std():.2f} |",
         f"| sd of standardised total residual (1.0 = calibrated spread) | {((g['actual_total'] - g['sim_total']) / g['sim_total_sd']).std():.2f} | |",
         f"| sd of standardised margin residual | {((g['result'] - g['sim_margin']) / g['sim_margin_sd']).std():.2f} | |"]
    return "\n".join(L) + f"\n\n({len(g)} games with both closing lines.)\n"


def scoreboard_report(margin_hist: np.ndarray, truth_margins: np.ndarray, n_games: int) -> str:
    """Simulated vs realised distribution of the absolute regulation margin at the NFL key numbers."""
    sim = margin_hist / margin_hist.sum()
    idx = np.arange(121) - 60
    sim_abs = {k: float(sim[np.abs(idx) == k].sum()) for k in range(0, 61)}
    emp = {k: float((np.abs(truth_margins) == k).mean()) for k in range(0, 61)}
    L = ["| |margin| | simulated | realised (regulation) | ratio | realised count / se |", "|---|---:|---:|---:|---|"]
    for k in (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14, 17):
        se = math.sqrt(emp[k] * (1 - emp[k]) / max(len(truth_margins), 1))
        L.append(f"| {k} | {sim_abs[k] * 100:.2f}% | {emp[k] * 100:.2f}% | {sim_abs[k] / emp[k] if emp[k] else float('nan'):.2f} | "
                 f"{int(round(emp[k] * len(truth_margins)))} / {se * 100:.2f}pp |")
    key = (3, 7, 6, 10, 14)
    L.append(f"\nKey numbers {key} together: simulated {sum(sim_abs[k] for k in key) * 100:.1f}% vs realised {sum(emp[k] for k in key) * 100:.1f}% "
             f"of regulation margins ({len(truth_margins)} games, {n_games} simulated).")
    L.append(f"Mean |margin|: simulated {sum(k * sim_abs[k] for k in sim_abs):.2f}, realised {np.abs(truth_margins).mean():.2f}.")
    return "\n".join(L) + "\n"


def league_check(season: int, n: int = 6000) -> pd.DataFrame:
    """Structural check: the NEUTRAL simulator (league-average teams, no ratings) fitted on seasons < `season`, against the
    realised per-team-game averages of `season`. Shows what the engine's mechanics alone reproduce, and the walk-forward
    drift (a league that changed since the training seasons)."""
    train = PP.prep(PP.load_pbp(range(FIRST_TRAIN_SEASON, season)))
    P = F.fit_params(train, seasons=tuple(range(FIRST_TRAIN_SEASON, season)))
    P.pace_scale = calibrate_pace(P, train.plays[train.plays["season"] == season - 1])
    te = PP.prep(PP.load_pbp([season]))
    pl = te.plays
    tg = pl["game_id"].nunique() * 2
    pas = pl[pl["is_pass"] == 1]
    emp = {"plays": len(pl) / tg, "pass attempts": (len(pas) - pas["sack"].sum()) / tg, "completions": pas["complete"].sum() / tg,
           "pass yds": pas.loc[pas["complete"] == 1, "yards"].sum() / tg, "pass TD": pas["pass_td"].sum() / tg,
           "interceptions": pas["intc"].sum() / tg, "sacks": pas["sack"].sum() / tg, "rush attempts": (pl["is_pass"] == 0).sum() / tg,
           "rush yds": pl.loc[pl["is_pass"] == 0, "yards"].sum() / tg, "rush TD": pl["rush_td"].sum() / tg, "drives": len(te.drives) / tg,
           "points (regulation)": (te.games["home_score_reg"] + te.games["away_score_reg"]).mean() / 2}
    R = E.simulate(P, (neutral_team("A"), neutral_team("B")), n=n, seed=3)
    t, st = R.team.astype(float), R.stats.astype(float)
    sim = {"plays": t[:, :, E.TS_PASSPL].mean() + t[:, :, E.TS_RUSHPL].mean(), "pass attempts": st[:, :, :, E.S_ATT].sum(2).mean(),
           "completions": st[:, :, :, E.S_CMP].sum(2).mean(), "pass yds": st[:, :, :, E.S_PYD].sum(2).mean(),
           "pass TD": st[:, :, :, E.S_PTD].sum(2).mean(), "interceptions": st[:, :, :, E.S_INT].sum(2).mean(),
           "sacks": t[:, :, E.TS_SACK].mean(), "rush attempts": st[:, :, :, E.S_CAR].sum(2).mean(),
           "rush yds": st[:, :, :, E.S_RYD].sum(2).mean(), "rush TD": st[:, :, :, E.S_RTD].sum(2).mean(),
           "drives": t[:, :, E.TS_DRIVES].mean(), "points (regulation)": R.pts.astype(float).mean()}
    for k, name in enumerate(["normal", "chasing (down 9+, 2nd half)", "milking (up 9+, 2nd half)"]):
        d = pl[pl["script"] == k]
        emp[f"pass share, {name}"] = float(d["is_pass"].mean())
        pn, rn = t[:, :, E.TS_SCRIPT0 + 2 * k].sum(), t[:, :, E.TS_SCRIPT0 + 1 + 2 * k].sum()
        sim[f"pass share, {name}"] = float(pn / max(pn + rn, 1.0))
    df = pd.DataFrame({"realised": emp, "simulated": sim})
    df["ratio"] = df["simulated"] / df["realised"]
    return df


def render_league(seasons: list) -> str:
    frames = {s: league_check(s) for s in seasons}
    L = ["| per team-game | " + " | ".join(f"{s} real / sim" for s in seasons) + " |", "|---|" + "---|" * len(seasons)]
    for k in frames[seasons[0]].index:
        L.append(f"| {k} | " + " | ".join(f"{frames[s].loc[k, 'realised']:.2f} / {frames[s].loc[k, 'simulated']:.2f}" for s in seasons) + " |")
    return "\n".join(L) + "\n"


OUTCOME_COLS = ["yards_gained", "sack", "complete_pass", "interception", "pass_touchdown", "rush_touchdown", "fumble_lost",
                "return_touchdown", "play_type", "air_yards", "receiver_player_id", "rusher_player_id", "passer_player_id",
                "fixed_drive_result", "touchdown", "field_goal_result", "kick_distance"]
LOG_OUTCOME_COLS = ["completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions", "carries", "rushing_yards",
                    "rushing_tds", "receptions", "targets", "receiving_yards", "receiving_tds"]


def scramble_pbp_from(pbp: pd.DataFrame, season: int, week: int, seed: int = 0) -> pd.DataFrame:
    """Permute every outcome column among the rows of (season, week >= `week`): destroys the information of those plays while
    keeping their identity columns (game, teams, week). A leakage-free pipeline cannot notice."""
    d = pbp.reset_index(drop=True).copy()
    idx = np.nonzero(((d["season"] == season) & (d["week"] >= week)).to_numpy())[0]
    perm = np.random.default_rng(seed).permutation(idx)
    cols = [c for c in OUTCOME_COLS if c in d.columns]
    d.loc[idx, cols] = d.loc[perm, cols].to_numpy()
    return d


def scramble_logs_from(logs: pd.DataFrame, t: int, seed: int = 0) -> pd.DataFrame:
    d = logs.reset_index(drop=True).copy()
    idx = np.nonzero((d["_sort"] >= t).to_numpy())[0]
    perm = np.random.default_rng(seed).permutation(idx)
    cols = [c for c in LOG_OUTCOME_COLS if c in d.columns]
    d.loc[idx, cols] = d.loc[perm, cols].to_numpy()
    return d


def leakage_check(season: int, week: int, n_games: int = 3, draws: int = 600) -> dict:
    """Real-data leakage test. The outcomes of every play / player-game from `week` onward are scrambled; the simulation of the
    games of `week` (same seeds) must be BIT-IDENTICAL, because nothing at or after the game may enter it. A negative control
    scrambles data from one week EARLIER and must change the draws, so the test has power."""
    from nfl.data.read import load
    sched = load("schedules")
    logs = EE.load_player_logs(range(FIRST_TRAIN_SEASON, season + 1))
    inj = _load_injuries(season)
    t = season * 100 + week
    pbp_all = PP.load_pbp(range(FIRST_TRAIN_SEASON, season + 1))

    def make(week_cut: Optional[int]):
        if week_cut is None:
            return Context(season, logs, sched, injuries=inj, quiet=True, pbp_loader=lambda seasons: pbp_all[pbp_all["season"].isin(list(seasons))])
        pb = scramble_pbp_from(pbp_all, season, week_cut)
        lg = scramble_logs_from(logs, season * 100 + week_cut)
        return Context(season, lg, sched, injuries=inj, quiet=True, pbp_loader=lambda seasons: pb[pb["season"].isin(list(seasons))])
    clean, dirty, early = make(None), make(week), make(week - 1)
    games = [g for g in clean.games.itertuples(index=False) if int(g.week) == week][:n_games]
    out = {"season": season, "week": week, "games": [g.game_id for g in games], "identical": [], "control_changed": []}
    for g in games:
        a, b, c = clean.specs(g), dirty.specs(g), early.specs(g)
        same_spec = all(x.off == y.off and x.deff == y.deff and [(p.pid, p.tgt_w, p.car_w, p.att_w) for p in x.players] ==
                        [(p.pid, p.tgt_w, p.car_w, p.att_w) for p in y.players] for x, y in zip(a, b))
        ra = E.simulate(clean.P, a, n=draws, seed=game_seed(g.game_id), pace_scale=clean.week_calibration(week)[1])
        rb = E.simulate(dirty.P, b, n=draws, seed=game_seed(g.game_id), pace_scale=dirty.week_calibration(week)[1])
        rc = E.simulate(early.P, c, n=draws, seed=game_seed(g.game_id), pace_scale=early.week_calibration(week)[1])
        out["identical"].append(bool(same_spec and np.array_equal(ra.stats, rb.stats) and np.array_equal(ra.pts, rb.pts)))
        out["control_changed"].append(bool(not (np.array_equal(ra.stats, rc.stats) and np.array_equal(ra.pts, rc.pts))))
    out["pass"] = all(out["identical"]) and any(out["control_changed"])
    return out


def cmd_leakage(a) -> int:
    r = leakage_check(a.season, a.week, a.games)
    print(json.dumps(r, indent=1))
    return 0 if r["pass"] else 1


def cmd_run(a) -> int:
    base = pd.read_parquet(a.base)
    rows, gd, mh = run_season(a.season, base, a.draws, parse_weeks(a.weeks), seed=a.seed, active_mode=a.active, anchor=a.anchor)
    rows.to_parquet(a.out + "_rows.parquet")
    gd.to_parquet(a.out + "_games.parquet")
    np.save(a.out + "_margin_hist.npy", mh)
    print(f"season {a.season}: {len(rows)} scored rows, {len(gd)} games -> {a.out}_*")
    return 0


def cmd_report(a) -> int:
    base = pd.read_parquet(a.base)
    sims = [pd.read_parquet(r + "_rows.parquet") for r in a.runs]
    gds = [pd.read_parquet(r + "_games.parquet") for r in a.runs]
    mh = sum(np.load(r + "_margin_hist.npy") for r in a.runs)
    res = merge_rows(base, pd.concat(sims, ignore_index=True))
    gd = pd.concat(gds, ignore_index=True)
    seasons = sorted(int(s) for s in res["season"].unique())
    held = [s for s in seasons if s >= a.heldout_from]
    L = []
    L.append(f"Rows scored by all three engines: {len(res):,} ({seasons[0]}-{seasons[-1]}); held-out seasons for the verdict: {held}.\n")
    cov = base[base["season"].isin(seasons)].groupby("stat").size().rename("baseline rows").to_frame().join(
        res.groupby("stat").size().rename("also scored by the simulator"))
    cov["coverage"] = (cov.iloc[:, 1] / cov.iloc[:, 0]).map("{:.1%}".format)
    L.append("Row coverage (a baseline row is skipped when the player is not in the simulator's as-of active set):\n")
    L.append("| stat | baseline rows | also scored by the simulator | coverage |\n|---|---:|---:|---:|\n" + "\n".join(
        f"| {k} | {r.iloc[0]:,} | {r.iloc[1]:,} | {r.iloc[2]} |" for k, r in cov.iterrows()) + "\n")
    L.append(render_compare(compare(res, "sim", "base", held), "Simulator minus SERVING EWM engine (held out; + log score is better)"))
    L.append(render_compare(compare(res, "sim", "hier", held), "Simulator minus HIERARCHICAL engine (held out)"))
    L.append(render_compare(compare(res, "hier", "base", held), "Hierarchical minus serving (same rows, for scale)"))
    L.append(render_compare(compare(res, "sim", "base", seasons), f"Simulator minus serving, ALL replayed seasons {seasons[0]}-{seasons[-1]}"))
    L.append("#### Per-season log-score difference, simulator minus serving\n")
    L.append("| stat | " + " | ".join(str(s) for s in seasons) + " |")
    L.append("|---|" + "---:|" * len(seasons))
    c_all = compare(res, "sim", "base", seasons)
    for stat in EE.HIER_STATS:
        ps = c_all.get(stat, {}).get("per_season")
        if ps:
            L.append(f"| {stat} | " + " | ".join(f"{ps.get(s, float('nan')):+.4f}" for s in seasons) + " |")
    L.append("\n#### MAE and mean by engine (held-out seasons)\n")
    L.append(pooled_mae_table(res, held))
    L.append("#### Dependence: simulated vs empirical\n")
    L.append(correlation_report(gd))
    L.append("#### Team totals and cover probabilities\n")
    L.append(team_accuracy(gd))
    from nfl.data.read import load
    tr = PP.prep(PP.load_pbp(seasons))
    tm = (tr.games["home_score_reg"] - tr.games["away_score_reg"]).to_numpy()
    L.append("#### Scoreboard: |regulation margin| at the key numbers\n")
    L.append(scoreboard_report(mh, tm, len(gd)))
    txt = "\n".join(L)
    print(txt)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(txt)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--season", type=int, required=True)
    r.add_argument("--base", required=True, help="parquet from engine_eval.replay_hierarchical")
    r.add_argument("--out", required=True)
    r.add_argument("--draws", type=int, default=2000)
    r.add_argument("--weeks")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--active", default="asof", choices=["asof", "oracle"])
    r.add_argument("--anchor", default="none", choices=["none", "lines"],
                   help="'lines' scales each offence so simulated points match the closing-line implied team totals")
    lc = sub.add_parser("league")
    lc.add_argument("--seasons", nargs="+", type=int, default=[2022, 2023, 2024, 2025])
    lk = sub.add_parser("leakage")
    lk.add_argument("--season", type=int, default=2024)
    lk.add_argument("--week", type=int, default=9)
    lk.add_argument("--games", type=int, default=3)
    q = sub.add_parser("report")
    q.add_argument("--base", required=True)
    q.add_argument("--runs", nargs="+", required=True)
    q.add_argument("--heldout-from", type=int, default=2024)
    q.add_argument("--out")
    a = ap.parse_args(argv)
    if a.cmd == "league":
        print(render_league(a.seasons))
        return 0
    if a.cmd == "leakage":
        return cmd_leakage(a)
    return cmd_run(a) if a.cmd == "run" else cmd_report(a)


if __name__ == "__main__":
    sys.exit(main())
