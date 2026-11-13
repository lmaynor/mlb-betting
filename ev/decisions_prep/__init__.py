"""ev.decisions_prep -- non-API prep for the Decisions-API routes adopted in
Argon's Nov 13, 2026 expansion ruling
(~/workspace/beezy/profitability/argon-decisions-api-expansion-VERBATIM-2026-11-13.md).

Contents per adopted route: the Decisions question schemas (built in the
merged client's published envelope style -- model / input / questions-map,
see nba/minutes_restriction/decisions_client.py), the deterministic
pre-filters and gates the ruling requires to run BEFORE any API call, and
the input-capture jobs of sequencing ruling 3(c) clock 1.

This package contains NO route implementations and makes NO live API calls.
Fixtures live under tests/fixtures/decisions/<route>/ with a README each.
"""
