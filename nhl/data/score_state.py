"""NHL score-state durations, true 5v5 SOG/60 by score state, and the H3 power-play matchup test.

Built for Lane 6 of the game-state build-out (Argon consult, Nov 11, 2026; verbatim at
~/workspace/beezy/profitability/argon-game-state-consult-VERBATIM-2026-11-11.md, H2/H3).

Argon's nitpick is the spec: raw shot ACCUMULATIONS by score state are unusable because they
confound rate with exposure time (games sit tied at 0-0 for long stretches of period 1). The
only valid form is SOG/60 by score state, which needs the clock time each team spent in each
state. That time lives in the raw play-by-play at NHL/raw/pbp/{game_id}.json.gz -- every play
carries `timeInPeriod`, `periodDescriptor` and `situationCode` -- while shot events live in the
shots masters (NHL/shots/shots_{season}.csv, themselves flattened from the same PBP by
nhl.data.flatten). This module is the join:

    durations   -- per (game, team, score state, strength) seconds, from the PBP clock:
                   the interval after a play inherits that play's situationCode, and the
                   score state in force once that play is processed (a goal changes the
                   state at its own timestamp; the goal EVENT itself is counted, on the
                   shots side, in the state BEFORE it).
    shot counts -- per (game, team, score state, strength) SOG / attempts, from the shots
                   master, with the score state reconstructed from its own GOAL events in
                   (period, time_sec, event_id) order.
    SOG/60      -- counts / durations, 5v5 only for the headline tables. "5v5" is strict:
                   situationCode skaters 5-against-5 AND both goalie digits 1 (nets dressed).
                   A 5-skater extra-attacker segment (own goalie pulled) is NOT 5v5, and
                   shootout plays never enter either side of the join.

On top of the joined tables:

    team_score_effect_slopes -- per-team Poisson GLM (IRLS, log-exposure offset) of 5v5 SOG
        rate on the team's capped goal differential (-3..+3): one slope per team, with an
        empirical-Bayes shrinkage toward the league slope, plus the raw per-state rates the
        Nov 24 SOG discovery screen consumes (see `screen_features`).
    penalty_matchup_test (H3) -- per directed team-game, power-play opportunities drawn
        against leave-one-game-out team rates: additive (drawn rate + opponent taken rate)
        vs the same + their product. H3 PROMOTES only if the interaction's nested F-test
        has p < 0.01 AND the R^2 lift is >= 0.015 (Argon's bars); otherwise H3 dies.

Pure functions over parsed PBP dicts / shots frames, except `main`, which runs the join over
local PBP .json.gz files + a shots CSV and writes the derived tables to --out-dir (bank them
to a scratch/evidence prefix, never a production prefix).

    python -m nhl.data.score_state --pbp-dir /path/to/pbp --shots-csv shots_2024.csv \
        --out-dir /path/to/evidence [--game-types 2]
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import os

import numpy as np
import pandas as pd

log = logging.getLogger("nhl.data.score_state")

# Score states, from the perspective of the team being described (own goals - opponent goals,
# capped at +/-3). Order is the canonical display order, most-trailing first.
STATES = ("down3plus", "down2", "down1", "tied", "up1", "up2", "up3plus")
STATE_VALUE = {"down3plus": -3, "down2": -2, "down1": -1, "tied": 0,
               "up1": 1, "up2": 2, "up3plus": 3}

REG_PERIOD_SEC = 1200
OT_PERIOD_SEC = {2: 300, 3: 1200}  # regular season OT is 5:00; playoff OT periods are 20:00


def state_label(diff: int) -> str:
    """Capped score-state label for a goal differential (own - opponent)."""
    if diff <= -3:
        return "down3plus"
    if diff >= 3:
        return "up3plus"
    return {0: "tied", -1: "down1", -2: "down2", 1: "up1", 2: "up2"}[diff]


def clock_seconds(time_in_period) -> int | None:
    """'MM:SS' -> seconds; None for missing/malformed."""
    if not time_in_period or ":" not in str(time_in_period):
        return None
    try:
        m, s = str(time_in_period).split(":")
        return int(m) * 60 + int(s)
    except ValueError:
        return None


def decode_situation(code) -> tuple | None:
    """situationCode -> (away_goalie_in, away_skaters, home_skaters, home_goalie_in) or None.

    Digits are (away goalie in net, away skaters, home skaters, home goalie in net);
    a 0 goalie digit means that net is empty (goalie pulled).
    """
    if code is None or (isinstance(code, float) and math.isnan(code)):
        return None
    s = str(int(code)) if isinstance(code, float) else str(code)
    if len(s) != 4 or not s.isdigit():
        return None
    return int(s[0]), int(s[1]), int(s[2]), int(s[3])


def strength_for(code, is_home: bool) -> tuple | None:
    """(skaters_for, skaters_against, both_goalies_in) for the home/away side, or None."""
    d = decode_situation(code)
    if d is None:
        return None
    ag, a_sk, h_sk, hg = d
    if is_home:
        return h_sk, a_sk, (ag == 1 and hg == 1)
    return a_sk, h_sk, (ag == 1 and hg == 1)


def is_5v5(code, is_home: bool) -> bool:
    """Strict 5v5: skaters 5-against-5 AND both goalies in net (no extra attacker)."""
    st = strength_for(code, is_home)
    return bool(st and st[0] == 5 and st[1] == 5 and st[2])


def _abs_seconds(period: int, period_type: str, t: int, game_type: int) -> int | None:
    """Absolute game-clock second of a play; None for shootout plays (excluded everywhere)."""
    if period_type == "SO":
        return None
    if period_type == "OT":
        return 3 * REG_PERIOD_SEC + t
    return (period - 1) * REG_PERIOD_SEC + t


def _period_end_abs(period: int, period_type: str, game_type: int) -> int | None:
    if period_type == "SO":
        return None
    if period_type == "OT":
        return 3 * REG_PERIOD_SEC + OT_PERIOD_SEC.get(game_type, 300)
    return period * REG_PERIOD_SEC


def game_segments(pbp: dict) -> list:
    """Clock segments of one game: [{period, period_type, start, end, situation_code,
    home_score, away_score}] with the score state in force AFTER the segment-opening play.

    Plays are walked in sortOrder. A segment runs from its play's clock time to the next
    play's (clipped at the period end, so a missing period-end play cannot leak time into
    the next period). Goal plays update the running score from the goal's own details
    (awayScore/homeScore are the score AFTER the goal) before their segment opens, so the
    seconds after a goal belong to the new state. Shootout plays open no segment.
    """
    game_type = int(pbp.get("gameType") or 2)
    plays = sorted(pbp.get("plays") or [], key=lambda p: p.get("sortOrder") or 0)
    home_score = away_score = 0
    opened = []
    for play in plays:
        pd_ = play.get("periodDescriptor") or {}
        ptype = pd_.get("periodType")
        period = int(pd_.get("number") or 0)
        t = clock_seconds(play.get("timeInPeriod"))
        if t is None or period < 1:
            continue
        start = _abs_seconds(period, ptype, t, game_type)
        if start is None:
            continue
        if play.get("typeDescKey") == "goal" and ptype != "SO":
            d = play.get("details") or {}
            if d.get("awayScore") is not None and d.get("homeScore") is not None:
                away_score, home_score = int(d["awayScore"]), int(d["homeScore"])
            else:  # older/flattened shapes without the score fields: increment by owner
                home_id = (pbp.get("homeTeam") or {}).get("id")
                if d.get("eventOwnerTeamId") == home_id:
                    home_score += 1
                elif d.get("eventOwnerTeamId") is not None:
                    away_score += 1
        opened.append({"period": period, "period_type": ptype, "start": start,
                       "situation_code": play.get("situationCode"),
                       "home_score": home_score, "away_score": away_score})
    segments = []
    for i, seg in enumerate(opened):
        nxt = opened[i + 1]["start"] if i + 1 < len(opened) else None
        cap = _period_end_abs(seg["period"], seg["period_type"], game_type)
        end = seg["start"] if nxt is None else min(nxt, cap if cap is not None else nxt)
        end = max(end, seg["start"])
        segments.append({**seg, "end": end, "duration": end - seg["start"]})
    return segments


def durations_from_pbp(pbp: dict) -> pd.DataFrame:
    """One row per (team, score state, strength): game_id, team_id, is_home, state,
    strength ('5v5', '5v4', ... with '_en' suffix when a net is empty), seconds."""
    game_id = int(pbp.get("id") or 0)
    home_id = (pbp.get("homeTeam") or {}).get("id")
    away_id = (pbp.get("awayTeam") or {}).get("id")
    acc: dict = {}
    for seg in game_segments(pbp):
        if seg["duration"] <= 0:
            continue
        for is_home, team_id in ((True, home_id), (False, away_id)):
            if team_id is None:
                continue
            diff = (seg["home_score"] - seg["away_score"]) if is_home else \
                (seg["away_score"] - seg["home_score"])
            st = strength_for(seg["situation_code"], is_home)
            if st is None:
                key_strength = "unknown"
            else:
                key_strength = f"{st[0]}v{st[1]}" + ("" if st[2] else "_en")
            key = (int(team_id), is_home, state_label(diff), key_strength)
            acc[key] = acc.get(key, 0) + seg["duration"]
    rows = [{"game_id": game_id, "team_id": tid, "is_home": home, "state": state,
             "strength": strength, "seconds": sec}
            for (tid, home, state, strength), sec in acc.items()]
    return pd.DataFrame(rows, columns=["game_id", "team_id", "is_home", "state",
                                       "strength", "seconds"])


def shots_by_state(shots: pd.DataFrame) -> pd.DataFrame:
    """Shots-master rows -> per (game, team, score state): sog, attempts, and the 5v5-only
    splits (sog_5v5, attempts_5v5). The score state of an event is the score BEFORE it:
    goals are counted in the state they were scored FROM, then update the running score.
    Shootout events are dropped; a game's running score starts 0-0."""
    cols = ["game_id", "team_id", "is_home", "state", "sog", "attempts",
            "sog_5v5", "attempts_5v5"]
    if shots is None or len(shots) == 0:
        return pd.DataFrame(columns=cols)
    s = shots[shots["period_type"] != "SO"].copy()
    s = s.sort_values(["game_id", "period", "time_sec", "event_id"], kind="mergesort")
    acc: dict = {}
    for gid, sub in s.groupby("game_id", sort=False):
        home_goals = away_goals = 0
        for r in sub.itertuples(index=False):
            is_home = bool(r.is_home)
            diff = (home_goals - away_goals) if is_home else (away_goals - home_goals)
            key = (int(gid), int(r.team_id), is_home, state_label(diff))
            rec = acc.setdefault(key, [0, 0, 0, 0])
            five = is_5v5(r.situation_code, is_home)
            rec[1] += 1
            if five:
                rec[3] += 1
            if r.event in ("sog", "goal"):
                rec[0] += 1
                if five:
                    rec[2] += 1
            if r.event == "goal":
                if is_home:
                    home_goals += 1
                else:
                    away_goals += 1
    rows = [{"game_id": g, "team_id": t, "is_home": h, "state": st, "sog": v[0],
             "attempts": v[1], "sog_5v5": v[2], "attempts_5v5": v[3]}
            for (g, t, h, st), v in acc.items()]
    return pd.DataFrame(rows, columns=cols)


