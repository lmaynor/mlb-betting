"""
Tests for mlb_core.notify.discord._format_bet_headline, specifically the
2+/3+ threshold sub-market formatting added 2026-08-19.

Regression coverage for two confirmed bugs (found via code investigation,
not guessed at): the BATTER_TB/BATTER_HITS branches used a 2-way OVER/UNDER
ternary with no third case, so a "2PLUS" side silently rendered as "Under"
(actively wrong, not just ugly); the K/OUTS branch had a raw-string fallback
that would have printed "2PLUS" unformatted.
"""
from mlb_core.notify.discord import _format_bet_headline, _side_word


def test_side_word_handles_over_under_and_threshold():
    assert _side_word("OVER") == "Over"
    assert _side_word("UNDER") == "Under"
    assert _side_word("2PLUS") == "2+"
    assert _side_word("3PLUS") == "3+"
    assert _side_word("SOMETHING_ELSE") == "SOMETHING_ELSE"  # unknown -> raw, not guessed


def test_k_threshold_headline():
    b = {"player": "Gerrit Cole", "team": "NYY", "side": "2PLUS", "line": 2.0,
         "bet_type": "K_2PLUS_2.0"}
    headline = _format_bet_headline(b, "K")
    assert headline == "Gerrit Cole (NYY) - 2+ Strikeouts"
    assert "2.0" not in headline  # no redundant "2+ 2.0 Strikeouts"


def test_outs_threshold_headline():
    b = {"player": "Gerrit Cole", "team": "NYY", "side": "3PLUS", "line": 3.0,
         "bet_type": "OUTS_3PLUS_3.0"}
    headline = _format_bet_headline(b, "OUTS")
    assert headline == "Gerrit Cole (NYY) - 3+ Outs Recorded"


def test_batter_tb_threshold_headline_not_mislabeled_under():
    """The actual bug: this used to render 'Under Total Bases' for a 2+ bet."""
    b = {"player": "Aaron Judge", "side": "2PLUS", "line": 2.0,
         "bet_type": "BATTER_TB_2PLUS_2.0"}
    headline = _format_bet_headline(b, "BATTER_TB")
    assert headline == "Aaron Judge - 2+ Total Bases"
    assert "Under" not in headline


def test_batter_hits_threshold_headline_not_mislabeled_under():
    b = {"player": "Aaron Judge", "side": "2PLUS", "line": 2.0,
         "bet_type": "BATTER_HITS_2PLUS_2.0"}
    headline = _format_bet_headline(b, "BATTER_HITS")
    assert headline == "Aaron Judge - 2+ Hits"
    assert "Under" not in headline


def test_existing_over_under_headlines_unaffected():
    """Confirm the fix didn't change the pre-existing, already-correct
    OVER/UNDER formatting for the main line."""
    assert _format_bet_headline(
        {"player": "Gerrit Cole", "team": "NYY", "side": "OVER", "line": 7.5,
         "bet_type": "K_OVER_7.5"}, "K",
    ) == "Gerrit Cole (NYY) - Over 7.5 Strikeouts"
    assert _format_bet_headline(
        {"player": "Aaron Judge", "side": "UNDER", "line": 1.5,
         "bet_type": "BATTER_TB_UNDER_1.5"}, "BATTER_TB",
    ) == "Aaron Judge - Under 1.5 Total Bases"
