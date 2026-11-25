"""Tests for ev.maker_settlement and scripts.maker_settlement_scorecard."""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ev.maker_settlement import (
    DictResolver,
    FeeBreakdown,
    NHLSogResolver,
    Outcome,
    STATUS_LOSS,
    STATUS_PENDING,
    STATUS_UNRECONCILED,
    STATUS_VOID,
    STATUS_WIN,
    kalshi_fees,
    norm_name,
    novig_fees,
    prophetx_fees,
    scorecard,
    settle_fill,
    venue_fees,
    z_cal,
)

EXPECTED_LEDGER_KEYS = {
    "fill_id",
    "order_id",
    "venue",
    "lane_id",
    "rung_id",
    "event_id",
    "market_ticker",
    "family",
    "side",
    "window_type",
    "mode",
    "post_ts",
    "fill_ts",
    "fill_price",
    "filled_contracts",
    "notional_cost_usd",
    "consensus_at_post",
    "consensus_at_fill",
    "venue_touch_at_post",
    "venue_spread_at_post",
    "consensus_book_count",
    "consensus_plus_5m",
    "consensus_plus_30m",
    "consensus_close",
    "venue_close_mid",
    "pickoff_drift_pp",
    "gross_closing_markout_c",
    "settlement_status",
    "settlement_ts",
    "outcome_source",
    "settlement_price",
    "stat_value",
    "gross_realized_pnl_usd",
    "venue_fee_assessed_usd",
    "venue_credit_or_rebate_usd",
    "fee_source",
    "fee_model",
    "net_realized_pnl_usd",
    "net_closing_markout_usd",
    "unreconciled_reason",
}


def _make_fill(**overrides) -> dict:
    base = {
        "order_id": "ord-1",
        "client_id": "cli-1",
        "intent_id": "nhl:2026-11-14:MIN@CHI:mats_zuccarello:sog:1.5:yes",
        "game_key": "nhl:2026-11-14:MIN@CHI",
        "game_day": "2026-11-14",
        "sport": "NHL",
        "stat": "sog",
        "player": "Mats Zuccarello",
        "player_norm": "mats zuccarello",
        "line": 1.5,
        "side": "yes",
        "t_post": "2026-11-14T22:00:00Z",
        "t_fill": "2026-11-14T22:05:00Z",
        "t_end": "2026-11-14T23:55:00Z",
        "quote_price": 0.375,
        "contracts_units": 5.0,
        "qty": 5.0,
        "fill_qty_units": 4.44,
        "p_fill": 0.375,
        "novig_bid_at_post": 0.37,
        "novig_ask_at_post": 0.41,
        "novig_spread_at_post": 0.04,
        "p_cons_at_post": 0.42,
        "p_blend_at_post": 0.41,
        "p_cons_close": 0.45,
        "p_cons_fill_plus_5m": 0.43,
        "p_cons_fill_plus_30m": 0.44,
        "p_novig_close": None,
        "in_news_window": False,
        "terminal_status": "PARTIAL",
        "commence": "2026-11-15T00:00:00Z",
    }
    base.update(overrides)
    return base


