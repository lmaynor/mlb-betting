"""ev.props_exec: pass logic with fakes (no network). Reuses the MLB executor's gates/sizing/risk/ledger."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from ev import props_exec as px
from ev.props_config import props_config
from mlb.execution.kalshi.book import Book
from mlb.execution.kalshi.ledger import Ledger
from mlb.execution.kalshi.risk import RiskGate
from mlb.execution.kalshi.venue import Ack, PaperVenue

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
RANGES = json.dumps([[0.0, 1.0, 0.01]])


def row(**kw):
    r = {"ts": T0.isoformat(), "ticker": "KXNFLRECYDS-26OCT04INDWAS-WASKALLEN31-50", "game_key": "nfl:2026-10-04:IND@WAS",
         "stat": "rec_yds", "player": "Terry McLaurin", "player_norm": "terry mclaurin", "team": "WAS", "strike": 49.5,
         "commence": (T0 + timedelta(hours=5)).isoformat(), "mins_to_kick": 300.0, "p_over": 0.60, "n_books": 4,
         "disp": 0.01, "ev_yes": 0.08, "ev_no": -0.5, "ask_yes": 0.50, "ask_no": 0.52, "ranges": RANGES,
         "exchange_index": 0, "fee_mult": 1.0}
    r.update(kw)
    return r


class FakeData:
    """Order book: YES ask ladder 0.52 x 200 (a NO bid at 0.48), YES bids at 0.50."""
    def __init__(self, t=T0):
        self.t, self.calls = t, 0

    def get_orderbook(self, ticker):
        self.calls += 1
        return Book(ticker, yes_bids=[(0.50, 300.0)], no_bids=[(0.48, 200.0)], fetched_at=self.t.timestamp())


def make(tmp_path, **cfgkw):
    cfg = props_config(env={}, **cfgkw)
    ledger = Ledger(str(tmp_path / "l.sqlite"))
    data = FakeData()
    return cfg, ledger, data, RiskGate(cfg, ledger, "shadow"), PaperVenue(data.get_orderbook)


def go(tmp_path, rows, **kw):
    cfg, ledger, data, risk, paper = make(tmp_path)
    latest = {"ts": T0.isoformat(), "rows": rows}
    clock = lambda: T0 + timedelta(seconds=0.5)                              # book fetched 0.5s ago
    s = px.run_pass(latest=latest, cfg=cfg, data_client=data, paper=paper, ledger=ledger, risk=risk, clock=clock, **kw)
    return s, ledger, cfg


def test_plan_is_paper_filled_against_the_real_ladder_and_logged(tmp_path):
    s, ledger, cfg = go(tmp_path, [row(ask_yes=0.52, ev_yes=0.08)])
    assert s["hist"].get("EXECUTED") == 1, s
    e = s["executed"][0]
    assert e["side"] == "yes" and e["ask"] == pytest.approx(0.52) and e["filled"] > 0
    assert e["filled"] * 0.52 <= cfg.per_bet_cap + 1e-6                         # Kelly stake respects the per-bet cap
    day = px.et_day(T0)
    assert ledger.orders_today(day, "shadow") == 1 and ledger.deployed_today(day, "shadow") > 0


def test_second_strike_on_same_player_stat_is_held_not_stacked(tmp_path):
    cfg, ledger, data, risk, paper = make(tmp_path)
    clock = lambda: T0 + timedelta(seconds=0.5)
    rows = [row(ev_yes=0.09), row(ticker="KXNFLRECYDS-26OCT04INDWAS-WASKALLEN31-60", strike=59.5, ev_yes=0.08)]
    s = px.run_pass(latest={"ts": T0.isoformat(), "rows": rows}, cfg=cfg, data_client=data, paper=paper,
                    ledger=ledger, risk=risk, clock=clock)
    assert s["hist"].get("EXECUTED") == 1 and s["hist"].get("REJ_GROUP_HELD") == 1


def test_rerun_does_not_double_order(tmp_path):
    cfg, ledger, data, risk, paper = make(tmp_path)
    clock = lambda: T0 + timedelta(seconds=0.5)
    latest = {"ts": T0.isoformat(), "rows": [row()]}
    px.run_pass(latest=latest, cfg=cfg, data_client=data, paper=paper, ledger=ledger, risk=risk, clock=clock)
    s2 = px.run_pass(latest=latest, cfg=cfg, data_client=data, paper=paper, ledger=ledger, risk=risk, clock=clock)
    assert s2["hist"].get("REJ_GROUP_HELD") == 1 and ledger.orders_today(px.et_day(T0), "shadow") == 1


def test_stale_consensus_is_rejected_not_traded(tmp_path):
    cfg, ledger, data, risk, paper = make(tmp_path)
    old = (T0 - timedelta(minutes=40)).isoformat()
    clock = lambda: T0 + timedelta(seconds=0.5)
    s = px.run_pass(latest={"ts": old, "rows": [row()]}, cfg=cfg, data_client=data, paper=paper, ledger=ledger,
                    risk=risk, clock=clock)
    assert s["hist"].get("REJ_CONS_STALE") == 1 and not s["executed"]


def test_thin_consensus_and_started_game_rejected(tmp_path):
    s, _, _ = go(tmp_path, [row(n_books=1)])
    assert s["hist"].get("REJ_CONS_THIN") == 1
    s, _, _ = go(tmp_path, [row(commence=(T0 + timedelta(minutes=5)).isoformat())])
    assert s["hist"].get("REJ_GAME_STARTED") == 1


def test_no_edge_rows_are_prefiltered_without_an_orderbook_fetch(tmp_path):
    cfg, ledger, data, risk, paper = make(tmp_path)
    s = px.run_pass(latest={"ts": T0.isoformat(), "rows": [row(ev_yes=-0.2, ev_no=-0.3)]}, cfg=cfg, data_client=data,
                    paper=paper, ledger=ledger, risk=risk, clock=lambda: T0)
    assert s["considered"] == 0 and data.calls == 0


def test_kill_switch_stops_the_pass(tmp_path):
    cfg, ledger, data, risk, paper = make(tmp_path)
    risk.trip("test")
    s = px.run_pass(latest={"ts": T0.isoformat(), "rows": [row()]}, cfg=cfg, data_client=data, paper=paper,
                    ledger=ledger, risk=risk, clock=lambda: T0)
    assert s["hist"].get("REJ_KILLED") == 1 and s["killed"] and not s["executed"]


def test_demo_attempt_is_recorded_separately_and_errors_do_not_trip_shadow(tmp_path):
    class Demo:
        def submit(self, intent):
            return Ack(None, "kxdemo", "ERROR", error="insufficient_shard_balance")
    cfg, ledger, data, risk, paper = make(tmp_path)
    demo_risk = RiskGate(cfg, ledger, "demo")
    s = px.run_pass(latest={"ts": T0.isoformat(), "rows": [row()]}, cfg=cfg, data_client=data, paper=paper,
                    ledger=ledger, risk=risk, demo_venue=Demo(), demo_risk=demo_risk,
                    clock=lambda: T0 + timedelta(seconds=0.5))
    day = px.et_day(T0)
    assert ledger.orders_today(day, "shadow") == 1 and ledger.orders_today(day, "demo") == 1
    assert not risk.killed


def test_grade_uses_kalshi_settled_result(tmp_path):
    s, ledger, cfg = go(tmp_path, [row()])
    class C:
        def get_market(self, t):
            return {"result": "yes"}
    g = px.grade(ledger, C())
    assert g["settled"] == 1 and g["wins"] == 1 and g["pnl"] > 0 and g["open"] == 0
    class C2:
        def get_market(self, t):
            return {"result": ""}
    assert px.grade(ledger, C2())["open"] == 1


# ---- Gap A: pooled-consensus evidence must be collected by the LOCAL join too (Kalshi blocks Cloud Run IPs) ----------
def _patch_local_join(monkeypatch, pooled_fn):
    import ev.sweep_nfl_props as sw
    consensus = json.dumps({"ts": T0.isoformat(), "cycle": 1, "events": [{"game_key": "nfl:2026-10-04:IND@WAS"}]})
    monkeypatch.setattr(px, "gcs_cat", lambda key: consensus.encode())
    monkeypatch.setattr(sw, "fetch_kalshi_legs", lambda kc: ({"nfl:2026-10-04:IND@WAS": []}, 0.5, {}))
    monkeypatch.setattr(sw, "rows_from_snapshot", lambda snap, legs, now, cfg, mult: ([row()], {"books_per_match": [3, 3]}))
    monkeypatch.setattr(sw, "pooled_rows_from_snapshot", pooled_fn)


def test_local_join_also_computes_pooled_evidence_with_the_kalshi_fee_multiplier(monkeypatch):
    seen = {}

    def pooled(snap, legs, now, cfg, mult):
        seen.update(mult=mult, n_games=len(snap), legs=list(legs))
        return [{"ticker": "KX-1", "pool_p_over": 0.6}], {"pool_legs": 1, "pool_recovered": 1}
    _patch_local_join(monkeypatch, pooled)
    latest = px.build_latest_local(object(), props_config(env={}))
    assert latest["pooled"] == [{"ticker": "KX-1", "pool_p_over": 0.6}]
    assert latest["pool_stats"] == {"pool_legs": 1, "pool_recovered": 1}
    assert seen == {"mult": 0.5, "n_games": 1, "legs": ["nfl:2026-10-04:IND@WAS"]}
    assert latest["rows"] and latest["books_hist"] == {3: 2}                   # the existing join is unchanged


def test_pooled_failure_never_breaks_the_local_join(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("pool exploded")
    _patch_local_join(monkeypatch, boom)
    latest = px.build_latest_local(object(), props_config(env={}))
    assert latest["pooled"] == [] and latest["pool_stats"] == {} and len(latest["rows"]) == 1


def test_publish_local_writes_a_pooled_gz_next_to_the_main_evidence(monkeypatch):
    puts = []
    monkeypatch.setattr(px, "gcs_put", lambda key, data: puts.append((key, data)) or True)
    monkeypatch.setattr(px, "gcs_cat", lambda key: None)
    px.publish_local({"rows": [row()], "pooled": [{"ticker": "KX-1", "pool_p_over": 0.6}]}, "2026-10-04")
    keys = sorted(k.split("/")[-1].rsplit("_", 1)[0] for k, _ in puts)
    assert keys == ["local", "local_pooled"]
    import gzip
    pooled = next(d for k, d in puts if "local_pooled_" in k)
    assert b"pool_p_over" in gzip.decompress(pooled) and all(k.endswith(".csv.gz") for k, _ in puts)


def test_publish_local_skips_pooled_when_there_is_none_and_still_writes_it_without_main_rows(monkeypatch):
    puts = []
    monkeypatch.setattr(px, "gcs_put", lambda key, data: puts.append(key) or True)
    monkeypatch.setattr(px, "gcs_cat", lambda key: None)
    px.publish_local({"rows": [row()], "pooled": []}, "2026-10-04")
    assert len(puts) == 1 and "local_pooled" not in puts[0]
    puts.clear()
    px.publish_local({"rows": [], "pooled": [{"ticker": "KX-1"}]}, "2026-10-04")   # pooled evidence is independent of main rows
    assert len(puts) == 1 and "local_pooled_" in puts[0]


# ---- Gap B CLI guard ---------------------------------------------------------------------------------------------
def test_novig_paper_orders_requires_novig(capsys):
    with pytest.raises(SystemExit) as e:
        px.main(["run", "--novig-paper-orders"])
    assert e.value.code == 2 and "requires --novig" in capsys.readouterr().err

