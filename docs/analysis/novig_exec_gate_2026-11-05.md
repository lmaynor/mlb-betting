# Novig automated-execution gate: forward-CLV slice gate + liability caps (2026-11-05)

Roadmap item: "Automated cross-venue execution on Novig: once a sport/stat slice clears the 250-bet forward CLV gate,
route official plays to automated execution on 0%-fee pre-game venues (Novig) with per-game and per-player liability
caps." This note covers the SAFETY LAYER only. Nothing here places, routes or sizes a real-money order, and nothing was
deployed, scheduled, or written to GCS.

## Verdict (computed 2026-11-05 on the real shadow ledger)

**Every slice is CLOSED. Forward CLV for Novig plays CAN be computed from banked data, but there are 2 forward plays, not 250.**

| slice          | status             | n usable / filled | game days | mean CLV (same-book close) | lower 95% CI | mean CLV vs closing consensus | open |
|----------------|--------------------|-------------------|-----------|----------------------------|--------------|-------------------------------|------|
| nfl\|rec_yds   | insufficient_data  | 2 / 2             | 1         | -1.10%                     | n/a (1 day)  | +2.70%                        | no   |
| nfl\|rush_yds  | no_data            | 0 / 0             | 0         | -                          | -            | -                             | no   |
| nfl\|pass_yds  | no_data            | 0 / 0             | 0         | -                          | -            | -                             | no   |
| nfl\|receptions| no_data            | 0 / 0             | 0         | -                          | -            | -                             | no   |
| nfl\|pass_tds  | no_data            | 0 / 0             | 0         | -                          | -            | -                             | no   |

Every other sport (MLB, NBA, NHL, soccer, ...) has NO Novig path at all (`ev/novig/map.py` maps five NFL stats and
`load_novig_legs` is hard-wired to NFL), so any slice outside the table is closed by default. The two numbers in the rec_yds
row are an anecdote (two plays on one Monday-night game, Greg Dortch and Blake Corum receiving yards, both BUF@LAR on
2026-10-12), not evidence: n=2, one cluster, opposite-sign readings of the two CLV definitions.

Reproduce (read-only on the ledger, local raw mirror, no network beyond the `gcloud storage cp` that built the mirror):

    gcloud storage cp 'gs://<bucket>/EVSweep/nfl_props/raw/2026-10-12/*' <mirror>/raw/2026-10-12/
    python -m ev.novig.clv_gate compute --db ~/.kalshi_exec/ledger_nfl_props.sqlite --raw-dir <mirror> --out verdicts.json

## The gate rule

A slice `(sport, stat)` is OPEN only when ALL hold; anything else, including no data, no verdict file, a verdict older than
72 hours, or a hand-edited file, is CLOSED (`ev/novig/clv_gate.py`):

1. **n >= 250** forward Novig plans that FILLED in the shadow ledger (mode `novig`) AND have a usable closing line.
   Bets whose close is unusable are dropped from n and counted in `coverage.dropped_reasons` (a line that moved off the point
   or a stale last snapshot is not a clean close; this is a survivorship choice and is reported, not hidden).
2. **mean CLV > 0.** CLV is the repo's standard same-book, same-side price ratio, `(decimal_entry / decimal_close - 1)`
   (`mlb_core.odds.utils.clv_pct_from_prices`, but without rounding the entry price to a whole American number). Entry is the
   shadow fill's all-in VWAP (`(filled_cost + fee) / fill_count`, fee 0 pre-game). Close is Novig's own quote for the same
   (game, player, stat, line, side) in the last banked pre-kick ParlayAPI snapshot.
3. **lower CI bound > 0.** CI choice: two-sided 95% interval of the mean, **cluster-robust by game day**,
   `V = G/(G-1) * sum_g (sum_{i in g} (x_i - xbar))^2 / n^2`, Student-t on `G-1` degrees of freedom. A day is the cluster because
   props within one game/day share game flow, injury news and line moves, so treating 250 props from 3 Sundays as 250
   independent draws would overstate n (tested: a 300-bet, 3-day sample that an iid CI opens is closed by the clustered CI).
   With fewer than 2 game days, or zero variance, the interval cannot be formed and the slice is closed. The t quantile is
   implemented in the module (no scipy dependency; checked against table values in the tests).
4. **Consensus confirmation** (extra restriction, `NOVIG_GATE_REQUIRE_CONSENSUS_CONFIRM`, default on): mean of
   `closing leave-Novig-out consensus fair P(side) / entry price - 1` must also be > 0, so a stale Novig quote cannot open a
   slice by itself.

Usable close = the game's last banked pre-kick snapshot is within 60 min of kick (`nfl.props.lines.CLOSE_FRESH_MIN`; the sweep
stops fetching a game ~20 min out, so this is a closing PROXY, never the true close), is later than the entry, still quotes the
same line, and the quote's own `last_update` age is <= 30 min.

The repo's existing paper -> live bar (`mlb_core.risk.clv.clv_verdict`: mean >= +2%, t > 2, n >= 100) is reported next to it
(`t17`, evaluated with min_n 250 and the clustered t) for reference; this gate is the roadmap's, not a replacement for T17.

## What data exists today, and what is missing

Exists:
- The Novig shadow ledger (`~/.kalshi_exec/ledger_nfl_props.sqlite`, sqlite on the laptop): per decision the player, stat, line,
  side and ask; per order the fill VWAP. Mode `novig` = decided and paper-filled against the PRODUCTION public Novig book.
  Mode `novig_paper` (sandbox) validates plumbing only and is never counted.
