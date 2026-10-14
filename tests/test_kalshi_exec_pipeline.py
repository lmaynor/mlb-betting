"""Decision layer for mlb.execution.kalshi: mapping, consensus, gates, venues, risk, client auth."""
import base64
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from mlb.execution.kalshi import gates
from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.client import (
    KalshiClient, ProductionNotEnabled, load_private_key, read_env_keys, sign,
)
from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.consensus import ConsPoint, ConsensusStore
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.mapping import Leg, MapFail, map_market
from mlb.execution.kalshi.price_grid import PriceRange
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import (
    Ack, KalshiVenue, OrderIntent, PaperVenue, client_order_id,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 3, 23, 40, tzinfo=UTC)
START = datetime(2026, 10, 4, 0, 30, tzinfo=UTC)          # 8:30pm EDT
GRID = (PriceRange(0.0, 1.0, 0.01),)


# ============================ mapping ============================
def _ml_market(**kw):
    m = {
        "ticker": "KXMLBGAME-26OCT032030SDMIL-SD", "event_ticker": "KXMLBGAME-26OCT032030SDMIL",
        "title": "San Diego wins", "yes_sub_title": "San Diego", "exchange_index": 3,
        "rules_primary": "If San Diego wins the San Diego vs Milwaukee professional baseball game ...",
        "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}],
    }
    m.update(kw)
    return m


def test_map_ml_away_team_and_start_time():
    leg = map_market("KXMLBGAME", _ml_market(), lambda d, a, h: 849830)
    assert isinstance(leg, Leg)
    assert (leg.away, leg.home, leg.game_pk, leg.game_date) == ("SD", "MIL", 849830, "2026-10-03")
    assert (leg.selection_yes, leg.selection_no, leg.market) == ("AWAY", "HOME", "game_ml")
    assert leg.start_utc == START                              # 20:30 EDT == 00:30Z next day
    assert leg.exchange_index == 3 and leg.ranges == (PriceRange(0.0, 1.0, 0.01),)


def test_map_ml_home_team():
    leg = map_market("KXMLBGAME", _ml_market(ticker="KXMLBGAME-26OCT032030SDMIL-MIL",
                                             title="Milwaukee wins", yes_sub_title="Milwaukee",
                                             rules_primary="If Milwaukee wins ..."), lambda *a: 1)
    assert (leg.selection_yes, leg.selection_no) == ("HOME", "AWAY")


def test_map_ml_wording_mismatch_is_a_failure_not_a_guess():
    r = map_market("KXMLBGAME", _ml_market(title="San Diego to lead after 5"), lambda *a: 1)
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_map_unknown_outcome_team():
    r = map_market("KXMLBGAME", _ml_market(ticker="KXMLBGAME-26OCT032030SDMIL-NYY"), lambda *a: 1)
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


def test_map_rfi_yes_is_yrfi():
    mk = {"ticker": "KXMLBRFI-26OCT032030SDMIL", "event_ticker": "KXMLBRFI-26OCT032030SDMIL",
          "title": "1st inning: Over 0.5 runs"}
    leg = map_market("KXMLBRFI", mk, lambda *a: 7)
    assert (leg.selection_yes, leg.selection_no, leg.market, leg.line) == ("YRFI", "NRFI", "nrfi_ou", None)
    bad = map_market("KXMLBRFI", dict(mk, title="1st inning: Over 1.5 runs"), lambda *a: 7)
    assert isinstance(bad, MapFail)


def test_map_total_requires_greater_strike_and_matching_title():
    mk = {"ticker": "KXMLBTOTAL-26OCT032030SDMIL-8", "event_ticker": "KXMLBTOTAL-26OCT032030SDMIL",
          "title": "Over 8.5 runs scored", "floor_strike": 8.5, "strike_type": "greater"}
    leg = map_market("KXMLBTOTAL", mk, lambda *a: 7)
    assert (leg.selection_yes, leg.selection_no, leg.line) == ("OVER", "UNDER", 8.5)
    assert isinstance(map_market("KXMLBTOTAL", dict(mk, strike_type="less"), lambda *a: 7), MapFail)
    assert isinstance(map_market("KXMLBTOTAL", dict(mk, title="Over 7.5 runs scored"), lambda *a: 7), MapFail)


