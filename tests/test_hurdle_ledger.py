"""ev.hurdleledger (OPS-12): the three stream readers, the rec write path,
the monthly rollup, the hurdle state machine, and the plain-language report.
All figures in these tests are computed from fixture source records -- the
ledger accepts no hand-entered totals, and neither do its tests."""

import json
from datetime import date

import pytest

from ev.hurdleledger import rec as rec_mod
from ev.hurdleledger import streams
from ev.hurdleledger.config import LedgerConfig, load_config
from ev.hurdleledger.ledger import build_ledger
from ev.hurdleledger.report import render_text


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _settlement(day, pnl, mode="live", signal="s1"):
    return {"ts": f"{day}T15:00:00Z", "day": day, "event": "settlement",
            "mode": mode, "signal_id": signal, "pnl_usd": pnl}


# ---------------------------------------------------------------- config

def test_config_defaults_are_the_adopted_hurdle():
    cfg = LedgerConfig()
    assert cfg.target_usd == 500.0
    assert cfg.consecutive_months == 2
    assert cfg.window_start == "2026-11-16"
    assert cfg.window_days == 90


def test_config_env_override():
    cfg = load_config({"HURDLE_TARGET_USD": "750", "HURDLE_DATA_DIR": "/tmp/hl"})
    assert cfg.target_usd == 750.0
    assert cfg.rec_log.endswith("rec-bets.jsonl")
    assert cfg.rec_log.startswith("/tmp/hl")


# ---------------------------------------------------------------- stream 1: Kalshi

def test_kalshi_counts_live_only_and_reports_paper(tmp_path):
    audit = tmp_path / "ownbettor"
    _write_jsonl(audit / "audit-2026-12-05.jsonl", [
        _settlement("2026-12-05", 300.0),
        _settlement("2026-12-05", -40.0, signal="s2"),
        {"ts": "2026-12-05T10:00:00Z", "day": "2026-12-05", "event": "order_result",
         "mode": "live", "status": "FILLED"},  # not a settlement: ignored
    ])
    _write_jsonl(audit / "audit-2027-01-03.jsonl", [
        _settlement("2027-01-03", 250.0),
        _settlement("2027-01-03", 999.0, mode="dry_run", signal="paper1"),
    ])
    out = streams.read_kalshi(str(audit))
    assert out.monthly_live == {"2026-12": 260.0, "2027-01": 250.0}
    assert out.monthly_paper == {"2027-01": 999.0}
    assert out.live_count == 3 and out.paper_count == 1


def test_kalshi_missing_dir_is_zero_with_note(tmp_path):
    out = streams.read_kalshi(str(tmp_path / "nope"))
    assert out.monthly_live == {} and out.live_count == 0
    assert any("reads $0" in n for n in out.notes)


def test_kalshi_paper_only_explains_itself(tmp_path):
    audit = tmp_path / "ownbettor"
    _write_jsonl(audit / "audit-2026-11-16.jsonl", [
        _settlement("2026-11-16", 12.5, mode="dry_run")])
    out = streams.read_kalshi(str(audit))
    assert out.monthly_live == {}
    assert any("paper-only" in n and "never counts" in n for n in out.notes)


# ---------------------------------------------------------------- stream 3: subscriptions

def test_subscriptions_net_payments_and_refunds(tmp_path):
    path = tmp_path / "subscription-records.jsonl"
    _write_jsonl(path, [
        {"date": "2026-12-01", "amount_usd": 39.0, "kind": "payment"},
        {"date": "2026-12-15", "amount_usd": 19.0, "kind": "payment"},
        {"date": "2026-12-20", "amount_usd": -39.0, "kind": "refund"},
        {"date": "2027-01-02", "amount_usd": 299.0, "kind": "payment"},
    ])
    out = streams.read_subscriptions(str(path))
    assert out.monthly == {"2026-12": 19.0, "2027-01": 299.0}
    assert out.row_count == 4


def test_subscriptions_missing_file_is_zero_with_note(tmp_path):
    out = streams.read_subscriptions(str(tmp_path / "none.jsonl"))
    assert out.monthly == {}
    assert any("pre-launch" in n for n in out.notes)