def sog60_tables(durations: pd.DataFrame, shots_state: pd.DataFrame) -> tuple:
    """The H2 join: (per-(game,team,state) joined frame, league table, team table).

    SOG/60 uses the 5v5 slice on both sides: 5v5 seconds from `durations`
    (strength == '5v5') and sog_5v5 from `shots_by_state`.
    """
    d5 = durations[durations["strength"] == "5v5"].groupby(
        ["game_id", "team_id", "is_home", "state"], as_index=False)["seconds"].sum()
    joined = d5.merge(shots_state[["game_id", "team_id", "is_home", "state", "sog_5v5",
                                   "attempts_5v5"]],
                      on=["game_id", "team_id", "is_home", "state"], how="left")
    joined[["sog_5v5", "attempts_5v5"]] = joined[["sog_5v5", "attempts_5v5"]].fillna(0)

    def _table(group_cols):
        g = joined.groupby(group_cols, as_index=False).agg(
            seconds_5v5=("seconds", "sum"), sog_5v5=("sog_5v5", "sum"),
            attempts_5v5=("attempts_5v5", "sum"), team_games=("game_id", "nunique"))
        g["minutes_5v5"] = g["seconds_5v5"] / 60.0
        g["sog60_5v5"] = np.where(g["seconds_5v5"] > 0,
                                  g["sog_5v5"] * 3600.0 / g["seconds_5v5"], np.nan)
        g["attempts60_5v5"] = np.where(g["seconds_5v5"] > 0,
                                       g["attempts_5v5"] * 3600.0 / g["seconds_5v5"], np.nan)
        return g

    league = _table(["state"])
    league["state_value"] = league["state"].map(STATE_VALUE)
    league = league.sort_values("state_value").drop(columns="state_value")
    team = _table(["team_id", "state"])
    return joined, league, team