def test_map_doubleheader_and_unenabled_series_rejected():
    dh = _ml_market(event_ticker="KXMLBGAME-26OCT032030SDMILG2")
    r = map_market("KXMLBGAME", dh, lambda *a: 1)
    assert isinstance(r, MapFail) and r.reason == "REJ_AMBIGUOUS"
    # run line is deliberately NOT mapped (Kalshi 'wins by over 1.5' != books' '+1.5')
    r2 = map_market("KXMLBSPREAD", _ml_market(), lambda *a: 1)
    assert isinstance(r2, MapFail) and r2.reason == "REJ_UNMAPPED"


def test_map_unresolvable_game_pk():
    r = map_market("KXMLBGAME", _ml_market(), lambda *a: None)
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


# ============================ consensus ============================
def _rows(market, books, selection, line, fairs, ts, game_pk=1):
    return [dict(market=market, book=b, selection=selection, line=line, fair_prob=f,
                 snapshot_ts=ts, game_pk=game_pk) for b, f in zip(books, fairs)]


def test_consensus_median_over_real_books_excludes_synthetic_rows():
    rows = _rows("game_ml", ["draftkings", "fanduel", "betmgm", "caesars", "consensus", "open", "kalshi"],
                 "HOME", None, [0.60, 0.62, 0.64, 0.66, 0.99, 0.01, 0.50], "2026-10-03 23:32:33")
    store = ConsensusStore.from_frames({"game_ml": pd.DataFrame(rows)})
    cp = store.point("game_ml", 1, None, "HOME")
    assert cp.n_books == 4 and cp.p == pytest.approx(0.63)      # median(.60,.62,.64,.66)
    assert cp.disp == pytest.approx(0.02)
    assert cp.ts == datetime(2026, 10, 3, 23, 32, 33, tzinfo=UTC)


def test_consensus_nrfi_yes_no_maps_to_yrfi_nrfi():
    rows = (_rows("nrfi_ou", ["a", "b", "c", "d"], "YES", None, [0.43] * 4, "2026-10-03 23:00:00")
            + _rows("nrfi_ou", ["a", "b", "c", "d"], "NO", None, [0.57] * 4, "2026-10-03 23:00:00"))
    store = ConsensusStore.from_frames({"nrfi_ou": pd.DataFrame(rows)})
    assert store.point("nrfi_ou", 1, None, "YRFI").p == pytest.approx(0.43)
    assert store.point("nrfi_ou", 1, None, "NRFI").p == pytest.approx(0.57)
    assert store.point("nrfi_ou", 1, None, "YES") is None        # raw label must not leak through


def test_consensus_prev_point_for_lag_signature():
    rows = (_rows("game_ml", list("abcd"), "HOME", None, [0.60] * 4, "2026-10-03 21:00:00")
            + _rows("game_ml", list("abcd"), "HOME", None, [0.64] * 4, "2026-10-03 23:00:00"))
    store = ConsensusStore.from_frames({"game_ml": pd.DataFrame(rows)})
    cp = store.point("game_ml", 1, None, "HOME", lookback_min=60)
    assert cp.p == pytest.approx(0.64) and cp.prev_p == pytest.approx(0.60)
    assert store.point("game_ml", 1, None, "HOME", lookback_min=600).prev_p is None   # nothing that old


def test_consensus_totals_keyed_by_line_and_kalshi_history_sane_filtered():
    rows = (_rows("game_total", list("abcd"), "OVER", 8.5, [0.48] * 4, "2026-10-03 23:00:00")
            + _rows("game_total", list("abcd"), "OVER", 7.5, [0.62] * 4, "2026-10-03 23:00:00")
            + _rows("game_total", ["kalshi"], "OVER", 8.5, [0.47], "2026-10-03 21:00:00")
            + _rows("game_total", ["kalshi"], "OVER", 8.5, [0.02], "2026-10-03 20:00:00"))   # stale print
    store = ConsensusStore.from_frames({"game_total": pd.DataFrame(rows)})
    assert store.point("game_total", 1, 8.5, "OVER").p == pytest.approx(0.48)
    assert store.point("game_total", 1, 7.5, "OVER").p == pytest.approx(0.62)
    assert store.point("game_total", 1, 9.5, "OVER") is None
    k = store.kalshi_prev("game_total", 1, 8.5, "OVER", NOW, lookback_min=60)
    assert k == pytest.approx(0.47)                               # the 0.02 print was dropped


