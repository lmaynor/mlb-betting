"""
tests/test_daily_card.py -- Tests for strict Official Card selection gate, dynamic 1..5 sizing,
deduplication, and persistence.
"""
from datetime import datetime
import pytest

from ev.daily_card import (
    build_daily_card,
    is_ev_outlier,
    is_official_card_play,
    load_locked_card,
    save_locked_card,
)


def _proj_row(**kw):
    base = {
        "sport": "nba",
        "game_key": "nba:2026-10-28:BOS@NYK",
        "player": "Jayson Tatum",
        "stat": "points",
        "side": "over",
        "line": 26.5,
        "book": "fanduel",
        "american": 112,
        "n_books": 6,
        "market_ev": 0.054,
        "model_edge": 0.032,
        "confluence": True,
        "beezy_score": 74,
        "mins_to_start": 120,
    }
    base.update(kw)
    return base


def _mlb_row(**kw):
    base = {
        "sport": "mlb",
        "system": "K",
        "game_key": "mlb:NYY@BOS",
        "player": "Gerrit Cole",
        "bet_type": "K",
        "side": "over",
        "odds": 115,
        "edge": 0.045,
        "validated": True,
        "beezy_score": 72,
        "mins_to_start": 90,
    }
    base.update(kw)
    return base


def test_official_card_gate_projection_adverse_selection_cap():
    # ev > 0.10 must be rejected as adverse selection trap
    row = _proj_row(market_ev=0.115)
    assert not is_official_card_play(row)

    # ev < 0.03 must be rejected as insufficient edge
    row_low = _proj_row(market_ev=0.025)
    assert not is_official_card_play(row_low)

    # sweet spot 3% - 10% accepted
    row_ok = _proj_row(market_ev=0.054)
    assert is_official_card_play(row_ok)


def test_official_card_gate_projection_n_books():
    # n_books < 4 must be rejected
    row_thin = _proj_row(n_books=3)
    assert not is_official_card_play(row_thin)

    row_thick = _proj_row(n_books=4)
    assert is_official_card_play(row_thick)


def test_official_card_gate_requires_confluence():
    # confluence == False rejected for projection rows
    row_no_conf = _proj_row(confluence=False)
    assert not is_official_card_play(row_no_conf)

    # model_edge < 0.015 rejected
    row_thin_edge = _proj_row(model_edge=0.010)
    assert not is_official_card_play(row_thin_edge)

    # beezy_score < 68 rejected
    row_low_score = _proj_row(beezy_score=65)
    assert not is_official_card_play(row_low_score)


def test_official_card_gate_mlb():
    # Validated MLB system play in sweet spot accepted
    assert is_official_card_play(_mlb_row())

    # Unvalidated or outlier EV rejected
    assert not is_official_card_play(_mlb_row(validated=False))
    assert not is_official_card_play(_mlb_row(edge=0.12))
    assert not is_official_card_play(_mlb_row(edge=0.02))
    assert not is_official_card_play(_mlb_row(beezy_score=60))


def test_ev_outlier_gate():
    # Soft book price with 3+ books and 2.5% - 15% EV without confluence is an outlier
    outlier = _proj_row(confluence=False, market_ev=0.06, n_books=4)
    assert is_ev_outlier(outlier)

    # If it is already an official confluence play, is_ev_outlier is False
    official = _proj_row(confluence=True, market_ev=0.06, n_books=4)
    assert not is_ev_outlier(official)


def test_build_daily_card_never_pads_dynamic_size():
    # Only 2 plays qualify -> returns exactly 2 plays, never 5
    p1 = _proj_row(player="Player One", beezy_score=75)
    p2 = _proj_row(player="Player Two", beezy_score=70)
    weak1 = _proj_row(player="Player Three", confluence=False, beezy_score=50)
    weak2 = _proj_row(player="Player Four", n_books=2, beezy_score=55)

    card = build_daily_card([p1, p2, weak1, weak2])
    assert len(card) == 2
    assert [p["player"] for p in card] == ["Player One", "Player Two"]


def test_build_daily_card_deduplicates_max_one_per_player():
    # Multiple plays for same player -> keeps highest score
    p1_pts = _proj_row(player="Tatum", stat="points", beezy_score=72, market_ev=0.04)
    p1_reb = _proj_row(player="Tatum", stat="rebounds", beezy_score=78, market_ev=0.05)
    p2 = _proj_row(player="Brown", stat="points", beezy_score=70)

    card = build_daily_card([p1_pts, p1_reb, p2])
    assert len(card) == 2
    tatum_pick = next(p for p in card if p["player"] == "Tatum")
    assert tatum_pick["stat"] == "rebounds"
    assert tatum_pick["beezy_score"] == 78


def test_build_daily_card_deduplicates_max_two_per_game():
    # 3 plays for same game -> only keeps top 2
    g = "nba:2026-10-28:BOS@NYK"
    p1 = _proj_row(player="P1", game_key=g, beezy_score=80)
    p2 = _proj_row(player="P2", game_key=g, beezy_score=75)
    p3 = _proj_row(player="P3", game_key=g, beezy_score=70)
    p4 = _proj_row(player="P4", game_key="nba:2026-10-28:LAL@GSW", beezy_score=69)

    card = build_daily_card([p1, p2, p3, p4])
    assert len(card) == 3
    game_bos_nyk = [p for p in card if p["game_key"] == g]
    assert len(game_bos_nyk) == 2
    assert [p["player"] for p in game_bos_nyk] == ["P1", "P2"]


def test_save_and_load_locked_card(tmp_path, monkeypatch):
    import mlb_core.storage as storage
    stored: dict[str, bytes] = {}

    monkeypatch.setattr(storage, "exists", lambda k: k in stored)
    monkeypatch.setattr(storage, "read_bytes", lambda k: stored[k])
    monkeypatch.setattr(storage, "write_bytes", lambda data, k: stored.update({k: data}))

    card = [_proj_row(player="Tatum")]
    key, written = save_locked_card(card, ct_day="2026-10-28", force=False)
    assert written is True
    assert "2026-10-28" in key

    # Second save without force returns False (does not overwrite)
    key2, written2 = save_locked_card([_proj_row(player="Other")], ct_day="2026-10-28", force=False)
    assert written2 is False

    # Force save overwrites
    key3, written3 = save_locked_card([_proj_row(player="New")], ct_day="2026-10-28", force=True)
    assert written3 is True

    loaded = load_locked_card("2026-10-28")
    assert loaded is not None
    assert loaded["date"] == "2026-10-28"
    assert loaded["plays"][0]["player"] == "New"