def _poisson_slope_irls(values: np.ndarray, counts: np.ndarray, exposure_sec: np.ndarray,
                        max_iter: int = 100) -> tuple:
    """Poisson GLM log(rate) = a + b*value with log-exposure offset, fit by IRLS.
    Returns (slope b, se(b)). Cells with zero exposure are dropped by the caller."""
    x = np.column_stack([np.ones(len(values)), values.astype(float)])
    off = np.log(exposure_sec.astype(float))
    beta = np.array([math.log(max(counts.sum(), 1) / (exposure_sec.sum() / 3600.0)), 0.0])
    for _ in range(max_iter):
        mu = np.exp(off + x @ beta)
        w = mu
        z = x @ beta + (counts - mu) / np.maximum(mu, 1e-9)
        xtwx = x.T @ (x * w[:, None])
        try:
            new = np.linalg.solve(xtwx, x.T @ (w * z))
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(new - beta)) < 1e-10:
            beta = new
            break
        beta = new
    mu = np.exp(off + x @ beta)
    xtwx = x.T @ (x * mu[:, None])
    try:
        cov = np.linalg.inv(xtwx)
        se = float(math.sqrt(max(cov[1, 1], 0.0)))
    except np.linalg.LinAlgError:
        se = float("nan")
    return float(beta[1]), se


