"""ev.ownbettor risk: kill switch persistence, daily caps, loss stop, error breaker."""
from ev.ownbettor.audit import AuditLog
from ev.ownbettor.config import BettorConfig
from ev.ownbettor.risk import RiskGate

DAY = "2026-11-16"


def make(tmp_path, **kw):
    cfg = BettorConfig(audit_dir=str(tmp_path), **kw)
    audit = AuditLog(str(tmp_path))
    return cfg, audit, RiskGate(cfg, audit)


def placed(audit: AuditLog, cost: float = 10.0):
    audit.append("order_result", DAY, signal_id="sx", status="FILLED", filled_cost=cost)


def test_kill_switch_persists_across_instances(tmp_path):
    _cfg, _audit, risk = make(tmp_path)
    risk.trip("test trip")
    _cfg2, audit2, risk2 = make(tmp_path)      # a fresh process over the same audit dir
    assert risk2.killed and risk2.kill_reason == "test trip"
    assert risk2.check(DAY, 5.0) == (False, "REJ_KILLED")


def test_manual_reset_clears_the_kill(tmp_path):
    _cfg, _audit, risk = make(tmp_path)
    risk.trip("x")
    risk.reset()
    assert not risk.killed
    assert risk.check(DAY, 5.0) == (True, "")


def test_daily_bet_cap_rejects_without_tripping(tmp_path):
    _cfg, audit, risk = make(tmp_path, daily_bet_cap=2)
    placed(audit)
    placed(audit)
    assert risk.check(DAY, 5.0) == (False, "REJ_DAILY_BET_CAP")
    assert not risk.killed


def test_daily_spend_cap_rejects(tmp_path):
    _cfg, audit, risk = make(tmp_path, daily_spend_cap_usd=100.0)
    placed(audit, cost=80.0)
    assert risk.check(DAY, 30.0) == (False, "REJ_DAILY_SPEND_CAP")
    assert risk.check(DAY, 20.0) == (True, "")


def test_loss_stop_trips_on_settled_losses(tmp_path):
    _cfg, audit, risk = make(tmp_path)          # stop = $50 shipped
    audit.append("settlement", DAY, signal_id="s1", pnl_usd=-30.0)
    audit.append("settlement", DAY, signal_id="s2", pnl_usd=-25.0)
    assert risk.check_loss_stop(DAY) is True
    assert risk.killed
    assert risk.check(DAY, 5.0) == (False, "REJ_KILLED")


def test_loss_stop_also_bites_at_check_time(tmp_path):
    _cfg, audit, risk = make(tmp_path)
    audit.append("settlement", DAY, signal_id="s1", pnl_usd=-60.0)
    assert risk.check(DAY, 5.0) == (False, "REJ_LOSS_STOP")
    assert risk.killed


def test_error_breaker_trips_after_max_consecutive(tmp_path):
    _cfg, _audit, risk = make(tmp_path, max_consecutive_errors=3)
    risk.note_submit_error()
    risk.note_submit_error()
    assert not risk.killed
    risk.note_submit_error()
    assert risk.killed


def test_auth_failure_trips_immediately(tmp_path):
    _cfg, _audit, risk = make(tmp_path)
    risk.note_submit_error(auth=True)
    assert risk.killed and "auth" in (risk.kill_reason or "")


def test_success_resets_the_error_counter(tmp_path):
    _cfg, audit, risk = make(tmp_path, max_consecutive_errors=3)
    risk.note_submit_error()
    risk.note_submit_error()
    risk.note_submit_ok()
    assert audit.consecutive_errors == 0
    risk.note_submit_error()
    risk.note_submit_error()
    assert not risk.killed
