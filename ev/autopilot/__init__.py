"""
ev.autopilot -- AUT-01 Phase 0 SHADOW for Autopilot (RUL-20261115-autopilot-design,
RUL-20261115-autopilot-clv-definition).

SHADOW ONLY.  This package evaluates the AUTOPILOT_ELIGIBLE signal stream against banked
Odds API snapshots (the DEC-08 accumulator feed), simulates fills under the ruling's
execution policy, and logs every intent / skip / simulated fill to its own JSONL ledger
so the Phase 0 dual CLV bar can be scored.  There is deliberately NO order path here:
nothing in this package imports a venue client, a signing helper, or a paper/live venue,
and no credential of any kind is read.  Real execution is a later phase behind separate
gates (AUT-02..AUT-08); this build cannot place, mirror, or route an order.

The kill switch is the config flag AUTOPILOT_SHADOW_ARMED (default OFF): with it unset,
the stream runner refuses to run outside an explicit --dry-run analysis invocation, so
merging this package changes no live behavior.  NOV-01's executor (ev.novig) is untouched.
"""
