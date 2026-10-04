# Multi-sport EV sweep + Kalshi execution (NFL / NBA / NHL) -- design spec

Status: DRAFT for owner review (2026-10-04). Nothing here is implemented. Real-money trading is NOT
authorized by this document (see s9). Every numeric default is an unvalidated starting point to be
calibrated in shadow mode, not a measured result. Extends (does not replace)
docs/designs/kalshi_auto_execution.md, whose MLB executor (mlb/execution/kalshi/, demo-verified, 99
tests, currently UNCOMMITTED) is the code this builds on.

## IMPLEMENTATION STATUS (2026-10-04) -- NFL props shadow sweep is LIVE; nothing trades real money

Built and running (branch feat/multisport-ev-sweep):
- `ev/props.py` + `ev/kalshi_props.py` + `ev/sweep_nfl_props.py` + `ev/props_exec.py` (+ `ev/kalshi_map.py` for
  game lines, built but not the trading target). 60 tests. All 3,639 live Kalshi NFL prop markets map.
- Sweep = Cloud Run job `ev-nfl-props-sweep` (image gcr.io/.../mlb-betting-ev:shadow, built separately; the
  production service is untouched). Every ~15 min: ParlayAPI slate + per-event props (5 credits/event), per-book
  2-way devig, median consensus, join to every open Kalshi NFL prop at the same point, evidence CSV
  `EVSweep/nfl_props/{ET day}/cycle_*.csv`, raw payloads `EVSweep/nfl_props/raw/`, `latest.json` for the executor.
- Discord: new candidates post to DISCORD_WEBHOOK_ALERTS, else DISCORD_WEBHOOK_OPS (admin-only). NEVER the member
  channels (these signals are unvalidated). Currently the ops channel.
- Executor `python -m ev.props_exec run --demo-orders`: reuses the MLB gates/Kelly/risk/ledger. SHADOW = paper fills
  against fresh PRODUCTION Kalshi books on a $1,000 notional bankroll (the evidence ledger, graded on Kalshi's own
  settled result via `... pnl`). DEMO = identical IOC order to the Kalshi demo exchange. PROBED: demo NFL-prop books
  are ~empty (receiving yds 29 of 1,000 two-sided; passing/rushing 0), so demo orders will mostly NO_FILL --
  mechanics only. One position per (game, player, stat) per day.
- Credit use: ~76/cycle with 15 games; run capped at 9,000 credits. Account showed ~19.3k remaining (the 100k plan
  was not yet applied when checked).

Known limits / what to watch: (1) ParlayAPI prop coverage is thin early in the day -- at ~2am Sunday only 1
real sportsbook per prop; the per-cycle `books_per_match_hist` log shows whether coverage reaches the 3-book
minimum near kickoff. (2) one-sided alternate ladders are not used. (3) lag history is not kept yet, so every
plan sizes at the persistent-tier Kelly fraction (0.10). (4) FIRST/ANYTD TD props not built.

Owner decisions captured 2026-10-04:
- Leagues: pro only -- NFL, NBA, NHL. (NCAAF/NCAAB are a config addition later, not v1.)
- Odds: ParlayAPI upgraded to 100k credits/month ($20). BettingPros + SGO are cross-checks, not the
  primary feed. 4codds.com is EXCLUDED (robots.txt: its JSON API "is not for crawlers" beyond the
  board data a page renders; AI crawlers blocked) unless the operator grants access.
- First milestone: sweep + demo-order mechanics built together. Shadow mode is still the evidence
  source; demo is mechanics only (demo books are thin and unrepresentative).

---

## 0. What the data already says (do not skip)

1. MLB aligned Phase-0: 73 Kalshi-vs-books flags in 8 weeks; +1.5% ROI net of fee, t=0.12; only 8
   passed the executor's gates. "Kalshi lags the books" is UNPROVEN even in MLB.
2. The +7.56% EV-alert paper ROI is at SPORTSBOOK prices (soft book vs Pinnacle-anchored consensus).
   It is evidence for a book-vs-consensus alert product, NOT for trading on Kalshi.
3. Kalshi lifetime volume on NBA/NFL/NHL game series is 10-100x MLB's, but depth on a given game
   today is a different question: a live probe (2026-10-04, early in the NFL week) shows most
   spread/total ladder strikes at vol 0 with wide quotes (e.g. 0.15 / 0.41). More liquidity can
   mean a SHARPER Kalshi, not a softer one. The sweep exists to measure this before money is at risk.
