"""Tests and runnable load check for scripts/kalshi_rfq_daemon.py decoupled intake + $10 preview drop + RFQTape intake_counters."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import logging
import os
import threading
import time

import pytest

from mlb.execution.kalshi.rfq_maker import RFQTape


def _daemon():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "kalshi_rfq_daemon", os.path.join(root, "scripts", "kalshi_rfq_daemon.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------ (a) producer non-blocking
def test_producer_returns_without_running_classification():
    d = _daemon()

    async def run():
        entered = threading.Event()
        release = threading.Event()
        seen = []

        class BlockingHandler:
            def on_event(self, typ, msg, ts):
                entered.set()
                release.wait(timeout=5.0)
                seen.append((typ, msg["id"]))

        intake = d.IntakePipeline(BlockingHandler())
        intake.start()
        try:
            t0 = time.monotonic()
            await intake.on_event("rfq_created", {"id": "r-block", "target_cost_dollars": "25.00"}, 100.0)
            elapsed = time.monotonic() - t0
            assert elapsed < 0.05
            assert seen == []
            assert await asyncio.to_thread(entered.wait, 2.0)
            release.set()
            await intake.drain()
            assert seen == [("rfq_created", "r-block")]
            assert intake.counters()["enqueued"] == 1
            assert intake.counters()["processed"] == 1
        finally:
            await intake.stop()

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ (b) per-RFQ ordering
def test_per_rfq_ordering_preserved_across_sharded_workers():
    d = _daemon()

    async def run():
        gate = threading.Event()
        records_by_id: dict[str, list[str]] = {}
        lock = threading.Lock()

        class OrderingHandler:
            def on_event(self, typ, msg, ts):
                if msg.get("wait"):
                    gate.wait(timeout=5.0)
                rid = msg.get("id") or msg.get("rfq_id")
                with lock:
                    records_by_id.setdefault(rid, []).append(f"{typ}:{msg.get('seq')}")

        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=16))
        intake = d.IntakePipeline(OrderingHandler(), n_shards=8)
        intake.start()
        try:
            # Find two distinct rfq_ids that map to different shards so one can block while the other runs.
            rid_a = "rfq-order-a"
            shard_a = intake.shard_for(rid_a)
            rid_b = next(f"rfq-order-{i}" for i in range(100) if intake.shard_for(f"rfq-order-{i}") != shard_a)

            # First event on shard_a blocks until gate is opened; follow-up events on rid_a must still arrive in order.
            await intake.on_event("rfq_created", {"id": rid_a, "seq": 1, "wait": True, "target_cost_dollars": "25.00"}, 1.0)
            await intake.on_event("quote_accepted", {"rfq_id": rid_a, "seq": 2}, 2.0)
            await intake.on_event("rfq_deleted", {"id": rid_a, "seq": 3}, 3.0)

            # Different shard completes even while shard_a is blocked.
            await intake.on_event("rfq_created", {"id": rid_b, "seq": 1, "target_cost_dollars": "25.00"}, 1.5)
            await asyncio.sleep(0.05)
            assert records_by_id.get(rid_b) == ["rfq_created:1"]
            assert rid_a not in records_by_id

            gate.set()
            await intake.drain()
            assert records_by_id[rid_a] == ["rfq_created:1", "quote_accepted:2", "rfq_deleted:3"]

            # Also verify many RFQs with multiple events each preserve per-RFQ sequence strictly.
            records_by_id.clear()
            for seq in range(5):
                for k in range(32):
                    rid = f"multi-{k}"
                    typ = "rfq_created" if seq == 0 else ("rfq_deleted" if seq == 4 else "quote_accepted")
                    payload = {"id": rid, "rfq_id": rid, "seq": seq}
                    if typ == "rfq_created":
                        payload["target_cost_dollars"] = "50.00"
                    await intake.on_event(typ, payload, float(seq))
            await intake.drain()
            for k in range(32):
                assert [s.split(":")[1] for s in records_by_id[f"multi-{k}"]] == ["0", "1", "2", "3", "4"]
        finally:
            await intake.stop()

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ (c) & (d) preview drop
@pytest.mark.parametrize("raw,expected", [
    ("10.0000", True),
    ("10.00", True),
    ("10.0", True),
    ("10", True),
    (10.0, True),
    (10, True),
    ("10.000", True),
    ("10.01", False),
    ("9.99", False),
    ("25.00", False),
    (None, False),
    ("", False),
    ("bad", False),
    (True, False),
])
def test_is_preview_10_msg_matches_tape_semantics(raw, expected):
    d = _daemon()
    msg = {"id": "r1"} if raw is None else {"id": "r1", "target_cost_dollars": raw}
    assert d.is_preview_10_msg(msg) is expected


def test_exact_10_preview_create_and_delete_dropped_while_non_10_passes():
    d = _daemon()

    async def run():
        handled = []

        class StubHandler:
            def on_event(self, typ, msg, ts):
                handled.append((typ, msg, ts))

        intake = d.IntakePipeline(StubHandler(), preview_cap=3)
        intake.start()
        try:
            # (c) Exact $10.00 create + delete dropped and counted
            p_msg = {"id": "prev-1", "target_cost_dollars": "10.0000", "mve_selected_legs": []}
            await intake.on_event("rfq_created", p_msg, 10.0)
            await intake.on_event("rfq_deleted", {"id": "prev-1"}, 11.0)
            # Duplicate delete after discard passes through (id already removed from preview set)
            await intake.on_event("rfq_deleted", {"id": "prev-1"}, 12.0)

            # (d) Non-$10 create reaches handler unchanged, and its delete passes through
            real_msg = {"id": "real-1", "target_cost_dollars": "25.00", "mve_selected_legs": [{"market_ticker": "T1"}]}
            await intake.on_event("rfq_created", real_msg, 13.0)
            await intake.on_event("rfq_deleted", {"id": "real-1"}, 14.0)
            # Absent target_cost_dollars is contract-sized, NOT preview
            no_cost_msg = {"id": "real-2"}
            await intake.on_event("rfq_created", no_cost_msg, 15.0)
            # Non-create/non-delete passes through untouched
            await intake.on_event("quote_accepted", {"rfq_id": "real-1", "quote_id": "q1"}, 16.0)

            await intake.drain()
            assert len(handled) == 5
            assert {t[0] + ":" + (t[1].get("id") or t[1].get("rfq_id")) + f"@{t[2]}" for t in handled} == {
                "rfq_deleted:prev-1@12.0",
                "rfq_created:real-1@13.0",
                "rfq_deleted:real-1@14.0",
                "rfq_created:real-2@15.0",
                "quote_accepted:real-1@16.0",
            }
            real1_seq = [t for t in handled if (t[1].get("id") or t[1].get("rfq_id")) == "real-1"]
            assert real1_seq == [
                ("rfq_created", real_msg, 13.0),
                ("rfq_deleted", {"id": "real-1"}, 14.0),
                ("quote_accepted", {"rfq_id": "real-1", "quote_id": "q1"}, 16.0),
            ]
            c = intake.counters()
            assert c["dropped_preview_creates"] == 1
            assert c["dropped_preview_deletes"] == 1
            assert c["enqueued"] == 5
            assert c["processed"] == 5

            # Bounded FIFO eviction test (cap=3): insert p1, p2, p3, p4 -> p1 evicted
            handled.clear()
            for idx in range(1, 5):
                await intake.on_event("rfq_created", {"id": f"ev-{idx}", "target_cost_dollars": "10.00"}, 20.0 + idx)
            await intake.on_event("rfq_deleted", {"id": "ev-1"}, 25.0)  # evicted -> reaches handler
            await intake.on_event("rfq_deleted", {"id": "ev-2"}, 26.0)  # still in set -> dropped
            await intake.drain()
            assert handled == [("rfq_deleted", {"id": "ev-1"}, 25.0)]
            assert intake.counters()["dropped_preview_creates"] == 5
            assert intake.counters()["dropped_preview_deletes"] == 2
        finally:
            await intake.stop()

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ (e) full queue drops & counts
def test_full_queue_drops_counts_and_throttles_warning(caplog):
    d = _daemon()

    async def run():
        now_m = [100.0]

        class NeverCalled:
            def on_event(self, typ, msg, ts):
                raise AssertionError("workers not started")

        intake = d.IntakePipeline(
            NeverCalled(), n_shards=1, maxsize=2, warn_interval_sec=30.0, clock=lambda: now_m[0]
        )
        with caplog.at_level(logging.WARNING):
            # Fill queue (maxsize=2)
            assert intake.enqueue("rfq_created", {"id": "q1", "target_cost_dollars": "20.00"}, 1.0) is True
            assert intake.enqueue("rfq_created", {"id": "q2", "target_cost_dollars": "20.00"}, 2.0) is True
            # Overflow 1: warns
            assert intake.enqueue("rfq_created", {"id": "q3", "target_cost_dollars": "20.00"}, 3.0) is False
            # Overflow 2 within 30s: throttled (no second warning)
            now_m[0] = 105.0
            assert intake.enqueue("rfq_created", {"id": "q4", "target_cost_dollars": "20.00"}, 4.0) is False
            # Overflow 3 after 30s: warns again
            now_m[0] = 131.0
            assert intake.enqueue("rfq_created", {"id": "q5", "target_cost_dollars": "20.00"}, 5.0) is False

        c = intake.counters()
        assert c["enqueued"] == 2
        assert c["dropped_queue_full"] == 3
        full_warnings = [r for r in caplog.records if "intake queue full" in r.message]
        assert len(full_warnings) == 2

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ (f) RFQTape intake_counters
def test_flushed_chunk_carries_intake_counters_delta_and_handles_failures():
    t = [1_800_000_000.0]
    writes = []
    fail = [False]

    def writer(data, key):
        if fail[0]:
            raise OSError("gcs down")
        writes.append((key, [json.loads(line) for line in data.decode("ascii").splitlines()]))

    counts = {
        "enqueued": 10,
        "processed": 8,
        "dropped_preview_creates": 90,
        "dropped_preview_deletes": 85,
        "dropped_queue_full": 2,
    }
    tape = RFQTape(
        clock=lambda: t[0],
        storage_prefix="Kalshi/rfq_tape/production",
        writer=writer,
        flush_sec=30.0,
        intake_counters_fn=lambda: dict(counts),
    )
    tape.append({"kind": "rfq", "n": 1})
    n1 = tape.flush()
    assert n1 == 2
    assert len(writes) == 1
    recs1 = writes[0][1]
    assert recs1[0] == {"kind": "rfq", "n": 1}
    assert recs1[1]["kind"] == "intake_counters"
    assert recs1[1]["tape_schema"] == 2
    assert recs1[1]["ts"] == t[0]
    assert recs1[1]["enqueued"] == 10
    assert recs1[1]["processed"] == 8
    assert recs1[1]["dropped_preview_creates"] == 90
    assert recs1[1]["dropped_preview_deletes"] == 85
    assert recs1[1]["dropped_queue_full"] == 2

    # Second flush with no buffer and no counter change emits nothing.
    t[0] += 5.0
    assert tape.flush() == 0
    assert len(writes) == 1

    # Preview-only window (buffer empty, only preview counters changed) still emits intake_counters.
    counts["dropped_preview_creates"] += 15
    counts["dropped_preview_deletes"] += 12
    t[0] += 5.0
    assert tape.flush() == 1
    recs2 = writes[1][1]
    assert len(recs2) == 1
    assert recs2[0]["kind"] == "intake_counters"
    assert recs2[0]["enqueued"] == 0
    assert recs2[0]["processed"] == 0
    assert recs2[0]["dropped_preview_creates"] == 15
    assert recs2[0]["dropped_preview_deletes"] == 12
    assert recs2[0]["dropped_queue_full"] == 0

    # Failed flush preserves both buffered records and un-flushed counter delta for next flush.
    fail[0] = True
    tape.append({"kind": "rfq", "n": 2})
    counts["enqueued"] += 1
    counts["processed"] += 3
    assert tape.flush() == 0
    fail[0] = False
    counts["enqueued"] += 4
    counts["processed"] += 4
    assert tape.flush() == 2
    recs3 = writes[2][1]
    assert recs3[0] == {"kind": "rfq", "n": 2}
    assert recs3[1]["kind"] == "intake_counters"
    assert recs3[1]["enqueued"] == 5
    assert recs3[1]["processed"] == 7
    assert recs3[1]["dropped_preview_creates"] == 0


# ------------------------------------------------------------------------------------------ heartbeat & stats
def test_heartbeat_stats_and_intake_lag():
    d = _daemon()

    async def run():
        wall = [1_800_000_010.0]

        class StubHandler:
            def on_event(self, typ, msg, ts):
                pass

        intake = d.IntakePipeline(StubHandler(), n_shards=8, wall_clock=lambda: wall[0])
        hb0 = d.format_heartbeat(1, 42, intake.stats())
        assert "connects=1" in hb0
        assert "rfqs_seen=0" in hb0
        assert "book_legs=42" in hb0
        assert "q_max=0" in hb0
        assert "intake_lag=none" in hb0

        intake.start()
        try:
            await intake.on_event(
                "rfq_created",
                {"id": "r-iso", "target_cost_dollars": "25.00", "created_ts": "2027-01-15T08:00:08Z"},
                wall[0],
            )
            await intake.drain()
            # 2027-01-15T08:00:08Z == 1800000008.0 -> lag is 2.0s at wall[0]=1800000010.0
            hb1 = d.format_heartbeat(2, 50, intake.stats())
            assert "rfqs_seen=1" in hb1
            assert "enqueued=1" in hb1
            assert "processed=1" in hb1
            assert "dropped_preview_creates=0" in hb1
            assert "dropped_preview_deletes=0" in hb1
            assert "dropped_queue_full=0" in hb1
            assert "intake_lag=2.000s" in hb1
        finally:
            await intake.stop()

    asyncio.run(run())


# ------------------------------------------------------------------------------------------ amain wiring check
def test_amain_sets_executor_size_and_wires_stream_poll_and_tape(tmp_path, monkeypatch):
    d = _daemon()
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def get_rfqs(self, status="open"):
            return [
                {"id": "poll-prev", "target_cost_dollars": "10.0000"},
                {"id": "poll-real", "target_cost_dollars": "25.00"},
            ]

    class FakeStream:
        def __init__(self, env, key_id, key, on_event):
            self.connects = 1
            self.on_event = on_event
            captured["stream_on_event"] = on_event

        def stop(self):
            captured["stopped"] = True

        async def run(self):
            await self.on_event("rfq_created", {"id": "ws-prev", "target_cost_dollars": "10.00"}, 1.0)
            await self.on_event("rfq_deleted", {"id": "ws-prev"}, 2.0)
            await self.on_event("rfq_created", {"id": "ws-real", "target_cost_dollars": "30.00"}, 3.0)
            await asyncio.sleep(0.15)

    class FakeFeedState:
        def __init__(self, *a, **kw):
            pass

        def refresh_props(self):
            return 0

        def refresh_banked(self):
            return 0

        def refresh_games(self):
            return 0

    writes = []

    class FakeTape(RFQTape):
        def __init__(self, directory, storage_prefix=None, **kw):
            super().__init__(
                str(tmp_path),
                storage_prefix="Kalshi/rfq_tape/test",
                writer=lambda data, key: writes.append((key, [json.loads(l) for l in data.decode("ascii").splitlines()])),
            )
            captured["tape"] = self

    monkeypatch.setattr(d, "load_credentials", lambda env_file, rfq_env: ("kid", b"pem"))
    monkeypatch.setattr(d, "load_private_key", lambda pem: pem)
    monkeypatch.setattr(d, "RFQClient", FakeClient)
    monkeypatch.setattr(d, "FeedState", FakeFeedState)
    monkeypatch.setattr(d, "KalshiRFQStream", FakeStream)
    monkeypatch.setattr(d, "RFQTape", FakeTape)

    args = type("Args", (), {
        "env": "demo", "mode": "record", "env_file": None, "feed": "unused", "hot_games_file": None,
        "db": None, "tape_dir": str(tmp_path), "tape_gcs_prefix": None, "max_runtime_sec": 0.12,
        "poll_sec": 10.0, "reload_sec": 60.0, "game_sports": "", "game_markets": "h2h",
        "game_source": "auto", "banked_reload_sec": 300.0, "game_reload_sec": 3600.0,
        "max_daily_game_credits": 120.0, "prop_sports": "", "max_unpriced_legs": 0,
        "allow_same_game": False, "quote_single_leg": False,
    })()

    async def run():
        rc = await d.amain(args)
        assert rc == 0
        loop = asyncio.get_running_loop()
        assert loop._default_executor._max_workers >= 16

    asyncio.run(run())
    assert captured.get("stopped") is True
    assert writes, "final tape flush should have written at least one chunk"
    all_recs = [rec for _, recs in writes for rec in recs]
    rfq_ids = {rec["rfq_id"] for rec in all_recs if rec.get("kind") == "rfq"}
    assert rfq_ids == {"poll-real", "ws-real"}
    ic_recs = [rec for rec in all_recs if rec.get("kind") == "intake_counters"]
    assert ic_recs
    assert sum(r["dropped_preview_creates"] for r in ic_recs) == 2
    assert sum(r["dropped_preview_deletes"] for r in ic_recs) == 1
    assert sum(r["enqueued"] for r in ic_recs) == 2


# ------------------------------------------------------------------------------------------ load check
def run_load_check(n_events: int = 20_000, service_sec: float = 0.005) -> dict:
    """Drive IntakePipeline with n_events (90% $10 previews) against a 5ms stub handler."""
    d = _daemon()
    if n_events % 10 != 0:
        raise ValueError("n_events must be divisible by 10")
    n_preview_total = (n_events * 9) // 10
    n_preview_creates = n_preview_total // 2
    n_preview_deletes = n_preview_total - n_preview_creates
    n_non_preview = n_events - n_preview_total

    events: list[tuple[str, dict, float]] = []
    # Mix preview creates, matching preview deletes, and non-preview creates evenly
    p_idx = 0
    d_idx = 0
    r_idx = 0
    for i in range(n_events):
        mod = i % 10
        if mod == 0 and r_idx < n_non_preview:
            events.append((
                "rfq_created",
                {"id": f"real-{r_idx}", "target_cost_dollars": "25.00", "created_ts": 1_800_000_000.0},
                float(i),
            ))
            r_idx += 1
        elif p_idx < n_preview_creates and (mod in (1, 3, 5, 7, 9) or d_idx >= p_idx):
            events.append((
                "rfq_created",
                {"id": f"prev-{p_idx}", "target_cost_dollars": "10.0000", "created_ts": 1_800_000_000.0},
                float(i),
            ))
            p_idx += 1
        else:
            events.append(("rfq_deleted", {"id": f"prev-{d_idx}"}, float(i)))
            d_idx += 1

    async def run():
        handled = 0
        lock = threading.Lock()

        class SlowStub:
            def on_event(self, typ, msg, ts):
                nonlocal handled
                time.sleep(service_sec)
                with lock:
                    handled += 1

        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=d.EXECUTOR_WORKERS))
        intake = d.IntakePipeline(SlowStub())
        intake.start()
        try:
            t_start = time.perf_counter()
            for typ, msg, ts in events:
                await intake.on_event(typ, msg, ts)
            t_produce = time.perf_counter() - t_start
            await intake.drain()
            t_total = time.perf_counter() - t_start
            c = intake.counters()
            return {
                "events_sent": len(events),
                "preview_events": n_preview_total,
                "non_preview_events": n_non_preview,
                "handler_calls": handled,
                "produce_sec": round(t_produce, 4),
                "drain_sec": round(t_total, 4),
                **c,
            }
        finally:
            await intake.stop()

    return asyncio.run(run())


def test_load_check_burst_20k_with_90pct_previews():
    res = run_load_check(n_events=20_000, service_sec=0.005)
    assert res["events_sent"] == 20_000
    assert res["dropped_preview_creates"] + res["dropped_preview_deletes"] == 18_000
    assert res["dropped_queue_full"] == 0
    assert res["enqueued"] == 2_000
    assert res["processed"] == 2_000
    assert res["handler_calls"] == 2_000
    # 2,000 events * 5ms / 8 shards ~= 1.25s; without preview drop it would be >= 12.5s (sharded) or 100s (serial).
    assert res["produce_sec"] < 1.0
    assert res["drain_sec"] < 5.0


if __name__ == "__main__":
    print(json.dumps(run_load_check(), indent=2, sort_keys=True))