def test_consensus_skips_rows_without_game_pk_or_fair_prob():
    rows = _rows("game_ml", list("abcd"), "HOME", None, [0.6, 0.6, None, 0.6], "2026-10-03 23:00:00")
    rows += _rows("game_ml", list("abcd"), "HOME", None, [0.6] * 4, "2026-10-03 23:00:00", game_pk=None)
    store = ConsensusStore.from_frames({"game_ml": pd.DataFrame(rows)})
    assert store.point("game_ml", 1, None, "HOME").n_books == 3


# ============================ lag signature ============================
def test_lag_signature_classes():
    m = 0.02
    assert gates.lag_signature(0.03, 0.0, m) == gates.CONSENSUS_MOVED
    assert gates.lag_signature(0.0, -0.03, m) == gates.KALSHI_INFORMED
    assert gates.lag_signature(0.0, 0.0, m) == gates.PERSISTENT
    assert gates.lag_signature(0.03, 0.03, m) == gates.PERSISTENT       # both moved together
    assert gates.lag_signature(None, 0.0, m) == gates.UNKNOWN
    assert gates.lag_signature(0.03, None, m) == gates.UNKNOWN


# ============================ gates ============================
def _leg(**kw):
    base = dict(series="KXMLBGAME", ticker="KXMLBGAME-26OCT032030SDMIL-SD",
                event_ticker="KXMLBGAME-26OCT032030SDMIL", market="game_ml", game_pk=849830,
                game_date="2026-10-03", away="SD", home="MIL", selection_yes="AWAY",
                selection_no="HOME", line=None, start_utc=START, ranges=GRID,
                exchange_index=3, title="San Diego wins")
    base.update(kw)
    return Leg(**base)


def _book(yes_ask=0.36, yes_bid=0.35, size=5000.0, fetched=None):
    # yes ask = 1 - best no bid ; no ask = 1 - best yes bid
    return Book("T", [(yes_bid, size)], [(round(1 - yes_ask, 4), size)],
                fetched_at=(NOW.timestamp() - 0.5) if fetched is None else fetched)


def _cons(p=0.40, n=8, disp=0.01, age_min=3.0, prev_p=None):
    return ConsPoint(p=p, n_books=n, disp=disp, ts=NOW - timedelta(minutes=age_min), prev_p=prev_p)


def _eval(leg=None, book=None, cons=None, k_prev=None, cfg=None, budget=1e9, now=NOW):
    cfg = cfg or ExecConfig()
    return gates.evaluate(leg or _leg(), book or _book(), cons if cons is not None else _cons(),
                          k_prev, now, cfg, cfg.bankroll, budget)


def test_gate_happy_path_buys_cheap_yes_side():
    d = _eval(cons=_cons(p=0.40, prev_p=0.36), k_prev=0.355)         # books rose 4pts, Kalshi flat
    assert d.outcome == "PLAN" and d.side == "yes" and d.selection_bought == "AWAY"
    assert d.lag_class == gates.CONSENSUS_MOVED
    assert d.plan.contracts > 0 and d.plan.ev >= ExecConfig().ev_min
    assert d.plan.limit_price == pytest.approx(0.36)
    assert d.p_hat < d.p_cons                                         # shrinkage applied


def test_gate_buys_no_side_when_yes_is_overpriced():
    # consensus says YES is only 30% -> NO is 70%; Kalshi NO ask = 1 - yes_bid = 0.65
    d = _eval(cons=_cons(p=0.30, prev_p=0.34), k_prev=0.355)
    assert d.outcome == "PLAN" and d.side == "no" and d.selection_bought == "HOME"
    assert d.plan.limit_price == pytest.approx(0.65)


def test_gate_same_economic_bet_has_same_intent_id_across_both_markets():
    away_mkt = _leg()                                                  # yes = AWAY
    home_mkt = _leg(ticker="KXMLBGAME-26OCT032030SDMIL-MIL", selection_yes="HOME", selection_no="AWAY")
    assert gates.intent_id(away_mkt, "AWAY") == gates.intent_id(home_mkt, "AWAY")    # yes-AWAY == no-HOME-market
    assert gates.intent_id(away_mkt, "AWAY") != gates.intent_id(away_mkt, "HOME")


