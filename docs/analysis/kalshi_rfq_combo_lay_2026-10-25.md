# Kalshi RFQ market maker / combo lay engine (2026-10-25)

Status: CODE + OFFLINE TESTS ONLY. Nothing has been run against any Kalshi exchange. No edge is claimed.

## Thesis (unproven)
Public-book passive making on Kalshi props loses to queue priority (market_making_2026-10-12.md). RFQs bypass the
FIFO queue: a retail requester asks makers for a private quote on a market, almost always a multi-leg combo (MVE)
where they BUY YES. We hold a de-vigged 3+ book consensus for the standard lines retail builds combos from, so we can
price the joint probability and quote only `no_bid` (lay the parlay) with a padded margin.

Whether retail combos are actually priced richly enough for this to clear fees is the open question. The record /
shadow modes exist to answer it at $0 risk before anything is sent.

## What shipped
| File | Role |
|---|---|
| `mlb/execution/kalshi/rfq_client.py` | `RFQClient` REST (`get_rfqs`, `create_quote`, `confirm_quote`, `delete_quote`, `get_quotes`) + `KalshiRFQStream` (signed WS, `communications` channel, jittered exponential reconnect) |
| `ev/kalshi_rfq_pricer.py` | `InMemoryFairBook`, `LegFairValue`, `KalshiRFQPricer` (single-leg two-sided; combo lay-only) |
| `mlb/execution/kalshi/rfq_maker.py` | `RFQLedger`, `RFQRiskGate`, `RFQMaker` (last-look shield), `RFQRecorder` + `RFQTape` |
| `scripts/kalshi_rfq_daemon.py` | `--mode record\|shadow\|live` |
| `tests/test_kalshi_rfq.py` | 59 offline tests |
| `mlb/execution/kalshi/client.py` | `request()` gained `max_retries` / `paced` kwargs (defaults unchanged) so confirm can skip backoff and pacing |

## Deviations from the pasted spec (and why)
1. **Quoting is demo only.** The executor refuses production in code (`ProductionNotEnabled`); that guard is kept for
   every order / quote / confirm path. Real RFQ tape comes from a separate READ-ONLY production mode (below).
2. **Wire format from the live docs (2026-10-25), not the spec**: quote body is `{rfq_id, yes_bid, no_bid,
   rest_remainder[, post_only]}`; there is no `replace_existing` (a new quote replaces the old one server-side);
   confirm is `PUT /communications/rfqs/{rfq_id}/quotes/{quote_id}/confirm` (the bare quote path is deprecated);
   combo legs arrive as `mve_selected_legs = [{event_ticker, market_ticker, side}]` on ONE combo market ticker.
   Confirm window: 30 s standard, 3 s HVM, and every combo is an HVM.
3. **Leg `side` matters.** A `no` leg contributes `1 - p_yes`. The spec priced every leg as YES.
4. **Blend.** Uses `calibration.market_blend_prob` (c=0, i.e. pure market, for every NFL stat and every stat without
   a walk-forward fit) instead of a flat 85/15; the |model - market| > 0.15 guard is kept.
5. **Fees.** ROI-on-collateral is net of a reserve using the taker formula, because the quote-creator fee schedule is
   not documented.
6. **`game_key`** follows `ev.kalshi_map` (`nfl:2026-10-25:BUF@KC`), not `date|AWAY@HOME`.
7. **Single-leg quoting is OFF by default** (`--quote-single-leg`): the repo's own study says passive prop making is
   -EV; the engine's thesis is combos.
8. **Combos with P(YES) > 0.35 are rejected** (`REJ_COMBO_NOT_LONGSHOT`): v1 is lay-only; the spec did not say what to
   do above the threshold.
9. **Negative SGP correlations are clamped to 0** and unknown same-game pairs default to rho 0.15. `SGP_CORR_TABLE`
   values are the spec's, UNVALIDATED (the research/sgp-empirical-rho branch found real but small prop correlations,
   so they err conservative for a lay).
10. **Aborting is not free**: Kalshi counts a failure to confirm toward the same penalties as any missed confirm. The
    shield therefore trips the kill switch after `max_aborts_per_window` (3 per 10 min) -- repeated aborts mean a
    stale book, not sniping.

