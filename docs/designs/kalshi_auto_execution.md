# Kalshi lag-trading: buy Kalshi when it is soft to the sportsbooks -- design spec

Status: DRAFT for owner review (2026-10-03, rev 3). Nothing here is implemented, deployed, or
authorized to trade real money. Every numeric default is an unvalidated starting point to be
calibrated in shadow mode (s8), not a measured result.

## IMPLEMENTATION STATUS (2026-10-04) -- built and verified on Kalshi DEMO; no production path

Code: `mlb/execution/kalshi/` (fees, price_grid, book, sizing, config, mapping, consensus, gates,
risk, ledger, venue, client, runner); tests `tests/test_kalshi_exec_{core,pipeline,runner}.py`
(99 passing). Run: `python -m mlb.execution.kalshi.runner run --mode shadow|demo [--enable]`,
`probe`, `fund-shard`, `status`, `reset-kill`. Bankroll $1,000 notional; caps per-bet $50 /
per-game $40 / daily-deploy $100 / daily-loss $50 (config.py; all env-overridable, KEXEC_*).

Verified against the REAL demo exchange: signed orders fill; IOC no-fill leaves no resting order;
duplicate `client_order_id` -> HTTP 409 (idempotent); buy-NO via `ask @ 1-p` fills on the NO side at
the right price; real pipeline (live demo book -> gates -> Kelly -> IOC order) executed 42 contracts
@ 45c, Kelly size exactly the floor of lambda*f*, second pass REJ_DUP. NOTE the strategy-to-order run
used a SYNTHETIC consensus (mechanics only); no real +EV decision has been observed yet because
the evaluable window is pre-first-pitch daytime/evening and the runs happened overnight.

Findings that changed the design:
1. **Fee multiplier is per-series and NOT 1.0.** `GET /series/KXMLBGAME` -> `fee_multiplier 0.5`
   (demo AND production). Runner reads it per series; every fill's actual fee is compared to the
   model and a material excess trips the kill switch. A 1-contract order on an in-progress game was
   charged at 1.0, so do not assume it is constant.
2. **The demo exchange is sharded.** MLB markets sit on `exchange_index` 3 while the account's cash
   is on shard 0; orders 404 (`insufficient_shard_balance`) until cash is moved (`fund-shard`).
   Cash is tracked per shard. Whether production is sharded is unverified.
3. **Kalshi nets offsetting YES/NO on one market** and returns $1 at once; ledger exposure
   (`filled_cost`) is therefore conservative when a signal flips within a day.
4. **Demo cash is $94.84 of mock money, not $1,000.** $1,000 is the notional Kelly bankroll;
   orders are additionally capped by the cash on the market's shard.
5. **`kalshi_vs_books` game_rl join bug -- FIXED 2026-10-03** (docs/solutions/integration-issues/
   kalshi-run-line-semantics-mismatch.md): Kalshi's "SD wins by over 1.5" is the books' "SD -1.5", and
   the books' "AWAY +1.5" is the complement of "HOME wins by over 1.5". The executor still does not map
   KXMLBSPREAD in v1 (needs its own verified mapping + Kalshi-side settlement check), but the
   proposition semantics are now known: Kalshi SPREAD-T-N == book T -N.
6. **PHASE 0 RESULT (aligned re-run 2026-10-03; supersedes the contaminated -0.1%/n=646):** over
   2026-08-10..10-03, game_ml + nrfi_ou, Kalshi-side flags with gross EV >= 3%: **n=73** (the old scan's
   646 was ~90% misalignment artifacts). Gross ROI +3.5% (t=0.26); net of Kalshi's fee +1.5%
   (t=0.12); through the executor's own gates (net EV 3-10%): n=8, ROI -12.4% (noise). So at the
   8x/day snapshot cadence the lag opportunity is RARE (~0.15 gated trades/day) and UNPROVEN either
   way; whether a 5-minute poll finds materially more is exactly what shadow mode must answer. Expect
   few trades; do not read the demo's order flow as a profitability signal.
