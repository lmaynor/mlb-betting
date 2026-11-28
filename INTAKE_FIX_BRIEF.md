# Build brief: recorder intake decoupling + preview drop (branch feat/recorder-intake-fix, base dc41919)

## Problem (measured, Nov 27, 2026)
Kalshi's communications channel delivers ~290,000 rfq_created/min (963,659 distinct
RFQs in a 200s counting probe, plus as many deletes). The recorder daemon's stream
dispatch awaits each message's classification before reading the next frame, so
intake runs at the classifier's serial speed (~1-6s/RFQ effective) and the daemon
reads an ever-older backlog, wedges, and dies. Its healthy-era capture was
~72 RFQs/min, none of them current.

Patient: `scripts/kalshi_rfq_daemon.py` (record mode path: `RFQRecorder` handler,
`KalshiRFQStream` from `mlb/execution/kalshi/rfq_client.py`). Do NOT change the
shared stream class; the quoter uses it and works. All changes live in the daemon
(plus tests). Keep every other daemon behavior identical.

## Changes required
1. **Decoupled intake.** Replace the daemon's direct `on_event` -> handler call
   with a producer/consumer split inside `amain`:
   - The stream callback (and the REST `poll()` feeder, same path) does O(1)
     work only: preview check (below), then enqueue `(typ, msg, ts)` and return.
     It must never await classification.
   - Shard queues by RFQ id (hash into N=8 `asyncio.Queue`s, one worker task
     per shard) so events for the same RFQ are processed in arrival order
     while different RFQs process in parallel. Each worker calls the existing
     `handler.on_event` via `asyncio.to_thread`. Size the default executor so
     the workers are not starved (set a ThreadPoolExecutor with >= 16 workers
     for the loop).
   - Bounded queues: maxsize 50,000 per shard. On full: drop the incoming
     create, increment `dropped_queue_full`, log a throttled warning. Staying
     current beats completeness; the counter makes the loss visible.
2. **$10 preview drop at the producer.** For `rfq_created` whose raw size
   fields show an exact $10.00 target cost (the tape's existing preview_10
   definition; find how classification computes it and mirror the cheapest
   equivalent check on the raw message): do not enqueue. Increment
   `dropped_preview_creates` and remember the id in a bounded set (cap
   ~250k entries, FIFO eviction). For `rfq_deleted` whose id is in that set:
   do not enqueue, increment `dropped_preview_deletes`, discard the id.
   All other events pass through untouched.
3. **Counters in the tape.** The three counters (plus `enqueued` and
   `processed` totals) must land in the banked tape, not just logs: extend
   `RFQTape` minimally so each flushed chunk can carry one
   `{"kind": "intake_counters", ...}` record with the counts since the
   previous flush, emitted on flush when any counter changed. Existing
   consumers filter by kind, so a new kind is safe; keep every existing
   record shape byte-identical.
4. **Heartbeat truth.** The daemon watchdog heartbeat line gains: queue
   depths per shard (max), drop counters, and intake lag (now minus the
   created_ts of the most recently processed rfq_created). No behavior
   change to the watchdog's connect check.

## Tests
- Existing suite must pass in the CI venv (~/workspace/mod04d-fix/ci-venv):
  at minimum tests/test_kalshi_rfq.py and tests/test_kalshi_game_banked.py,
  then the full suite.
- New tests proving: (a) the producer returns without running classification
  (a blocking handler does not delay the callback); (b) per-RFQ ordering is
  preserved across the sharded workers; (c) an exact-$10 create is counted
  and never reaches the handler, and its delete is counted too; (d) a
  non-$10 create reaches the handler unchanged; (e) a full queue drops and
  counts instead of blocking; (f) a flushed chunk contains the
  intake_counters record with the right totals.
- Load check (script or test, your choice, but runnable): drive the new
  producer path with a synthetic burst of >= 20,000 mixed events (90%
  $10 previews) against a stub handler with a 5ms service time and report
  drain time and drop counts. The preview drop must keep the stub from
  being the bottleneck for the preview share.

## Out of scope
No changes to pricing, classification, tape schema for existing kinds, the
REST poll cadence, the fair book, or any other script. No deploy steps:
no docker build, no GCS upload, no VM or metadata changes. Report the diff
summary, the test results (with counts), and the load-check numbers.