def test_gate_game_started_or_inside_close_buffer():
    assert _eval(now=START).outcome == "REJ_GAME_STARTED"
    assert _eval(now=START - timedelta(minutes=5), book=_book(fetched=(START - timedelta(minutes=5)).timestamp() - 0.5),
                 cons=_cons()).outcome == "REJ_GAME_STARTED"


def test_gate_book_stale():
    assert _eval(book=_book(fetched=NOW.timestamp() - 30)).outcome == "REJ_BOOK_STALE"


def test_gate_book_illiquid_variants():
    assert _eval(book=Book("T", [(0.35, 100.0)], [], fetched_at=NOW.timestamp() - 0.5)).outcome == "REJ_BOOK_ILLIQUID"
    assert _eval(book=Book("T", [(0.60, 100.0)], [(0.50, 100.0)], fetched_at=NOW.timestamp() - 0.5)
                 ).outcome == "REJ_BOOK_ILLIQUID"                                       # crossed
    assert _eval(book=_book(yes_ask=0.45, yes_bid=0.35)).outcome == "REJ_BOOK_ILLIQUID"  # 10c spread


def test_gate_consensus_missing_stale_thin():
    assert gates.evaluate(_leg(), _book(), None, None, NOW, ExecConfig(), 1000.0, 1e9).outcome == "REJ_CONS_THIN"
    assert _eval(cons=_cons(age_min=45)).outcome == "REJ_CONS_STALE"
    assert _eval(cons=_cons(n=2)).outcome == "REJ_CONS_THIN"
    assert _eval(cons=_cons(disp=0.2)).outcome == "REJ_CONS_THIN"


def test_gate_no_edge_when_ask_at_or_above_consensus():
    d = _eval(book=_book(yes_ask=0.41, yes_bid=0.40), cons=_cons(p=0.40))
    assert d.outcome == "REJ_EV_LOW"


def test_gate_edge_eaten_by_fee_is_rejected():
    # 2.5pt gross gap at a ~40c ask: fee ~4.2% of cost + shrink leaves < 3% net EV
    d = _eval(book=_book(yes_ask=0.38, yes_bid=0.37), cons=_cons(p=0.405))
    assert d.outcome == "REJ_EV_LOW"


def test_gate_ev_cap_rejects_instead_of_clamping():
    d = _eval(book=_book(yes_ask=0.30, yes_bid=0.29), cons=_cons(p=0.40))   # ~28% gross
    assert d.outcome in ("REJ_EV_TOO_HIGH", "REJ_KALSHI_OFF")


def test_gate_kalshi_off_vs_consensus_is_treated_as_news():
    cfg = ExecConfig(kdev_max=0.05)
    d = _eval(book=_book(yes_ask=0.36, yes_bid=0.35), cons=_cons(p=0.42), cfg=cfg)
    assert d.outcome == "REJ_KALSHI_OFF"


def test_gate_kalshi_informed_selling_is_rejected():
    # Kalshi mid fell 4pts over the lookback while consensus was flat
    d = _eval(cons=_cons(p=0.40, prev_p=0.40), k_prev=0.395)
    assert d.outcome == "REJ_KALSHI_INFORMED" and d.lag_class == gates.KALSHI_INFORMED


def test_gate_persistent_gap_uses_reduced_kelly():
    bk = _book(yes_ask=0.35, yes_bid=0.34)
    lagging = _eval(book=bk, cons=_cons(p=0.40, prev_p=0.36), k_prev=0.345)
    persistent = _eval(book=bk, cons=_cons(p=0.40, prev_p=0.40), k_prev=0.345)
    assert lagging.plan.contracts > persistent.plan.contracts > 0
    assert persistent.lag_class == gates.PERSISTENT
    unknown = _eval(book=bk, cons=_cons(p=0.40, prev_p=None), k_prev=None)
    assert unknown.lag_class == gates.UNKNOWN and unknown.plan.contracts == persistent.plan.contracts


