"""Cloud Run job entry for the Kalshi liquidity probe.

    python -m ev.kalshi_probe.job [--snapshot-if-empty]

Behaviour:
  * At/after HARD_STOP_UTC (2026-11-24T08:00:00Z): exit 0 immediately --
    no API calls at all (the probe is over).
  * No target game in the pre-game window: exit 0 after discovery only
    (no orderbook calls, nothing banked).
  * Otherwise: sample every in-window market's book and bank one
    create-only JSONL chunk under Kalshi/probe/dt=<ET date>/.

``--snapshot-if-empty`` (arming-time only, not set on the scheduled job):
if nothing is in window, bank a discovery snapshot of today's slate
instead, so the banking path is proven end to end.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from ev.kalshi_probe.sampler import HARD_STOP_UTC, is_hard_stopped, run_probe


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    snapshot = "--snapshot-if-empty" in argv
    now = datetime.now(timezone.utc)
    if is_hard_stopped(now):
        print(json.dumps({"status": "hard_stop",
                          "hard_stop_utc": HARD_STOP_UTC.isoformat(),
                          "now_utc": now.isoformat()}))
        return 0
    summary = run_probe(now=now, snapshot_if_empty=snapshot)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
