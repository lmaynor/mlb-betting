"""Tape schema v2 tests: materialized-at-write fields on RFQTape records, the creator rolling flag,
q_kalshi intake capture, the run header, the day summary sidecar, and the derived-layer finalize.

Fixture shapes (combo_rfq / mk_leg / book_of) mirror tests/test_kalshi_rfq.py."""
import io
import json
import os
from types import SimpleNamespace

import pytest

from ev.kalshi_rfq_pricer import InMemoryFairBook, KalshiRFQPricer, LegFairValue
from mlb.execution.kalshi.rfq_maker import RFQRecorder, RFQTape

NOW = 1_800_000_000.0
GK = "nfl:2026-10-25:BUF@KC"
DAY = "2027-01-15"           # UTC day of NOW


def mk_leg(ticker="T1", gk=GK, player="p1", stat="rec_yds", p=0.5, n=5, disp=0.01, ts=NOW, **kw):
    return LegFairValue(ticker=ticker, sport="nfl", game_key=gk, market_class="player_prop", player_norm=player,
                        stat=stat, strike=49.5, p_cons_yes=p, n_books=n, disp=disp, p_model_yes=None, pooled=False,
                        updated_ts=ts, **kw)


def book_of(*legs):
    b = InMemoryFairBook(clock=lambda: NOW)
    b.replace_all(legs)
    return b


def three_cross_game(p=0.5):
    return [mk_leg(f"T{i}", f"nfl:2026-10-25:A{i}@B{i}", f"p{i}", p=p) for i in (1, 2, 3)]


def combo_rfq(tickers, rfq_id="r1", contracts=50.0, sides=None):
    sides = sides or ["yes"] * len(tickers)
    return {"id": rfq_id, "market_ticker": "KXMVE-" + rfq_id, "contracts_fp": f"{contracts:.2f}",
            "mve_collection_ticker": "KXMVECOLL", "mve_selected_legs": [
                {"event_ticker": "E", "market_ticker": t, "side": s} for t, s in zip(tickers, sides)]}


def stub_decision(legs=(), action="REJECT", no_bid=0.0, notes=None):
    return SimpleNamespace(action=action, reason="REJ_X" if action != "QUOTE" else "PLAN",
                           yes_bid=0.0, no_bid=no_bid, p_yes_true=None, p_yes_cons=None,
                           ev_per_contract=None, roi_on_collateral=None, legs=tuple(legs),
                           notes=notes or {})


def read_records(tape):
    return [json.loads(l) for l in open(tape.path())]


_call_n = [0]


def one_rfq(tmp_path, msg, decision=None, clock=None):
    _call_n[0] += 1
    tape = RFQTape(str(tmp_path / f"t{_call_n[0]}"), clock=clock or (lambda: NOW))
    tape.record_rfq(msg, decision or stub_decision())
    return read_records(tape)[0]


# ------------------------------------------------------------------------------------------ pricer snap / vintage
def test_snap_carries_v2_leg_fields_and_vintage_id():
    book = book_of(*three_cross_game())
    assert InMemoryFairBook(clock=lambda: NOW).vintage_id() == "empty"
    vid = book.vintage_id()
    assert len(vid) == 16 and all(c in "0123456789abcdef" for c in vid)
    d = KalshiRFQPricer(book).price(combo_rfq(["T1", "T2", "T3"]), NOW)
    snap = d.legs[0]
    for k in ("sport", "market_class", "stat", "strike", "team", "disp", "p_max", "p_cons_yes",
              "p_model_yes", "pooled", "is_hot_scratch", "protected", "fv_updated_ts"):
        assert k in snap
    assert snap["fv_updated_ts"] == NOW and snap["p_cons_yes"] == 0.5 and snap["sport"] == "nfl"
    assert snap["p_leg"] == 0.5 and snap["n_books"] == 5          # v1 keys unchanged


# ------------------------------------------------------------------------------------------ preview_10
@pytest.mark.parametrize("raw,preview,num", [
    ("10.0000", True, 10.0),
    ("10.01", False, 10.01),
    (10.0, True, 10.0),
    ("25.00", False, 25.0),
])
def test_preview_10_exact_decimal_compare(tmp_path, raw, preview, num):
    msg = combo_rfq(["T1", "T2"])
    msg["target_cost_dollars"] = raw
    rec = one_rfq(tmp_path, msg)
    assert rec["tape_schema"] == 2 and rec["preview_10"] is preview
    assert rec["target_cost_dollars_num"] == num


