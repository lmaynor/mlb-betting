# contest/ -- the Bankroll Challenge engine

A private, invite-only, play-money betting contest. Sport-agnostic shared infra (not a pillar): one `contest` row per contest, `ContestRules` JSON on it, one sport per
contest (`ncaaf` or `ncaab`). Money is integer cents; times are ISO-8601 UTC text in the DB and aware datetimes in code; the server clock is the only clock.
Everything is OFF unless `CONTEST_ENABLED` is on. Contract: `CONTEXT.md` section 28. Plan and decisions: `docs/designs/bowl-bankroll-contest-scope.md`.
Evidence: `docs/analysis/contest_sportsbook_experiments_2026-11-17.md`. Gotchas: `docs/solutions/` (grep `contest`). Vocabulary: `CONCEPTS.md`.
Launch, flags and rollback: `docs/solutions/conventions/contest-launch-and-rollback-runbook.md`.

## Modules (dependency order, roughly)

| module | job |
|---|---|
| `odds.py` | exact-decimal American odds, integer-cent half-up payouts, de-vig |
| `rules.py` | `ContestRules` (every tunable number, per-sport defaults, JSON round trip) |
| `slips.py` | `Leg` / `Slip` / `Quote` / `GameInfo`, `validate_slip` (limits, close buffer, stale / moved quotes, alt legs, SGP, halftime gate), round robins |
| `margin_model.py`, `altlines.py` | the margin model and alt-line pricing (also used by cash out) |
| `sgp.py` | same-game parlay pricing (Gaussian copula) |
| `cashout.py` | cash-out value from the current board |
| `grading.py` | leg results from a final score; settling straights, parlays, teasers, SGPs |
| `schema.py`, `store.py` | DDL (`ensure_schema`, additive) and every DB operation: each state change is ONE transaction, every money movement a ledger row |
| `blackjack.py`, `lifeline.py` | the hidden blackjack table under $100 (deck and hole card stay on the server) |
| `names.py` | display-name rules (unique per contest, reserved words, 3 renames) |
| `espn.py`, `sync.py`, `refresh.py` | ESPN scoreboard -> games, quotes, live state, grading; the scheduled pass (the only place that talks to ESPN) |
| `halftime.py` | Kalshi pricing and the halftime window state machine (OFF) |
| `notify.py` | Discord announcements (OFF) |
| `ratelimit.py` | per-user sliding-window limits |
| `service.py` | requests in, the page's feed out; organiser actions; no HTTP |
| `api.py` | Flask routes, registered from `main.py` behind an ImportError guard |

Tables: `contest`, `contest_invite`, `contest_entry`, `contest_game`, `contest_quote`, `contest_bet`, `contest_leg`, `contest_ledger`, `contest_blackjack`,
`contest_halftime`, `contest_notice`, `contest_admin_log`.

## Rules that must not be broken

- the client never names a price: a pick carries the line and price the player SAW and the engine refuses with `PRICE_CHANGED` (carrying the current one);
- nobody sees another player's pick before its game kicks off: read others' bets only through `store.visible_bets`;
- the ledger is append-only and `store.reconcile(engine, contest_id)` must return `[]`;
- new behaviour ships OFF behind a flag; nothing here places a real-money order.

## Tests

Offline (SQLite in a temp file; no network, no GCS). The repo `conftest.py` needs sklearn / xgboost, so run the contest files without it:

```
python -m pytest tests/test_contest_*.py -q --noconftest
```

The same files run on a throwaway Postgres with `CONTEST_TEST_PG_URL=postgresql+pg8000://...` (see `docs/solutions/logic-errors/contest-tests-on-postgres-teardown-and-leaked-connections.md`;
every new table must be added to `tests/_contest_engine.py`). Frontend mirrors (`beezy-vip/lib/contest/`: odds, slip, cash out, margin model, SGP) have golden
parity tests against the Python engine: `beezy-vip/tests/contest-*.test.ts*`.

## Scripts

`scripts/contest_admin.py` (create / invite / refresh / status / rename / adjust), `scripts/contest_history_pull.py` + `scripts/contest_sportsbook_research.py`
(research, read-only), `scripts/contest_halftime_capture.py` (records real halftimes; note its football total series name is stale, see the Kalshi solutions entry),
`deploy/setup_contest_scheduler.sh` (jobs and the one-step turn-on).
