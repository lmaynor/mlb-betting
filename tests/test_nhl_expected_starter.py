"""nhl.props.xgfeat.expected_starter_features: the opposing goalie as knowable BEFORE the puck drops."""
import numpy as np
import pandas as pd
import pytest

from nhl.props import xgfeat as X


def frames(starts, rest=None, extra_future=False):
    """starts = [(date, goalie_id)] for team 1 in order; one game per entry. Goalie `after` stats encode the goalie id so a
    lookup is identifiable (sv_l60_post = 0.90 + id / 1000, stamped on every appearance)."""
    gt_rows, tt_rows = [], []
    for i, (d, g) in enumerate(starts):
        gt_rows.append({"game_id": 100 + i, "team_id": 1, "player_id": g, "date": d, "is_start": 1,
                        "sv_l60_post": 0.90 + g / 1000.0 + (0.05 if (extra_future and i >= 12) else 0.0),
                        "gsax100_l60_post": float(g), "xga_shot_l30_post": 0.1 + g / 100.0})
        tt_rows.append({"game_id": 100 + i, "team_id": 1, "date": d, "rest_days": (rest or {}).get(i, 2)})
    return pd.DataFrame(gt_rows), pd.DataFrame(tt_rows)


def days(n, start="2026-01-01"):
    d0 = pd.Timestamp(start)
    return [(d0 + pd.Timedelta(days=2 * i)).strftime("%Y-%m-%d") for i in range(n)]


def test_expected_starter_is_the_most_frequent_of_the_last_ten_starts():
    d = days(14)
    seq = [7, 7, 7, 8, 7, 7, 8, 7, 7, 7, 7, 8, 7, 7]
    gt, tt = frames(list(zip(d, seq)))
    e = X.expected_starter_features(gt, tt).set_index("game_id")
    assert e.loc[113, "opp_g_gsax100_l60"] == 7.0                    # goalie 7 started 7 of the previous 10
    assert e.loc[113, "opp_g_sv_l60"] == pytest.approx(0.907)
    assert 100 not in e.index                                        # no earlier starts: no expectation, no guess
    assert int(e.loc[113, "exp_is_actual"]) == 1 and int(e.loc[111, "exp_is_actual"]) == 0   # 8 started game 111


def test_back_to_back_after_the_starter_played_yesterday_expects_the_backup():
    d = days(12)
    seq = [7] * 6 + [8] + [7] * 5
    gt, tt = frames(list(zip(d, seq)))
    tt.loc[tt["game_id"] == 111, "rest_days"] = 0                     # zero days' rest, and 7 started game 110
    e = X.expected_starter_features(gt, tt).set_index("game_id")
    assert e.loc[111, "opp_g_gsax100_l60"] == 8.0                     # the backup
    assert e.loc[110, "opp_g_gsax100_l60"] == 7.0                     # a rested game: still the starter
    tt.loc[tt["game_id"] == 111, "rest_days"] = 1
    assert X.expected_starter_features(gt, tt).set_index("game_id").loc[111, "opp_g_gsax100_l60"] == 8.0


def test_goalie_form_is_the_latest_value_strictly_before_the_game_date_and_ignores_the_future():
    d = days(14)
    seq = [7] * 14
    clean_gt, tt = frames(list(zip(d, seq)))
    dirty_gt, _ = frames(list(zip(d, seq)), extra_future=True)       # appearances from game 112 on carry different form
    a = X.expected_starter_features(clean_gt, tt).set_index("game_id")
    b = X.expected_starter_features(dirty_gt, tt).set_index("game_id")
    for gid in range(101, 112):                                       # every game BEFORE the first altered appearance
        assert a.loc[gid, "opp_g_sv_l60"] == b.loc[gid, "opp_g_sv_l60"]
    assert b.loc[113, "opp_g_sv_l60"] == pytest.approx(0.957)         # game 113 sees game 112's AFTER-game value ...
    assert b.loc[112, "opp_g_sv_l60"] == pytest.approx(0.907)         # ... but game 112 does not see its own


def test_skater_features_rejects_an_unknown_starter_mode():
    with pytest.raises(ValueError):
        X.skater_features(pd.DataFrame({"game_id": [1], "team_id": [1], "opp_id": [2], "player_id": [3]}),
                          pd.DataFrame({"game_id": [1], "team_id": [1], "xgf_l10": [0.0], "xgf_l30": [0.0], "ppf_l30": [0.0],
                                        "goals_against_pop": [0.0], "goals_for_pop": [0.0]}), pd.DataFrame(), "psychic")
