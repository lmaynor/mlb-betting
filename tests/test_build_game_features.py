"""
Regression test for the 2026-08-16 audit's GAME home/away pitcher-inversion
fix (finding A4): build_starter_features()/build_bullpen_features() must
attribute the Top-half pitcher to the HOME team, not away (the team not
currently batting is the one pitching -- Top half = away team batting =
home pitcher on the mound). Verified against build_nrfi_features.py's
independently-confirmed convention for the identical inning_topbot field.

See docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md.
"""
import pandas as pd

from mlb.runners.build_game_features import build_starter_features, build_bullpen_features


def _sc_row(game_pk, pitcher, inning_topbot, at_bat_number, game_date="2024-05-01",
            inning=1, events="strikeout", **extra):
    row = {
        "game_pk": game_pk,
        "game_date": game_date,
        "pitcher": pitcher,
        "inning": inning,
        "inning_topbot": inning_topbot,
        "at_bat_number": at_bat_number,
        "events": events,
        "home_team": "CLE",
        "away_team": "LAA",
    }
    row.update(extra)
    return row


def test_starter_features_top_half_pitcher_is_home():
    """Top-half pitcher (100) must land in home_df; Bot-half pitcher (200) in
    away_df -- the exact inversion this fix corrects."""
    sc = pd.DataFrame([
        _sc_row(999, 100, "Top", 1),
        _sc_row(999, 200, "Bot", 2),
    ])
    home_df, away_df = build_starter_features(sc, None, None, lookback_days=90, run_date="2024-05-02")
    assert list(home_df["pitcher"]) == [100], (
        f"expected the Top-half pitcher (100) in home_df, got {list(home_df['pitcher'])}"
    )
    assert list(away_df["pitcher"]) == [200], (
        f"expected the Bot-half pitcher (200) in away_df, got {list(away_df['pitcher'])}"
    )


def test_bullpen_features_top_half_reliever_credited_to_home_team():
    """Direction-sensitive: a prior game's Top-half reliever throws only
    strikeouts (bp_k_pct=1.0), the Bot-half reliever throws none (0.0). The
    CURRENT game's rolled bullpen_k_pct_L14 (shift(1)-ed from that one prior
    game) must show 1.0 for the HOME team (CLE) and 0.0 for AWAY (LAA) -- if
    the Top/Bot <-> home/away mapping were still inverted, these would be
    swapped, so this test fails loudly on a regression rather than merely
    checking both teams appear (which an inverted mapping would also pass)."""
    sc = pd.DataFrame([
        # Prior game (998): starters (excluded from bullpen via is_starter),
        # then one reliever per side with opposite strikeout outcomes.
        _sc_row(998, 100, "Top", 1, game_date="2024-04-01"),
        _sc_row(998, 200, "Bot", 2, game_date="2024-04-01"),
        _sc_row(998, 110, "Top", 30, game_date="2024-04-01", inning=7, events="strikeout"),
        _sc_row(998, 210, "Bot", 31, game_date="2024-04-01", inning=7, events="field_out"),
        # Current game (999): just needs starters + one reliever per side to
        # exist so both teams get a row; its own-game stats aren't checked.
        _sc_row(999, 101, "Top", 1, game_date="2024-05-01"),
        _sc_row(999, 201, "Bot", 2, game_date="2024-05-01"),
        _sc_row(999, 111, "Top", 30, game_date="2024-05-01", inning=7, events="field_out"),
        _sc_row(999, 211, "Bot", 31, game_date="2024-05-01", inning=7, events="field_out"),
    ])
    bp = build_bullpen_features(sc, lookback_days=90, run_date="2024-05-02")
    row_999 = bp[bp["game_pk"] == 999].set_index("team")
    assert set(row_999.index) == {"CLE", "LAA"}
    assert row_999.loc["CLE", "bullpen_k_pct_L14"] == 1.0, (
        "home team (CLE, Top-half reliever) should carry the prior game's "
        "100% strikeout rate forward -- got the away team's rate instead, "
        "the Top/Bot home/away mapping is inverted again"
    )
    assert row_999.loc["LAA", "bullpen_k_pct_L14"] == 0.0, (
        "away team (LAA, Bot-half reliever) should carry the prior game's "
        "0% strikeout rate forward -- got the home team's rate instead, "
        "the Top/Bot home/away mapping is inverted again"
    )