def team_score_effect_slopes(joined: pd.DataFrame) -> tuple:
    """Per-team 5v5 score-effect slope: Poisson slope of SOG rate on capped goal
    differential (per team-game-state cell, exposure = 5v5 seconds in that state).
    A NEGATIVE slope is the classic score effect (a team shoots less per 60 as it leads).

    Returns (team_frame, league_slope, league_se). team_frame carries the raw slope, its
    SE, the empirical-Bayes shrunk slope (toward the league slope, prior sd = the
    cross-team slope sd net of sampling variance), and the team's 5v5 exposure.
    """
    cells = joined.copy()
    cells["value"] = cells["state"].map(STATE_VALUE)
    cells = cells[cells["seconds"] > 0]
    league_slope, league_se = _poisson_slope_irls(
        cells["value"].to_numpy(), cells["sog_5v5"].to_numpy(), cells["seconds"].to_numpy())
    rows = []
    for tid, sub in cells.groupby("team_id"):
        if sub["value"].nunique() < 3 or sub["seconds"].sum() < 600:
            continue
        b, se = _poisson_slope_irls(sub["value"].to_numpy(), sub["sog_5v5"].to_numpy(),
                                    sub["seconds"].to_numpy())
        rows.append({"team_id": int(tid), "slope_raw": b, "slope_se": se,
                     "seconds_5v5": int(sub["seconds"].sum()),
                     "team_games": int(sub["game_id"].nunique())})
    team = pd.DataFrame(rows)
    if len(team):
        var_obs = float(team["slope_raw"].var(ddof=1)) if len(team) > 1 else 0.0
        var_samp = float((team["slope_se"] ** 2).mean())
        tau2 = max(var_obs - var_samp, 1e-6)
        w = tau2 / (tau2 + team["slope_se"] ** 2)
        team["slope_shrunk"] = w * team["slope_raw"] + (1 - w) * league_slope
    return team, league_slope, league_se