def test_preview_10_absent_field_is_not_a_preview(tmp_path):
    rec = one_rfq(tmp_path, combo_rfq(["T1", "T2"]))
    assert rec["preview_10"] is False and rec["target_cost_dollars_num"] is None


# ------------------------------------------------------------------------------------------ excluded series / denominator
@pytest.mark.parametrize("tickers,cls", [
    (["KXNBA1Q-26JAN15LALBOS-LAL"], "KXNBA1Q"),
    (["KXNCAAFGAME-26SEP05ABC"], "KXNCAAFGAME"),
    (["KXNBA1Q-X", "KXNCAAFGAME-Y"], "both"),
    (["KXNFLGAME-26OCT25BUFKC-KC", "T2"], "none"),
])
def test_excluded_series_class(tmp_path, tickers, cls):
    rec = one_rfq(tmp_path, combo_rfq(tickers))
    assert rec["excluded_series_class"] == cls
    assert rec["in_denominator"] is (cls == "none")               # not a preview here


def test_in_denominator_excludes_previews_and_excluded_series(tmp_path):
    msg = combo_rfq(["T1", "T2"])
    msg["target_cost_dollars"] = "10.0000"
    assert one_rfq(tmp_path, msg)["in_denominator"] is False      # preview, plain series
    msg2 = combo_rfq(["KXNBA1Q-X", "T2"])
    assert one_rfq(tmp_path, msg2)["in_denominator"] is False     # excluded series, no preview
    assert one_rfq(tmp_path, combo_rfq(["T1", "T2"]))["in_denominator"] is True


# ------------------------------------------------------------------------------------------ combo_id
def test_combo_id_stable_under_leg_reordering(tmp_path):
    a = one_rfq(tmp_path, combo_rfq(["T1", "T2", "T3"], sides=["yes", "no", "yes"]))
    b = one_rfq(tmp_path, combo_rfq(["T3", "T2", "T1"], sides=["yes", "no", "yes"]))
    assert a["combo_id"] == b["combo_id"] and len(a["combo_id"]) == 64
    c = one_rfq(tmp_path, combo_rfq(["T1", "T2", "T3"], sides=["yes", "yes", "yes"]))
    assert c["combo_id"] != a["combo_id"]                          # side is part of the leg identity
    single = one_rfq(tmp_path, {"id": "s1", "market_ticker": "SOLO"})
    assert single["n_legs"] == 1 and single["is_combo"] is False


# ------------------------------------------------------------------------------------------ creator rolling flag
def _feed_creator(tape, t, creator, times):
    last = None
    for ts in times:
        t[0] = ts
        msg = combo_rfq(["T1", "T2"], rfq_id=f"{creator}-{ts}")
        msg["creator_id"] = creator
        tape.record_rfq(msg, stub_decision())
        last = tape
    return last


def _last_record(tape):
    return read_records(tape)[-1]


def test_creator_flag_needs_60_in_two_consecutive_windows(tmp_path):
    t = [0.0]
    tape = RFQTape(str(tmp_path), clock=lambda: t[0])
    _feed_creator(tape, t, "c59", list(range(0, 59)) + list(range(660, 719)))
    rec = _last_record(tape)
    assert rec["creator_count_10m"] == 59 and rec["creator_flagged"] is False
    assert rec["creator_flag_since_ts"] is None


def test_creator_flag_sets_at_60_sustained(tmp_path):
    t = [0.0]
    tape = RFQTape(str(tmp_path), clock=lambda: t[0])
    _feed_creator(tape, t, "c60", list(range(0, 60)))
    rec = _last_record(tape)
    assert rec["creator_count_10m"] == 60 and rec["creator_flagged"] is False   # first window only
    _feed_creator(tape, t, "c60", list(range(660, 720)))
    rec = _last_record(tape)
    assert rec["creator_count_10m"] == 60 and rec["creator_flagged"] is True
    assert rec["creator_flag_since_ts"] == 719.0


def test_creator_fields_null_without_creator(tmp_path):
    rec = one_rfq(tmp_path, combo_rfq(["T1", "T2"]))
    assert rec["creator_count_10m"] is None and rec["creator_flagged"] is False
    assert rec["creator_flag_since_ts"] is None and rec["creator_id"] is None