# ---------------------------------------------------------------- stream 2: recreational log

def test_rec_log_and_settle_round_trip(tmp_path):
    log = str(tmp_path / "rec-bets.jsonl")
    bet = rec_mod.log_bet(log, book="draftkings", description="Tatum over 26.5 points",
                          stake_usd=25.0, price_american=-110)
    assert bet["price_decimal"] == pytest.approx(1.9091, abs=1e-4)
    st = rec_mod.settle_bet(log, bet["id"], "win")
    assert st["pnl_usd"] == 22.73  # 25 * 100/110
    read = streams.read_rec(log)
    assert read.open_bets == []
    assert sum(read.monthly.values()) == 22.73


def test_rec_result_math():
    assert rec_mod.profit_for_result(25.0, 1.9091, "win") == 22.73
    assert rec_mod.profit_for_result(25.0, 1.9091, "loss") == -25.0
    assert rec_mod.profit_for_result(25.0, 1.9091, "push") == 0.0
    assert rec_mod.profit_for_result(25.0, 3.5, "win") == 62.5
    assert rec_mod.american_to_decimal(150) == 2.5
    assert rec_mod.decimal_to_american(2.5) == 150
    assert rec_mod.decimal_to_american(1.5) == -200


def test_rec_settle_guards(tmp_path):
    log = str(tmp_path / "rec-bets.jsonl")
    bet = rec_mod.log_bet(log, book="fanduel", description="x", stake_usd=10.0,
                          price_decimal=2.0)
    with pytest.raises(ValueError):
        rec_mod.settle_bet(log, "rec-00000000-deadbeef", "win")
    rec_mod.settle_bet(log, bet["id"], "loss")
    with pytest.raises(ValueError):
        rec_mod.settle_bet(log, bet["id"], "win")  # already settled
    st = rec_mod.settle_bet(log, bet["id"], "win", supersede=True)
    assert st["pnl_usd"] == 10.0
    read = streams.read_rec(log)
    assert sum(read.monthly.values()) == 10.0  # latest settlement wins
    assert any("superseded" in n for n in read.notes)


def test_rec_settlement_month_is_settlement_month(tmp_path):
    log = tmp_path / "rec-bets.jsonl"
    _write_jsonl(log, [
        {"type": "bet", "id": "rec-20261031-aaaa0001", "ts_placed": "2026-10-31T20:00:00Z",
         "book": "betmgm", "description": "placed in October", "price_american": 100,
         "price_decimal": 2.0, "stake_usd": 50.0, "source": "manual"},
        {"type": "settlement", "bet_id": "rec-20261031-aaaa0001",
         "ts_settled": "2026-11-02T03:00:00Z", "result": "win", "pnl_usd": 50.0},
    ])
    read = streams.read_rec(str(log))
    assert read.monthly == {"2026-11": 50.0}  # settles in November: counts in November


def test_rec_open_bets_listed_not_counted(tmp_path):
    log = str(tmp_path / "rec-bets.jsonl")
    rec_mod.log_bet(log, book="caesars", description="open one", stake_usd=40.0,
                    price_american=-105)
    read = streams.read_rec(log)
    assert read.monthly == {}
    assert len(read.open_bets) == 1


# ---------------------------------------------------------------- rollup + hurdle state

def _cfg(tmp_path, kalshi_rows=None, rec_rows=None, sub_rows=None):
    audit = tmp_path / "ownbettor"
    audit.mkdir(exist_ok=True)
    if kalshi_rows:
        _write_jsonl(audit / "audit-2026-12-05.jsonl", kalshi_rows)
    rec_path = tmp_path / "rec-bets.jsonl"
    if rec_rows:
        _write_jsonl(rec_path, rec_rows)
    sub_path = tmp_path / "subscription-records.jsonl"
    if sub_rows:
        _write_jsonl(sub_path, sub_rows)
    return LedgerConfig(kalshi_audit_dir=str(audit), rec_log_path=str(rec_path),
                        sub_records_path=str(sub_path))