def screen_features(league: pd.DataFrame, team_table: pd.DataFrame,
                    slopes: pd.DataFrame) -> pd.DataFrame:
    """The form the Nov 24 SOG discovery screen consumes: one row per team with its 5v5
    SOG/60 in the tied / down-1 / up-1 states, its (shrunk) score-effect slope, and the
    league curve attached as reference columns. Pregame use (per Argon's NHL note) is
    slope x win_prob inside the workload regression; this table is the measured input,
    not a prediction."""
    piv = team_table.pivot_table(index="team_id", columns="state", values="sog60_5v5")
    out = pd.DataFrame({"team_id": piv.index})
    for st, col in (("tied", "sog60_tied_5v5"), ("down1", "sog60_down1_5v5"),
                    ("up1", "sog60_up1_5v5"), ("down2", "sog60_down2_5v5"),
                    ("up2", "sog60_up2_5v5")):
        out[col] = piv[st].reindex(out["team_id"]).to_numpy() if st in piv else np.nan
    out = out.merge(slopes[["team_id", "slope_raw", "slope_shrunk", "slope_se",
                            "seconds_5v5"]], on="team_id", how="left")
    lg = league.set_index("state")["sog60_5v5"]
    for st in STATES:
        out[f"league_sog60_{st}_5v5"] = float(lg.get(st, np.nan))
    return out


# -- H3: power-play opportunities by discipline matchup -------------------------

def penalty_events_from_pbp(pbp: dict) -> list:
    """Penalty rows of one game: {game_id, committed_team_id, drawn_team_id, duration_min,
    type_code}. Team of a player comes from rosterSpots; the committed team falls back to
    the play's eventOwnerTeamId, the drawn team to the opponent when unresolved."""
    game_id = int(pbp.get("id") or 0)
    home_id = (pbp.get("homeTeam") or {}).get("id")
    away_id = (pbp.get("awayTeam") or {}).get("id")
    player_team = {}
    for r in pbp.get("rosterSpots") or []:
        if r.get("playerId") is not None and r.get("teamId") is not None:
            player_team[int(r["playerId"])] = int(r["teamId"])
    out = []
    for play in pbp.get("plays") or []:
        if play.get("typeDescKey") != "penalty":
            continue
        d = play.get("details") or {}
        committed = player_team.get(d.get("committedByPlayerId")) or d.get("eventOwnerTeamId")
        drawn = player_team.get(d.get("drawnByPlayerId"))
        if drawn is None and committed in (home_id, away_id):
            drawn = away_id if committed == home_id else home_id
        out.append({"game_id": game_id, "committed_team_id": committed,
                    "drawn_team_id": drawn, "duration_min": d.get("duration"),
                    "type_code": d.get("typeCode")})
    return out


