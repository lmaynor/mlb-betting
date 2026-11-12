"""Tests for nhl.data.score_state (Lane 6, H2/H3 game-state build-out).

Fixtures are synthetic: a two-period toy game whose clock, goals and penalties are fully
known, so durations, state attribution, SOG/60 and the H3 regression mechanics can be
checked exactly rather than against banked data.
"""
import numpy as np
import pandas as pd
import pytest

from nhl.data.score_state import (build_matchup_frame, clock_seconds, decode_situation,
                                  durations_from_pbp, game_segments, is_5v5,
                                  penalty_events_from_pbp, penalty_matchup_test,
                                  shots_by_state, sog60_tables, state_label,
                                  team_score_effect_slopes)


def _play(sort, period, t, code, kind, details=None, ptype="REG"):
    return {"sortOrder": sort, "periodDescriptor": {"number": period, "periodType": ptype},
            "timeInPeriod": t, "situationCode": code, "typeDescKey": kind,
            "details": details or {}}


def _toy_pbp():
    """Away (id 1) at home (id 2). P1: tied 5v5 0:00-10:00, away scores at 10:00,
    then up-1 5v5 until 20:00. P2: away takes a penalty at 0:00 (home 5v4) until 2:00,
    then tied... no: still up 1, 5v5, to 20:00. Only 2 periods are scripted; durations
    are asserted per period segment, not as a full 60-minute game."""
    plays = [
        _play(1, 1, "00:00", "1551", "period-start"),
        _play(2, 1, "10:00", "1551", "goal",
              {"eventOwnerTeamId": 1, "awayScore": 1, "homeScore": 0}),
        _play(3, 1, "20:00", "1551", "period-end"),
        _play(4, 2, "00:00", "1451", "penalty",
              {"eventOwnerTeamId": 1, "committedByPlayerId": 11, "drawnByPlayerId": 21,
               "duration": 2, "typeCode": "MIN"}),
        _play(5, 2, "02:00", "1551", "faceoff"),
        _play(6, 2, "20:00", "1551", "period-end"),
    ]
    return {"id": 2024020001, "gameType": 2, "season": 20242025,
            "awayTeam": {"id": 1}, "homeTeam": {"id": 2},
            "rosterSpots": [{"playerId": 11, "teamId": 1}, {"playerId": 21, "teamId": 2}],
            "plays": plays}


def test_clock_and_state_label():
    assert clock_seconds("07:05") == 425
    assert clock_seconds(None) is None and clock_seconds("x") is None
    assert [state_label(d) for d in (-9, -3, -2, -1, 0, 1, 2, 3, 8)] == \
        ["down3plus", "down3plus", "down2", "down1", "tied", "up1", "up2",
         "up3plus", "up3plus"]


def test_situation_decode_and_5v5():
    assert decode_situation("1551") == (1, 5, 5, 1)
    assert decode_situation(1541) == (1, 5, 4, 1)
    assert decode_situation("bad") is None
    assert is_5v5("1551", True) and is_5v5("1551", False)
    assert not is_5v5("1541", True)          # home is 4 skaters
    assert not is_5v5("0651", False)         # away goalie pulled: not strict 5v5
    assert not is_5v5("1560", True)          # 6 home skaters, home net empty


def test_segments_goal_changes_state_at_its_timestamp():
    segs = game_segments(_toy_pbp())
    p1 = [s for s in segs if s["period"] == 1]
    assert [(s["duration"], s["away_score"]) for s in p1] == [(600, 0), (600, 1), (0, 1)]


def test_durations_by_state_and_strength():
    d = durations_from_pbp(_toy_pbp())
    away = d[d["team_id"] == 1].set_index(["state", "strength"])["seconds"]
    home = d[d["team_id"] == 2].set_index(["state", "strength"])["seconds"]
    assert away[("tied", "5v5")] == 600
    assert away[("up1", "5v5")] == 600 + 1080       # rest of P1 + P2 after the penalty
    assert away[("up1", "4v5")] == 120              # away short-handed in P2
    assert home[("down1", "5v4")] == 120            # home on the power play is 5v4 FOR home
    assert home[("tied", "5v5")] == 600