def test_gate_price_band_blocks_extremes():
    d = _eval(book=_book(yes_ask=0.97, yes_bid=0.96), cons=_cons(p=0.99))
    assert d.outcome in ("REJ_PRICE_BAND", "REJ_EV_LOW", "REJ_KALSHI_OFF")
    d2 = _eval(book=_book(yes_ask=0.02, yes_bid=0.01), cons=_cons(p=0.06))
    assert d2.outcome == "REJ_PRICE_BAND"


def test_gate_stale_consensus_gets_larger_haircut():
    cfg = ExecConfig()
    fresh = gates.shrink(_cons(age_min=3, disp=0.01), NOW, cfg)
    old = gates.shrink(_cons(age_min=14, disp=0.01), NOW, cfg)
    wide = gates.shrink(_cons(age_min=3, disp=0.05), NOW, cfg)
    assert old > fresh and wide > fresh and fresh == pytest.approx(cfg.h0 + cfg.h_disp * 0.01)


def test_gate_budget_exhausted_is_size_reject():
    d = _eval(cons=_cons(p=0.40, prev_p=0.36), k_prev=0.355, budget=0.0)
    assert d.outcome == "REJ_SIZE_TOO_SMALL"


# ============================ venues ============================
def test_client_order_id_is_deterministic_and_short():
    a, b = client_order_id("abc"), client_order_id("abc")
    assert a == b and a != client_order_id("abd") and len(a) <= 32


def test_kalshi_api_price_conversion_yes_and_no():
    yes = OrderIntent("i", "T", "yes", 10, 0.3649, GRID)
    assert KalshiVenue.to_api(yes) == ("bid", pytest.approx(0.36))               # snap DOWN
    no = OrderIntent("i", "T", "no", 10, 0.65, GRID)
    side, price = KalshiVenue.to_api(no)
    assert side == "ask" and price == pytest.approx(0.35)                         # 1 - 0.65
    no2 = OrderIntent("i", "T", "no", 10, 0.6549, GRID)
    side, price = KalshiVenue.to_api(no2)
    assert price == pytest.approx(0.35) and (1 - price) <= 0.6549 + 1e-9          # never pays MORE than the cap


class FakeClient:
    def __init__(self, fills=None, order_id="ord-1", raise_exc=None):
        self.calls, self._fills, self._oid, self._exc = [], fills or [], order_id, raise_exc

    def create_order(self, **kw):
        self.calls.append(kw)
        if self._exc:
            raise self._exc
        return {"order_id": self._oid, "client_order_id": kw["client_order_id"], "fill_count": "0",
                "remaining_count": "0"}

    def get_fills(self, **kw):
        return self._fills


def test_kalshi_venue_submit_builds_ioc_order_and_reads_authoritative_fills():
    fills = [{"fill_id": "f1", "order_id": "ord-1", "ticker": "T", "outcome_side": "no", "count_fp": "7.00",
              "yes_price_dollars": "0.3500", "no_price_dollars": "0.6500", "fee_cost": "0.1100",
              "created_time": "2026-10-03T23:40:01Z"}]
    fc = FakeClient(fills)
    ack = KalshiVenue(fc).submit(OrderIntent("intent-1", "T", "no", 10, 0.66, GRID, exchange_index=3))
    call = fc.calls[0]
    assert call["book_side"] == "ask" and call["price"] == pytest.approx(0.34)
    assert call["time_in_force"] == "immediate_or_cancel" and call["count"] == 10.0
    assert call["exchange_index"] == 3 and call["client_order_id"] == client_order_id("intent-1")
    assert ack.status == "PARTIAL" and ack.fill_count == 7.0
    assert ack.fills[0].price == pytest.approx(0.65)                             # NO-side price, not yes
    assert ack.filled_cost == pytest.approx(7 * 0.65 + 0.11) and ack.fee == pytest.approx(0.11)


def test_kalshi_venue_error_is_captured_not_raised():
    ack = KalshiVenue(FakeClient(raise_exc=RuntimeError("boom"))).submit(OrderIntent("i", "T", "yes", 1, 0.5, GRID))
    assert ack.status == "ERROR" and "boom" in ack.error and ack.order_id is None


