"""ev.novig.liability + ev.novig.gate wiring into run_pass: caps and the CLV slice gate only ever ADD restrictions.
Fakes only, no network, no orders."""
import dataclasses
import json
import os
import re
from datetime import timedelta
from pathlib import Path

import pytest

from ev.novig import clv_gate as cg
from ev.novig import exec as nx
from ev.novig import gate as ng
from ev.novig import liability as lb
from ev.novig import paper_orders as po
from ev.novig.client import Book
from ev.novig.map import event_index, map_prop_market
from ev.props_config import props_config
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from tests.test_novig_paper_orders import FakeTC, PLEG  # noqa: F401  (paper-sandbox fake client + the paper leg)

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "ev", "novig_markets.json")))
EVENTS = event_index(FX["events"])
M = next(m for m in FX["markets"] if m["marketType"] == "RECEIVING_YARDS")
LEG = map_prop_market(M, EVENTS)
T0 = LEG.start_utc - timedelta(hours=5)
LC = lb.LiabilityConfig()


# ------------------------------------------------------------------ liability: pure function
def test_defaults_are_conservative_zero_fee_and_env_overridable():
    assert LC.fee_rate == 0.0 and LC.required_fee_charged == "WHEN_LIVE"
    assert (LC.per_bet_cap, LC.per_player_cap, LC.per_game_cap) == (10.0, 15.0, 30.0)
    assert LC.per_game_cap < props_config(env={}).per_game_cap                       # tighter than the Kalshi props shadow cap
    c = lb.load_liability_config({"NOVIG_LIAB_BANKROLL": "500", "NOVIG_LIAB_FEE_RATE": "0.02", "NOVIG_LIAB_PER_GAME_CAP_PCT": "0.1"})
    assert c.bankroll == 500 and c.fee_rate == 0.02 and c.per_game_cap == 50.0
    assert lb.load_liability_config({}) == LC


def test_plan_within_every_cap_is_untouched():
    r = lb.check_liability(LC, contracts=10, price=0.52, game_key="g", player_norm="p", exposures=[])
    assert r.allowed and r.contracts == 10 and r.stake == pytest.approx(5.2) and r.binding is None


def test_per_bet_cap_shrinks_the_plan():
    r = lb.check_liability(LC, contracts=40, price=0.50, game_key="g", player_norm="p", exposures=[])
    assert r.allowed and r.contracts == 20 and r.stake == pytest.approx(10.0) and r.binding == "bet"


def test_per_player_cap_counts_other_stats_of_the_same_player_only():
    held = [lb.Exposure("g", "p", "pass_yds", 9.0), lb.Exposure("g", "other", "rec_yds", 9.0)]
    r = lb.check_liability(LC, contracts=20, price=0.50, game_key="g", player_norm="p", exposures=held)
    assert r.allowed and r.binding == "player" and r.contracts == 12                  # 15 - 9 = 6 dollars of room
    r2 = lb.check_liability(LC, contracts=20, price=0.50, game_key="g", player_norm="fresh", exposures=held)
    assert r2.contracts == 20 and r2.binding is None


def test_per_game_cap_counts_every_player_in_the_game_and_ignores_other_games():
    held = [lb.Exposure("g", f"p{i}", "rec_yds", 8.5) for i in range(3)] + [lb.Exposure("other", "p", "rec_yds", 99.0)]
    r = lb.check_liability(LC, contracts=20, price=0.50, game_key="g", player_norm="new", exposures=held)
    assert r.allowed and r.binding == "game" and r.contracts == 9                     # 30 - 25.5 = $4.50 of room
    full = [lb.Exposure("g", f"p{i}", "rec_yds", 10.0) for i in range(3)]
    r = lb.check_liability(LC, contracts=20, price=0.50, game_key="g", player_norm="new", exposures=full)
    assert not r.allowed and r.reason == "REJ_LIABILITY_CAP" and r.contracts == 0


def test_a_shrunk_order_below_the_minimum_stake_is_rejected_not_placed():
    held = [lb.Exposure("g", "p", "rec_yds", 14.0)]                                    # $1 of player room < min_stake $2
    r = lb.check_liability(LC, contracts=20, price=0.50, game_key="g", player_norm="p", exposures=held)
    assert not r.allowed and r.reason == "REJ_LIABILITY_MIN"


def test_the_zero_fee_assumption_is_config_and_a_fee_charged_market_is_rejected():
    r = lb.check_liability(LC, contracts=10, price=0.5, game_key="g", player_norm="p", exposures=[], fee_charged="ALWAYS")
    assert not r.allowed and r.reason == "REJ_LIABILITY_FEE"
    fee = dataclasses.replace(LC, fee_rate=0.10)                                      # a configured fee raises liability per contract
    r = lb.check_liability(fee, contracts=40, price=0.50, game_key="g", player_norm="p", exposures=[])
    assert r.contracts == 18 and r.stake == pytest.approx(18 * 0.55)


