"""
Regression test for the 2026-08-16 audit's K/OUTS starter-identification fix
(finding A12): _identify_starters must keep one starter PER TEAM per game,
not collapse to a single game-wide idxmax that silently drops whichever
side's pitcher faced fewer batters.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd

from mlb.runners.build_k_features import _identify_starters


def _pa_row(game_pk, pitcher, inning_topbot, at_bat_number, events="strikeout"):
    return {
        "game_pk": game_pk,
        "game_date": "2024-05-01",
        "pitcher": pitcher,
        "inning_topbot": inning_topbot,
        "at_bat_number": at_bat_number,
        "events": events,
        "home_team": "CLE",
        "away_team": "LAA",
    }


def test_both_teams_starters_survive_not_just_higher_bf_side():
    """Home starter (100, Top half) faces fewer batters than the away
    starter (200, Bot half) in a lopsided/bullpen-heavy game -- both must
    still appear, one row each, not just the higher-bf side."""
    rows = (
        [_pa_row(999, 100, "Top", i) for i in range(1, 4)]        # 3 PA, home starter
        + [_pa_row(999, 200, "Bot", i) for i in range(4, 12)]     # 8 PA, away starter
    )
    sc = pd.DataFrame(rows)
    starters = _identify_starters(sc)
    got = dict(zip(starters["pitcher"], starters["bf"]))
    assert got == {100: 3, 200: 8}, (
        f"expected both starters (100 with bf=3, 200 with bf=8), got {got} -- "
        f"the game-wide idxmax regression drops whichever side faced fewer "
        f"batters"
    )


def test_falls_back_gracefully_without_team_columns():
    """If home_team/away_team/inning_topbot are unavailable, degrade to the
    old per-game-only behavior rather than crash."""
    sc = pd.DataFrame([
        {"game_pk": 999, "game_date": "2024-05-01", "pitcher": 100, "at_bat_number": 1, "events": "strikeout"},
        {"game_pk": 999, "game_date": "2024-05-01", "pitcher": 200, "at_bat_number": 2, "events": "strikeout"},
    ])
    starters = _identify_starters(sc)
    assert len(starters) == 1  # can't disambiguate sides -- one row, as before