def test_kalshi_venue_no_fill():
    ack = KalshiVenue(FakeClient([])).submit(OrderIntent("i", "T", "yes", 5, 0.5, GRID))
    assert ack.status == "NO_FILL" and ack.fill_count == 0


def test_paper_venue_ioc_respects_limit_and_walks_levels():
    book = Book("T", [(0.30, 100.0)], [(0.60, 4.0), (0.58, 100.0)])    # yes asks: .40 x4, .42 x100
    pv = PaperVenue(lambda t: book)
    ack = pv.submit(OrderIntent("i", "T", "yes", 10, 0.41, GRID))      # limit .41 -> only the .40 level
    assert ack.status == "PARTIAL" and ack.fill_count == 4.0
    full = PaperVenue(lambda t: book).submit(OrderIntent("i", "T", "yes", 10, 0.42, GRID))
    assert full.status == "FILLED" and full.fill_count == 10.0
    assert full.filled_cost == pytest.approx(4 * 0.40 + 6 * 0.42 + full.fee)
    none = PaperVenue(lambda t: book).submit(OrderIntent("i", "T", "yes", 10, 0.39, GRID))
    assert none.status == "NO_FILL"


# ============================ ledger + risk ============================
def test_ledger_intent_dedup_and_exposure_sums():
    L = Ledger(":memory:")
    L.record_order(day="d", mode="shadow", intent_id="a", client_order_id="c", order_id="o", ticker="T",
                   game_pk=1, side="yes", contracts=5, limit_price=0.4, status="FILLED", fill_count=5,
                   filled_cost=2.0)
    assert L.has_intent("a") and not L.has_intent("b")
    with pytest.raises(Exception):
        L.record_order(day="d", mode="shadow", intent_id="a", client_order_id="c", order_id="o", ticker="T",
                       game_pk=1, side="yes", contracts=5, limit_price=0.4, status="FILLED")
    L.record_order(day="d", mode="shadow", intent_id="b", client_order_id="c2", order_id="o2", ticker="T2",
                   game_pk=1, side="no", contracts=3, limit_price=0.5, status="FILLED", filled_cost=1.5)
    L.record_order(day="d", mode="demo", intent_id="c", client_order_id="c3", order_id="o3", ticker="T3",
                   game_pk=2, side="no", contracts=3, limit_price=0.5, status="FILLED", filled_cost=9.0)
    assert L.game_exposure(1, "d", "shadow") == pytest.approx(3.5)
    assert L.deployed_today("d", "shadow") == pytest.approx(3.5) and L.deployed_today("d") == pytest.approx(12.5)
    assert L.orders_today("d", "shadow") == 2 and L.game_exposure(None, "d") == 0.0


def test_ledger_histogram_and_kv():
    L = Ledger(":memory:")
    for o in ("PLAN", "REJ_EV_LOW", "REJ_EV_LOW"):
        L.record_decision(day="d", mode="shadow", outcome=o)
    assert L.histogram("d") == {"PLAN": 1, "REJ_EV_LOW": 2}
    L.kv_set("k", "v"); assert L.kv_get("k") == "v"
    L.kv_del("k"); assert L.kv_get("k") is None


def _gate(cfg=None):
    return RiskGate(cfg or ExecConfig(), Ledger(":memory:"), "shadow")


def test_risk_budget_is_min_of_game_daily_cash():
    g = _gate()
    assert g.budget_cap(1, "d", available_cash=1e9) == pytest.approx(40.0)        # per-game 4%
    assert g.budget_cap(1, "d", available_cash=12.0) == pytest.approx(12.0)       # cash binds
    g.ledger.record_order(day="d", mode="shadow", intent_id="x", client_order_id="c", order_id="o", ticker="T",
                          game_pk=1, side="yes", contracts=1, limit_price=.5, status="FILLED", filled_cost=30.0)
    assert g.budget_cap(1, "d", 1e9) == pytest.approx(10.0)                       # 40 - 30
    assert g.budget_cap(2, "d", 1e9) == pytest.approx(40.0)                       # other game unaffected