- Banked ParlayAPI NFL prop payloads (`EVSweep/nfl_props/raw/{ET day}/...`, day folders 2026-10-04 through 2026-11-05) that
  include Novig as a book with BOTH sides priced (about 8,000 Novig quote rows across the 18 snapshots of the 2026-10-12 game, ~3-4% two-way hold, so the
  quotes look like asks, which is what a shadow taker pays). `nfl/props/lines.py` turns them into the closing-line tables.
  Closing quotes were available for both 10-12 plays (last snapshot 31 min before kick).

Missing (why n is 2):
1. **Almost no forward plays.** The ledger's last write is 2026-10-12 (2 filled `novig` plans; 35 `REJ_EV_LOW`; 2,464
   `REJ_BOOK_ILLIQUID`; how many of those pre-date the wide-book-on-consensus fix is not recorded). The shadow loop is a manually started laptop
   process (`python -m ev.props_exec run --novig`), not a scheduled job, so nothing has accumulated since. 250 filled plans per slice
   needs many game days of that loop running; the fill rate cannot be extrapolated from 2.
2. **No durable copy of the ledger.** `publish_local` writes the quote-vs-consensus evidence CSVs and alerts to GCS, not the ledger.
   The CLV gate therefore depends on one laptop file. Banking the ledger's `orders`/`decisions` (or at least a per-fill row with
   player, stat, line, side, entry price, ts) to GCS nightly is the missing banking step. NOT done here (no GCS writes, no jobs).
3. **NFL only.** No Novig market mapper, shadow loop or banked closes exist for NBA, NHL or MLB. A slice there cannot even
   accumulate n.
4. **The close is a proxy.** The banked snapshot cadence is ~15 min and stops ~20 min before kick, and exact-point keys mean a prop
   whose main line moved has no close (dropped). Whether ParlayAPI's `novig` quote equals what the order book would have filled
   at is inferred from the two-way hold, not verified against Novig's own book.
5. **Paper fills are optimistic.** A shadow fill takes the visible production book at decision time with no latency, queue or
   adverse selection (the ledger already flags wide-book fills as the likeliest stale/adverse ones). Positive CLV on shadow
   fills is necessary, not sufficient, for live routing.

## Liability caps (`ev/novig/liability.py`)

Buying a $1-payout contract at price p costs p and that is the maximum loss, so liability == all-in cost. Caps are fractions of a
notional bankroll (default $1,000) and every field is overridable with `NOVIG_LIAB_<FIELD>`:

| cap              | default | dollars | note |
|------------------|---------|---------|------|
| per bet          | 1.0%    | $10     | |
| per player       | 1.5%    | $15     | all stats of one player in one game/day |
| per game         | 3.0%    | $30     | every position in the game; props in a game are correlated (Kalshi props shadow cap is 6%) |
| minimum stake    | $2      | -       | a plan shrunk below this is rejected, not placed |

`check_liability` shrinks a plan to the tightest cap or rejects it (`REJ_LIABILITY_CAP`, `_MIN`, `_FEE`); it can never enlarge one.
The **0%-fee assumption lives in config**, not logic: `fee_rate = 0.0`, justified only while the market's own flag
`fee.charged == 'WHEN_LIVE'` (`required_fee_charged`); a leg with any other flag is rejected with `REJ_LIABILITY_FEE`. Raising
`fee_rate` raises the liability per contract. Existing exposure is read from the shadow ledger (`ledger_exposures`).
The Kalshi `RiskGate` and the (deliberately loosened, DEMO-only) Kalshi RFQ caps are untouched.

## Wiring (`ev/novig/gate.py`, `ev.novig.exec.run_pass(..., gate=...)`)

Default OFF. `NOVIG_GATE_ENABLED=1` makes `props_exec run --novig` build the gate; otherwise `gate is None` and the pass is exactly
what it was. When on, a PLAN that already passed every existing gate and the RiskGate is additionally:

- checked against the liability caps (shrunk or rejected, in the shadow ledger too);
- annotated `inputs.exec_gate = {slice, open, status, live_eligible, liability}` in the decision row;
- for a slice that is not open, NOT mirrored to the Novig PAPER sandbox (`NOVIG_GATE_SKIP_CLOSED_PAPER`, default on).

Deliberate design point: the CLV gate never blocks the production-priced SHADOW fill of a closed slice. That fill is the only
source of forward CLV, so gating it would pin every slice at n=0 forever. `live_eligible` is an annotation for a future,
separately reviewed executor; this PR adds no order client, no production endpoint, no credentials. The only order calls
remain the existing paper-sandbox ones, and a test asserts the new modules contain no order/network/credential code.

Verdict file: `NOVIG_GATE_VERDICT_FILE` (default `~/.kalshi_exec/novig_gate_verdicts.json`), written by
`python -m ev.novig.clv_gate compute ... --out`, read once at startup; `python -m ev.novig.clv_gate status` prints it.

## Not verified

- The gate has only been run on 2 real plays; its behaviour at n >= 250 is exercised by synthetic tests, not real data.
- `compute` was run against a local mirror (read-only `gcloud storage cp`) of the one day's raw payloads that has a ledger play; the full 2026-10-04..11-05 raw bank was not mirrored
  because the ledger has no plays outside 2026-10-12.
- Nothing was run against the live Novig API; `props_exec run --novig` with the gate on was exercised only through `run_pass` fakes.
