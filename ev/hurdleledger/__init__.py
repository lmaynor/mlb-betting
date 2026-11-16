"""
ev.hurdleledger -- OPS-12, the mission-hurdle profit ledger.

The scoreboard for the hurdle Luc adopted Nov 16, 2026 (MISSION.md): within 90 days of
Nov 16, 2026, combined net profit of at least $500/month sustained for 2 consecutive
months, counting three streams together:

  1. automated Kalshi betting P&L -- read from AUT-09's audit settlement records
     (live mode only; paper settlements are reported, never counted);
  2. Luc's logged recreational book bets -- an append-only structured log written
     through this package's CLI (see rec.py);
  3. paid subscription revenue -- a records file in the same ledger data dir
     (pre-launch today: a true zero with a note, not an error).

Every monthly figure is computed from source records. Nothing here accepts a
hand-entered total. The package is read-only toward AUT-01, AUT-09, PRD-04 and
RES-11: it parses AUT-09's audit JSONL files from disk and imports nothing from
those packages.
"""