7. Consensus freshness is bounded by the 8x/day snapshot cadence; the single-event refresh in s3 S3
   is NOT built (stale consensus is rejected, not refreshed).
8. Daily-loss kill is now FED: `make_pnl_fn` = venue positions (realized + mark-to-bid - fees) for
   tickers this executor filled; verified against the real demo account (-$0.104 on the probe orders).

Not done: unattended/standing operation (launchd) -- declined by the permission system, left to the
owner. The `--loop-seconds` loop now survives transient per-pass errors (exceptions are logged and the
next cycle retries; a tripped kill switch still exits).

Pillar: mlb. Related: CONTEXT.md s5 "EV bet tracking",
`mlb/analysis/kalshi_vs_books.py` (`scan_kalshi_side`, `_kalshi_ev`),
`mlb/runners/fast_alert_loop.py` + `mlb/runners/kalshi_alert.py` (existing pagers),
`mlb_core/odds/kalshi.py`, `mlb/analysis/quote_survival.py`, and `~/market-maker`
(Kalshi REST client, order plumbing, risk layer -- demo-validated, used here for TAKING only).

---

## 0. The strategy

The sportsbooks (Pinnacle-anchored, devigged) are the sharp reference. Kalshi is a slower, thinner
exchange whose ask sometimes lags a sportsbook move. When Kalshi's executable ask for a contract is
below the books' fair probability by more than Kalshi's fee plus a safety margin, buy that contract
on Kalshi, sized by fractional Kelly. Kalshi is the only venue in this stack with a public order
API, so it is both the thing that is mispriced and the place the bet is made.

    detect -> LIVE Kalshi book -> LIVE book-pack consensus -> net-of-fee EV gate
           -> Kelly -> risk gate -> IOC limit order -> reconcile -> settle

This is **taking, not making**: no quoting, no resting orders, no inventory management. Every order
is an immediate-or-cancel limit order that either fills at or better than a price we have already
decided is +EV, or does nothing.

### What "assume +EV on Kalshi" has to mean here

Two precisions, both of which change the arithmetic (not the strategy):

1. **EV is net of Kalshi's taker fee, and "soft" is measured at the ASK.** Fee per contract is
   `ceil_centicent(M_taker * 0.07 * a * (1-a))`, i.e. a fraction `0.07 * (1-a)` of cost:

   | ask a | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 |
   |---|---|---|---|---|---|---|---|---|---|
   | fee / cost | 6.3% | 5.6% | 4.9% | 4.2% | 3.5% | 2.8% | 2.1% | 1.4% | 0.7% |

   The existing scanner's `ev_pct` (`_kalshi_ev`) is explicitly GROSS of this fee ("back it out
   before sizing"). A 3% gross edge at a 50c ask is roughly breakeven after fee. Favorites (a>=0.7)
   are cheap to trade; longshots (HR-style) are expensive.

2. **Soft-vs-books is a hypothesis the data has not yet confirmed.** The one real test of
   buy-Kalshi-vs-books (`scan_kalshi_side --validate`, corrected 2026-10-02, n=646): ROI -0.1%,
   game_ml -3.0% (t=-0.26, n=550), nrfi_ou +16.7% (t=1.43, n=96). Neither significant; gross of fee;
   at the historical ask with no depth limit (an optimistic bound). So the system's first job is
   to measure, in shadow mode, whether Kalshi is soft *net of fee and slippage* -- the gates below
   are built so a negative answer shows up as "almost nothing passes", not as money lost.

The +7.56% EV-alert paper ROI is NOT evidence for this strategy: it is priced at sportsbook odds
(where the edge is a soft book vs Pinnacle). It transfers only as "consensus moved" signal (s2, T1).

---

## 1. Scope