def test_risk_daily_deploy_cap_and_order_count():
    g = _gate(ExecConfig(max_orders_per_day=2))
    for i in range(2):
        g.ledger.record_order(day="d", mode="shadow", intent_id=f"i{i}", client_order_id="c", order_id="o",
                              ticker="T", game_pk=i, side="yes", contracts=1, limit_price=.5, status="FILLED",
                              filled_cost=45.0)
    assert g.budget_cap(9, "d", 1e9) == pytest.approx(10.0)                       # 100 - 90
    assert g.check_order(5.0, 9, "d", 1e9) == (False, "REJ_RISK")                 # 2 orders already


def test_risk_kill_switch_persists_and_needs_manual_reset():
    g = _gate()
    assert g.check_order(1.0, 1, "d", 1e9) == (True, "")
    g.check_pnl(-60.0)                                                            # > 5% of 1000
    assert g.killed and g.check_order(1.0, 1, "d", 1e9) == (False, "REJ_KILLED")
    assert RiskGate(g.cfg, g.ledger, "shadow").killed                             # survives "restart"
    g.reset()
    assert not g.killed and g.check_order(1.0, 1, "d", 1e9) == (True, "")


def test_risk_pnl_within_limit_does_not_trip():
    g = _gate(); g.check_pnl(-49.0); assert not g.killed


def test_risk_consecutive_submit_errors_trip():
    g = _gate(ExecConfig(max_consecutive_rejects=3))
    g.note_submit_error(); g.note_submit_error(); g.note_submit_ok(); g.note_submit_error()
    assert not g.killed
    g.note_submit_error(); g.note_submit_error()
    assert g.killed and "consecutive" in g.kill_reason


# ============================ client / auth ============================
def _rsa():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return key, pem


def test_signature_verifies_with_rsa_pss_sha256_digest_salt():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    key, pem = _rsa()
    sig = sign(load_private_key(pem), "1700000000000GET/trade-api/v2/portfolio/balance")
    key.public_key().verify(base64.b64decode(sig), b"1700000000000GET/trade-api/v2/portfolio/balance",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())


def test_client_signs_full_path_without_query_and_refuses_production():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    key, pem = _rsa()
    c = KalshiClient("demo", "KEY-ID", pem)
    h = c._auth_headers("get", "/portfolio/balance")
    assert h["KALSHI-ACCESS-KEY"] == "KEY-ID"
    msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "GET" + "/trade-api/v2/portfolio/balance").encode()
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
    for env in ("production", "prod", "live"):
        with pytest.raises(ProductionNotEnabled):
            KalshiClient(env, "k", pem)
    with pytest.raises(RuntimeError):
        KalshiClient("demo", None, None)._auth_headers("GET", "/x")             # no creds -> no signed call