def _toy_shots():
    rows = [
        # P1 tied, 5v5: away sog at 5:00; the away goal at 10:00 counts as SOG from tied
        dict(game_id=2024020001, event_id=1, period=1, period_type="REG", time_sec=300,
             event="sog", team_id=1, is_home=False, situation_code=1551),
        dict(game_id=2024020001, event_id=2, period=1, period_type="REG", time_sec=600,
             event="goal", team_id=1, is_home=False, situation_code=1551),
        # P1 up 1 for away: home sog 5v5 at 15:00
        dict(game_id=2024020001, event_id=3, period=1, period_type="REG", time_sec=900,
             event="sog", team_id=2, is_home=True, situation_code=1551),
        # P2: home PP sog (5v4) at 1:00 -- must NOT enter the 5v5 split
        dict(game_id=2024020001, event_id=4, period=2, period_type="REG", time_sec=60,
             event="sog", team_id=2, is_home=True, situation_code=1451),
        # shootout attempt: dropped everywhere
        dict(game_id=2024020001, event_id=5, period=5, period_type="SO", time_sec=0,
             event="sog", team_id=2, is_home=True, situation_code=1011),
    ]
    return pd.DataFrame(rows)


def test_shots_by_state_pre_goal_attribution():
    s = shots_by_state(_toy_shots()).set_index(["team_id", "state"])
    assert s.loc[(1, "tied"), "sog"] == 2 and s.loc[(1, "tied"), "sog_5v5"] == 2
    assert s.loc[(2, "down1"), "sog"] == 2 and s.loc[(2, "down1"), "sog_5v5"] == 1
    assert s.loc[(2, "down1"), "attempts"] == 2


def test_sog60_join_arithmetic():
    d = durations_from_pbp(_toy_pbp())
    joined, league, team = sog60_tables(d, shots_by_state(_toy_shots()))
    lg = league.set_index("state")
    # tied: 1200 team-seconds of 5v5 (600 per side), 2 SOG -> 6.0 SOG/60
    assert lg.loc["tied", "seconds_5v5"] == 1200
    assert lg.loc["tied", "sog60_5v5"] == pytest.approx(6.0)
    # down1 (home perspective): 1680 s 5v5, 1 SOG -> 3600/1680
    assert lg.loc["down1", "sog60_5v5"] == pytest.approx(3600.0 / 1680.0)


def test_slope_sign_on_synthetic_score_effect():
    # 20 team-games; the trailing state shoots at double the leading rate per second.
    rows = []
    for g in range(20):
        for state, sec, sog in (("down1", 900, 9), ("tied", 900, 6), ("up1", 900, 3)):
            rows.append({"game_id": g, "team_id": 7, "is_home": True, "state": state,
                         "seconds": sec, "sog_5v5": sog, "attempts_5v5": sog})
    joined = pd.DataFrame(rows)
    slopes, league_slope, _ = team_score_effect_slopes(joined)
    assert league_slope < 0
    assert slopes.iloc[0]["slope_raw"] < 0


def test_penalty_events_teams_from_roster():
    pen = penalty_events_from_pbp(_toy_pbp())
    assert len(pen) == 1
    assert pen[0]["committed_team_id"] == 1 and pen[0]["drawn_team_id"] == 2


def _h3_frame(interaction_strength=0.0, rounds=8, seed=3):
    rng = np.random.default_rng(seed)
    teams = list(range(1, 9))
    drawn_true = {t: rng.uniform(2.0, 4.0) for t in teams}
    taken_true = {t: rng.uniform(2.0, 4.0) for t in teams}
    pen_rows, gt_rows = [], []
    gid = 0
    for _ in range(rounds):
        for i, a in enumerate(teams):
            for b in teams[i + 1:]:
                gid += 1
                for drawn_t, taken_t in ((a, b), (b, a)):
                    lam = 0.4 * drawn_true[drawn_t] + 0.4 * taken_true[taken_t] - 1.5
                    if interaction_strength:
                        lam += interaction_strength * drawn_true[drawn_t] * taken_true[taken_t]
                    n = int(rng.poisson(max(lam, 0.2)))
                    pen_rows += [{"game_id": gid, "drawn_team_id": drawn_t,
                                  "committed_team_id": taken_t}] * n
                gt_rows += [{"game_id": gid, "team_id": a, "opp_team_id": b},
                            {"game_id": gid, "team_id": b, "opp_team_id": a}]
    frame = build_matchup_frame(pd.DataFrame(pen_rows), pd.DataFrame(gt_rows))
    return frame


def test_h3_no_interaction_dies():
    res = penalty_matchup_test(_h3_frame(interaction_strength=0.0))
    assert res["verdict"] == "DIED"
    assert res["r2_lift_abs"] < 0.015


def test_h3_strong_interaction_structure_detected():
    frame = _h3_frame(interaction_strength=0.35)
    res = penalty_matchup_test(frame)
    assert res["interaction_coef"] > 0
    assert res["r2_full"] >= res["r2_additive"]