In scope: automated taker execution on Kalshi MLB series (`KXMLBGAME, KXMLBRFI, KXMLBTOTAL,
KXMLBSPREAD, KXMLBF5, KXMLBHR, KXMLBKS, KXMLBOUTS, KXMLBTB, KXMLBHIT` per
`kalshi_to_history.SERIES_MAP`), Kelly-sized, behind hard risk limits.

**v1 market allowlist: two-sided game-level markets only** (`game_ml`, `nrfi_ou`, `game_total`,
`game_rl`). Reasons: Kalshi depth is real there (`kalshi_vs_books.LIQUID`); the book consensus is
two-sided-devigged (reliable). Player props are v2: Kalshi prop depth is thin, and `hr_yn` books
are one-sided, so their "fair" comes from an assumed/empirical vig (`devig_unilateral`) -- too
noisy to size real money against a 6%-fee longshot.

Out of scope: quoting/resting orders (maker mode), hedging, sportsbook execution, any series
outside `SERIES_MAP`, model-probability-driven bets (the fair probability is always the book
consensus, never `model_prob`).

Not authorized by this document: placing real orders. Go-live requires the owner to provision
production credentials and flip the enable flag (s8 Phase 3). Credentials never go in chat, the
repo, or committed `.env` files.

---

## 2. Definitions and triggers

Proposition `key = (market, game_pk, player_id, line, selection)`.

Kalshi leg: `(ticker, side in {yes,no})` such that buying `side` pays $1 iff the proposition is
true under Kalshi's rules. Ask ladder for that side: `[(a_i, size_i)]` ascending.

    p_B   = book-pack fair prob: median of per-book 2-way-devigged fair_prob over >= n_min books
            (== _kalshi_book_fair / outlier_scan consensus_fair). n_books, disp = MAD, cons_ts.
    c(N)  = VWAP of the first N contracts of the ladder + fee(N)/N         # all-in cost per contract
    p_hat = p_B - h                                                        # shrunk fair prob (s3 S4)
    ev    = p_hat / c - 1                                                  # NET of fee and shrink

Triggers (either creates a candidate; both feed one dedup space):

- **T1 consensus-moved signal.** An existing pager alert (`fast_alert_loop` / `kalshi_alert`) on a
  game-level `key` means the book pack just moved while a laggard book did not -- evidence that
  Kalshi may also lag. It is a *hint*, not proof; Kalshi's own price is tested from scratch.
- **T2 direct Kalshi-side scan.** `scan_kalshi_side` each consensus refresh: Kalshi ask below
  `p_B`. No soft-book alert required.
Phase 1 A/Bs T1-sourced vs T2-only candidates on identical data (s9 Q4).

---

## 3. Pipeline (every stage is a gate; any failure ends the candidate with a reason code)

### S0 -- Intake and dedup
- One live intent per `key` per day: `intent_id = sha256(key | game_date | strategy_version)`.
- Skip if game started / start within `t_close_buf`.
- Cumulative exposure per `key` includes prior partial fills -- a re-trigger on the next run must
  not stack on top of a half-filled order beyond the Kelly cap for the *total* position.

### S1 -- Proposition -> Kalshi contract mapping (STRICT)
- Reuse the mapper that produced the signal: `kalshi.parse_event_ticker`, `SERIES_MAP` /
  `_rows_for_market`, `_NRFI_SELECTION_MAP`. Do not write a second one.
- **Equivalence check, not just lookup:** assert the market's `title` / `yes_sub_title` /
  `rules_primary` text agrees with `(line, selection)`; any mismatch aborts. This repo has already
  shipped exactly this bug class (HR Under graded as 1+ HR wins; nrfi YRFI/NRFI silently
  inverted/zero-matched -- 2026-09-19, 2026-10-02). With real orders a mismap is a wrong BET.
- Per-series rules read before enabling the series (void/postponement/suspended-game handling;
  CONTEXT s10 has DK's, Kalshi's may differ). Series with unconfirmed rules stay disabled.
