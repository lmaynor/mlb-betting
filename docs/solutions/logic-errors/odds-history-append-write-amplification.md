---
title: odds_history append writes re-read + rewrote the whole partition every call -- made fast_alert_loop take 4-8 min/run
module: mlb/analysis/odds_history.py, mlb/runners/fast_alert_loop.py, mlb/runners/track_bettingpros.py, mlb/runners/monitor_ops.py
tags: [odds_history, parquet, gcs, performance, cloud-run-jobs, cost, write-amplification, fast_alert_loop]
problem_type: logic_error
category: logic-errors
date: 2026-09-01
---

## Problem

`mlb-fast-alert` (the intraday +EV pager, `mlb.runners.fast_alert_loop`) took
**4-8 minutes per run** at a 29x/day cadence (every 15 min, 19:00-23:59 UTC,
then every 2h overnight). Its structurally similar sibling jobs --
`mlb-kalshi-alert` (6x/day) and `mlb-odds-alert` (3x/day), same 1Gi/1cpu spec
-- ran in ~2-3 minutes. Found while investigating a high GCP bill (see
`handoffs/` around 2026-09-01): this alone was estimated at ~$9/mo in Cloud
Run Jobs compute, more than every other alert-pager job combined, purely
from the runtime gap.

## Symptoms

- `gcloud logging read` on a real execution showed the entire runtime
  concentrated in ONE step: `track_bettingpros.run()` (fast_alert_loop's own
  "bank a fresh snapshot" call), logging `DONE. banked 896130 odds_history
  rows` and taking ~4m39s. The scan + notify + EV-logging steps after it
  completed in ~4 seconds combined.
- `kalshi_alert`/`odds_alert` never call `track_bettingpros.run()` at all --
  they're read-only against already-banked `odds_history` -- which is
  exactly why they didn't have this problem.
- The banked-row count (896,130) was wildly larger than what a single
  snapshot (15ish games x 15 markets x 2 days) could plausibly contain --
  the tell that the number wasn't "rows added this call."

## Root cause

`odds_history.write_partition(df, market, date, append=True)` -- the path
every intraday tracker uses (`track_bettingpros.py`, `kalshi_to_history.py`,
`kalshi_history.py`, `parlayapi_to_history.py`, `bettingpros_to_parquet.py`)
-- read the **entire existing partition file**, concatenated the new batch,
de-duped the whole thing, and rewrote the **entire file** back to GCS, on
every single call:

```python
if append:
    raw = storage.read_bytes(partition_path(market, game_date))
    existing = pd.read_parquet(io.BytesIO(raw))
    df = pd.concat([existing, df], ignore_index=True)
df = df.drop_duplicates(subset=DEDUP_KEYS, keep="last")
storage.write_bytes(_to_parquet_bytes(df), partition_path(market, game_date))
return len(df)   # <- partition's post-merge TOTAL, not rows added
```

A `(market, date)` partition for "today" gets written by every intraday
snapshot event -- up to ~34x/day combined across the standalone
`mlb-track-bettingpros` schedule (5x/day) and fast_alert_loop's own */15
calls (up to 29x/day, since it banks its OWN snapshot on every run). Each
write did O(existing partition size) work, and the existing size only grows
across the day -- so cost compounded with every call. The returned "rows"
count was the post-merge TOTAL (co-incidentally why the log line showed
896,130 instead of the ~1-2k rows actually fetched that run), which also
masked how much of that number was genuinely new.

## Fix

`write_partition(append=True)` now writes the (self-deduped) incoming batch
as its **own new file** (`part-{uuid}.parquet`) alongside whatever's already
in the partition directory -- zero reads of existing data, so cost is O(new
rows) instead of O(partition size). `read_history()` is what merges +
de-dupes across every file in a partition directory now, once, at read
time (parallelized with a `ThreadPoolExecutor` since it's pure I/O wait) --
instead of on every write.

`write_partition()`'s return value changed as a result: it's now **rows
written by this call**, not the partition's post-merge total. Nothing
outside logging/summary dicts depended on the old semantic (checked every
call site); one test (`test_convert_does_not_clobber_a_concurrent_writers_
rows`) encoded the old contract explicitly and was updated -- its actual
point (concurrent writers to the same partition must not clobber each
other) now asserts directly against `read_history()`'s merged output, which
is a more direct check than before anyway.

`append=False` (the deliberate "replace corrupt rows" full-overwrite path)
now deletes every existing file in the partition directory first, not just
`part-0.parquet` -- otherwise a "clean rewrite" would silently leave stale
append-mode files for `read_history()` to merge back in.

`monitor_ops._check_odds_history_freshness()` hardcoded a check for
`part-0.parquet`'s existence -- fixed to list the partition directory
instead, since a healthy, actively-banking partition may now never contain
a literal `part-0.parquet`.

Two regression tests added in `tests/test_bettingpros_odds.py`:
`test_write_partition_append_does_not_read_existing_data` (monkeypatches
`storage.read_bytes` to assert it's never called during an append write --
directly guards against this regression coming back) and
`test_write_partition_overwrite_clears_stale_append_files`.

## Related

- [[project_gcp_cost_review_2026-09-01]] -- the cost investigation that
  surfaced this.
- `mlb/runners/fast_alert_loop.py`'s own docstring already documents that it
  deliberately re-snapshots on every run (freshness is the whole point of
  running every 15 min instead of relying on the 5x/day standalone
  tracker) -- so the fix had to make the snapshot-and-bank step itself
  cheap, not remove it.
- `docs/solutions/conventions/` -- deploy-time Docker layer caching fix
  (same 2026-09-01 cost review) is a separate, unrelated finding from the
  same session.