def build_matchup_frame(penalties: pd.DataFrame, game_teams: pd.DataFrame) -> pd.DataFrame:
    """Directed team-game observations for H3. `game_teams` = one row per (game_id,
    team_id, opp_team_id). Response: PP opportunities = penalties committed by the
    opponent and drawn by this team in this game. Predictors are LEAVE-ONE-GAME-OUT
    season rates (per game): own drawn rate, opponent taken rate, and their product."""
    pen = penalties.dropna(subset=["committed_team_id", "drawn_team_id"]).copy()
    pen["committed_team_id"] = pen["committed_team_id"].astype(int)
    pen["drawn_team_id"] = pen["drawn_team_id"].astype(int)
    counts = pen.groupby(["game_id", "drawn_team_id", "committed_team_id"]).size()
    game_committed = pen.groupby(["game_id", "committed_team_id"]).size().to_dict()
    rows = []
    # season totals per team for the LOO rates
    drawn_tot = pen.groupby("drawn_team_id").size()
    taken_tot = pen.groupby("committed_team_id").size()
    games_played = game_teams.groupby("team_id")["game_id"].nunique()
    for r in game_teams.itertuples(index=False):
        gid, tid, oid = int(r.game_id), int(r.team_id), int(r.opp_team_id)
        ppo = int(counts.get((gid, tid, oid), 0))
        g_t = max(int(games_played.get(tid, 0)) - 1, 1)
        g_o = max(int(games_played.get(oid, 0)) - 1, 1)
        drawn_rate = (float(drawn_tot.get(tid, 0)) - ppo) / g_t
        # penalties the opponent took in this game that this team did NOT draw belong to
        # the opponent's taken total but not to this row's response; LOO taken rate nets
        # out ALL of the opponent's penalties in this game.
        opp_taken_game = int(game_committed.get((gid, oid), 0))
        taken_rate = (float(taken_tot.get(oid, 0)) - opp_taken_game) / g_o
        rows.append({"game_id": gid, "team_id": tid, "opp_team_id": oid, "ppo": ppo,
                     "drawn_rate_loo": drawn_rate, "taken_rate_loo": taken_rate,
                     "interaction": drawn_rate * taken_rate})
    return pd.DataFrame(rows)


def _ols(X: np.ndarray, y: np.ndarray) -> tuple:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return beta, r2, ss_res


def penalty_matchup_test(frame: pd.DataFrame) -> dict:
    """H3 verdict: nested F-test of [drawn + taken] vs [drawn + taken + drawn*taken].

    PASS (promotes) iff F-test p < 0.01 AND R^2 lift >= 0.015; otherwise H3 DIES.
    Both the absolute lift and the relative lift are reported (the bar is read as the
    stricter absolute 1.5 points of R^2)."""
    from scipy import stats

    f = frame.dropna(subset=["ppo", "drawn_rate_loo", "taken_rate_loo", "interaction"])
    y = f["ppo"].to_numpy(float)
    ones = np.ones(len(f))
    X_add = np.column_stack([ones, f["drawn_rate_loo"], f["taken_rate_loo"]])
    X_full = np.column_stack([X_add, f["interaction"]])
    _, r2_add, ss_add = _ols(X_add, y)
    beta_full, r2_full, ss_full = _ols(X_full, y)
    df1, df2 = 1, len(f) - X_full.shape[1]
    f_stat = ((ss_add - ss_full) / df1) / (ss_full / df2) if ss_full > 0 else float("nan")
    p_val = float(stats.f.sf(f_stat, df1, df2)) if df2 > 0 else float("nan")
    lift_abs = r2_full - r2_add
    lift_rel = lift_abs / r2_add if r2_add > 0 else float("nan")
    passed = bool(p_val < 0.01 and lift_abs >= 0.015)
    return {"n_directed_team_games": int(len(f)), "r2_additive": r2_add,
            "r2_full": r2_full, "r2_lift_abs": lift_abs, "r2_lift_rel": lift_rel,
            "f_stat": float(f_stat), "p_value": p_val,
            "interaction_coef": float(beta_full[-1]),
            "verdict": "PASS" if passed else "DIED",
            "bars": "F-test p < 0.01 AND R^2 lift >= 0.015 (absolute)"}


