"""Unit tests for ev.kalshi_wide_quoter_rules (RUL-20261125 Half 2 / MAK-01)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ev.kalshi_wide_quoter_rules import (
    ACTION_HALT,
    ACTION_QUOTE_BOTH,
    ACTION_QUOTE_NO,
    ACTION_QUOTE_YES,
    ACTION_SKIP,
    ActiveQuoteView,
    BookState,
    MODE_IMPROVE_1C,
    MODE_JOIN_TOP,
    QuoterState,
    SERIES_FEE_TYPES,
    WideSpreadConfig,
    evaluate_cancel,
    evaluate_quote,
    maker_fee_cents,
)


TIP = datetime(2026, 11, 22, 0, 0, 0, tzinfo=timezone.utc)


def test_config_is_frozen_with_spec_defaults():
    cfg = WideSpreadConfig()
    assert cfg.spread_min_c == 5
    assert cfg.spread_max_c == 25
    assert cfg.min_net_edge_c == 1.5
    assert cfg.tail_min_c == 10
    assert cfg.tail_max_c == 90
    assert cfg.quote_size == 10
    assert cfg.window_open_min == 240
    assert cfg.window_close_min == 45
    assert cfg.cap_market_net_contracts == 20
    assert cfg.cap_player_risk_usd == 25.0
    assert cfg.cap_game_risk_usd == 50.0
    assert cfg.cap_daily_open_risk_usd == 150.0
    assert cfg.cap_daily_stop_loss_usd == 50.0
    assert SERIES_FEE_TYPES == {
        "KXNBAREB": "quadratic",
        "KXNBA3PT": "quadratic",
        "KXNBAPTS": "quadratic",
    }
    with pytest.raises(Exception):
        cfg.quote_size = 20  # type: ignore[misc]


def test_maker_fee_cents_schedule():
    assert maker_fee_cents(50.0, fee_type="quadratic") == 0.0
    assert maker_fee_cents(25.0, fee_type="quadratic") == 0.0
    # 0.0175 * 1.0 * 50 * 50 / 100 = 0.4375c -> ceil to centicent = 0.44c
    assert maker_fee_cents(50.0, fee_type="quadratic_with_maker_fees") == pytest.approx(0.44)
    assert maker_fee_cents(50.0, fee_type="quadratic_with_combo_maker_fees") == pytest.approx(0.44)
    assert maker_fee_cents(50.0, fee_type="quadratic_with_combo_maker_fees", multiplier=2.0) == pytest.approx(0.88)
    with pytest.raises(ValueError):
        maker_fee_cents(50.0, fee_type="unknown_type")


def test_window_and_spread_gates():
    cfg = WideSpreadConfig()
    state = QuoterState()
    book = BookState(best_yes_bid_c=40, best_no_bid_c=45, yes_bid_size=20, no_bid_size=20)  # spread=15c

    # Outside window (> T-4h and <= T-45m)
    assert evaluate_quote(book, 0.50, state, TIP - timedelta(minutes=241), TIP, cfg).reason_code == "outside_window"
    assert evaluate_quote(book, 0.50, state, TIP - timedelta(minutes=45), TIP, cfg).reason_code == "outside_window"

    # Inside window boundaries (T-240m and T-46m)
    assert evaluate_quote(book, 0.50, state, TIP - timedelta(minutes=240), TIP, cfg).action == ACTION_QUOTE_BOTH
    assert evaluate_quote(book, 0.50, state, TIP - timedelta(minutes=46), TIP, cfg).action == ACTION_QUOTE_BOTH

    now = TIP - timedelta(minutes=120)
    # One-sided / crossed / narrow (<5c) / wide (>25c)
    for bad_book in (
        BookState(best_yes_bid_c=None, best_no_bid_c=50),
        BookState(best_yes_bid_c=40, best_no_bid_c=None),
        BookState(best_yes_bid_c=48, best_no_bid_c=48),  # spread 4c
        BookState(best_yes_bid_c=37, best_no_bid_c=37),  # spread 26c
        BookState(best_yes_bid_c=55, best_no_bid_c=50),  # crossed
    ):
        dec = evaluate_quote(bad_book, 0.50, state, now, TIP, cfg)
        assert dec.action == ACTION_SKIP
        assert dec.reason_code == "spread_gate"


def test_penny_improve_join_top_and_single_sided():
    cfg = WideSpreadConfig()
    now = TIP - timedelta(minutes=90)
    state = QuoterState()
    # yes_bid=44, no_bid=44 -> yes_ask=56, spread=12c.
    # fv=0.50 -> fv_yes=50 -> max_yes_bid = floor(50 - 1.5) = 48 -> target=min(45, 48)=45 (IMPROVE_1C)
    # fv_no=50 -> max_no_bid=48 -> target=45 (IMPROVE_1C)
    dec = evaluate_quote(BookState(44, 44, 10, 10), 0.50, state, now, TIP, cfg)
    assert dec.action == ACTION_QUOTE_BOTH
    assert dec.yes_bid_c == 45
    assert dec.no_bid_c == 45
    assert dec.yes_mode == MODE_IMPROVE_1C
    assert dec.no_mode == MODE_IMPROVE_1C
    assert dec.ex_ante_net_edge_yes == pytest.approx(5.0)
    assert dec.ex_ante_net_edge_no == pytest.approx(5.0)

    # JOIN_TOP when max_bid == best_bid, and skip opposite side when max_bid < best_bid
    # yes_bid=48, no_bid=40 -> yes_ask=60, spread=12c.
    # fv_yes=50.0 -> max_yes=48 -> JOIN_TOP at 48.
    # fv_no=50.0 -> max_no=48 -> IMPROVE_1C at 41.
    dec2 = evaluate_quote(BookState(48, 40, 10, 10), 0.50, state, now, TIP, cfg)
    assert dec2.yes_mode == MODE_JOIN_TOP
    assert dec2.yes_bid_c == 48
    assert dec2.no_mode == MODE_IMPROVE_1C
    assert dec2.no_bid_c == 41

    # Single-sided quoting: fv=0.45 -> fv_yes=45 (max_yes=43 < yes_bid=45 -> skip YES),
    # fv_no=55 (max_no=53 >= no_bid=45 -> quote NO at 46)
    dec3 = evaluate_quote(BookState(45, 45, 10, 10), 0.45, state, now, TIP, cfg)
    assert dec3.action == ACTION_QUOTE_NO
    assert dec3.yes_bid_c is None
    assert dec3.no_bid_c == 46
    assert dec3.no_mode == MODE_IMPROVE_1C


def test_tail_guard_blocks_below_10_and_above_90():
    cfg = WideSpreadConfig()
    now = TIP - timedelta(minutes=90)
    state = QuoterState()
    # yes_bid=8 -> improve to 9 -> blocked by tail_min_c=10; no_bid=85 -> improve to 86 -> QUOTE_NO!
    dec_ok = evaluate_quote(BookState(8, 85), 0.12, state, now, TIP, cfg)
    assert dec_ok.action == ACTION_QUOTE_NO
    assert dec_ok.no_bid_c == 86

    # Both sides blocked by tail_guard: yes_bid=3, no_bid=90 (spread=7c), fv=0.06 ->
    # target_yes=4 (<10 tail_guard) and target_no=min(91, 92)=91 (>90 tail_guard)
    dec_tail = evaluate_quote(BookState(3, 90), 0.06, state, now, TIP, cfg)
    assert dec_tail.action == ACTION_SKIP
    assert dec_tail.reason_code == "tail_guard"


def test_inventory_caps_and_daily_stop_loss():
    cfg = WideSpreadConfig()
    now = TIP - timedelta(minutes=90)
    book = BookState(40, 40, 10, 10)  # spread 20c, fv=0.50 -> bids 41/41 ($4.10 risk per 10 contracts)

    # Market net >= +20 blocks YES, keeps NO (inventory-reducing) quotable!
    dec_net = evaluate_quote(book, 0.50, QuoterState(market_net_contracts=20), now, TIP, cfg)
    assert dec_net.action == ACTION_QUOTE_NO
    assert dec_net.no_bid_c == 41

    # Player open risk $22 + $4.10 = $26.10 > $25 cap -> blocks both when flat
    dec_player = evaluate_quote(book, 0.50, QuoterState(player_open_risk_usd=22.0), now, TIP, cfg)
    assert dec_player.action == ACTION_SKIP
    assert dec_player.reason_code == "inventory_cap"

    # Game open risk $47 + $4.10 > $50 cap -> blocked
    dec_game = evaluate_quote(book, 0.50, QuoterState(game_open_risk_usd=47.0), now, TIP, cfg)
    assert dec_game.action == ACTION_SKIP
    assert dec_game.reason_code == "inventory_cap"

    # Daily open risk $147 + $4.10 > $150 cap -> blocked
    dec_daily = evaluate_quote(book, 0.50, QuoterState(daily_open_risk_usd=147.0), now, TIP, cfg)
    assert dec_daily.action == ACTION_SKIP
    assert dec_daily.reason_code == "inventory_cap"

    # Daily stop-loss >= $50 -> HALT / daily_stop
    dec_halt = evaluate_quote(book, 0.50, QuoterState(daily_realized_loss_usd=50.0), now, TIP, cfg)
    assert dec_halt.action == ACTION_HALT
    assert dec_halt.reason_code == "daily_stop"


def test_evaluate_cancel_triggers_and_cooldown():
    cfg = WideSpreadConfig()
    now = TIP - timedelta(minutes=90)
    good_view = ActiveQuoteView(best_yes_bid_c=40, best_no_bid_c=45, yes_bid_c=41, no_bid_c=46)

    # Hold when healthy
    c_ok = evaluate_cancel(good_view, True, "ok", "Probable", "Probable", 1.0, now, TIP, cfg)
    assert c_ok.cancel is False
    assert c_ok.reason_code == "hold"

    # 1. Spread / one-sided
    c_spread = evaluate_cancel(ActiveQuoteView(48, 49, 48, 49), False, "quorum", None, "Out", 5.0, now, TIP, cfg)
    assert c_spread.cancel is True
    assert c_spread.reason_code == "spread_gate"

    # 2. Anchor stale/dispersion
    c_anchor = evaluate_cancel(good_view, False, "dispersion", None, "Out", 5.0, now, TIP, cfg)
    assert c_anchor.cancel is True
    assert c_anchor.reason_code == "dispersion"

    # 3. Injury flip
    c_inj = evaluate_cancel(good_view, True, "ok", "Probable", "Questionable", 0.5, now, TIP, cfg)
    assert c_inj.cancel is True
    assert c_inj.reason_code == "injury_flip"

    # Already Questionable -> Doubtful is both in pull set, so not a flip from active; None -> Out IS a flip
    c_inj2 = evaluate_cancel(good_view, True, "ok", None, "Out", 0.5, now, TIP, cfg)
    assert c_inj2.cancel is True
    assert c_inj2.reason_code == "injury_flip"

    # 4. FV jump >= 3.0pp within 5 min -> cancel + 15-min cooldown
    c_jump = evaluate_cancel(good_view, True, "ok", "Probable", "Probable", -3.2, now, TIP, cfg)
    assert c_jump.cancel is True
    assert c_jump.reason_code == "fv_jump"
    assert c_jump.cooldown_until == now + timedelta(minutes=15)

    # Cooldown prevents quoting in evaluate_quote until cooldown_until
    dec_cd = evaluate_quote(
        BookState(40, 40), 0.50, QuoterState(cooldown_until=c_jump.cooldown_until), now + timedelta(minutes=5), TIP, cfg
    )
    assert dec_cd.action == ACTION_SKIP
    assert dec_cd.reason_code == "cooldown"

    # 5. T-45m reached
    c_t45 = evaluate_cancel(good_view, True, "ok", "Probable", "Probable", 0.5, TIP - timedelta(minutes=45), TIP, cfg)
    assert c_t45.cancel is True
    assert c_t45.reason_code == "t_minus_45m"