def _rec_pair(bet_id, settle_ts, pnl):
    return [
        {"type": "bet", "id": bet_id, "ts_placed": "2026-12-01T00:00:00Z", "book": "dk",
         "description": "x", "price_american": 100, "price_decimal": 2.0,
         "stake_usd": 10.0, "source": "manual"},
        {"type": "settlement", "bet_id": bet_id, "ts_settled": settle_ts,
         "result": "win", "pnl_usd": pnl},
    ]


def test_hurdle_met_after_two_complete_clearing_months(tmp_path):
    cfg = _cfg(
        tmp_path,
        kalshi_rows=[_settlement("2026-12-05", 300.0), _settlement("2027-01-03", 250.0)],
        rec_rows=_rec_pair("rec-a", "2026-12-20T00:00:00Z", 250.0)
        + _rec_pair("rec-b", "2027-01-20T00:00:00Z", 300.0))
    led = build_ledger(cfg, today=date(2027, 2, 1))
    by_month = {m["month"]: m for m in led["months"]}
    assert by_month["2026-12"]["combined"] == 550.0 and by_month["2026-12"]["clears"]
    assert by_month["2027-01"]["combined"] == 550.0 and by_month["2027-01"]["clears"]
    assert by_month["2026-11"]["partial"] is True
    assert led["hurdle"]["state"] == "met"
    assert led["hurdle"]["met_months"] == ["2026-12", "2027-01"]


def test_hurdle_on_track_when_second_month_still_open(tmp_path):
    cfg = _cfg(tmp_path, kalshi_rows=[_settlement("2026-12-05", 600.0),
                                      _settlement("2027-01-03", 700.0)])
    led = build_ledger(cfg, today=date(2027, 1, 20))
    assert led["hurdle"]["state"] == "on_track"


def test_hurdle_in_progress_with_one_clearing_month(tmp_path):
    cfg = _cfg(tmp_path, kalshi_rows=[_settlement("2026-12-05", 600.0)])
    led = build_ledger(cfg, today=date(2027, 1, 15))
    assert led["hurdle"]["state"] == "in_progress"
    assert led["best_month"]["month"] == "2026-12"


def test_hurdle_window_closed_not_met(tmp_path):
    cfg = _cfg(tmp_path, kalshi_rows=[_settlement("2026-12-05", 100.0)])
    led = build_ledger(cfg, today=date(2027, 3, 1))
    assert led["hurdle"]["state"] == "not_met_window_closed"
    assert led["window_end"] == "2027-02-14"


def test_paper_kalshi_never_counts_toward_hurdle(tmp_path):
    cfg = _cfg(tmp_path, kalshi_rows=[_settlement("2026-12-05", 5000.0, mode="dry_run")])
    led = build_ledger(cfg, today=date(2027, 1, 15))
    dec = [m for m in led["months"] if m["month"] == "2026-12"][0]
    assert dec["kalshi"] == 0.0 and dec["combined"] == 0.0 and not dec["clears"]
    assert led["kalshi_paper"]["monthly"] == {"2026-12": 5000.0}


# ---------------------------------------------------------------- report

def test_report_zero_state_is_plain_and_complete(tmp_path):
    cfg = _cfg(tmp_path)
    text = render_text(build_ledger(cfg, today=date(2026, 11, 16)))
    assert "Hurdle: not met yet" in text
    assert "Kalshi (automated):" in text and "Recreational books:" in text
    assert "Subscriptions:" in text and "Combined:" in text
    assert "pre-launch" in text  # the subscriptions zero explains itself
    assert "February 14, 2027" in text  # the window deadline, in plain words


def test_report_met_state_names_the_months(tmp_path):
    cfg = _cfg(
        tmp_path,
        kalshi_rows=[_settlement("2026-12-05", 300.0), _settlement("2027-01-03", 250.0)],
        rec_rows=_rec_pair("rec-a", "2026-12-20T00:00:00Z", 250.0)
        + _rec_pair("rec-b", "2027-01-20T00:00:00Z", 300.0))
    text = render_text(build_ledger(cfg, today=date(2027, 2, 1)))
    assert "Hurdle: MET" in text
    assert "December 2026 and January 2027" in text
    assert "$550.00" in text