def test_caps_never_enlarge_a_plan():
    for n in (1, 3, 7, 25, 90):
        r = lb.check_liability(LC, contracts=n, price=0.4, game_key="g", player_norm="p", exposures=[])
        assert r.contracts <= n


# ------------------------------------------------------------------ gate wiring in run_pass
class FakeNC:
    def __init__(self, over_bid=0.50, under_bid=0.48, qty=100_000):
        self.b = {LEG.over_id: [{"price": str(over_bid), "qty": qty}], LEG.under_id: [{"price": str(under_bid), "qty": qty}]}

    def get_book(self, market_id, depth=10):
        return Book(market_id, self.b, 1, T0.timestamp())


def point(p_over=0.55):
    return {"player_norm": LEG.player_norm, "stat": LEG.stat, "point": LEG.strike, "p_over": p_over, "n_books": 4,
            "disp": 0.01, "books": list("abcd"), "n_onesided": 0, "n_dfs": 0, "team_hints": [],
            "book_fair": {b: p_over for b in "abcd"}}


def make_gate(tmp_path, *, open_slice=False, liab=None, gcfg=None, enabled=True):
    gcfg = gcfg or cg.GateConfig(enabled=enabled, verdict_file=str(tmp_path / "v.json"))
    if open_slice:
        cg.write_verdicts(gcfg.verdict_file, {"computed_at": T0.isoformat(), "slices": {
            cg.slice_key("nfl", LEG.stat): {"status": "open", "open": True, "n": 300, "mean_clv_pct": 4.0, "ci_lo": 1.0}}})
    return ng.NovigExecGate(gcfg, liab or LC, cg.SliceGate.load(gcfg, T0))


def run(tmp_path, *, gate=None, tc=None, ledger=None, sub="", leg=LEG):
    cfg = props_config(env={})
    ledger = ledger or Ledger(str(tmp_path / f"l{sub}.sqlite"))
    risk, prisk = RiskGate(cfg, ledger, nx.MODE), po.PaperRiskGate(cfg, ledger, nx.PAPER_MODE)
    pv = po.NovigPaperVenue(tc, {po.leg_key(LEG): PLEG}, sleep=lambda s: None) if tc is not None else None
    s = nx.run_pass(snap_ts=T0.isoformat(), cands=[(leg, point(), 300.0)], cfg=cfg, nc=FakeNC(), ledger=ledger, risk=risk,
                    clock=lambda: T0 + timedelta(seconds=0.5), pace_s=0, paper_venue=pv, paper_risk=prisk if pv else None,
                    gate=gate)
    return s, ledger


def decision_inputs(ledger):
    with ledger._lock:
        return [json.loads(r[0]) for r in ledger._c.execute("SELECT inputs_json FROM decisions")]


def orders(ledger, mode):
    with ledger._lock:
        return [tuple(r) for r in ledger._c.execute("SELECT contracts, fill_count, filled_cost FROM orders WHERE mode=?", (mode,))]


def test_gate_off_is_the_unchanged_path(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, tc=tc)
    assert s["hist"].get("EXECUTED") == 1 and len(tc.placed) == 1 and "gate" not in s
    assert "exec_gate" not in decision_inputs(ledger)[0]
    assert ng.load_exec_gate({}) is None and ng.load_exec_gate({"NOVIG_GATE_ENABLED": "0"}) is None


def test_env_flag_builds_the_gate_and_without_a_verdict_file_every_slice_is_closed(tmp_path):
    g = ng.load_exec_gate({"NOVIG_GATE_ENABLED": "1", "NOVIG_GATE_VERDICT_FILE": str(tmp_path / "missing.json")})
    assert g is not None and g.slices.stale and not g.slices.is_open("nfl", "rec_yds")


def test_closed_slice_is_annotated_keeps_its_shadow_fill_and_skips_the_paper_mirror(tmp_path):
    tc = FakeTC()
    gate = make_gate(tmp_path)                                                         # no verdict file -> closed
    s, ledger = run(tmp_path, gate=gate, tc=tc)
    assert s["hist"].get("EXECUTED") == 1                                              # evidence-collecting shadow fill survives
    assert orders(ledger, nx.MODE) and tc.placed == [] and orders(ledger, nx.PAPER_MODE) == []
    note = [i for i in decision_inputs(ledger) if "exec_gate" in i][0]["exec_gate"]
    assert note["open"] is False and note["live_eligible"] is False and note["slice"] == "nfl|rec_yds"
    assert s["gate"]["closed_annotated"] == 1 and s["gate"]["paper_skipped_closed"] == 1


