# NHL shots-on-goal (SOG) props -- validation study (2026-10-14)

Status: IN PROGRESS. Section 1 (pre-registration) was written and committed BEFORE any outcome (shot count) was joined
to the quotes. Results are appended below it.

## 1. Pre-registration (written before outcomes were touched)

What I had looked at when this was written: schema, row counts, price/line distributions, quote timing, event->game
join coverage, name coverage. NOT looked at: any realized shot count against any quote.

### Data

`nhl_data.db` (from the untrusted `nhl-sog-predictor` bundle; data only, no code from it was run, no pickles loaded):
`nhl_player_props` = 96,090 SOG quotes, 2026-01-01..2026-03-23, 9 books, `(event, book, player, side)` is the table PK so
exactly ONE line per player per book (alternate ladders were overwritten, not stored). One snapshot per quote, taken a
median ~184 min before puck drop. Therefore:

* There is NO closing line and NO CLV can be computed from this data. Under the repo rule (CLV, not ROI, is the go/no-go)
  the best verdict this dataset can ever support is "no edge found" or "promising but INSUFFICIENT evidence"; it can
  not produce PROMOTE_CANDIDATE.
* `game_date` in the table is the SNAPSHOT date, not the game date (99/488 events differ); the game date is the ET date
  of `commence_time`. 822 quotes were captured 0-5 min AFTER the scheduled start; they are dropped.
* `Utah Mammoth` appears as a full name where an abbreviation is expected; mapped to `UTA`.

### Gradable-set rules (fixed in advance)

* Pair Over/Under per (event, book, player, line); a quote missing either side is dropped (2 rows).
* Player resolved to a league id inside the event's two teams only (roster of that game from the box scores). Unresolved
  -> dropped and counted. Player resolved but did not play (no box row or TOI = 0) -> VOID, excluded (not a loss).
* Lines are half-points (a handful of 0.5 lines); no pushes expected. Flat 1u stake at the quoted American price.
* De-vig: proportional two-way (same default as `ev.props.build_consensus`).

### Tests and the variant budget

Reported statistic for every ROI test: mean per-unit return, n, standard error CLUSTERED by game (quotes for the same
prop across books and players within a game are correlated), t-stat. Per-quote and per-prop (best price) views are both
shown where relevant.

Pre-registered ROI strategies (the ONLY ones whose ROI I will headline; K = 14):

* A1: Sink A, repo thresholds. Quote vs leave-self-out median de-vigged fair of the OTHER books at the same
  (player, line): >= 3 others, dispersion (MAD) <= 0.06, EV >= 3% at the quoted price, EV <= 15%. Both sides.
* A2: as A1 with EV >= 5%.
* A3: as A1 with no EV cap.
* A4: as A1 but the reference is Pinnacle's de-vigged fair alone (23 days only).
* B1: model P(over) vs consensus (median fair over all books at the same player/line, >= 3 books): bet the side where
  model - consensus >= 5pp, priced at the best available book price. B2: same at >= 3pp. B3: same at >= 8pp.
* B4-B6: B1-B3 priced at the MEDIAN book price instead of the best (removes the soft-line shopping component).
* H1a, H1b: the sog-predictor report's headline strategy, as best I can reconstruct it: blended p = 0.5 * model +
  0.5 * consensus of the "sharp" books {betonlineag, draftkings, fanduel}; bet UNDERS only at {betmgm, williamhill_us}
  when EV at the quoted price >= 5% (H1a) or >= 3% (H1b). Their "model" is not reproducible; mine is substituted.
* H1c, H1d: the same without the model (sharp consensus alone), to separate model skill from the book/side filter.

Multiple-comparison haircut: K = 14 headline tests, so a result is only called "significant" if |t| >= 2.7 (Bonferroni
at alpha = 0.05, two-sided, K = 14, normal approximation). Any additional cut (per-book, per-line, per-month) I make
later is labelled EXPLORATORY and counted in the final variant tally; none can upgrade the verdict.

### Non-ROI tests (these carry more weight than ROI)

* M1: calibration, log-loss and Brier of each book's de-vigged P(over), of the consensus, and of Pinnacle, against
  realized outcomes (the market vs reality).
* C1: the count model (negative binomial over SOG; NO market-derived features) trained strictly on dates < 2026-01-01
  with an expanding monthly retrain (Jan, Feb, Mar each use data before that month only). Compare model P(over) vs
  consensus P(over) by log-loss and Brier on the same props.
* C2: the blend test: out-of-sample logistic regression `y ~ a + b*logit(consensus) + c*(logit(model) - logit(consensus))`
  with game-clustered SEs. c significantly > 0 = the model adds information the market lacks. Plus log-loss of
  `w*model + (1-w)*consensus` blends.
* Edge ladder: ROI/win-rate by edge bucket (2-4, 4-6, 6-10, 10+pp). A real edge climbs; "only the top bucket pays" is the
  documented artifact shape (docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md).

### Verdict rule (fixed in advance)

* NO_EDGE: C2's c is not significantly positive AND no pre-registered strategy clears the haircut with a monotone ladder.
* INSUFFICIENT_N (promising): some signal clears the haircut but CLV cannot be measured on this dataset, so it cannot be
  promoted; it would need forward-banked snapshots with closing lines.
* PROMOTE_CANDIDATE: unattainable on this dataset by construction (no CLV).

### Longer-history model check

The 2019+ history in `NHL/*_master.csv` is still being backfilled (the backfill is chronological; the 2025-26 season that
overlaps the quote window will land last). The same feature/training code runs on those masters via
`nhl.props.gamelog.from_masters`; see the runnable command at the end of this document.