def _write_boxscores_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "skater_boxscores_master.csv"
    lines = ["date,team_abbr,name,sog,game_id"]
    for r in rows:
        lines.append(f"{r['date']},{r['team_abbr']},{r['name']},{r['sog']},{r['game_id']}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_norm_name_diacritics_and_punctuation():
    assert norm_name("Jonathan Lekkerimäki") == "jonathan lekkerimaki"
    assert norm_name("jonathan lekkerimaki") == "jonathan lekkerimaki"
    assert norm_name("Jonathan  Lekkerimäki!") == norm_name("jonathan lekkerimaki")
    assert norm_name("Tim Stützle") == "tim stutzle"


def test_kalshi_fee_math():
    fb1 = kalshi_fees(0.50, 100)
    assert isinstance(fb1, FeeBreakdown)
    assert fb1.fee_usd == 0.44
    assert fb1.credit_usd == 0.0
    assert fb1.source == "SIMULATED"
    assert fb1.model == "kalshi_maker_single"

    assert kalshi_fees(0.50, 1).fee_usd == 0.01
    assert kalshi_fees(0.20, 100).fee_usd == 0.28

    fb_combo = kalshi_fees(0.50, 100, is_combo=True)
    assert fb_combo.fee_usd == 0.88
    assert fb_combo.model == "kalshi_maker_combo"


def test_novig_fees():
    pre = novig_fees(0.50, 100, "PREGAME")
    assert pre.fee_usd == 0.0
    assert pre.credit_usd == 0.0
    assert pre.source == "SIMULATED"
    assert pre.model == "novig_maker"

    live = novig_fees(0.535, 10, "LIVE")
    assert live.fee_usd == 0.0
    assert live.credit_usd == pytest.approx(0.25 * 0.06 * 0.535 * 0.465 * 10, rel=1e-6)
    assert live.credit_usd == pytest.approx(0.03731625, abs=1e-6)


def test_prophetx_fees_and_venue_dispatch():
    win = prophetx_fees(0.40, 10, 1.0)
    assert win.fee_usd == pytest.approx(0.12)
    assert win.credit_usd == 0.0
    assert win.model == "prophetx_net_gains"
    assert "UNVERIFIED" in win.note

    loss = prophetx_fees(0.40, 10, 0.0)
    assert loss.fee_usd == 0.0
    assert "UNVERIFIED" in loss.note

    unsettled = prophetx_fees(0.40, 10, None)
    assert unsettled.fee_usd == 0.0
    assert "unsettled; fee not assessable" in unsettled.note

    assert venue_fees("Kalshi", 0.50, 100).fee_usd == 0.44
    assert venue_fees("novig", 0.535, 10, window_type="LIVE").credit_usd == pytest.approx(0.03731625)
    assert venue_fees("ProphetX", 0.40, 10, settlement_price=1.0).fee_usd == pytest.approx(0.12)
    with pytest.raises(ValueError):
        venue_fees("unknown_book", 0.50, 10)


def test_nhl_sog_void_push(tmp_path: Path):
    csv_path = _write_boxscores_csv(
        tmp_path,
        [{"date": "2026-11-14", "team_abbr": "MIN", "name": "Mats Zuccarello", "sog": 2, "game_id": "2026020100"}],
    )
    resolver = NHLSogResolver(csv_path)
    fill = _make_fill(line=2.0, p_fill=0.45, fill_qty_units=10.0)
    rec = settle_fill(fill, resolver=resolver, venue="novig")
    assert rec["settlement_status"] == STATUS_VOID
    assert rec["settlement_price"] == 0.45
    assert rec["gross_realized_pnl_usd"] == 0.0
    assert rec["net_realized_pnl_usd"] == 0.0


def test_nhl_sog_unreconciled_paths(tmp_path: Path):
    csv_path = _write_boxscores_csv(
        tmp_path,
        [
            {"date": "2026-11-12", "team_abbr": "MIN", "name": "Kirill Kaprizov", "sog": 4, "game_id": "1"},
            {"date": "2026-11-14", "team_abbr": "VAN", "name": "Dup Player", "sog": 2, "game_id": "2"},
            {"date": "2026-11-14", "team_abbr": "CGY", "name": "Dup Player", "sog": 3, "game_id": "3"},
        ],
    )
    resolver = NHLSogResolver(csv_path)

    missing_fill = _make_fill(player="Jonathan Lekkerimäki", player_norm="jonathan lekkerimaki")
    rec = settle_fill(missing_fill, resolver=resolver, venue="novig")
    assert rec["settlement_status"] == STATUS_UNRECONCILED
    assert rec["settlement_price"] is None
    assert rec["net_realized_pnl_usd"] is None
    assert rec["unreconciled_reason"]
    assert "2026-11-14" in rec["unreconciled_reason"]
    assert "jonathan lekkerimaki" in rec["unreconciled_reason"]

    ambiguous_fill = _make_fill(player="Dup Player", player_norm=None)
    amb_rec = settle_fill(ambiguous_fill, resolver=resolver)
    assert amb_rec["settlement_status"] == STATUS_UNRECONCILED
    assert "ambiguous" in amb_rec["unreconciled_reason"]

    bad_family_fill = _make_fill(sport="NBA", stat="pts")
    fam_rec = settle_fill(bad_family_fill, resolver=resolver)
    assert fam_rec["settlement_status"] == STATUS_UNRECONCILED
    assert "unsupported family" in fam_rec["unreconciled_reason"]


def test_settle_fill_end_to_end_zuccarello_and_schema(tmp_path: Path):
    csv_path = _write_boxscores_csv(
        tmp_path,
        [
            {"date": "2026-11-14", "team_abbr": "MIN", "name": "Mats Zuccarello", "sog": 5, "game_id": "10"},
            {"date": "2026-11-14", "team_abbr": "VAN", "name": "Jonathan Lekkerimäki", "sog": 1, "game_id": "11"},
        ],
    )
    resolver = NHLSogResolver(csv_path)
    fill = _make_fill(p_fill=0.375, fill_qty_units=4.44, line=1.5, side="yes")
    rec = settle_fill(
        fill,
        resolver=resolver,
        venue="novig",
        lane_id="lane_a",
        rung_id="r1",
        mode="PAPER",
    )

    assert set(rec.keys()) == EXPECTED_LEDGER_KEYS
    assert rec["settlement_status"] == STATUS_WIN
    assert rec["settlement_price"] == 1.0
    assert rec["stat_value"] == 5.0
    assert rec["gross_realized_pnl_usd"] == pytest.approx((1.0 - 0.375) * 4.44)
    assert rec["net_realized_pnl_usd"] == pytest.approx((1.0 - 0.375) * 4.44)
    assert rec["family"] == "nhl:sog"
    assert rec["venue_touch_at_post"] == {"bid": 0.37, "ask": 0.41}
    assert rec["pickoff_drift_pp"] == pytest.approx((0.42 - 0.45) * 100.0)
    assert rec["gross_closing_markout_c"] == pytest.approx((0.45 - 0.375) * 100.0)
    assert rec["unreconciled_reason"] is None

    # Under win via diacritic name lookup
    under_fill = _make_fill(
        order_id="ord-2",
        player="Jonathan Lekkerimäki",
        player_norm=None,
        line=1.5,
        side="no",
        p_fill=0.60,
        fill_qty_units=10.0,
    )
    under_rec = settle_fill(under_fill, resolver=resolver, venue="prophetx", fee_source_override="ACTUAL")
    assert under_rec["settlement_status"] == STATUS_WIN
    assert under_rec["fee_source"] == "ACTUAL"
    assert under_rec["venue_fee_assessed_usd"] == pytest.approx(0.02 * 0.40 * 10.0)

    # Resolver=None -> PENDING
    pending_rec = settle_fill(fill, resolver=None, venue="prophetx")
    assert pending_rec["settlement_status"] == STATUS_PENDING
    assert pending_rec["settlement_price"] is None
    assert pending_rec["venue_fee_assessed_usd"] == 0.0

    # DictResolver
    dres = DictResolver({"ord-99": Outcome(0.0, STATUS_LOSS, "nba_box", "pts=12", 12.0)})
    loss_rec = settle_fill(_make_fill(order_id="ord-99"), resolver=dres)
    assert loss_rec["settlement_status"] == STATUS_LOSS
    missing_drec = settle_fill(_make_fill(order_id="ord-missing"), resolver=dres)
    assert missing_drec["settlement_status"] == STATUS_UNRECONCILED
    assert missing_drec["unreconciled_reason"] == "no outcome supplied"


def test_z_cal_toy_sets():
    z0, n0 = z_cal([])
    assert z0 is None and n0 == 0

    z_zero, n_zero = z_cal(
        [
            {"settlement_price": 1.0, "consensus_close": 0.5},
            {"settlement_price": 0.0, "consensus_close": 0.5},
        ]
    )
    assert z_zero == pytest.approx(0.0)
    assert n_zero == 2

    z_four, n_four = z_cal(
        [{"settlement_price": 1.0, "consensus_close": 0.5} for _ in range(4)]
    )
    assert z_four == pytest.approx(2.0)
    assert n_four == 4


def test_scorecard_blocks_and_freeze(tmp_path: Path):
    csv_path = _write_boxscores_csv(
        tmp_path,
        [{"date": "2026-11-14", "team_abbr": "MIN", "name": "Mats Zuccarello", "sog": 5, "game_id": "10"}],
    )
    resolver = NHLSogResolver(csv_path)
    rec = settle_fill(_make_fill(), resolver=resolver, venue="novig")

    report = scorecard([rec])
    assert "BLOCK A — PRICE QUALITY (MARKOUT)" in report
    assert "BLOCK B — CASH REALITY (SETTLEMENT)" in report
    assert "Model-expected value assuming unbiased closing consensus; NOT cash profit." in report
    assert "Winner's-curse gap" not in report
    assert "FREEZE:" not in report

    report_wc = scorecard(
        [rec],
        unfilled_quotes=[{"p_cons_at_post": 0.44, "p_cons_close": 0.44}],
    )
    assert "Winner's-curse gap" in report_wc

    # 100 all-loss fills at consensus_close=0.5 -> Z_cal = -10.0 < -2.5 -> FREEZE line
    loss_records = [
        settle_fill(
            _make_fill(order_id=f"ord-{i}", p_cons_close=0.5, p_fill=0.5, fill_qty_units=1.0),
            resolver=DictResolver({f"ord-{i}": Outcome(0.0, STATUS_LOSS, "test", "loss", 0.0)}),
        )
        for i in range(100)
    ]
    freeze_report = scorecard(loss_records)
    assert "FREEZE: Z_cal < -2.5 on >=100 settled fills — audit the consensus anchor." in freeze_report


def test_cli_scorecard_json_and_chunk_dir(tmp_path: Path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "maker_settlement_scorecard.py"
    csv_path = _write_boxscores_csv(
        tmp_path,
        [{"date": "2026-11-14", "team_abbr": "MIN", "name": "Mats Zuccarello", "sog": 5, "game_id": "10"}],
    )
    fills_json = tmp_path / "fills.json"
    fills_json.write_text(
        json.dumps(
            [
                _make_fill(order_id="ord-win", p_fill=0.375, fill_qty_units=4.44),
                _make_fill(order_id="ord-miss", player="Absent Skater", player_norm="absent skater"),
            ]
        ),
        encoding="utf-8",
    )
    out_jsonl = tmp_path / "ledger.jsonl"
    summary_json = tmp_path / "summary.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "--fills",
            str(fills_json),
            "--boxscores",
            str(csv_path),
            "--venue",
            "novig",
            "--out",
            str(out_jsonl),
            "--summary-out",
            str(summary_json),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "BLOCK A" in proc.stdout
    assert "BLOCK B" in proc.stdout

    ledger_lines = [json.loads(line) for line in out_jsonl.read_text(encoding="utf-8").splitlines()]
    assert len(ledger_lines) == 2

    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["n_fills"] == 2
    assert summary["n_settled"] == 1
    assert summary["n_win"] == 1
    assert summary["n_loss"] == 0
    assert summary["n_void"] == 0
    assert summary["n_unreconciled"] == 1
    assert summary["realized_pnl_usd"] == round((1.0 - 0.375) * 4.44, 4)
    assert summary["unreconciled_order_ids"] == ["ord-miss"]

    # Directory of .jsonl.gz chunks (last-wins per order_id) without --boxscores
    chunks_dir = tmp_path / "chunks" / "2026-11-14"
    chunks_dir.mkdir(parents=True)
    chunk_rows = [
        {"hdr": {"schema": 1}},
        {"t": "quote_state", "r": _make_fill(order_id="ord-chunk", fill_qty_units=0.0)},
        {"t": "quote_state", "r": _make_fill(order_id="ord-chunk", fill_qty_units=5.0, p_fill=0.5)},
        {"t": "quote_state", "r": _make_fill(order_id="ord-unfilled", fill_qty_units=0.0)},
        {"t": "run", "r": {"ok": True}},
    ]
    with gzip.open(chunks_dir / "run1_abcd1234.jsonl.gz", "wt", encoding="ascii") as gz:
        for row in chunk_rows:
            gz.write(json.dumps(row) + "\n")

    no_box_ledger = tmp_path / "no_box.jsonl"
    no_box_summary = tmp_path / "no_box_summary.json"
    subprocess.run(
        [
            sys.executable,
            str(script),
            "--fills",
            str(tmp_path / "chunks"),
            "--out",
            str(no_box_ledger),
            "--summary-out",
            str(no_box_summary),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    nb_rows = [json.loads(line) for line in no_box_ledger.read_text(encoding="utf-8").splitlines()]
    assert len(nb_rows) == 1
    assert nb_rows[0]["order_id"] == "ord-chunk"
    assert nb_rows[0]["settlement_status"] == STATUS_UNRECONCILED
    assert nb_rows[0]["unreconciled_reason"] == "no resolver"
    nb_summary = json.loads(no_box_summary.read_text(encoding="utf-8"))
    assert nb_summary["n_unreconciled"] == 1
    assert nb_summary["unreconciled_order_ids"] == ["ord-chunk"]