- Output `(ticker, side, price_grid)` or `REJ_UNMAPPED` / `REJ_AMBIGUOUS` / `REJ_RULES_DIFFER`.

### S2 -- Fresh Kalshi book
Fetch the order book (public `GET /markets/{ticker}/orderbook`; top-of-book alone is insufficient,
depth is needed). Require:
- fetch age <= `T_k` (default 2 s); decision uses this ONE snapshot.
- market `active`, exchange not paused, `close_time` > now + `t_close_buf`.
- two-sided, not crossed, `spread <= s_max`, `size_1 >= min_top_size`.
- sane range on liquid markets `0.03 <= a <= 0.97` (`_KALSHI_SANE_RANGE`: the stale-print failure,
  docs/solutions/integration-issues/kalshi-historical-backfill-stale-closing-candles.md, produced
  fake +-900% edges); and never `a < 0.02` or `> 0.98` (market-maker ADR 0017).

### S3 -- Fresh consensus AND the lag signature
Two things, and the second is what separates "Kalshi is slow" from "Kalshi knows something":

**Fresh consensus.** The alert's consensus can be up to a snapshot interval old (fast_alert
`*/15 19-23 UTC`; odds_history 8 ParlayAPI snapshots/day). If `age(cons_ts) > T_c` (default
10 min) refresh with a single-event ParlayAPI pull, recompute `p_B, n_books, disp`. ParlayAPI is a
shared 20k/month credit pool that has been exhausted before (ops incident 2026-08-27): the executor
gets its own monthly allowance and degrades to REJECT -- never to "use the stale consensus".
Require `n_books >= n_min` and `disp <= disp_max`.

**Lag signature** (how the gap opened, from the last `lookback` of consensus + Kalshi snapshots):

| Gap origin | Detected as | Action |
|---|---|---|
| consensus moved, Kalshi did not | `dp_B >= m` and `dp_K < m/2` over lookback | PROCEED, full tier -- the lag thesis |
| Kalshi moved away, consensus flat | `dp_K <= -m` (Kalshi ask fell) and `dp_B ~ 0` | **REJECT** `REJ_KALSHI_INFORMED` -- someone sold Kalshi down; informed flow / news |
| gap persistent, nothing recently moved | neither | PROCEED at reduced tier -- either structural softness (good, what the mirror backtest tests) or systematic bias (bad); shadow data decides |

