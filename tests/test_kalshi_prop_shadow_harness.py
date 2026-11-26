"""Unit tests for ev.kalshi_prop_shadow_harness (RUL-20261125 Half 2 / MAK-01)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from ev.consensus_fv_anchor import AnchorResult
from ev.kalshi_prop_shadow_harness import (
    GuardedClient,
    ParlaySnapshotProvider,
    ReadOnlyViolationError,
    ShadowHarness,
    espn_injury_status_map,
    finalize_day,
    format_scorecard,
    outcome_from_market,
    scorecard,
    settle_bank,
    write_part,
)
from ev.kalshi_wide_quoter_rules import (
    ACTION_CANCEL,
    ACTION_QUOTE_BOTH,
    ACTION_QUOTE_YES,
    ACTION_SKIP,
    MODE_IMPROVE_1C,
    MODE_JOIN_TOP,
    WideSpreadConfig,
)
from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.client import KalshiClient, PUBLIC_DATA_ENV


TIP = datetime(2026, 11, 22, 1, 0, 0, tzinfo=timezone.utc)
NOW = TIP - timedelta(minutes=120)

SAMPLE_MARKET = {
    "ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15",
    "title": "Ayo Dosunmu: 15+ points",
    "floor_strike": 14.5,
    "tip_ts_utc": TIP.isoformat(),
}


class _FakeKalshiClient:
    def __init__(self) -> None:
        self.markets_by_series: dict[str, list[dict]] = {}
        self.books: dict[str, Book] = {}
        self.trades: dict[str, list[dict]] = {}
        self.series_obj: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def get_markets(self, series_ticker: str, status: str = "open") -> list[dict]:
        return list(self.markets_by_series.get(series_ticker, []))

    def get_orderbook(self, ticker: str) -> Book:
        return self.books[ticker]

    def get_series(self, series_ticker: str) -> dict:
        return self.series_obj.get(series_ticker, {"fee_type": "quadratic", "fee_multiplier": 1})

    def request(self, method: str, path: str, params=None, **kwargs):
        self.calls.append((method, path))
        if path == "/markets/trades":
            ticker = (params or {}).get("ticker")
            return {"trades": list(self.trades.get(ticker, []))}
        return {}


def test_guarded_client_enforces_strict_readonly():
    real_client = KalshiClient(env=PUBLIC_DATA_ENV, key_id=None, private_key_pem=None)
    guarded = GuardedClient(real_client)

    for forbidden in (
        "create_order",
        "cancel_order",
        "amend_order",
        "amend_size_down",
        "amend_size_up",
        "amend_price",
        "amend_expiry",
        "transfer_between_shards",
        "get_balance",
        "_s",
    ):
        with pytest.raises(ReadOnlyViolationError):
            getattr(guarded, forbidden)

    for method in ("POST", "PUT", "DELETE", "PATCH"):
        with pytest.raises(ReadOnlyViolationError):
            guarded.request(method, "/portfolio/events/orders")

    with pytest.raises(ReadOnlyViolationError):
        guarded.any_new_attr = 123  # type: ignore[attr-defined]

    # Allowed read methods delegate cleanly
    fake = _FakeKalshiClient()
    fake.trades["T1"] = [{"trade_id": "a1", "yes_price": 45, "count": 5}]
    g_fake = GuardedClient(fake)  # type: ignore[arg-type]
    assert g_fake.get_trades("T1") == [{"trade_id": "a1", "yes_price": 45, "count": 5}]
    assert fake.calls == [("GET", "/markets/trades")]


def test_espn_injury_guard_pandas_223_pattern():
    frame = pd.DataFrame(
        {
            "player": ["Ayo Dosunmu", None, np.nan, "Valid Healthy", "Empty Str"],
            "status": ["Out", "Out", "Questionable", None, ""],
        }
    )
    frame.attrs["ok"] = True
    # Also ensure literal "None"/"nan" strings are skipped
    frame.loc[len(frame)] = ["Literal None", "None"]
    frame.loc[len(frame)] = ["Literal NaN", "nan"]

    res = espn_injury_status_map(frame)
    assert res == {"ayo dosunmu": "Out"}

    # Failed frame (ok=False) returns empty map
    bad_frame = frame.copy()
    bad_frame.attrs["ok"] = False
    assert espn_injury_status_map(bad_frame) == {}


def test_parlay_snapshot_provider(tmp_path):
    ts = (NOW - timedelta(seconds=20)).isoformat()
    payload = {
        "home_team": "Minnesota Timberwolves",
        "away_team": "Memphis Grizzlies",
        "bookmakers": [
            {
                "key": "pinnacle",
                "markets": [
                    {
                        "key": "player_points",
                        "last_update": ts,
                        "outcomes": [
                            {"name": "Over", "description": "Ayo Dosunmu", "point": 14.5, "price": -105},
                            {"name": "Under", "description": "Ayo Dosunmu", "point": 14.5, "price": -105},
                        ],
                    }
                ],
            },
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_points",
                        "last_update": ts,
                        "outcomes": [
                            {"name": "Over", "description": "Ayo Dosunmu", "point": 14.5, "price": -110},
                            {"name": "Under", "description": "Ayo Dosunmu", "point": 14.5, "price": -110},
                        ],
                    }
                ],
            },
            {
                "key": "fanduel",
                "markets": [
                    {
                        "key": "player_points",
                        "last_update": ts,
                        "outcomes": [
                            {"name": "Over", "description": "Ayo Dosunmu", "point": 14.5, "price": -110},
                            {"name": "Under", "description": "Ayo Dosunmu", "point": 14.5, "price": -110},
                        ],
                    }
                ],
            },
        ],
    }
    (tmp_path / "event.json").write_text(json.dumps(payload), encoding="utf-8")
    provider = ParlaySnapshotProvider(snapshot_dir=tmp_path)
    from ev.kalshi_nba_ticker_mapper import parse_market

    mapped = parse_market(SAMPLE_MARKET)
    anchor = provider(mapped, NOW)
    assert anchor.ok is True
    assert anchor.n_fresh_books == 3
    assert anchor.fv_yes_prob == pytest.approx(0.5, abs=1e-9)


def _ok_anchor(fv: float = 0.50) -> AnchorResult:
    return AnchorResult(
        fv_yes_prob=fv,
        ok=True,
        reason="ok",
        n_fresh_books=3,
        max_book_age_sec=10.0,
        dispersion_pp=1.0,
        books_used=(("pinnacle", fv, 2.0), ("dk", fv, 1.0), ("fd", fv, 1.0)),
    )


def test_harness_improve_1c_latency_denial_and_join_top_queue():
    fake = _FakeKalshiClient()
    ticker = SAMPLE_MARKET["ticker"]
    bad_market = {"ticker": "KXNBAGAME-26NOV21MEMMIN-MIN", "title": "bad", "floor_strike": 0.5}
    fake.markets_by_series["KXNBAPTS"] = [SAMPLE_MARKET, bad_market]
    # Book: yes_bid=44c (size 20), no_bid=44c (yes_ask=56c, spread=12c)
    fake.books[ticker] = Book(ticker, yes_bids=[(0.44, 20.0)], no_bids=[(0.44, 20.0)])

    fv_state = {"val": 0.50}
    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(fv_state["val"]),
    )

    # Cycle 0: places IMPROVE_1C bids at 45c YES and 45c NO
    rows0 = harness.run_once(NOW)
    assert len(rows0) == 1  # bad_market skipped
    r0 = rows0[0]
    assert r0.action == ACTION_QUOTE_BOTH
    assert r0.sim_yes_bid == 45
    assert r0.yes_queue_mode == MODE_IMPROVE_1C
    assert r0.fill_conservative_qty is None

    # Cycle 1 (+30s): trade at 45c only 2s after placement -> Primary denies (<5s), Secondary fills!
    fake.trades[ticker] = [
        {"trade_id": "t1", "yes_price": 45, "count": 6, "created_time": (NOW + timedelta(seconds=2)).isoformat()},
        {"trade_id": "t2", "yes_price": 45, "count": 4, "created_time": (NOW + timedelta(seconds=10)).isoformat()},
    ]
    rows1 = harness.run_once(NOW + timedelta(seconds=30))
    r1 = rows1[0]
    assert r1.fill_side == "yes"
    assert r1.fill_price == 45.0
    assert r1.fill_conservative_qty == pytest.approx(4.0)  # t1 denied, t2 credited
    assert r1.fill_secondary_qty == pytest.approx(10.0)    # t1 + t2 credited


def test_harness_join_top_queue_back_and_toxic_fill():
    fake = _FakeKalshiClient()
    ticker = SAMPLE_MARKET["ticker"]
    fake.markets_by_series["KXNBAPTS"] = [SAMPLE_MARKET]
    # yes_bid=48c (size 30), no_bid=40c (yes_ask=60c, spread=12c). fv=0.50 -> JOIN_TOP YES at 48c (max_bid=48)
    fake.books[ticker] = Book(ticker, yes_bids=[(0.48, 30.0)], no_bids=[(0.40, 20.0)])

    fv_state = {"val": 0.50}
    inj_state = {"status": "Probable"}
    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(fv_state["val"]),
        injury_provider=lambda p: inj_state["status"],
    )

    rows0 = harness.run_once(NOW)
    assert rows0[0].yes_queue_mode == MODE_JOIN_TOP
    assert rows0[0].sim_yes_bid == 48

    # Cycle 1 (+30s): 35 contracts trade at 48c -> <= 30 + 10 = 40 -> 0 Primary fill;
    # Secondary credits 35 * (10 / (30 + 10)) = 8.75 contracts!
    fake.trades[ticker] = [
        {"trade_id": "j1", "yes_price": 48, "count": 35, "created_time": (NOW + timedelta(seconds=12)).isoformat()},
    ]
    rows1 = harness.run_once(NOW + timedelta(seconds=30))
    assert rows1[0].fill_conservative_qty == pytest.approx(0.0)
    assert rows1[0].fill_secondary_qty == pytest.approx(8.75)

    # Cycle 2 (+60s): 10 more contracts at 48c -> cum = 45 > 40 -> excess 5 credited on Primary!
    fake.trades[ticker] = [
        {"trade_id": "j2", "yes_price": 48, "count": 10, "created_time": (NOW + timedelta(seconds=45)).isoformat()},
    ]
    rows2 = harness.run_once(NOW + timedelta(seconds=60))
    assert rows2[0].fill_conservative_qty == pytest.approx(5.0)
    assert rows2[0].sim_inventory_net == 5

    # Cycle 3 (+90s): FV drops by 4.0pp in one poll (0.50 -> 0.46) -> toxic force-fill of remaining 5 YES
    # BEFORE cancel fires, AND 5m FV jump cancels + sets 15m cooldown!
    fv_state["val"] = 0.46
    rows3 = harness.run_once(NOW + timedelta(seconds=90))
    r3 = rows3[0]
    assert r3.fill_side == "yes"
    assert r3.fill_price == 48.0
    assert r3.fill_conservative_qty == pytest.approx(5.0)
    assert r3.fill_secondary_qty == pytest.approx(0.0)
    assert r3.action == ACTION_CANCEL
    assert r3.reason_code == "fv_jump"
    assert r3.sim_inventory_net == 10

    # Cycle 4 (+120s): still in 15m cooldown -> SKIP / cooldown
    rows4 = harness.run_once(NOW + timedelta(seconds=120))
    assert rows4[0].action == ACTION_SKIP
    assert rows4[0].reason_code == "cooldown"


def test_harness_injury_flip_cancels_resting():
    fake = _FakeKalshiClient()
    ticker = SAMPLE_MARKET["ticker"]
    fake.markets_by_series["KXNBAPTS"] = [SAMPLE_MARKET]
    fake.books[ticker] = Book(ticker, yes_bids=[(0.44, 20.0)], no_bids=[(0.44, 20.0)])

    inj_state = {"status": "Probable"}
    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(0.50),
        injury_provider=lambda p: inj_state["status"],
    )
    assert harness.run_once(NOW)[0].action == ACTION_QUOTE_BOTH

    # Flip to Out and make book narrow on same cycle? Keep book wide so injury_flip fires,
    # and make anchor fail or keep anchor ok: wait, if anchor is ok, step 4 could re-quote unless
    # anchor is ok? Actually step 4 re-evaluates new quote; let's check step 2 cancels resting quote!
    inj_state["status"] = "Out"
    # With no_anchor on same cycle? No, let's verify resting quote is cleared by checking trades don't fill old bid
    fake.books[ticker] = Book(ticker, yes_bids=[(0.40, 20.0)], no_bids=[(0.40, 20.0)])
    # Also verify injury_flip triggers cancel when book stays valid and anchor is disabled after
    harness.anchor_provider = lambda m, now: _ok_anchor(0.50)
    # Wait: if evaluate_quote doesn't see injury status, it only pulls on evaluate_cancel;
    # let's test evaluate_cancel via spread collapse or anchor invalid after injury flip:
    # Actually let's check resting quote was removed before trade fill check in cycle 1:
    fake.trades[ticker] = [
        {"trade_id": "t_after_inj", "yes_price": 45, "count": 10, "created_time": (NOW + timedelta(seconds=15)).isoformat()},
    ]
    rows1 = harness.run_once(NOW + timedelta(seconds=30))
    # Resting quote at 45c was cancelled by injury_flip BEFORE trade-fill check, so 45c trade did NOT fill!
    assert rows1[0].fill_conservative_qty is None


def test_parquet_banking_settlement_and_scorecard(tmp_path):
    fake = _FakeKalshiClient()
    ticker = SAMPLE_MARKET["ticker"]
    fake.markets_by_series["KXNBAPTS"] = [SAMPLE_MARKET]
    fake.books[ticker] = Book(ticker, yes_bids=[(0.44, 20.0)], no_bids=[(0.44, 20.0)])

    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(0.50),
    )
    rows0 = harness.run_once(NOW)
    p0 = write_part(rows0, tmp_path, "2026-11-21", run_id="r1", seq=0)
    assert p0.exists()
    with pytest.raises(FileExistsError):
        write_part(rows0, tmp_path, "2026-11-21", run_id="r1", seq=0)

    # Trade fill in cycle 1
    fake.trades[ticker] = [
        {"trade_id": "f1", "yes_price": 45, "count": 10, "created_time": (NOW + timedelta(seconds=20)).isoformat()},
    ]
    rows1 = harness.run_once(NOW + timedelta(seconds=30))
    p1 = write_part(rows1, tmp_path, "2026-11-21", run_id="r1", seq=1)
    assert p1.exists()

    final_path = finalize_day(tmp_path, "2026-11-21")
    assert final_path.exists()
    df = pd.read_parquet(final_path)
    assert len(df) == 2

    # Async update via new finalize (parts stay untouched)
    filled_opp = df.loc[df["fill_conservative_qty"].notna(), "opp_id"].iloc[0]
    finalize_day(
        tmp_path,
        "2026-11-21",
        updates=[
            {
                "opp_id": filled_opp,
                "fv_t_plus_15m": 0.52,
                "fv_close_t_minus_5m": 0.54,
                "kalshi_mid_close": 0.53,
            }
        ],
    )
    df_upd = pd.read_parquet(final_path)
    assert outcome_from_market({"result": "yes"}) == "yes"
    assert outcome_from_market({"result": "no"}) == "no"
    assert outcome_from_market({"result": ""}) is None

    settled_df, z_val, z_n = settle_bank(df_upd, {ticker: "yes"})
    assert z_n == 1
    assert z_val is not None
    settled_row = settled_df.loc[settled_df["opp_id"] == filled_opp].iloc[0]
    assert settled_row["settled_outcome"] == "yes"
    # 10 contracts bought at 45c -> gross win = 10 * 55c = 550c minus maker_settlement.kalshi_fees(0.45, 10)
    assert settled_row["realized_pnl_cents"] < 550.0
    assert settled_row["realized_pnl_cents"] > 500.0

    rep = scorecard(settled_df)
    assert rep["n_fills"] == 1
    assert rep["total_contracts"] == pytest.approx(10.0)
    assert rep["closing_markout_c"] == pytest.approx(9.0)  # 54c - 45c - 0c
    assert rep["closing_markout_vs_mid_c"] == pytest.approx(8.0)  # 53c - 45c - 0c
    assert rep["pickoff_drift_pp"] == pytest.approx(2.0)  # (0.52 - 0.50) * 100
    assert rep["z_cal_n"] == 1
    assert "KXNBAPTS" in rep["by_series"]
    assert "IMPROVE_1C" in rep["by_queue_mode"]
    txt = format_scorecard(rep)
    assert "KALSHI WIDE-SPREAD NBA PROP SHADOW SCORECARD" in txt


def test_scorecard_no_fill_signing_and_graduation_kill():
    cfg = WideSpreadConfig()
    rows = []
    # Build 7 slates x 9 fills/slate = 63 NO fills at 40c when fv_yes=0.50 (fv_no=0.50),
    # fv_close_t_minus_5m=0.45 (fv_no_close=0.55 -> +15c markout!), fv_t_plus_15m=0.49 (fv_no=+1pp)
    for d in range(7):
        tip_dt = datetime(2026, 11, 10 + d, 23, 0, 0, tzinfo=timezone.utc)
        for i in range(9):
            won = i < 5  # 5/9 ~= 0.556 win rate matching 0.55 close
            rows.append(
                {
                    "opp_id": f"o-{d}-{i}",
                    "ts_utc": tip_dt - timedelta(hours=2),
                    "game_id": f"g-{d}",
                    "tip_ts_utc": tip_dt,
                    "minutes_to_tip": 120.0,
                    "kalshi_ticker": f"KXNBAREB-26NOV{10+d}BOSSAC-SACDSABONIS11-10",
                    "series": "KXNBAREB",
                    "player_id": "DSABONIS11",
                    "line_threshold": 9.5,
                    "best_yes_bid": 45,
                    "best_yes_ask": 60,
                    "yes_bid_size": 20.0,
                    "yes_ask_size": 20.0,
                    "spread_cents": 15,
                    "is_two_sided": True,
                    "last_trade_price": 50,
                    "cum_volume": 100.0,
                    "interval_trade_volume": 10.0,
                    "fv_yes_prob": 0.50,
                    "n_fresh_books": 3,
                    "max_book_age_sec": 10.0,
                    "book_dispersion_pp": 1.0,
                    "constituent_books_json": "[]",
                    "action": ACTION_QUOTE_BOTH,
                    "reason_code": "ok",
                    "sim_yes_bid": 46,
                    "sim_no_bid": 40,
                    "sim_size": 10,
                    "yes_queue_mode": MODE_IMPROVE_1C,
                    "no_queue_mode": MODE_IMPROVE_1C,
                    "ex_ante_net_edge_yes": 4.0,
                    "ex_ante_net_edge_no": 10.0,
                    "sim_inventory_net": -10,
                    "fill_conservative_qty": 10.0,
                    "fill_secondary_qty": 10.0,
                    "fill_side": "no",
                    "fill_price": 40.0,
                    "fill_ts_utc": tip_dt - timedelta(minutes=90),
                    "fv_t_plus_15m": 0.49 - 0.001 * (i % 3),
                    "fv_close_t_minus_5m": 0.45 + 0.005 * (i % 3),
                    "kalshi_mid_close": 0.46,
                    "settled_outcome": "no" if won else "yes",
                    "realized_pnl_cents": 600.0 if won else -400.0,
                }
            )
    df_go = pd.DataFrame(rows)
    rep_go = scorecard(df_go, cfg=cfg)
    assert rep_go["n_slates"] == 7
    assert rep_go["n_fills"] == 63
    assert rep_go["closing_markout_c"] > 10.0
    assert rep_go["pickoff_drift_pp"] > 0.0
    assert rep_go["overall_status"] == "PASS"

    # Early kill test: 3 slates, 25 fills, pickoff drift = -4.0pp
    df_kill = df_go.iloc[:27].copy()
    df_kill["fv_yes_prob"] = 0.50
    df_kill["fv_t_plus_15m"] = 0.54  # YES rose 4pp against NO fill -> -4.0pp drift!
    rep_kill = scorecard(df_kill, cfg=cfg)
    assert rep_kill["pickoff_drift_pp"] == pytest.approx(-4.0)
    assert "early_pickoff_drift" in rep_kill["kill_triggers"]
    assert rep_kill["overall_status"] == "KILL"


def test_tip_ts_prefers_occurrence_datetime():
    """Real Kalshi markets carry tip time in occurrence_datetime; expected_expiration_time
    is ~tip+3h and must NOT be used as the tip (it would shift the T-window by hours)."""
    fake = _FakeKalshiClient()
    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(0.50),
    )
    from ev.kalshi_nba_ticker_mapper import parse_market

    mapped = parse_market(SAMPLE_MARKET)
    market = {
        "occurrence_datetime": TIP.isoformat(),
        "expected_expiration_time": (TIP + timedelta(hours=3)).isoformat(),
        "close_time": (TIP + timedelta(days=1)).isoformat(),
    }
    assert harness._resolve_tip_ts(mapped, market) == TIP


def test_async_columns_captured_and_finalized(tmp_path):
    """A banked fill must later receive fv_t_plus_15m (at fill+15m) and the T-5m close
    snapshots via drain_updates -> finalize_day, or markout/drift can never be scored."""
    fake = _FakeKalshiClient()
    ticker = SAMPLE_MARKET["ticker"]
    fake.markets_by_series["KXNBAPTS"] = [SAMPLE_MARKET]
    fake.books[ticker] = Book(ticker, yes_bids=[(0.44, 20.0)], no_bids=[(0.44, 20.0)])

    fv_state = {"val": 0.50}
    harness = ShadowHarness(
        client=GuardedClient(fake),  # type: ignore[arg-type]
        anchor_provider=lambda m, now: _ok_anchor(fv_state["val"]),
    )

    rows0 = harness.run_once(NOW)
    assert rows0[0].action == ACTION_QUOTE_BOTH

    fake.trades[ticker] = [
        {"trade_id": "a1", "yes_price": 45, "count": 10, "created_time": (NOW + timedelta(seconds=10)).isoformat()},
    ]
    rows1 = harness.run_once(NOW + timedelta(seconds=30))
    r1 = rows1[0]
    assert r1.fill_conservative_qty == pytest.approx(10.0)
    assert harness.drain_updates() == []

    # +16 min after the fill: fv_t_plus_15m owed, captured at the then-current fv
    fv_state["val"] = 0.52
    harness.run_once(NOW + timedelta(minutes=16))
    updates = harness.drain_updates()
    fv15 = [u for u in updates if u["opp_id"] == r1.opp_id]
    assert len(fv15) == 1
    assert fv15[0]["fv_t_plus_15m"] == pytest.approx(0.52)

    # T-4m cycle: closing snapshots owed (fv + Kalshi yes mid of the 44/56 book = 0.50)
    fv_state["val"] = 0.55
    harness.run_once(TIP - timedelta(minutes=4))
    updates += harness.drain_updates()
    close_upd = [u for u in updates if u["opp_id"] == r1.opp_id and "fv_close_t_minus_5m" in u]
    assert len(close_upd) == 1
    assert close_upd[0]["fv_close_t_minus_5m"] == pytest.approx(0.55)
    assert close_upd[0]["kalshi_mid_close"] == pytest.approx(0.50)

    # End to end: parts banked, finalize applies the async updates to the fill row
    et_date = NOW.date().isoformat()
    write_part(rows0, tmp_path, et_date, run_id="t", seq=0)
    write_part(rows1, tmp_path, et_date, run_id="t", seq=1)
    final_path = finalize_day(tmp_path, et_date, updates=updates)
    bank = pd.read_parquet(final_path)
    fill_row = bank[bank["opp_id"] == r1.opp_id].iloc[0]
    assert fill_row["fv_t_plus_15m"] == pytest.approx(0.52)
    assert fill_row["fv_close_t_minus_5m"] == pytest.approx(0.55)
    assert fill_row["kalshi_mid_close"] == pytest.approx(0.50)