def test_read_env_keys_returns_only_requested_keys(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nKALSHI_ENV=demo\nKALSHI_API_KEY_ID='abc'\nGEMINI_API_KEY=SECRET\nTYPESAFE_API_KEY=SECRET2\n")
    got = read_env_keys(str(f), ("KALSHI_ENV", "KALSHI_API_KEY_ID"))
    assert got == {"KALSHI_ENV": "demo", "KALSHI_API_KEY_ID": "abc"}


def test_public_data_client_is_read_only_and_cannot_sign():
    _, pem = _rsa()
    c = KalshiClient("public-data", None, None)
    assert "api.elections.kalshi.com" in c.base_url
    with pytest.raises(RuntimeError):
        c._auth_headers("POST", "/portfolio/events/orders")        # no credentials -> cannot trade
    with pytest.raises(ProductionNotEnabled):
        KalshiClient("public-data", "key", pem)                     # never a prod client WITH creds


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body or {}, headers or {}
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._b


class _Sess:
    def __init__(self, seq):
        self.seq, self.calls, self.headers = list(seq), 0, {}

    def request(self, *a, **k):
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def _fast_client(seq):
    c = KalshiClient("public-data", None, None, session=_Sess(seq))
    c.min_interval_s = 0.0
    c._backoff = staticmethod(lambda attempt, ra=None: 0.0)
    return c


def test_get_retries_429_and_5xx_then_succeeds():
    c = _fast_client([_Resp(429), _Resp(503), _Resp(200, {"ok": 1})])
    assert c.request("GET", "/x", auth=False) == {"ok": 1} and c._s.calls == 3


def test_post_retries_429_but_never_retries_5xx():
    c = _fast_client([_Resp(429), _Resp(200, {"order_id": "o"})])
    assert c.request("POST", "/x", body={}, auth=False)["order_id"] == "o" and c._s.calls == 2
    from mlb.execution.kalshi.client import KalshiAPIError
    c2 = _fast_client([_Resp(500, {"e": 1}), _Resp(200, {"order_id": "dup"})])
    with pytest.raises(KalshiAPIError):
        c2.request("POST", "/x", body={}, auth=False)
    assert c2._s.calls == 1                      # an ambiguous POST failure is surfaced, not replayed


def test_post_timeout_is_not_retried():
    import requests as rq
    c = _fast_client([rq.Timeout("slow"), _Resp(200, {"order_id": "dup"})])
    with pytest.raises(rq.Timeout):
        c.request("POST", "/x", body={}, auth=False)
    assert c._s.calls == 1


def test_get_gives_up_after_max_retries():
    from mlb.execution.kalshi.client import KalshiAPIError
    c = _fast_client([_Resp(429)] * 10)
    c.max_retries = 2
    with pytest.raises(KalshiAPIError):
        c.request("GET", "/x", auth=False)
    assert c._s.calls == 3


# ============================ P&L feed ============================
def test_position_pnl_realized_unrealized_and_fees_scoped_to_our_tickers():
    from mlb.execution.kalshi.risk import position_pnl
    pos = [
        # long 42 YES, cost $18.90, can be sold at 0.43 now -> 42*0.43 - 18.90 = -0.84
        {"ticker": "A", "position_fp": "42.00", "market_exposure_dollars": "18.90",
         "realized_pnl_dollars": "0.000000", "fees_paid_dollars": "0.36"},
        # netted pair: realized -0.04, flat, fees 0.064
        {"ticker": "B", "position_fp": "0.00", "market_exposure_dollars": "0.00",
         "realized_pnl_dollars": "-0.040000", "fees_paid_dollars": "0.064"},
        # long 10 NO (negative position), cost 6.00, sellable at 0.62 -> 10*0.62 - 6 = +0.20
        {"ticker": "C", "position_fp": "-10.00", "market_exposure_dollars": "6.00",
         "realized_pnl_dollars": "0", "fees_paid_dollars": "0.10"},
        # someone else's market (market-maker weather) must NOT count
        {"ticker": "KXHIGHNY-X", "position_fp": "5", "market_exposure_dollars": "1",
         "realized_pnl_dollars": "-99", "fees_paid_dollars": "9"},
    ]
    r, u, f = position_pnl(pos, {"A", "B", "C"}, {"A": (0.43, 0.56), "C": (0.37, 0.62)})
    assert r == pytest.approx(-0.04) and f == pytest.approx(0.524)
    assert u == pytest.approx(-0.84 + 0.20)


def test_position_pnl_unmarked_open_position_is_carried_at_cost():
    from mlb.execution.kalshi.risk import position_pnl
    pos = [{"ticker": "A", "position_fp": "42", "market_exposure_dollars": "18.9",
            "realized_pnl_dollars": "0", "fees_paid_dollars": "0"}]
    assert position_pnl(pos, {"A"}, {}) == (0.0, 0.0, 0.0)
    assert position_pnl(None, {"A"}, {}) == (0.0, 0.0, 0.0)


def test_ledger_tickers_only_includes_filled_orders_in_mode():
    L = Ledger(":memory:")
    L.record_order(day="d", mode="demo", intent_id="1", client_order_id="c", order_id="o", ticker="A", game_pk=1,
                   side="yes", contracts=5, limit_price=.4, status="FILLED", fill_count=5, filled_cost=2.0)
    L.record_order(day="d", mode="demo", intent_id="2", client_order_id="c", order_id="o", ticker="B", game_pk=2,
                   side="yes", contracts=5, limit_price=.4, status="NO_FILL", fill_count=0)
    L.record_order(day="d", mode="probe", intent_id="3", client_order_id="c", order_id="o", ticker="C", game_pk=3,
                   side="yes", contracts=1, limit_price=.4, status="FILLED", fill_count=1, filled_cost=.4)
    assert L.tickers("demo") == {"A"} and L.tickers() == {"A", "C"}
