# Kalshi RFQ market maker / combo lay engine (2026-10-25)

Status (updated 2026-11-01): code merged (PRs #115, #120-123), 101 offline tests pass. Only the DEMO recorder has run (4 executions,
2026-10-26/27, 546 tape chunks, 2,730 RFQs). The PRODUCTION read-only recorder is NOT provisioned (no job, scheduler or `kalshi-prod-*`
secrets in GCP). No edge is claimed, and the demo tape cannot support one (see "Demo tape findings" and "Production recorder runbook").

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
  REC PASSTDS PASSINT PASSATT TD`; NBA `PTS REB AST 3PT PRA STL BLK`; NHL `GOAL PTS AST SAVE`. Not found: `KXNHLSOG/SHOTS/BLK`,
  `KXNBADD/TD`. Re-probed 2026-11-02: `KXNBAPR` and `KXNFLANYTD` DO return series metadata but, like `KXNBAPA/RA`, had zero open markets (so
  nothing to map yet; re-check before adding). `KXNFLTD` maps only the 1+ rung (anytime TD has no two-sided book market, so it stays unpriced).
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

## Demo tape findings (read 2026-11-01; `Kalshi/rfq_tape/demo/`, 2026-10-26 to 10-27)
* 2,730 RFQs: 2,720 combos, 10 single-market. Combo size: 2,055 two-leg, 234 fourteen-leg, the rest 3-18.
* Decisions: 16 QUOTE (0.6%, all 2-3 legs, est. ROI 4-6%), 2,636 `REJ_COMBO_LEG_UNVALIDATED` (97%), 58 `REJ_COMBO_NOT_LONGSHOT`.
  Coverage of legs, not risk, is the binding limit.
* The 490 follow-up trade rows are NOT usable as evidence: the demo prints a flat 0.45 yes / 0.55 no for 100 contracts on most
  of them (demo bot), and `fetch_trades_after` matches by market ticker, which differs from the RFQ's combo ticker
  (`KXMVECROSSCATEGORY0-...` vs `KXMVECROSSCATEGORY-...`). Do not compare our `no_bid` to those prices.
* Per-leg `legs` diagnostics are present on only 32 records, so the `why` histogram cannot be built from this tape.
* Not exercised: `quote_accepted`, confirm latency, `accepted_side`, tick grid. The recorder never quotes.

## Quoting more, long combos first (2026-11-01; all UNVALIDATED, quoting still demo-only)
Why the demo tape's 14-leggers never quoted: (a) 234 RFQs are only THREE distinct combos (one demo bot repeating), each ONE game
(winner + 13 NBA props), so they hit `REJ_SGP_DISABLED` / `REJ_COMBO_LEG_UNVALIDATED`; (b) with SGP allowed, the additive pair buffer
(2.5c per PAIR, 91 pairs) saturated at min(p) and rejected every same-game group of 4+ priced legs as `REJ_COMBO_NOT_LONGSHOT`;
(c) in shadow/live the risk gate would still reject them: 64.18 contracts x no_bid 0.86-0.93 = $55-60 > the $50 per-quote cap
(`REJ_RISK_SIZE`), and one combo charges its whole collateral to EACH of 14 players (cap $75).
Changes: `PricerConfig.sgp_buffer_cap` (0.075, i.e. 3 pairs' worth: groups of <= 3 legs price exactly as before; a 14-leg same-game combo at
p=0.5 per leg now quotes at no_bid ~0.86 instead of rejecting); daemon `--allow-same-game` also via `KALSHI_RFQ_ALLOW_SAME_GAME=1`;
new `--max-liability-per-quote / --max-exposure-per-game / --max-exposure-per-player / --max-open-quotes` (defaults unchanged); recorder
script defaults `MAX_UNPRICED_LEGS=16` and `ALLOW_SAME_GAME=1` (record-only, so it only widens the would-be-quote tape).
To quote long combos on the DEMO exchange: `KALSHI_RFQ_LIVE=1 python scripts/kalshi_rfq_daemon.py --mode live --allow-same-game
--max-unpriced-legs 16` (the risk-cap flags are no longer needed at the loosened defaults).
**Risk caps loosened for scaffolding (owner decision 2026-11-01):** `RFQConfig` defaults are now $1000 per quote / $5000 per game / $2500 per player /
100 open quotes (were 50 / 150 / 75 / 25), so the demo exchange exercises the whole pipeline. The gates are to be set later, before any production
quoting exists. Unchanged: the production-quoting refusal in code, `KALSHI_RFQ_LIVE=1`, the kill switch, the last-look shield, abort limits and
the per-game cooldown. The `--max-*` flags above still override. Tests of the gate mechanics pin the old values via `TIGHT`.
Caveats: dropping unpriced legs is a sound upper bound only if the PRICED legs and their correlations are right (copula rho 0.15 is ~3x the
measured pairwise lift, plus the capped buffer). Whether other makers undercut our no_bid (0.86-0.93 against a fair ~0.995+) is unknowable
offline; fill rate is the open question. "14-legs are -EV for retail" rests on 3 distinct combos so far. A leg count above 8 or so with
only 2-3 legs priced gives a loose bound, so expect those quotes to be uncompetitive until the NBA prop mapper's coverage improves.

## Game legs from the banked feeds (2026-11-03; `ev/kalshi_game_banked.py`, UNVALIDATED)
Why: `fetch_game_rows` pulled a live ParlayAPI slate (1 credit per market per sport; the 120/day budget stretches the cadence to ~1.8 h, far
beyond the 1500 s staleness gate, so live game rows were stale most of the time) and with no `PARLAY_API_KEY` every game / spread / total leg
was unmapped. The same lines are already banked for free: BestBook ladders (`Odds/bestbook_live/{nfl,nba,nhl}/game_lines_latest.json`, hourly,
~16 venues, ~90 spread and ~90 total rungs per NFL / NBA game) and, for NBA / NHL, the banked ParlayAPI snapshot (`nba.game.odds_feed`).
* Order per sport: BestBook (<= 180 min old) -> banked ParlayAPI (NBA / NHL only; NFL game lines are not banked) -> the live pull, which now
  runs ONLY for a sport the free feeds could not serve. `--game-source live` (or `KALSHI_RFQ_GAME_SOURCE=live`) restores the old behaviour;
  `--banked-reload-sec` (300) is how often the free feeds are re-read. No new env is needed on the recorder job.
* Rows go through the SAME `slate_rows` as the live path (leave-Kalshi-out, DFS excluded, Kambi folded, NHL totals disabled, rung
  interpolation); BestBook quotes are converted to the slate shape first. `updated_ts` is the feed's own timestamp (BestBook `fetched_at`),
  never the read time, and started games are dropped.
* Measured on the 2026-11-03 03:50 UTC files against Kalshi's OPEN markets: NFL game / spread / total 75% / 75% / 76% of open markets priced
  (24 / 306 / 228); NHL game 22 of 98, spread 44 of 108 (the rest are games outside BestBook's window); NBA 0 (all of that night's games had
  started and the next slate was not posted). 12 NFL games -> 24 ML + 755 spread + 674 total rows. This is coverage, not edge.
* **Open limit: staleness.** BestBook refreshes hourly and the pricer rejects a leg older than `max_staleness_sec` (1500 s), so a banked row
  is usable for roughly the first 25 of every 60 minutes unless that gate is widened for game lines. The gate was NOT changed (it is a risk
  gate; the owner sets it). Sanity: JAC at BAL comes out 0.79 to win and a coin flip at JAC -8.5, internally consistent.

## Point-in-time replay of REAL combos (2026-11-01; `ev/kalshi_rfq_replay.py`, `scripts/kalshi_rfq_replay.py`)
Kalshi's public `/markets?mve_filter=only&status=settled` lists every settled combo with legs, last price, volume and result (no key). A combo
market is created when its RFQ is accepted, so `created_time` ~ trade time. The replay prices each combo with the latest banked NFL sweep
cycle (`EVSweep/nfl_props/{day}/cycle_*.csv`, keyed by Kalshi ticker) stamped BEFORE that time (no look-ahead, max age 40 min), once with a
permissive "measure" config and once with the daemon's quote config.
```
python scripts/kalshi_rfq_replay.py pull   --start 2026-10-16 --end 2026-11-01 --out combos.jsonl     # public API, capped sample per window
GCS_BUCKET=<data bucket> python scripts/kalshi_rfq_replay.py replay --combos combos.jsonl --csv out.csv
```
Defects found and fixed in the join (`python scripts/kalshi_rfq_replay.py audit --combos combos.jsonl` prints the per-series table):
1. **`<SERIES>PROT` "Protected" twins were unregistered** (~half the NFL prop legs and most NBA points legs in real flow). `ev/kalshi_props.PROTECTED_SUFFIX`
   now maps each twin to the base stat (priced as the base P(over); approximation: a mid-game exit settles at the pre-kickoff fair price).
2. **Other rungs were not interpolated**: the replay now builds the live `PropFairIndex` from each cycle's exact rows.
3. **Started games were priceable from a pre-game snapshot** (a bias: a live parlay is not priced by a pre-game consensus). `SnapshotStore.book_at` now drops
   legs/index entries of games whose `commence` <= the combo's created_time (the live index drops started games too).
4. **Snapshot max age** is now a parameter (`--max-age-min`, default 120). The sweep runs every 30 min early on a game day but hourly near kickoff
   and stops at kickoff, so the old 40-min gate threw away ~half the combos; results at 40 min are the same sign (below).

Why coverage is still low: this is mostly NOT cleansing. Of 1,248,194 leg occurrences in 169,186 traded combos, 5.1% hit. The misses (`audit`, 120 min):
* **Sports with no data banked** (largest): NCAAF game/spread/total (~17% of all legs), UCL / EPL / Serie A soccer (~8%), NFL/NBA/NHL game, spread, total
  and team-total lines (`no_game_line`: the sweep banks props only), NBA props (`unknown_series`: no NBA index in the replay; NBA is banked twice a day, best price only).
* **Anytime TD** (33.8k + 30.2k legs): 74% of the Protected twin's misses are `no_stat` (not swept).
* **Players books do not quote** (`no_stat` 16-29% on yardage props, `no_rung` 15-31% on receptions): Kalshi lists every player and rung; books quote the
  top players. The curve will not extrapolate past its anchors; a Poisson/NegBin family fit from one line would add some rungs (modelling, not cleansing).
* **Games outside the sweep window** (`no_game` 9-21% of base NFL props) and **days with no cycles at all** (10-15 TNF 4,740 combos, 10-16, 10-20, 10-23, 10-27,
  10-30): the sweep did not run or did not bank them. `bettingpros_live/nfl` and `bestbook_live/nfl` hold only `latest.json` (overwritten), so there is
  nothing to backfill from: **start banking point-in-time snapshots of the live feeds** if replay coverage matters.

Second pass (2026-11-02): raw all-book NFL / NBA / NHL snapshots (`EVSweep/nfl_props/raw`, `OddsAccum/{basketball_nba,icehockey_nhl}/raw`; NBA/NHL props are
banked ~10-14x/day since ~10-29, 2x before), the alternate-line ladder fit (`ev/prop_ladder.py`), one-sided de-vig (anytime TD, alternate ladders), and the
name-code fix (hyphenated first names: `Amon-Ra St. Brown` -> `astbrown`). `audit` hit rate on all legs 5.1% -> 13.2%. Replay (120 min max age; every pull
window truncated at 8,000 scanned markets, so a SAMPLE):
| | cycle rows only | + raw books, ladder, one-sided |
|---|---|---|
| replayed | 38,283 | 60,964 |
| fully priced | 243 | 2,653 |
| would-quote / would-fill | 4,025 / 44 | 10,319 / 249 |

* Fully priced (n=2,653 over 11 days): mean yes price 0.212, mean p_true 0.257, realized yes 9.4%; laying at the traded price earned +10.9c per contract after
  fees; **day-cluster 95% CI [-1.6c, +17.7c]**, volume-weighted +6.6c. It is NOT uniform: cross-game combos (n=332) hit 27.7% against a 27.0% price (-1.7c, fair);
  SAME-GAME combos (n=2,321) hit 6.8% against a 20.4% price (+12.7c), and 10-23 / 10-27 alone are 1,548 combos that almost never hit (a few games each). The
  retail-overpay signal is an SGP signal resting on a handful of games, not a broad one.
* Bounded (n=20,966): +3.4c (CI [-3.8c, +7.6c], 17 days). By leg count: 2-3 legs +7.4c, 4-7 +3.9c, 8-15 +1.7c, 16+ -1.0c.
* (Superseded, see "Correlations and calibration" below: the combo-level `p_true` 25.7% vs 9.4% realized is mostly a few games. 10-23 / 10-27 alone are
  1,548 combos riding one or two games that did not hit. The 8,881-leg calibration is the reliable check.)
* **Would-fill (n=249, 10 days): -21c per contract** (naive se 3c; 61% same-game). Where our bid reaches the price that traded, the combos traded at a mean yes
  price of 0.55 against our p_true of 0.21 and hit 47%: the market was right and we were not. Adverse selection by construction: our quote only fills where we
  disagree with the market most.
## Correlations and calibration (2026-11-02; `scripts/kalshi_sgp_rho.py`, tests/test_kalshi_sgp_rho.py)
Data: `kalshi_rfq_replay.py legs` exports each combo's priced legs (fair P(YES) at creation); settled leg results come from the public API (8,881 of 9,014 legs).
**1. Market-implied rho (what the platform charges).** 785 two-leg combos with both legs priced (612 same-game). Cross-game 2-leg combos trade at a median 1.016x the
product of the legs, so the platform adds essentially no markup over multiplying the leg prices. Same-game, DIFFERENT players: priced near independence
(rec yds x rec yds lift 1.02, rec yds x receptions 1.07, rec yds x rush yds 1.03, pass yds x rec yds 1.09; implied rho ~0.0-0.1; all NFL receiving, NBA is too thin).
SAME player (rec yds x receptions) lift 1.53. The pooled implied rho is 0.108 (0.081 margin-adjusted) against our 0.15 default.
**2. Realized rho (what happens).** Gaussian-copula MLE on every pair of priced legs inside a game (YES/YES), game-clustered bootstrap (40 reps):
| pair | pairs / games | lift | rho [95% CI] | old table |
|---|---|---|---|---|
| NBA points x rebounds (diff players) | 13,732 / 68 | 1.34 | 0.16 [0.10, 0.25] | 0.15 |
| NBA points x threes | 12,291 / 73 | 1.40 | 0.18 [0.10, 0.30] | 0.15 |
| NBA assists x threes | 5,170 / 66 | 1.46 | 0.28 [0.20, 0.40] | 0.15 |
| NBA assists x rebounds | 5,930 / 64 | 1.37 | 0.20 [0.10, 0.30] | 0.15 |
| NBA points x points (diff players) | 15,282 / 96 | 1.21 | 0.14 [0.05, 0.25] | 0.15 |
| NFL pass yds x rec yds | 7,907 / 42 | 1.13 | 0.26 [0.15, 0.35] | 0.38 |
| NFL rec yds x rec yds | 6,430 / 41 | 1.10 | 0.04 [-0.05, 0.15] | 0.15 |
| NFL rec yds x rush yds | 10,359 / 42 | 1.07 | 0.00 [-0.05, 0.10] | 0.15 |
| NFL rush yds x rush yds | 3,325 / 41 | 0.89 | -0.14 [-0.25, -0.05] | 0.15 |
| NFL pass yds x rush yds | 5,715 / 42 | 0.98 | -0.14 [-0.25, 0.05] | -0.15 |
| SAME player: NFL rec yds x receptions | 1,946 / 41 | 1.40 | 0.76 [0.60, 0.85] | (rejected) |
| SAME player: NBA points x threes | 1,740 / 68 | 1.58 | 0.70 [0.60, 0.80] | (rejected) |
NBA different-player rho is a stable 0.14-0.28 (our flat 0.15 was about right, assists/threes pairs higher); NFL receiving-corps pairs are ~0 (our 0.15 and the spec's 0.38
were too high). `SGP_CORR_TABLE` now holds these point estimates (different players only; pairs with CIs wider than ~0.3, NHL and pass TDs x pass yds, stay on the default).
**3. Marginals are NOT overstated.** Single-leg calibration (our fair P(YES) vs the settled outcome, n=8,881): NFL realized runs +2 to +5 points ABOVE our p in the mid bins;
NBA +9 to +13 points above p for p in 0.35-0.80; NHL +9 to +21 (n=306). Not understood: retail picking legs on information beyond the consensus, or a bias in the
consensus. It means p_true errs low for the joint, not high.
**4. Same-player combos.** The pricer rejects them (REJ_SGP_SAME_PLAYER, 5,640 in the replay). The market charges lift 1.53 where the realized lift is 1.40 (rho 0.76): a
possible lay edge, but one cell with a few dozen games; not modelled.
Verdict: the join is good enough to measure correlations and they are now measured for NFL and NBA. What is NOT established: that retail overpays. The lay-at-market
result (+10.9c) is mostly same-game combos from a few games (CI [-1.6c, +17.7c]), and the would-fill set loses 21c. Do not quote beyond the demo exchange until a
held-out set of games confirms a positive lay result with the new table.

## Production recorder runbook (NOT yet done)
Why: only real retail combo flow can answer whether combos are priced richly enough to lay after fees. Cost: a read-only key,
2 secrets, 1 Cloud Run Job, 1 scheduler; ParlayAPI game pulls capped at 120 credits/day (`KALSHI_RFQ_GAME_BUDGET`).
1. **Owner decision:** create a production Kalshi API key, READ-ONLY if the UI offers it. The daemon never sends a non-GET on this env,
   but a scoped key is the fourth layer.
2. **Secrets** (this script never sees values):
   ```
   printf '%s' "<key id>" | gcloud secrets create kalshi-prod-api-key-id --data-file=- --project=$PROJECT_ID
   gcloud secrets create kalshi-prod-private-key --data-file=/path/to/key.pem --project=$PROJECT_ID
   ```
3. **Image:** confirm `gcr.io/$PROJECT_ID/mlb-betting:latest` contains `scripts/kalshi_rfq_daemon.py` and `websockets` (the demo job already
   ran on it, so a rebuild is only needed if the code changed since 2026-10-26).
4. **Provision:** `PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_kalshi_rfq_recorder.sh` (creates `mlb-kalshi-rfq-recorder` + `mlb-kalshi-rfq-recorder-daily`, 10:00 UTC).
5. **First execution by hand, not the scheduler:** `gcloud run jobs execute mlb-kalshi-rfq-recorder --region=us-central1 --project=$PROJECT_ID`.
   Within ~3 min read the logs. Pass: a `heartbeat:` line with connects >= 1 and RFQs seen > 0. Fail: `RFQ stream never connected in 120 s`
   (blocked Cloud Run egress or a rejected key).
6. **If egress is blocked:** the demo job connected from Cloud Run, so only the production hosts (`external-api.kalshi.com` + WS) are in
   doubt. Fallbacks, in order: static egress IP (VPC connector + Cloud NAT), a small GCE VM, or run the daemon from the laptop.
7. **Check the tape** after a day: `gsutil ls gs://<bucket>/Kalshi/rfq_tape/production/<day>/`; RFQ rate, combo/single mix, share of legs
   priced.
8. **Before trusting any P&L-style comparison**, fix the follow-up join (see Demo tape findings): key trades to the RFQ's market via the
   combo ticker mapping, or record the RFQ's own `quote_executed` / settlement instead of the public trade tape.
9. **Pause / stop:** `PAUSE=1 PROJECT_ID=... ./deploy/setup_kalshi_rfq_recorder.sh`; cancel a running execution with
   `gcloud run jobs executions cancel`.
Note: `deploy/pause_mlb_offseason.sh` already excludes `mlb-kalshi-rfq-recorder-daily` from the offseason pause; until step 4 runs, that
entry matches nothing.

## Promotion criteria (suggested, not met)
Record: >= 2 weeks of tape, our would-be no_bid vs the price combos actually traded at afterwards. Shadow: PLAN rate,
REJ histogram, expected P&L (sum of ev * contracts on PLAN rows -- expected, NOT realised). Live-on-demo: confirm
latency p99 < 1 s, zero ABORT_SIDE_MISMATCH. Only then discuss production.