4. Season state: NFL regular season; NHL just opening; NBA is in PRESEASON (Kalshi lists preseason
   games; books are thin on them). Adapters carry an `in_season` flag; preseason is logged, never traded.

5. **Owner thesis (2026-10-04): player props are the soft lines; moneyline and spread are sharp.**
   Consistent with the MLB result (game-level Kalshi-vs-books: n=73, t=0.12) and with where the paper
   EV alerts earned (props). v1 is therefore PROPS-FIRST; game lines are kept as a diagnostic/sanity
   stream (and the mapper is built) but are not the trading target.
6. **Kalshi NFL props are deep and tight (probed live 2026-10-04, Sunday slate):** receiving yards 1,327
   open markets / 15 events, all two-sided, median spread $0.02, max open interest ~37k contracts;
   rushing yds 784 (spread $0.01), receptions 1,132, passing yds 271, passing TDs 125, first TD 401.
   NBA props: none listed (preseason). NHL props (goalscorer, points): thin -- 5-7c spreads, OI mostly
   zero. ParlayAPI's free catalog lists the matching NFL markets (player_pass_yds, _rush_yds,
   _reception_yds, _receptions, _pass_tds, _anytime_td, _1st_td) INCLUDING `*_alternate` ladders for
   yards/receptions -- so, unlike game lines, every Kalshi "N+" strike can be compared to a book line.
   Caveats that cut against the thesis: a 1-2c spread means active Kalshi market makers (not obviously
   soft); and the book reference for props is a thinner, noisier truth than for game lines (fewer books,
   bigger dispersion, mostly one-sided alternate prices needing an assumed vig) -- exactly where the
   repo's winner's-curse finding says big apparent edges are mostly reference error. The shadow
   histogram, not the thesis, decides.

Therefore the sweep has two sinks fed by one candidate stream (s2):
- Sink A (cheap, proven shape): Discord +EV alerts at SPORTSBOOK prices (generalizing
  fast_alert_loop). No Kalshi dependency; this is where the only positive paper evidence lives.
- Sink B (the ask): Kalshi taker execution, demo first, gated on net-of-fee CLV.
If Sink B's gate never passes, Sink A is still a working product. Do not conflate their P&L.

---

## 1. Architecture

New neutral package `ev/` (name is a proposal; trivially renamable). Shared infra stays in mlb_core
per CONTEXT s0; the pillar-per-sport `nba/` data package is untouched.

    ev/sports/{nfl,nba,nhl,mlb}.py   SportAdapter: everything sport-specific, nothing else
    ev/sweep/                        fetch -> normalize -> consensus -> candidates (sport-agnostic)
    ev/execution/kalshi/             hoisted from mlb/execution/kalshi/ (fees, price_grid, book,
                                     sizing, risk, ledger, venue, client unchanged); mapping.py and
                                     consensus.py become adapter-driven. Old import path kept as a shim.
    ev/sinks/{alerts,kalshi}.py      the two sinks

Prerequisite: commit the uncommitted MLB executor (and tests) on a branch BEFORE the hoist, so the
move is a reviewable rename, not a mixed diff.

### SportAdapter contract
- `kalshi_series`: e.g. NFL = KXNFLGAME, KXNFLSPREAD, KXNFLTOTAL; NBA = KXNBAGAME/SPREAD/TOTAL;
  NHL = KXNHLGAME/SPREAD/TOTAL. Props (KXNBAPTS, KXNFLANYTD, KXNFLPASSYDS, KXNFLRECYDS, ...) are v2.
- `parse_event(ticker) -> (date_et, away_code, home_code)` using a per-league code table; FAIL CLOSED
  if more than one split is valid (see s3).
- `parlay_sport_key` + book markets to pull; `in_season(date)`; `tie_policy`; `ot_included`.
- `game_key = "{sport}:{date_et}:{AWAY}@{HOME}"` replaces MLB's `game_pk` (no cross-sport id exists).
  ParlayAPI events join via team names + commence_time -> game_key; unjoinable = rejected, logged.

---

## 2. The sweep