# ------------------------------------------------------------------------------------------ settle keys / leg enrichment
def test_settle_key_canonicalizes_player_suffix_and_team_dialect(tmp_path):
    snap = {"ticker": "KXNBAPTS-26JAN15HOU-HOU", "game_key": "nba:2027-01-15:HOU@LAL",
            "player_norm": "Jabari Smith Jr.", "side": "yes", "p_leg": 0.5, "p_true_yes": 0.5,
            "n_books": 4, "sport": "nba", "market_class": "player_prop", "stat": "points",
            "strike": 24.5, "team": "LA", "disp": 0.01, "p_max": None, "p_cons_yes": 0.5,
            "p_model_yes": None, "pooled": False, "is_hot_scratch": False, "protected": False,
            "fv_updated_ts": NOW - 30.0}
    rec = one_rfq(tmp_path, combo_rfq(["KXNBAPTS-26JAN15HOU-HOU"]), stub_decision(legs=[snap]))
    leg = rec["legs"][0]
    assert leg["player_norm_canon"] == "Jabari Smith"
    assert leg["team_canon"] == "LAK"
    assert leg["series"] == "KXNBAPTS" and leg["family"] == "NBA_PROP"
    assert leg["settle_key"] == "KXNBAPTS|nba:2027-01-15:HOU@LAL|Jabari Smith|LAK|points|24.5|yes"
    assert leg["age_sec"] == 30.0 and leg["assumed_leg"] is False and leg["pricing_source"] == "book"
    assert leg["q_kalshi"] is None and leg["q_kalshi_bid"] is None and leg["q_kalshi_ts"] is None
    assert rec["family"] == "NBA_PROP" and rec["w2_day"] == "2027-01-15"
    assert rec["day_utc"] == DAY and rec["tier"] is None and rec["timing_approx"] is None
    assert rec["earliest_commence_ts"] is None and rec["mins_to_tip_min"] is None


def test_money_fields_only_on_quotes(tmp_path):
    q = one_rfq(tmp_path, combo_rfq(["T1", "T2"]), stub_decision(action="QUOTE", no_bid=0.80))
    assert q["is_fire"] is True and q["ask_no"] == 0.80
    from mlb.execution.kalshi.fees import taker_fee
    assert q["taker_fee_per_contract"] == taker_fee(0.80, 1.0)
    r = one_rfq(tmp_path, combo_rfq(["T1", "T2"]), stub_decision(action="REJECT", no_bid=0.80))
    assert r["is_fire"] is False and r["ask_no"] is None and r["taker_fee_per_contract"] is None


# ------------------------------------------------------------------------------------------ q_kalshi capture
class OrderbookClient:
    """Orderbook payloads per ticker; T3 has no NO side quoted."""

    def request(self, method, path, **kw):
        assert method == "GET" and kw["auth"] is False
        ticker = path.split("/")[2]
        if ticker == "T3":
            return {"orderbook_fp": {"yes_dollars": [["0.30", "10"]], "no_dollars": []}}
        return {"orderbook_fp": {"yes_dollars": [["0.40", "10"], ["0.42", "5"]],
                                 "no_dollars": [["0.50", "10"], ["0.55", "5"]]}}


