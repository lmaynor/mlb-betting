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

## Coverage, widened gates, unpriced legs (2026-10-26)
The first demo tape (361 RFQs, 2,697 legs, ~12 RFQs/min) had ZERO priceable legs: the flow is NBA props (~2,100 legs) and
game winners (NFL/NBA/NCAAF/NHL), the feed was NFL player props. Widening the gates changes nothing until legs map.
* `ev/kalshi_game_legs.py`: game-winner fair values from ONE ParlayAPI h2h slate pull per sport (1 credit each), built as
  `KX<LG>GAME-<YY><MON><DD><AWAY><HOME>-<CODE>` tickers and round-tripped through `ev.kalshi_map.parse_event`; Kalshi and DFS
  books excluded; unknown team / 3-way / no usable book drops the game. NFL, NBA, NHL only.
  **Credits**: sports x 86400 / `--game-reload-sec` per day (default nfl+nba hourly = 48/day). ParlayAPI had ~7.4k credits left
  on 2026-10-26, so do not shorten this casually.
* Leg gates widened: `min_books` 3->2, `max_disp` 0.06->0.08, `max_staleness_sec` 600->1500 (the feeds refresh every 10-15 min, so
  600 s rejected live legs part of every cycle). Still unvalidated.
* `max_unpriced_legs` (default 0 = strict; demo job uses 2): an unpriceable leg is DROPPED from the joint, a sound upper bound on
  P(YES) (a leg is <= 1), so the lay only gets more conservative. A HOT leg always rejects. Edge still scales with ALL legs.
* Every combo decision now records per-leg status (`notes.legs_status`: OK or the gate that failed) in the tape, so "some legs fail"
  is visible instead of one `REJ_COMBO_LEG_UNVALIDATED`.
* **Edge cap (deviation from spec)**: the spec's edge is `k * 1.5c`, linear in legs: a 14-leg combo demanded 21c (NO bid ~0.78 vs a
  fair ~0.998), so a long combo could never fill. Now `min(max_combo_edge=6c, max(3.5c, k*1.5c))`.
* `max_legs` 5 -> 16 (the demo flow has 14- and 16-leg combos).
* Replay of the OLD tape against today's feed priced 13 of 2,697 legs: most game legs were for games already in progress or over
  (the slate is upcoming games), and NBA props have no mapper. That is a timing/coverage limit of replaying, not a verdict;
  a fresh run is the test. Remaining gap: a Kalshi NBA prop mapper + NBA prop consensus (the bulk of the long combos).

