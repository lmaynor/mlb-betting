"""ev.prop_clv_banker (Argon Data D1): T-15 closing capture, snapshot windows, price-ratio CLV on settled
rows, and the >= 92% coverage report. All fixtures; no network, no ParlayAPI credits spent."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from ev import prop_clv_banker as B
from nba import config as C

TIP = "2026-11-12T00:30:00Z"                       # 19:30 ET, Nov 11
T_MINUS_25H = datetime(2026, 11, 10, 23, 30, tzinfo=timezone.utc)
T_MINUS_90M = datetime(2026, 11, 11, 23, 0, tzinfo=timezone.utc)
T_MINUS_10M = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)
AFTER_TIP = datetime(2026, 11, 12, 0, 35, tzinfo=timezone.utc)


def _event(eid="ev1", over=-110, under=-110, player="Test Player", point=20.5,
           books=("draftkings", "fanduel"), commence=TIP):
    return {"id": eid, "sport_key": "basketball_nba", "commence_time": commence,
            "home_team": "Boston Celtics", "away_team": "Los Angeles Lakers",
            "bookmakers": [{"key": b, "markets": [{"key": "player_points", "outcomes": [
                {"name": f"Over {player}", "description": player, "price": over, "point": point},
                {"name": f"Under {player}", "description": player, "price": under, "point": point}]}]}
                           for b in books]}


def _series(snaps):
    rows = []
    for ts, ev in snaps:
        rows += B.snapshot_rows_for_event(ev, "basketball_nba", ts)
    return pd.DataFrame(rows)


def _full_series():
    return _series([(T_MINUS_25H, _event()), (T_MINUS_90M, _event()),
                    (T_MINUS_10M, _event(over=-120, under=100)), (AFTER_TIP, _event())])


def _settled(**kw):
    row = {"event_id": "ev1", "player_norm": "test player", "stat": "points", "point": 20.5,
           "side": "OVER", "book": "draftkings", "american": -110, "decimal": 1.909091,
           "result": "win"}
    row.update(kw)
    return pd.DataFrame([row])


# ----------------------------------------------------------------------------------------------- windows
@pytest.mark.parametrize("mins,label", [(10, "close"), (45, "close"), (46, "t1h"), (90, "t1h"), (210, "t1h"),
                                        (211, "t6h"), (360, "t6h"), (900, "t6h"), (901, "t24h"), (1440, "t24h"),
                                        (2880, "t24h"), (2881, "early"), (5000, "early"), (0, "post_start"),
                                        (-5, "post_start")])
def test_window_label_bands(mins, label):
    assert B.window_label(mins) == label


def test_window_label_unknown_is_none():
    assert B.window_label(None) is None and B.window_label(float("nan")) is None


def test_open_is_not_a_decision_window():
    assert "early" in B.NON_DECISION_WINDOWS and "early" not in B.DECISION_WINDOWS
    assert B.DECISION_WINDOWS == ("t24h", "t6h", "t1h", "close")


# ----------------------------------------------------------------------------------------------- snapshot selection
def test_series_drops_post_start_snapshots_and_classes_windows():
    s = _full_series()
    assert len(s) == 3                                            # the after-tip snapshot produced no rows
    by_ts = {r.snap_ts: r for r in s.itertuples(index=False)}
    assert by_ts[T_MINUS_25H].window == "t24h" and by_ts[T_MINUS_25H].mins_to_kickoff == 1500.0
    assert by_ts[T_MINUS_90M].window == "t1h" and by_ts[T_MINUS_90M].mins_to_kickoff == 90.0
    assert by_ts[T_MINUS_10M].window == "close" and by_ts[T_MINUS_10M].mins_to_kickoff == 10.0


def test_closing_table_picks_last_before_start_with_close_age():
    ct = B.closing_table(_full_series())
    assert len(ct) == 1
    r = ct.iloc[0]
    assert r["close_ts"] == T_MINUS_10M and r["close_age_min"] == 10.0 and r["close_fresh"]
    assert r["t24h_ts"] == T_MINUS_25H and r["t24h_age_min"] == 1500.0
    assert r["t6h_ts"] == T_MINUS_25H                             # last print at or before T-6h is the T-25h one
    assert r["t1h_ts"] == T_MINUS_90M and r["t1h_age_min"] == 90.0
    assert r["n_snapshots"] == 3


def test_decision_selection_is_last_at_or_before_the_horizon():
    s = _series([(T_MINUS_25H, _event()), (T_MINUS_90M, _event()), (T_MINUS_10M, _event())])
    sel = B.select_last_at_or_before(s, 60.0)
    assert list(sel["snap_ts"]) == [T_MINUS_90M]                  # T-10m is AFTER the T-1h horizon: excluded
    assert B.select_last_at_or_before(s, 2000.0).empty


# ----------------------------------------------------------------------------------------------- CLV math (incl. de-vig)
def test_consensus_devigs_and_medians_across_books():
    ev = _event(over=-120, under=100, books=("draftkings",))
    ev["bookmakers"].append({"key": "fanduel", "markets": [{"key": "player_points", "outcomes": [
        {"name": "Over Test Player", "description": "Test Player", "price": -110, "point": 20.5},
        {"name": "Under Test Player", "description": "Test Player", "price": -110, "point": 20.5}]}]})
    rows = B.snapshot_rows_for_event(ev, "basketball_nba", T_MINUS_10M)
    # draftkings fair over: (120/220) / (120/220 + 100/200) = 0.521739; fanduel fair over: 0.5; median = 0.510870
    assert rows[0]["p_over"] == pytest.approx(0.510870, abs=1e-5)
    assert rows[0]["n_books"] == 2


def test_attach_clv_price_ratio_against_devigged_consensus():
    ct = B.closing_table(_full_series())                          # close: both books over -120 / under +100
    out = B.attach_clv(_settled(), ct, B.event_close_index(_full_series()))
    r = out.iloc[0]
    # fair P(over) = 0.521739 -> fair decimal 1.916667; entry -110 -> 1.909091; CLV = 1.909091/1.916667 - 1
    assert r["close_p_over"] == pytest.approx(0.521739, abs=1e-5)
    assert r["close_fair_decimal"] == pytest.approx(1.916667, abs=1e-5)
    assert r["clv_cons_pct"] == pytest.approx(-0.3948, abs=0.01)
    assert r["clv_missing_reason"] is None and r["close_age_min"] == 10.0 and r["close_n_books"] == 2
    # same-book CLV: draftkings moved -110 -> -120 against the side taken... entry beat the close: positive
    assert r["clv_pct"] == pytest.approx(4.1379, abs=0.01)


def test_attach_clv_positive_when_entry_beats_the_close():
    ct = B.closing_table(_full_series())
    out = B.attach_clv(_settled(american=120, decimal=2.2, side="UNDER"), ct,
                       B.event_close_index(_full_series()))
    r = out.iloc[0]
    # P(under) = 1 - 0.521739 = 0.478261 -> fair decimal 2.090909; CLV = 2.2/2.090909 - 1 = +5.217%
    assert r["clv_cons_pct"] == pytest.approx(5.2174, abs=0.01)


# ----------------------------------------------------------------------------------------------- missing markers (fail closed)
def test_missing_no_closing_snapshot_marker():
    out = B.attach_clv(_settled(event_id="nope"), B.closing_table(_full_series()),
                       B.event_close_index(_full_series()))
    r = out.iloc[0]
    assert r["clv_missing_reason"] == B.MISSING_NO_CLOSING
    assert r["clv_cons_pct"] is None and r["clv_pct"] is None and r["close_p_over"] is None


def test_missing_prop_not_quoted_at_close_marker():
    out = B.attach_clv(_settled(point=25.5), B.closing_table(_full_series()),
                       B.event_close_index(_full_series()))
    assert out.iloc[0]["clv_missing_reason"] == B.MISSING_NOT_QUOTED


def test_stale_close_is_attached_but_not_valid_clv():
    s = _series([(T_MINUS_25H, _event()), (datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc), _event())])
    ct = B.closing_table(s)
    assert ct.iloc[0]["close_age_min"] == 180.0 and not ct.iloc[0]["close_fresh"]
    out = B.attach_clv(_settled(), ct, B.event_close_index(s))
    r = out.iloc[0]
    assert r["clv_missing_reason"] == B.MISSING_CLOSE_STALE
    assert r["clv_cons_pct"] is None and r["close_p_over"] is not None and r["close_age_min"] == 180.0


def test_missing_entry_price_and_event_id_markers():
    ct = B.closing_table(_full_series())
    eci = B.event_close_index(_full_series())
    out = B.attach_clv(_settled(american=None, decimal=None), ct, eci)
    assert out.iloc[0]["clv_missing_reason"] == B.MISSING_ENTRY_PRICE
    out = B.attach_clv(_settled(event_id=None), ct, eci)
    assert out.iloc[0]["clv_missing_reason"] == B.MISSING_EVENT_ID


# ----------------------------------------------------------------------------------------------- coverage report
def _coverage_frame(n_valid, n_missing, extra_unsettled=2):
    rows = []
    for i in range(n_valid):
        rows.append({"result": "win", "clv_cons_pct": 1.0, "clv_missing_reason": None, "sport": "nba"})
    for i in range(n_missing):
        rows.append({"result": "loss", "clv_cons_pct": None,
                     "clv_missing_reason": B.MISSING_NO_CLOSING, "sport": "nba"})
    for i in range(extra_unsettled):
        rows.append({"result": "void", "clv_cons_pct": None, "clv_missing_reason": None, "sport": "nba"})
    return pd.DataFrame(rows)


def test_coverage_denominator_is_settled_rows_only():
    rep = B.coverage_report(_coverage_frame(23, 2))               # 25 settled, 2 void excluded
    assert rep["n_settled"] == 25 and rep["n_valid_clv"] == 23
    assert rep["share_valid_clv"] == pytest.approx(0.92)
    assert rep["passes_target"] is True and rep["zero_cap_overruns"] is True
    assert rep["missing_reasons"] == {B.MISSING_NO_CLOSING: 2}
    assert "win" in rep["denominator"] and "push" in rep["denominator"]
    assert rep["by_sport"]["nba"] == {"settled": 25, "valid_clv": 23, "share": 0.92}


def test_coverage_below_target_and_cap_overrun_fails():
    rep = B.coverage_report(_coverage_frame(20, 5))
    assert rep["share_valid_clv"] == pytest.approx(0.80) and rep["passes_target"] is False
    rep2 = B.coverage_report(_coverage_frame(23, 2), cap_overruns=1)
    assert rep2["passes_target"] is False and rep2["zero_cap_overruns"] is False


def test_coverage_empty_frame_is_zero_not_fabricated():
    rep = B.coverage_report(pd.DataFrame())
    assert rep["n_settled"] == 0 and rep["share_valid_clv"] is None and rep["passes_target"] is False


# ----------------------------------------------------------------------------------------------- banked-data end to end
def test_build_series_and_attach_from_banked_files(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    # ET folder/HHMM names (accumulator convention): T-25h = Nov 10 18:30 ET, T-90m = Nov 11 18:00 ET,
    # T-10m = Nov 11 19:20 ET
    for day, hhmm, ev in (("2026-11-10", "1830", _event()), ("2026-11-11", "1800", _event()),
                          ("2026-11-11", "1920", _event(over=-120, under=100))):
        d = tmp_path / "OddsAccum" / "basketball_nba" / "raw" / day
        d.mkdir(parents=True, exist_ok=True)
        (d / f"props_{hhmm}.json").write_text(json.dumps([ev]))
    out, report = B.attach_from_bank(_settled(), "basketball_nba", "2026-11-10", "2026-11-11")
    assert report["rows"] > 0
    r = out.iloc[0]
    assert r["clv_missing_reason"] is None and r["close_age_min"] == 10.0
    assert r["clv_cons_pct"] == pytest.approx(-0.3948, abs=0.01)


# ----------------------------------------------------------------------------------------------- T-15 capture
class FakeClient:
    credits_remaining = None

    def __init__(self, slate, props):
        self._slate, self._props = slate, props
        self.credits_last = None
        self.fetched = []

    def get_slate(self, sport, markets=None):
        self.credits_last = 1
        return self._slate

    def get_event_props(self, sport, event_id, markets):
        self.credits_last = len(markets)
        self.fetched.append(event_id)
        return self._props[event_id]


def _local_storage(tmp_path, monkeypatch):
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))


def test_capture_buys_only_imminent_events_and_banks_them(tmp_path, monkeypatch):
    _local_storage(tmp_path, monkeypatch)
    now = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)
    imminent = _event("ev1")
    slate = [imminent,
             _event("far", commence="2026-11-12T05:00:00Z"),
             _event("started", commence="2026-11-12T00:10:00Z")]
    client = FakeClient(slate, {"ev1": imminent})
    res = B.capture_closing("basketball_nba", client=client, now=now)
    assert client.fetched == ["ev1"]                              # far + already-started games never bought
    assert res["events_priced"] == 1 and res["credits_spent"] == 1 + 5   # slate + 5 NBA core markets
    raw = tmp_path / "OddsAccum" / "basketball_nba" / "raw" / "2026-11-11" / "props_1920.json"
    assert json.loads(raw.read_text())[0]["id"] == "ev1"
    assert B.closing_spent("basketball_nba", "2026-11") == 6      # carve-out sub-ledger recorded the spend
    from nba.odds import credit_ledger as L
    assert L.read_spent("basketball_nba", "2026-11") == 6         # ... and so did the shared accumulator ledger


def test_capture_no_imminent_events_costs_one_slate_credit(tmp_path, monkeypatch):
    _local_storage(tmp_path, monkeypatch)
    now = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)
    client = FakeClient([_event("far", commence="2026-11-12T05:00:00Z")], {})
    res = B.capture_closing("basketball_nba", client=client, now=now)
    assert res["events"] == 0 and res["credits_spent"] == 1 and client.fetched == []


def test_capture_fails_closed_when_carve_out_exhausted(tmp_path, monkeypatch):
    _local_storage(tmp_path, monkeypatch)
    d = tmp_path / "OddsAccum" / "basketball_nba" / "_credits_closing"
    d.mkdir(parents=True)
    cap = C.CLV_CLOSING_MONTHLY_BUDGET["basketball_nba"]
    (d / "2026-11.json").write_text(json.dumps({"month": "2026-11", "credits": cap}))
    client = FakeClient([_event("ev1")], {"ev1": _event("ev1")})
    res = B.capture_closing("basketball_nba", client=client,
                            now=datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc))
    assert res["skipped"] and "carve-out" in res["reason"] and client.fetched == []


def test_imminent_events_window_edges():
    now = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)
    slate = [_event("a", commence="2026-11-12T00:40:00Z"),        # exactly +20m: inside
             _event("b", commence="2026-11-12T00:41:00Z"),        # +21m: outside
             _event("c", commence="2026-11-12T00:20:00Z")]        # at start: outside (not pre-start)
    got = B.imminent_events(slate, now)
    assert [e["id"] for e in got] == ["a"]


# ----------------------------------------------------------------------------------------------- config: reallocation, not new budget
def test_closing_carve_out_is_inside_the_existing_accumulator_caps():
    assert sum(C.CLV_CLOSING_MONTHLY_BUDGET.values()) == 10_000   # Argon D1's reallocation size
    for sport, carve in C.CLV_CLOSING_MONTHLY_BUDGET.items():
        assert 0 < carve <= C.ODDS_BANK_DEFAULT_BUDGET[sport]     # inside the cap, never above it
    assert C.ODDS_BANK_DEFAULT_BUDGET["basketball_nba"] == 34400  # caps unchanged
    assert C.ODDS_BANK_DEFAULT_BUDGET["icehockey_nhl"] == 34400
    assert C.parlay_budget_total() == 99_500                      # shared-pool total unchanged
    assert C.CLV_COVERAGE_TARGET == 0.92