Also: `abs(kalshi_mid - p_B) > kdev_max` -> `REJ_KALSHI_OFF` (mirrors `classify()`'s `kalshi_off`):
a very large gap is more likely news (injury, weather, scratch) than a free edge.

### S4 -- Net EV gate
Walk the ask ladder; find the highest price `a_max` such that the VWAP all-in cost `c` still gives
`ev >= ev_min`, with `p_hat = p_B - h`:

    h = h0 + h_age * max(0, age_min(cons) - age0) + h_disp * disp

(`h` exists because large apparent edges are dominated by reference error: CLV flips sign with edge
size, small edges beat the close and large ones are winner's-curse -- profit review 2026-07-23,
/edge-analysis 2026-06-11.) Gate `ev_min <= ev <= ev_cap`. `ev_cap` REJECTS, never clamps -- an edge
that large is a stale quote, a mismap, or news. Defaults: `ev_min = 0.03` NET (stricter than the
pagers' gross 0.03), `ev_cap = 0.10` (EDGE_CAP finding, 0.10-0.12).

### S5 -- Kelly sizing
Binary contract, all-in cost `c`, pays $1, win prob `p_hat`:

    f* = (p_hat - c) / (1 - c)            # fraction of bankroll spent on cost
    f  = lambda * f*                      # lambda = 0.25 confirmed-lag tier, lower for persistent tier

(Derivation: `b = (1-c)/c`, `f* = (b*p - q)/b = (p-c)/(1-c)`. Same basis rule as
`mlb_core.odds.utils.kelly_stake`: probability and price on ONE basis -- the all-in executable
cost, never a mid or a devigged price.)

    B       = live Kalshi cash balance (read from the account, not the paper `bets` bankroll)
    stake_$ = min( f * B,
                   per_bet_cap_$,
                   per_game_cap_$ - open_exposure(game_pk),
                   daily_cap_$    - exposure_today,
                   depth_$ )               # cost of ladder levels with price <= a_max
    N       = floor(stake_$ / c); reject if N < N_min or stake_$ < stake_min_$

`c` depends on how deep the sweep goes: size at level 1, recompute VWAP across the consumed levels,
re-check `ev >= ev_min`, shrink `N` until it holds. The cap `max_pct` mirrors the repo's existing
5% (`kelly_stake`). Correlation: one position per `key`; same-game props and the game ML share the
per-game cap instead of independent Kelly (same choice as `mlb_core.risk.exposure`; CONTEXT E11).

### S6 -- Risk gate (independent of the strategy; no code path skips it)
Reuse the market-maker `RiskManager` shape: `check_order()` on every order, plus a separate timer
loop running `check_pnl()` regardless of strategy-loop health.
- `EXEC_ENABLED` env flag default OFF; per-series allowlist; shadow is the default mode.
- max single order $, per-game $, daily deployed $, open contracts per ticker, orders/day.
- max daily loss (realized + mark-to-market) -> kill switch, **manual reset only**, never auto-resume.
- consecutive-rejects and consecutive-losses breakers.
- `StaleDataCircuitBreaker`: if Kalshi or consensus feeds go quiet, stop trading (do not fall back).
- Unknown-state rule: any ambiguous submit (timeout/5xx) -> stop submitting, reconcile from the
  venue (open orders + fills), then continue. Never retry blind.

### S7 -- Execution (taker only)
- **Limit, `immediate_or_cancel`, price = `a_max` snapped DOWN to the market's price grid**
  (`priceGrid.snapPriceToGrid`; Kalshi rejects unsnapped prices -- learned live in market-maker).
  Never a market order. `CreateOrderV2RequestTimeInForceEnum` confirmed to expose
  `immediate_or_cancel` / `fill_or_kill`; the existing `KalshiLiveExecutionVenue` hardcodes
  `good_till_canceled` and needs a time-in-force parameter. No `post_only`, no resting remainder.
- `client_order_id = intent_id` (deterministic) so a retried submit can never double-fill.
- One final top-of-book fetch immediately before sending; abort if `a_1 > a_max`.
- Poll fills right after submit. Partial fill: do NOT chase; the remainder is already cancelled.

### S8 -- Reconcile, settle, measure
- Venue is the source of truth: `kalshi_orders` / `kalshi_fills` from Kalshi's own fill records
  (price, count, `fee_cost`), not from our intent.
- Settle on Kalshi's own market result (authoritative P&L); ALSO grade with the repo settlers and
  alarm on any disagreement -- catches mapping bugs and Kalshi-vs-DK rule differences early.
- Mirror to `bets` as `system="KALSHI_EV"`, deliberately NOT in `registry.SYSTEMS` (same reasoning
  as `EV`: not a model; must not feed the suppression-gate / performance-alert loop).
- Log per decision AND per rejection: Kalshi book snapshot, `p_B`, `p_hat`, `ev`, lag-signature
  class, reason code. At close: slippage (`fill_vwap - a_1_at_decision`), CLV vs Kalshi close and
  vs consensus close (`clv_pct_from_prices`), realized ROI.

---

## 4. Parameters (all unvalidated defaults)

| Param | Default | Param | Default |
|---|---|---|---|
| `ev_min` (net of fee+shrink) | 0.03 | `ev_cap` (reject) | 0.10 |
| `lambda` lag / persistent tier | 0.25 / 0.10 | `n_min` books | 4 |
| `T_k` Kalshi book age | 2 s | `T_c` consensus age | 10 min |
| `kdev_max` | 0.04 | `s_max` spread | 0.04 |
| `h0`, `h_age`, `h_disp` | 0.005, 0.001/min, 0.5 | `lookback` / `m` | 60 min / 0.02 |
| `t_close_buf` | 10 min | price band | [0.03,0.97] liquid; [0.02,0.98] always |
| bankroll, per-bet / per-game / daily $, max daily loss | owner-set -- NOT defaulted | | |

---

## 5. Decision outcomes (every candidate ends in exactly one; logged with inputs)

`EXECUTED`, `PARTIAL`, `NO_FILL`, `REJ_UNMAPPED`, `REJ_AMBIGUOUS`, `REJ_RULES_DIFFER`,
`REJ_GAME_STARTED`, `REJ_BOOK_STALE`, `REJ_BOOK_ILLIQUID`, `REJ_PRICE_BAND`, `REJ_CONS_STALE`,
`REJ_CONS_THIN`, `REJ_KALSHI_OFF`, `REJ_KALSHI_INFORMED`, `REJ_EV_LOW`, `REJ_EV_TOO_HIGH`,
`REJ_SIZE_TOO_SMALL`, `REJ_RISK`, `REJ_KILLED`, `REJ_DUP`, `ERR_UNKNOWN_STATE`.

The rejection histogram is a first-class output: S2/S4 dominating means no overlap or no edge net
of fee -- the cheapest possible answer to "is Kalshi actually soft", before any money is at risk.

Retry only on `REJ_BOOK_STALE` / `NO_FILL` (next run re-evaluates from S2). Never retry
`ERR_UNKNOWN_STATE` without a venue reconcile; never retry anything that failed S1.

---

## 6. Architecture and reuse

Recommendation: **Python inside this repo** (`mlb/execution/kalshi/`), porting the proven patterns
from `~/market-maker`. All inputs (odds_history, consensus, `kelly_pct`, `bets` table, exposure,
Secret Manager, Cloud Run Jobs, Discord) are Python on GCP; the market-maker repo is TypeScript on a
laptop `launchd`. Splitting hosts/languages adds a queue, a second source of truth, and laptop uptime
as a dependency of a money path. Kalshi auth is RSA-signed headers -- straightforward in Python.
Hook: a flag-gated stage at the end of the pagers' `run()`, wrapped so an executor exception can
never suppress alert posting or EV logging.

| market-maker piece | Use | Notes |
|---|---|---|
| `ExecutionVenue` + `PaperExecutionVenue` + `FillSimulator` | shadow/paper venue | identical decision code in shadow and live |
| `KalshiLiveExecutionVenue` | live venue (reference) | add `time_in_force`; fixed-point strings (`count`, `price` to 4dp) |
| `feeCalculator` (centicent ceil) | fee model | read multiplier from the series object at runtime, not hardcoded |
| `priceGrid` + `PriceGridCache` | mandatory | unsnapped price = live reject |
| `RiskManager`, `StaleDataCircuitBreaker` | risk layer | extend with s3 S6 limits |
| `SubmissionQueue` (serialize + 429 backoff) | submit path | |
| `OrderManager.reconcileFromVenue` | unknown-state recovery | |
| `auditLog`, calibration ledger (ADR 0013) | decision ledger | graded vs outcome |
| WS client / OrderBookManager | NOT v1 | REST depth is enough at a minutes-scale cadence |

NOT used: `QuoteEngine`, skew, weather market-family plugins, Jev/Gemini scanner.

Credentials: production Kalshi API key ID + RSA private key in Secret Manager
(`kalshi-prod-key-id`, `kalshi-prod-private-key`), mounted only on the executor job. The only key on
disk today is a demo key (`~/.kalshi/demo_private_key.pem`; market-maker `.env` is
`KALSHI_ENV=demo`). CONTEXT s15.9 applies: any new DB write or secret needs matching wiring on
EVERY Cloud Run Job that runs the code path ("runs successfully" != "persists").

---

## 7. Cadence and latency (set expectations)

Pagers run `*/15 19-23 UTC` (fast_alert) and 6x/day after snapshots (kalshi_alert). A 15-minute
poll cadence is slow relative to how fast a sharp move propagates to Kalshi; a lag edge that
survives 15 minutes is the survivor, and one that closes in 2 minutes is invisible. Shadow mode
measures this directly (decision-time Kalshi snapshot vs the same market N minutes later =
"Kalshi survival time", the Kalshi-side analogue of `quote_survival`). If survival is minutes, the
executor needs its own tighter loop and the WS book feed moves from "NOT v1" to "required".

---

## 8. Validation and rollout

**Phase 0 -- offline coverage study (read-only).** Re-run `scan_kalshi_side` over the clean window
(`--since 2026-08-10`), but NET of fee and with shrinkage, per market: fraction of Kalshi quotes
with `ev >= ev_min`; implied bets/day; implied $/day at 1/4 Kelly on a nominal bankroll. Limit:
`odds_history` does not persist Kalshi bid/ask SIZE, so depth cannot be measured historically.
**Start capturing Kalshi order-book depth snapshots now** (public endpoint, no credentials, no
risk) so Phase 1 has real depth data. Gate: if net-of-fee implied volume/edge is negligible, stop.

**Phase 1 -- shadow (live data, zero orders).** Full pipeline S0-S6; S7 sends to
`PaperExecutionVenue` against the real fetched ladder (no optimistic fills at stale prices).
Pre-register pass criteria BEFORE looking at results (market-maker ADR 0004 is the model).
**Gate:** net-of-fee mean CLV vs Kalshi close > 0, t > 2, on a pre-registered minimum n; modeled
slippage < assumed edge. Per repo rule CLV, not ROI, is the go/no-go: at a ~3% net edge and ~1.0
per-bet std, realized-ROI t=2 needs roughly (2/0.03)^2 ~ 4,400 bets.

**Phase 2 -- Kalshi demo (fake money).** Mechanics only: IOC semantics, price-grid snapping,
deterministic `client_order_id`, 429 backoff, kill switch, crash/restart reconcile. Demo books are
thin and unrepresentative -- draw NO edge conclusions.

**Phase 3 -- live, micro-stakes, owner-enabled.** Owner provisions credentials and sets
`EXEC_ENABLED`. Max daily loss trivial vs bankroll. Weekly review of rejection histogram,
slippage, CLV, and Kalshi-vs-own-settler disagreements. Scale only on sustained positive CLV.

---

## 9. Owner decisions / open questions

1. **Kalshi eligibility and rules.** Account can trade MLB contracts where you are; per-series
   rulebook read for void/scratch handling (gates which series v1 enables).
2. **Fee multipliers.** `docs/reference/kalshi-fee-schedule-2026-07-07.pdf` appears to list
   `KXMLBGAME` in the Non-Standard table (maker=1, taker=1; text extraction is garbled -- verify by
   eye); the other series I searched for do not appear (default taker=1/maker=0). Taker is 1 either
   way, but resolve by reading `fee_type`/`fee_multiplier` from the live series object.
3. **Bankroll and limits** (not defaulted).
4. **Trigger.** T1 (pager alerts as a consensus-moved hint) vs T2 (direct Kalshi-side scan): Phase 1
   A/Bs them. The alert is not required for T2.
5. **Python port vs reusing the TS executor.** Recommendation above is Python; the TS code is the
   demo-validated reference for API semantics, so the port should re-run market-maker's own
   `check-demo-auth` / live-demo checks against the Python venue before trusting it.
6. **Props (v2).** Enabling `k_ou`/`outs_ou`/`btb_ou`/`bhits_ou` needs Kalshi depth data
   (Phase 0 capture) and a decision on one-sided `hr_yn` consensus.