## Multi-sport props, spreads and totals (2026-10-26)
The demo flow is ~78% NBA player props plus game winners/spreads/totals, so the fair book now resolves those from the TICKER alone
(no Kalshi REST catalog, which is 403'd on Cloud Run egress and would be dozens of calls):
* `ev/kalshi_props.py`: `parse_prop_ticker` (`<SERIES>-<YYMONDD><AWAY><HOME>-<TEAM><INITIAL><SURNAME><JERSEY>-<N>`, N+ == Over N-0.5),
  `kalshi_player_codes` / `match_player_code` (first initial + surname letters; hyphens vanish; Kalshi DROPS non-ASCII letters,
  Nygard -> NYGRD; exactly one match or None), `register_series`.
* **Series verified against the public API 2026-10-26** (the spec's guesses for NHL/NBA were mostly wrong): NFL `PASSYDS RSHYDS RECYDS
  REC PASSTDS PASSINT PASSATT TD`; NBA `PTS REB AST 3PT PRA STL BLK`; NHL `GOAL PTS AST SAVE`. They do NOT exist: `KXNHLSOG/SHOTS/BLK`,
  `KXNBAPR/PA/RA`, `KXNFLANYTD`. `KXNFLTD` maps only the 1+ rung (anytime TD has no two-sided book market, so it stays unpriced).
* `ev/kalshi_rfq_props.py`: `PropFairIndex` built from `ev.daily_projections.load_events` (ParlayAPI bank + BettingPros live; ZERO credits).
  Exact rung with >= 2 two-sided books -> their de-vigged median; otherwise `ev.odds_board.fit_curve` over the player's rungs
  (pooled=True; interpolates, extrapolates at most the anchors' own span; flat curve never extrapolates); a single-book rung comes back
  with n_books=1 so the gate says LOW_BOOKS. Ambiguous player code, a team hint that contradicts the ticker, an unresolvable team, a
  started game -> None. DFS and Kalshi never enter a consensus. Misses carry a reason in the tape (`why`: no_game / no_player /
  ambiguous_player / team_mismatch / no_stat / no_rung).
* `InMemoryFairBook.get` falls back to the index for any prop ticker (cached; cache cleared on `attach_prop_indices`).
* `ev/kalshi_game_legs.py` now also emits `KX<LG>SPREAD-<tail>-<CODE><N>` (CODE -(N-0.5), de-vigged vs the other side) and
  `KX<LG>TOTAL-<tail>-<N>` (Over N-0.5). NHL totals are never emitted (`NHL.disabled`: shootout counts as a goal on Kalshi).
  Half-point lines only. NHL crosswalk added (repo LAK/NJD/SJS/TBL -> Kalshi LA/NJ/SJ/TB; the earlier game-winner loader was
  silently dropping those games).
* **Live check against ~2,000 OPEN Kalshi prop markets**: resolvable share is roughly 10-25% per series (e.g. NBA points 61 of 346,
  NHL points 57 of 132). The misses are mostly `no_rung`: Kalshi lists every N for a player while books quote a few rungs and the curve
  will not extrapolate past its own span; that is the intended fail-closed behaviour, and exactly the "some legs fail, not all" regime.
* **Credits**: game pulls cost 1 credit PER MARKET per sport (3 markets x 3 sports = 9/pull). The daemon now honours a daily budget
  (`--max-daily-game-credits`, default 120) by stretching the pull cadence (9 credits/pull -> every ~1.8 h). Props cost nothing.
  Game lines are NOT banked for NFL/NBA/NHL (only MLB, paused), so there is no zero-credit source for them.
* Prop freshness is whatever the banks give: `odds-bank-{nba,nhl}-props` run twice a day, `bp-live-*` hourly in season. The
  index stamps `updated_ts` from the feed's own age, so a stale bank is rejected by the staleness gate, not silently priced.

## Rung interpolation + reasons (2026-10-26, evening)
The midday demo run priced 3 of 192 legs. Besides coverage (college, soccer, games days out), spread/total legs missed because Kalshi
lists EVERY half-point rung ("wins by over 3.5 ... 17.5") while books quote one or two lines. `ev/kalshi_game_legs.py` now interpolates:
margin (spreads) / total is Normal(mu, sd) with mu fitted to the books' de-vigged probabilities (median across anchors). Rows are
`pooled=True`, `n_books` = the thinnest anchor's, `disp` = widest anchor MAD + 0.02 per sd of distance from the nearest quoted line,
and nothing beyond 1.0 sd from a quoted line is emitted. **sd values are UNVALIDATED rough league figures** (NFL margin 13.5 / total 12,
NBA 12 / 18, NHL margin 2.3, NHL totals still disabled): replace with fitted values once the tape grades them. Sanity: KC -3.5 coin flip
-> KC 'wins by over 0.5' 0.59 vs the moneyline 0.60; monotone in the rung; both teams' ladders agree.
The tape's per-leg `why` is now meaningful for game tickers: `no_rung` (we price that game, not that rung), `no_game_line` (covered league,
game not on the slate / started / unmappable team), `sport_not_covered` (NCAAF, soccer, ...), `unknown_series`.

## Running it in GCP (read-only recorder)
`deploy/setup_kalshi_rfq_recorder.sh` provisions Cloud Run Job `mlb-kalshi-rfq-recorder` (`--env production-readonly --mode record`)
plus a daily scheduler (10:00 UTC). One ~23h40m execution per day: the daemon exits cleanly at `--max-runtime-sec` and the
scheduler starts the next, leaving a ~20 min gap. A Job, not a Service: the daemon serves no HTTP port.
* **Credentials** come from Secret Manager (`kalshi-prod-api-key-id`, `kalshi-prod-private-key`) as env vars
  (`KALSHI_PROD_PRIVATE_KEY_PEM` holds the PEM, no key file on disk). The script checks the secrets exist and grants the job SA
  read on exactly those two; it never touches a value. Use a read-only Kalshi key if the UI offers one.
* **Tape** is written as immutable chunk objects `Kalshi/rfq_tape/production/{UTC day}/{HHMMSS}_{n}.jsonl` (GCS objects are not
  appendable and the job's disk dies with it), flushed every 30 s and on SIGTERM / clean exit. A failed write keeps the buffer.
  Every RFQ is recorded (single-market and combo, covered by the fair book or not), so the tape also measures how much of the
  flow we could even price.
* **Fails loud**: `max-retries=0`, and if the WebSocket has not connected within 120 s the job raises (`RFQ stream never connected
  in 120 s`). A `heartbeat:` line every 5 min (connects, RFQs seen, book legs) separates a quiet market from a dead feed.
* **Open risk -- egress**: Kalshi's CDN has 403'd shared Cloud Run egress IPs before (CONTEXT 15.11; the NFL sweep falls back to a
  laptop for that reason). The signed API host and the WS host may or may not behave the same. First execution answers it; if
  blocked, the options are a static egress IP (VPC connector + Cloud NAT) or a small GCE VM. Do not assume it works.
* **Feed**: `--feed` defaults to `EVSweep/nfl_props/latest.json` (NFL only). Missing at start is tolerated in record mode (raw RFQs
  are still taped; the reload loop retries).
* `websockets==14.1` was added to `requirements.txt`, so the next image build re-runs the dependency layer once.
* **Demo variant** (`RFQ_ENV=demo ./deploy/setup_kalshi_rfq_recorder.sh`): job `mlb-kalshi-rfq-recorder-demo`, secrets `kalshi-demo-api-key-id` /
  `kalshi-demo-private-key`, no scheduler, 30 min cap, tape under `Kalshi/rfq_tape/demo/`. A plumbing test only (demo has little RFQ flow).
* Pause: `PAUSE=1 PROJECT_ID=... ./deploy/setup_kalshi_rfq_recorder.sh`. No env in the script can arm quoting.

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