Cycle (per sport, per active window): pull whole-slate game lines -> devig per book (2-way, shin/
proportional as the repo does) -> per-proposition median over >= n_min books (same truth estimate the
MLB backtest validated: `_kalshi_book_fair`) -> compare to a FRESH Kalshi book -> candidate stream
with full inputs logged, including every rejection reason (rejection histogram is a first-class output).

Differences from the MLB executor that matter:
- Consensus comes from a direct fresh pull, not odds_history (MLB's 8x/day cadence was the binding
  limit; "consensus refresh NOT built" in the MLB doc). Stale consensus is rejected, never used.
- PRE-GAME ONLY. The MLB scanner's worst bug was in-play Kalshi vs pre-game books: ~90% of its flags
  were that artifact (27% -> 0.3% extreme rows once aligned). Reject any leg whose game has started or
  whose Kalshi market is not pre-game; the start time comes from the BOOK feed (Kalshi NFL/NBA/NHL
  event tickers carry a date but no start time; `close_time` is days after the game).
- Informed-flow windows are sport-specific and are the main adverse-selection risk (and also where
  the lag edge, if any, lives -- shadow data decides the buffer, do not hardcode a guess):
  NFL inactives ~90 min pre-kick; NHL confirmed starting goalie (a goalie swap moves a game several
  points); NBA late scratches / injury reports. Adapters expose `news_window_min`.

### Credit budget (ParlayAPI, 100k/month; ESTIMATES until s7 probe confirms alt-line costs)
| Use | Assumption | ~credits/mo |
|---|---|---|
| Game lines h2h+spreads+totals, 3 sports | 3 credits/poll/sport, 10-min poll, ~10 active h/day | 16k |
| ~~Alt spreads/totals~~ | PROBED 2026-10-04: ParlayAPI has NO alternate game-line markets (s3.1) | 0 |
| MLB (existing guard ceiling 19.5k; postseason then offseason) | | 20k |
| Props v2 + single-event refreshes before a decision | | 30k |
| Reserve | | ~12k |
Hard rules (from the 2026-08-27 incident): every ParlayAPI caller goes through ONE credit ledger
(extend OddsAccum/{sport}/_credits/{month}.json), per-sport allowances with the existing pace guard
(`PARLAY_CREDIT_CEILING` must be raised from 19.5k to ~97k deliberately, not by accident), a weekly
reconciliation of the ledger against the API's own `x-requests-remaining` header, and
`gcloud scheduler jobs list` checked for stray jobs after any new schedule. Out of credits -> degrade
to REJECT, never to stale data.

---

## 3. Kalshi -> proposition mapping (the part that can lose real money)