# -- run ------------------------------------------------------------------------

def load_pbp_dir(pbp_dir: str) -> list:
    out = []
    for name in sorted(os.listdir(pbp_dir)):
        if not name.endswith(".json.gz"):
            continue
        with gzip.open(os.path.join(pbp_dir, name), "rt", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def run(pbp_games: list, shots: pd.DataFrame, game_types=(2,)) -> dict:
    """Full H2+H3 computation over parsed games. Returns a dict of frames/results."""
    games = [g for g in pbp_games if int(g.get("gameType") or 2) in game_types]
    durations = pd.concat([durations_from_pbp(g) for g in games], ignore_index=True) \
        if games else pd.DataFrame(columns=["game_id", "team_id", "is_home", "state",
                                            "strength", "seconds"])
    game_ids = {int(g.get("id") or 0) for g in games}
    shots_state = shots_by_state(shots[shots["game_id"].isin(game_ids)]) \
        if shots is not None and len(shots) else shots_by_state(pd.DataFrame())
    joined, league, team_table = sog60_tables(durations, shots_state)
    slopes, league_slope, league_se = team_score_effect_slopes(joined)
    features = screen_features(league, team_table, slopes)

    pen = pd.DataFrame([r for g in games for r in penalty_events_from_pbp(g)])
    gt_rows = []
    for g in games:
        h, a = (g.get("homeTeam") or {}).get("id"), (g.get("awayTeam") or {}).get("id")
        if h and a:
            gt_rows.append({"game_id": int(g["id"]), "team_id": int(h), "opp_team_id": int(a)})
            gt_rows.append({"game_id": int(g["id"]), "team_id": int(a), "opp_team_id": int(h)})
    matchup = build_matchup_frame(pen, pd.DataFrame(gt_rows)) if len(pen) else pd.DataFrame()
    h3 = penalty_matchup_test(matchup) if len(matchup) else {"verdict": "DIED",
                                                             "reason": "no penalty data"}
    return {"durations": durations, "shots_state": shots_state, "joined": joined,
            "league": league, "team_table": team_table, "slopes": slopes,
            "league_slope": league_slope, "league_se": league_se,
            "features": features, "penalties": pen, "matchup": matchup, "h3": h3,
            "n_games": len(games)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NHL H2 score-state join + H3 matchup test")
    ap.add_argument("--pbp-dir", required=True, help="dir of raw PBP {game_id}.json.gz files")
    ap.add_argument("--shots-csv", required=True, help="shots master CSV (shots_{season}.csv)")
    ap.add_argument("--out-dir", required=True, help="evidence output dir (scratch, not production)")
    ap.add_argument("--game-types", default="2", help="comma list of NHL game types (2=regular)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    games = load_pbp_dir(a.pbp_dir)
    shots = pd.read_csv(a.shots_csv)
    res = run(games, shots, game_types=tuple(int(x) for x in a.game_types.split(",")))
    os.makedirs(a.out_dir, exist_ok=True)
    for name in ("durations", "shots_state", "joined", "league", "team_table", "slopes",
                 "features", "penalties", "matchup"):
        res[name].to_csv(os.path.join(a.out_dir, f"{name}.csv"), index=False)
    summary = {"n_games": res["n_games"], "league_slope": res["league_slope"],
               "league_slope_se": res["league_se"], "h3": res["h3"]}
    with open(os.path.join(a.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=float)
    log.info("games=%d league slope=%.4f (se %.4f) H3=%s", res["n_games"],
             res["league_slope"], res["league_se"], res["h3"].get("verdict"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