def test_closed_slice_paper_mirror_can_be_left_on_by_config(tmp_path):
    tc = FakeTC()
    gate = make_gate(tmp_path, gcfg=cg.GateConfig(enabled=True, verdict_file=str(tmp_path / "v.json"), skip_closed_paper=False))
    s, ledger = run(tmp_path, gate=gate, tc=tc)
    assert len(tc.placed) == 1 and s["gate"]["paper_skipped_closed"] == 0
    assert [i for i in decision_inputs(ledger) if "exec_gate" in i][0]["exec_gate"]["live_eligible"] is False


def test_open_slice_is_marked_live_eligible_and_still_only_uses_the_paper_path(tmp_path):
    tc = FakeTC()
    s, ledger = run(tmp_path, gate=make_gate(tmp_path, open_slice=True), tc=tc)
    assert len(tc.placed) == 1                                                         # the existing paper sandbox, nothing else
    note = [i for i in decision_inputs(ledger) if "exec_gate" in i][0]["exec_gate"]
    assert note["open"] is True and note["live_eligible"] is True
    with ledger._lock:
        modes = sorted(r[0] for r in ledger._c.execute("SELECT mode FROM orders"))
    assert modes == [nx.MODE, nx.PAPER_MODE]


def test_liability_cap_shrinks_the_shadow_plan_and_the_paper_order_follows_it(tmp_path):
    tc = FakeTC()
    base, _ = run(tmp_path, sub="base")
    cap = dataclasses.replace(LC, per_bet_cap_pct=0.003)                              # $3 per bet
    gate = make_gate(tmp_path, open_slice=True, liab=cap)
    s, ledger = run(tmp_path, gate=gate, tc=tc, sub="cap")
    (contracts, filled, cost), = orders(ledger, nx.MODE)
    assert cost <= 3.0 + 1e-6 and s["gate"]["liability_shrunk"] == 1
    assert base["executed"][0]["units"] > contracts                                    # strictly smaller than the ungated plan
    assert tc.placed[0]["qty"] == contracts * nx.UNIT


def test_liability_cap_rejection_and_fee_flag_rejection_are_recorded_and_send_nothing(tmp_path):
    tc = FakeTC()
    cap = dataclasses.replace(LC, per_bet_cap_pct=0.0005)                             # $0.50 < one contract
    s, ledger = run(tmp_path, gate=make_gate(tmp_path, open_slice=True, liab=cap), tc=tc, sub="a")
    assert s["hist"].get("REJ_LIABILITY_CAP") == 1 and not s["executed"] and tc.placed == [] and orders(ledger, nx.MODE) == []
    # a market that charges its fee pre-game breaks the 0%-fee assumption: rejected, nothing sent
    tc2 = FakeTC()
    s2, ledger2 = run(tmp_path, gate=make_gate(tmp_path, open_slice=True), tc=tc2, sub="b",
                      leg=dataclasses.replace(LEG, fee_charged="ALWAYS"))
    assert s2["hist"].get("REJ_LIABILITY_FEE") == 1 and tc2.placed == [] and orders(ledger2, nx.MODE) == []


def test_exposure_already_in_the_ledger_counts_toward_the_game_cap(tmp_path):
    ledger = Ledger(str(tmp_path / "e.sqlite"))
    s0, _ = run(tmp_path, ledger=ledger, gate=make_gate(tmp_path, open_slice=True), sub="e")     # first fill, caps loose
    held = lb.ledger_exposures(ledger, s0["day"], nx.MODE)
    assert len(held) == 1 and held[0].player_norm == LEG.player_norm and held[0].stat == LEG.stat and held[0].cost > 0
    assert lb.ledger_exposures(ledger, s0["day"], nx.PAPER_MODE) == [] and lb.ledger_exposures(ledger, "1999-01-01", nx.MODE) == []


# ------------------------------------------------------------------ safety: no order path was added
def test_new_modules_have_no_order_client_network_or_credential_code():
    root = Path(__file__).resolve().parent.parent / "ev" / "novig"
    banned = re.compile(r"place_order|requests|urllib|httpx|http\.client|socket|api_key|secret|password|private_key|"
                        r"signing|trading_client|NovigClient|os\.environ\[|KEXEC_RFQ", re.I)
    for name in ("clv_gate.py", "liability.py", "gate.py"):
        src = (root / name).read_text(encoding="ascii")                                # also proves ASCII-only
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        # docstrings may describe what the module does NOT do; strip them before scanning
        code = re.sub(r'"""[\s\S]*?"""', "", code)
        assert not banned.search(code), (name, banned.search(code).group(0))