## Needs verification on the demo exchange before trusting
* `accepted_side` semantics. Code assumes it names the side of OUR bid that was hit (we hold that side). With a
  combo quote that has `yes_bid = 0` the only valid value is `no`; anything else aborts (`ABORT_SIDE_MISMATCH`).
  If the meaning is the reverse, every fill would abort -- fail-safe, but it must be checked.
* Valid tick grid for MVE markets (code uses 1c, rounding DOWN; a coarser grid yields a 400, not a bad fill).
* `GET /communications/rfqs` status values (code sends `status=open`).
* Whether confirm is idempotent / what a late confirm returns.

## Fair-value feed
`--feed` takes the sweep's `EVSweep/nfl_props/latest.json` (storage key or local copy) or any rows with the keys in
`ev.kalshi_rfq_pricer.leg_from_row`. Only NFL props are wired: those are the only Kalshi prop tickers the repo maps
(`ev/kalshi_props.PROP_SERIES`). NBA/NHL prop and MLB game-line loaders are NOT built (no Kalshi ticker mapping or
consensus join for them yet) -- a combo containing any unmapped leg is rejected whole, by design.
`--hot-games-file` is a JSON list of game_keys flagged by a lineup/injury feed (no such feed is wired automatically).

## Read-only production recorder
`KALSHI_RFQ_ENV=production-readonly` (or `--env production-readonly`) records the REAL RFQ flow. It cannot send a
quote, by three independent layers:
1. `RFQReadOnlyClient` has no `create_quote` / `confirm_quote` / `delete_quote` methods at all.
2. `KalshiClient.request()` raises `ProductionNotEnabled` for any non-GET method on this env BEFORE any network call
   (so even the inherited `create_order` helper is dead).
3. The daemon refuses every mode but `record` for this env, whatever `KALSHI_RFQ_LIVE` says; the WebSocket only
   subscribes.
Credentials are separate from the demo ones: `KALSHI_PROD_API_KEY_ID` / `KALSHI_PROD_PRIVATE_KEY_PATH` (demo creds never
satisfy it and vice versa). Tape goes to `data/kalshi_rfq/production/`. The production REST host
(`external-api.kalshi.com`) and WS host come from the docs and the market-maker repo; not yet exercised.

## Env flips
| Env | Values | Effect |
|---|---|---|
| `KALSHI_RFQ_ENV` | `demo` (default), `production-readonly` | which exchange the daemon connects to |
| `KALSHI_RFQ_MODE` | `record` (default), `shadow`, `live` | run mode (CLI `--mode` wins) |
| `KALSHI_RFQ_LIVE` | `1` | arms `live`; without it `--mode live` refuses to start (checked in the daemon AND in `RFQMaker`) |
Going live on the DEMO exchange is therefore `KALSHI_RFQ_MODE=live KALSHI_RFQ_LIVE=1`, no code change. There is
deliberately NO env that enables quoting on production: that is a code change plus the verification list below.

## Running
```
pip install websockets                        # only the daemon's stream needs it
python scripts/kalshi_rfq_daemon.py --mode record --feed path/to/latest.json
python scripts/kalshi_rfq_daemon.py --mode shadow
KALSHI_RFQ_LIVE=1 python scripts/kalshi_rfq_daemon.py --mode live      # DEMO exchange only
KALSHI_RFQ_ENV=production-readonly python scripts/kalshi_rfq_daemon.py --mode record   # real tape, GET-only
python scripts/kalshi_rfq_daemon.py --reset-kill                        # kill switch is manual-reset-only
```
Kill switch key: `kill_reason:kalshi_rfq` (own key; never halts the other venues in the same ledger).

## Promotion criteria (suggested, not met)
Record: >= 2 weeks of tape, our would-be no_bid vs the price combos actually traded at afterwards. Shadow: PLAN rate,
REJ histogram, expected P&L (sum of ev * contracts on PLAN rows -- expected, NOT realised). Live-on-demo: confirm
latency p99 < 1 s, zero ABORT_SIDE_MISMATCH. Only then discuss production.