Observed live 2026-10-04 (public API), all three leagues: fee_type quadratic(_with_maker_fees),
fee_multiplier 1 (MLB's KXMLBGAME was 0.5 -- the runner must keep reading it PER SERIES, never assume).

| Kalshi series | Shape | Maps to | Notes |
|---|---|---|---|
| `*GAME` | 2 markets/event, "<City> wins", rules text names the team | moneyline | v1 core. |
| `*SPREAD` | STRIKE LADDER: "X wins by over N.5 points/goals" for many N | book spread X -(N.5) | yes = X covers -N.5. Opponent +N.5 = complement. Same semantics as the MLB run-line fix. |
| `*TOTAL` | STRIKE LADDER: "Over N.5 points/goals scored" | book total over N.5 | one side per market. |

Hard problems, each with a fail-closed rule:
1. **Ladders vs a single book line.** Books quote ONE main line per game; Kalshi lists a dozen
   strikes. PROBED 2026-10-04 (job, 37 credits): ParlayAPI has no alternate game-line markets --
   `alternate_spreads`/`alternate_totals` return HTTP 200 with an empty body (and are still charged);
   the free global catalog (87 keys) lists only `spreads`, `totals` plus player-prop `*_alternate`.
   So v1 evaluates ONLY the strikes where >= n_min books quote that exact main line; books often differ
   by a half point, so spread/total coverage will be sparse and MONEYLINE is the real v1 core.
   No interpolation across lines in v1 (NFL key numbers 3/7 and NBA/NHL totals are not smooth).

   Measured ParlayAPI coverage (region us, 2026-10-04): NFL 30 events, 14 books (bet365, betmgm,
   betonline, betrivers, bovada, caesars, draftkings, fanatics, fanduel, fliff, hardrock, parx,
   pinnacle, prophetx); NHL 12 events, 6 books (bovada, draftkings, fanduel, fliff, pinnacle,
   prophetx); NBA 23 events (preseason) but only 1-3 books per game (pinnacle, fliff, prophetx, ...).
   n_min_books=4 therefore rejects NBA preseason and is borderline for NHL; the shadow histogram
   will show it. Sport keys confirmed: americanfootball_nfl, icehockey_nhl, basketball_nba. Cost:
   h2h = 1 credit/sport/poll, spreads+totals = 2, whole slate (so ~3 per sport per poll).
2. **Team code splitting.** Tickers concatenate codes with no delimiter (BUFLAR, TBDAL, UTADEN,
   LAVGK = LA+VGK). Titles are city-only ("Los Angeles wins" is ambiguous in all three leagues). Use a
   per-league code table; if two splits are valid or the code is unknown -> REJ_AMBIGUOUS. Never
   trust the title for identity.
3. **Rules equivalence.** Assert title/yes_sub_title/rules_primary agree with the interpretation
   (as mapping.py does for MLB). READ LIVE 2026-10-04 (rules_secondary), now enforced in ev/kalshi_map.py:
   - NFL tie: each team resolves to $0.50 (push-like; matches book push-on-tie). The mapper requires
     that exact text and rejects the market if Kalshi changes it.
   - Postponement: game that starts within 48h of schedule resolves normally; otherwise "a fair price"
     (a void-like outcome). All three leagues.
   - **NHL TOTAL IS DISABLED in v1.** Kalshi: "shootout result will be recorded as one goal awarded to
     the winning team". Sportsbook NHL totals typically count regulation+OT only (UNVERIFIED per book),
     so a shootout game resolves an Over differently on Kalshi than at the book -- not the same
     proposition. Enable only after verifying each reference book's rule and modelling the shootout rate.
     NHL moneyline/spread are unaffected (a shootout win is still a win; margin 1 never clears a 1.5 line).
   - Wording differs by league and is asserted per league (NFL spread "more than", NHL "over"; NBA total
     "over N total points", NFL total "more than N points"); the ticker's embedded strike must equal
     floor_strike + 0.5, and whole-number lines are rejected (the book has a push Kalshi lacks).
   Validated against ALL 1,248 live open markets on 2026-10-04: every NFL and NBA market and every NHL
   moneyline/spread maps; the 162 NHL totals are refused by design. NBA preseason is still listed.
4. **Exchange sharding.** Observed: NFL/NHL markets on exchange_index 0, NBA on 3. Demo cash is per
   shard (`fund-shard`); production sharding unverified.
5. **Doubleheader analog:** none expected; if an event ticker carries a suffix beyond the code
   pattern -> REJ_AMBIGUOUS.

v1 market allowlist (REVISED 2026-10-04, props-first): NFL player props -- receiving/rushing/passing
yards, receptions, passing TDs, then first/anytime TD. Player-prop mapping needs (a) player identity
resolution Kalshi title/ticker -> ParlayAPI player name (fail closed on ambiguity; same name collisions
exist, e.g. two Max Muncys in MLB), (b) the ladder rule "N+ strike == book over (N-0.5)" with the book's
OWN under as the complement where two-sided, (c) the Kalshi rules read live: "active but never takes a
snap -> settles to the fair market price before game start" (a void; books also void DNPs), and (d) a
per-prop-series grader agreement check. NHL props: shadow-log only (thin). NBA props: when listed.
Game lines (`*GAME`/`*SPREAD`/`*TOTAL`, mapper already built + tested): diagnostic stream only.

---

## 4. Decision pipeline

Unchanged from the MLB doc s3 (S0 intake/dedup, S1 map, S2 fresh Kalshi book, S3 consensus + lag
signature, S4 net-of-fee EV gate with shrinkage, S5 fractional Kelly, S6 independent risk gate, S7 IOC
limit orders snapped to the price grid, S8 reconcile/settle) except: game_key replaces game_pk;
S3 uses the direct fresh consensus above; the news-window buffer is per sport. Defaults: ev_min 0.03
net, ev_cap 0.10 (reject, never clamp), lambda 0.25/0.10, $1,000 notional bankroll with the existing
caps (5% bet / 4% game / 10% daily deploy / 5% daily loss), all KEXEC_-overridable. Per-sport caps and
a global cross-sport daily cap are added (CONTEXT E11): three sports must not triple the daily limit.

Settlement/grading: Kalshi's own settled market `result` is the authoritative P&L and the shadow-row
grader (no per-sport boxscore settler needed to start). A second grader (NBA: SportsBlaze, exists;
NFL/NHL: to be chosen, unverified) is added as a disagreement alarm, as in MLB design S8.

---

## 5. Validation and rollout

- **P0 (days, read-only, ~no credits):** probe ParlayAPI sport keys for NFL/NHL (UNVERIFIED -- assumed
  americanfootball_nfl / icehockey_nhl by analogy with basketball_nba) and the credit cost of alt-line
  markets; probe BettingPros same-day per-book coverage for the three sports from Cloud Run; start
  banking Kalshi order-book DEPTH snapshots (public, free) for all nine series. Gate: if books or
  alt lines are unavailable, the v1 allowlist shrinks to moneylines.
- **P1 (shadow + demo together):** adapters, sweep, sinks. Shadow = production public books, paper
  fills against the real fetched ladder. Demo = signed IOC orders on the demo exchange (mechanics:
  IOC, grid snap, idempotent client_order_id, shard cash, kill switch, restart reconcile). Pre-register
  pass criteria BEFORE looking at results.
- **Gate (per sport, not pooled):** net-of-fee mean CLV vs Kalshi close > 0 with t > 2 on a
  pre-registered n. Sample sizes are small: NFL ~270 games/season, so NFL may never reach significance
  in one season alone. CLV, not ROI, is the go/no-go (repo rule). Expect few trades; do not read demo
  order flow as profitability.
- **P2 (owner only, s9):** micro-stake production.

---

## 6. Risks specific to this expansion
- Sharper-than-MLB Kalshi: the likeliest outcome is "almost nothing passes net of fee". That is a
  valid result; it should show up as a rejection histogram, not as losses.
- Informed flow near news (goalies, inactives): the lag signature (consensus moved, Kalshi did not)
  must be measured per sport, and `REJ_KALSHI_INFORMED` stays on.
- Correlation: same-game ML + spread + total are one bet for risk purposes (shared per-game cap).
- NFL concentration: ~16 games in a few windows per week -> daily caps bind; positions cluster.
- Legal/eligibility: confirm your account may trade sports contracts where you are (MLB doc s9.1).
- Scope creep: three sports x many markets x two sinks. REVISED order of build (props-first): NFL
  props shadow sweep first (the only place Kalshi depth + book coverage + ladders all exist today),
  then NHL props shadow-log, NBA props when Kalshi lists them, game lines as a diagnostic.
- Prop settlement/voids: DNP / inactive handling differs by book and by Kalshi; the executor must treat
  Kalshi's own settlement as truth and alarm on any disagreement with the book rule.

## 7. Open questions
1. ~~ParlayAPI coverage probe~~ -- DONE 2026-10-04 (s3.1). Findings: keys confirmed; no alt game
   lines; NFL deep (14 books), NHL thin (6), NBA preseason very thin. **The account still reports
   ~19.3k credits remaining, i.e. the 100k upgrade is NOT applied yet** -- apply it before the sweep
   runs (MLB's 19.5k pace ceiling alone consumes the old pool). Rate limits at the 100k tier unknown.
2. ~~Kalshi rulebooks per series~~ -- READ and enforced (s3.3). Remaining: each reference BOOK's own
   rules (NHL totals shootout treatment; NFL tie handling at DK/FD/etc.) -- needs the books' rulebooks.
3. Do you want Sink A (Discord +EV alerts at sportsbook prices for the three sports) in v1? It shares
   all the sweep code and is where the existing positive paper evidence lives.
4. Second grader source for NFL and NHL.

## 8. Not in scope
Quoting/resting orders, hedging, sportsbook execution, college leagues, player props (v2), any
model-probability-driven bet (fair prob is always the book consensus).

## 9. Real money
Production Kalshi is refused in code (`client.ProductionNotEnabled`) and stays that way. Enabling it
is the owner's explicit step after the P1 gate, with credentials provisioned by the owner (never in
chat, the repo, or a committed env file) and micro-stakes first.