def test_q_kalshi_attached_through_recorder(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    rec = RFQRecorder(KalshiRFQPricer(book_of(*three_cross_game())), tape, OrderbookClient(),
                      schedule=None, clock=lambda: NOW)
    assert rec.on_event("rfq_created", combo_rfq(["T1", "T2", "T3"], "r1", 10.0), NOW).quoted
    legs = {l["ticker"]: l for l in read_records(tape)[0]["legs"]}
    t1 = legs["T1"]
    assert t1["q_kalshi_bid"] == 0.42 and t1["q_kalshi_ask"] == pytest.approx(0.45)
    assert t1["q_kalshi"] == pytest.approx(0.435) and t1["q_kalshi_ts"] == NOW
    t3 = legs["T3"]
    assert t3["q_kalshi_bid"] == 0.30 and t3["q_kalshi_ask"] is None and t3["q_kalshi"] == 0.30


def test_q_capture_failure_degrades_to_nulls(tmp_path):
    class Down:
        def request(self, method, path, **kw):
            raise RuntimeError("cdn 403")

    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    rec = RFQRecorder(KalshiRFQPricer(book_of(*three_cross_game())), tape, Down(),
                      schedule=None, clock=lambda: NOW)
    rec.on_event("rfq_created", combo_rfq(["T1", "T2", "T3"], "r1", 10.0), NOW)
    leg = read_records(tape)[0]["legs"][0]
    assert leg["q_kalshi"] is None and leg["q_kalshi_bid"] is None and leg["q_kalshi_ask"] is None


# ------------------------------------------------------------------------------------------ run header + day summary
def test_run_header_written_once_and_first(tmp_path):
    tape = RFQTape(str(tmp_path), clock=lambda: NOW)
    tape.set_run_context("deadbeef", "cfghash")
    tape.set_book_build_id("book1")
    tape.record_run({"env": "demo", "mode": "record", "feed": "f"})
    tape.record_run({"env": "demo"})                              # no-op
    tape.record_rfq(combo_rfq(["T1", "T2"]), stub_decision())
    lines = read_records(tape)
    assert lines[0]["kind"] == "run" and lines[0]["tape_schema"] == 2
    assert lines[0]["git_sha"] == "deadbeef" and lines[0]["config_hash"] == "cfghash"
    assert lines[0]["book_build_id"] == "book1" and lines[0]["env"] == "demo"
    assert sum(1 for l in lines if l["kind"] == "run") == 1
    assert lines[1]["book_build_id"] == "book1"


def test_day_summary_counters_and_rollover_write(tmp_path):
    t = [NOW]
    tape = RFQTape(str(tmp_path), clock=lambda: t[0])
    msg = combo_rfq(["T1", "T2"])
    msg["creator_id"] = "c1"
    tape.record_rfq(msg, stub_decision(action="QUOTE", no_bid=0.8))
    msg2 = combo_rfq(["KXNBA1Q-X", "T2"], rfq_id="r2")
    msg2["target_cost_dollars"] = "10.0000"
    tape.record_rfq(msg2, stub_decision())
    tape.record_trades("r1", "M", [], NOW)
    s = tape.day_summary()
    assert s["evaluated"] == 2 and s["fires"] == 1 and s["in_denominator"] == 1
    assert s["preview_10"] == 1 and s["by_excluded_series_class"]["KXNBA1Q"] == 1
    assert s["followup_records"] == 1 and s["records_by_kind"] == {"rfq": 2, "followup": 1}
    assert s["top_creators"] == [{"creator_id": "c1", "count": 1}]
    t[0] = NOW + 86400.0                                          # next UTC day: rollover writes day 1
    tape.record_rfq(combo_rfq(["T1", "T2"], rfq_id="r3"), stub_decision())
    sidecar = tmp_path / f"day_summary_{DAY}.json"
    assert sidecar.exists()
    written = json.loads(sidecar.read_text())
    assert written["day"] == DAY and written["evaluated"] == 2 and written["fires"] == 1
    assert tape.day_summary()["evaluated"] == 1                   # new day accumulates fresh


# ------------------------------------------------------------------------------------------ finalize
class FakeBlob:
    def __init__(self, store, name):
        self._store, self.name = store, name

    def download_as_bytes(self):
        return self._store[self.name]

    def exists(self):
        return self.name in self._store

    def upload_from_string(self, data, content_type=None):
        self._store[self.name] = data.encode() if isinstance(data, str) else bytes(data)


class FakeBucket:
    def __init__(self, store):
        self._store = store

    def blob(self, key):
        return FakeBlob(self._store, key)


class FakeStorageClient:
    def __init__(self, store):
        self._store = store

    def bucket(self, name):
        return FakeBucket(self._store)

    def list_blobs(self, bucket, prefix=None):
        return [FakeBlob(self._store, k) for k in sorted(self._store) if k.startswith(prefix or "")]


def _v2_record(rfq_id="r1"):
    return {"kind": "rfq", "tape_schema": 2, "ts": NOW, "rfq": {"id": rfq_id}, "plan": "QUOTE",
            "reason": "PLAN", "yes_bid": 0.0, "no_bid": 0.8, "p_yes_true": 0.1, "p_yes_cons": 0.12,
            "ev_per_contract": 0.05, "roi": 0.06, "notes": {"bounded": False},
            "rfq_id": rfq_id, "creator_id": "c1", "created_ts": None, "target_cost_dollars_num": None,
            "contracts_fp_num": 50.0, "n_legs": 2, "is_combo": True, "collection_ticker": "KXMVECOLL",
            "combo_id": "abc", "preview_10": False, "excluded_series_class": "none",
            "in_denominator": True, "family": "NFL_PROP", "tier": None, "is_fire": True,
            "bounded": False, "timing_approx": None, "day_utc": DAY, "day_et": DAY, "w2_day": None,
            "earliest_commence_ts": None, "mins_to_tip_min": None, "ask_no": 0.8,
            "taker_fee_per_contract": 0.0112, "creator_count_10m": 1, "creator_flagged": False,
            "creator_flag_since_ts": None, "book_build_id": "b1",
            "legs": [{"ticker": "T1", "game_key": GK, "player_norm": "p1", "side": "yes", "p_leg": 0.5,
                      "p_true_yes": 0.5, "n_books": 5, "series": "T1", "family": "NFL_PROP",
                      "settle_key": "T1|gk|p1||rec_yds|49.5|yes", "q_kalshi": 0.45,
                      "q_kalshi_bid": 0.44, "q_kalshi_ask": 0.46, "q_kalshi_ts": NOW}]}


def _v1_record():
    return {"kind": "rfq", "ts": NOW, "rfq": {"id": "r0"}, "plan": "REJECT", "reason": "REJ_X",
            "yes_bid": 0.0, "no_bid": 0.0, "p_yes_true": None, "p_yes_cons": None,
            "ev_per_contract": None, "roi": None, "notes": {},
            "legs": [{"ticker": "T9", "game_key": GK, "player_norm": "p9", "side": "yes", "p_leg": 0.5,
                      "p_true_yes": 0.5, "n_books": 3}]}


def test_finalize_day_over_fake_storage():
    import pyarrow.parquet as pq
    from scripts.rfq_tape_finalize import finalize_day
    prefix = "Kalshi/rfq_tape/production"
    chunk = "\n".join(json.dumps(r) for r in (
        {"kind": "run", "tape_schema": 2, "ts": NOW, "git_sha": "x"},
        _v2_record(), _v1_record(),
        {"kind": "followup", "tape_schema": 2, "ts": NOW, "rfq_id": "r1", "market_ticker": "M",
         "since_ts": NOW, "trades": []})) + "\n"
    store = {f"{prefix}/{DAY}/000000_00001.jsonl": chunk.encode()}
    client = FakeStorageClient(store)
    out = finalize_day(DAY, prefix, bucket="bkt", storage_client=client)
    assert out["combos"] == 2 and out["legs"] == 2 and out["chunks"] == 1
    derived = f"{prefix}_derived"
    for key in (f"{derived}/dt={DAY}/combos.parquet", f"{derived}/dt={DAY}/legs.parquet",
                f"{derived}/dt={DAY}/day_summary.json", f"{derived}/index/rfq_index.parquet"):
        assert key in store, key
    combos = pq.read_table(io.BytesIO(store[f"{derived}/dt={DAY}/combos.parquet"])).to_pylist()
    by_id = {r["rfq_id"]: r for r in combos}
    assert by_id["r1"]["tape_schema"] == 2 and by_id["r1"]["preview_10"] is False
    assert by_id["r1"]["combo_id"] == "abc" and by_id["r1"]["is_fire"] is True
    assert by_id["r0"]["tape_schema"] is None and by_id["r0"]["combo_id"] is None   # v1: nulls, not zeros
    assert isinstance(by_id["r1"]["legs"], str) and isinstance(by_id["r1"]["rfq"], str)
    legs = pq.read_table(io.BytesIO(store[f"{derived}/dt={DAY}/legs.parquet"])).to_pylist()
    assert {l["rfq_id"] for l in legs} == {"r1", "r0"}
    leg1 = next(l for l in legs if l["rfq_id"] == "r1")
    assert leg1["q_kalshi"] == 0.45 and leg1["settle_key"].startswith("T1|") and leg1["day_utc"] == DAY
    summary = json.loads(store[f"{derived}/dt={DAY}/day_summary.json"])
    assert summary["evaluated"] == 2 and summary["fires"] == 1 and summary["followup_records"] == 1
    index = pq.read_table(io.BytesIO(store[f"{derived}/index/rfq_index.parquet"])).to_pylist()
    assert {(r["rfq_id"], r["line_no"]) for r in index} >= {("r1", 1), ("r0", 2)}
    out2 = finalize_day(DAY, prefix, bucket="bkt", storage_client=client)         # merge: stable, deduped
    assert out2["index_rows"] == out["index_rows"]
