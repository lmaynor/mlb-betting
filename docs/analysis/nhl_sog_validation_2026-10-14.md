# NHL shots-on-goal (SOG) props -- validation study (2026-10-14)

## Verdict up front

**NO_EDGE** for the SOG count model on the data that exists. **INSUFFICIENT_N for Sink A** (the soft-book-vs-consensus idea:
only 60 qualifying quotes in 63 days). PROMOTE_CANDIDATE was unattainable by construction (the history has no closing line, so no
CLV). Nothing is deployed; the scorer ships LOG-ONLY. How weak the evidence is: ONE ~3-month window, ONE snapshot per quote, 482
games, and (in section 2) a model trained on only ~3 months of 2025-26 history (the DB has no earlier seasons; section 3 uses our
longer masters). A true +3% ROI edge would need ~8,000 independent bets to see at |t| >= 2.7; this dataset has ~480 games. "No
edge detected" here is mostly "cannot detect small edges"; it is NOT proof that no edge exists. What it does show decisively:
(1) the market is well calibrated, (2) this model does NOT beat it and adds no detectable information on top of it, and (3) the
sog-predictor report's +14% claim does not reproduce.

Status: Section 1 (pre-registration) was written and committed BEFORE any outcome (shot count) was joined to the quotes (commit
4c6a2c6). Sections 2 onward are results.

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
`nhl.props.gamelog.from_masters`; see section 3 and the reproduce commands in section 5.

## 2. Results -- 2025-26 history only (DB game log; model trained on Oct-Dec 2025 and later months)

Reproduce: `PYTHONPATH=. python -m nhl.props.study --db nhl_data.db --players-master NHL/players_master.csv --json out.json`
(frozen numbers: `docs/analysis/nhl_sog_validation_2026-10-14_results_db_only.json`).

### 2.1 Data accounting

96,090 quote rows -> 822 dropped (captured after the start) -> 47,633 paired two-sided quotes. Resolved inside the event's teams:
47,295 exact, 20 compat, 173 ambiguous ("Elias Pettersson", two VAN players, dropped), 145 unknown (Yegor Chinakhov). Graded
46,606 (97.8%); 600 void (player did not dress: refunded, excluded); 318 unresolved; 109 with no final game (the last day).
7,964 distinct props (event, player, line) graded, 7,012 with >= 3 books. 482 games.

### 2.2 The market itself (M1)

* Hold is large: mean overround 6.4-7.6% across books. Betting every quote blind (over) = -5.9% ROI (t = -5.0), (under) = -7.0%
  (t = -5.8).
* Calibration is good. Consensus (>= 3 books, n = 7,012 props): log-loss 0.6816, Brier 0.2443 (vs 0.6880 for the in-sample
  line-level base rate). Reliability: predicted 38.6% -> realized 35.9% (n 535); 45.4 -> 46.7 (2,824); 55.2 -> 55.0 (2,903); 61.5
  -> 64.0 (750). Every individual book scores 0.678-0.683; none is detectably worse (coolbet best at 0.6782, n = 1,516, noise
  level). Pinnacle (23 days, n = 2,240) = 0.68076, identical to the consensus on the same props. There is no "stale soft book"
  signal in calibration.
* Over rates by line: 1.5 -> 55.4% (consensus 53.9%), 2.5 -> 46.4% (46.8%), 3.5 -> 43.1% (45.5%).

### 2.3 Sink A (soft quote beats leave-self-out consensus), flat 1u at the quoted price

| Variant | n | ROI | t (game-clustered) | note |
|---|---|---|---|---|
| A1 (>= 3 others, MAD <= .06, EV 3-15%) | 60 | +1.5% | +0.11 | claimed mean EV +4.0% |
| A2 (EV >= 5%) | 7 | +23.6% | +0.54 | 7 bets |
| A3 (A1 without the 15% cap) | 60 | +1.5% | +0.11 | identical (no quote exceeded the cap) |
| A4 (reference = Pinnacle only, 23 days) | 53 | -26.3% | -1.55 | |
| X1 exploratory (>= 2 others) | 62 | +4.7% | +0.33 | |
| X2 exploratory (EV >= 2%) | 135 | +2.8% | +0.30 | |

Only 60 of 46,606 quotes pass the repo's gates: de-vigged books agree to within a point or two, and with a 6-7% overround a quote
needs a real disagreement to show +3% EV. A1 by book (exploratory, n 1-25 each) is uninformative (betonlineag +28% on 25 bets,
t = 1.27). Claimed EV +4.0% vs realized +1.5% +- 14%: indistinguishable from either 0 or 4%. **INSUFFICIENT_N.** For scale, the
standard error of ROI on n = 60 is ~13 points; a +3% edge needs thousands of qualifying quotes, i.e. months of banked data.

### 2.4 The count model vs the market (C1, C2)

Model: XGBoost count:poisson + NegBin (alpha ~ 0.06-0.10), 22 features (rolling SOG / TOI / attempts / PP attempts per player,
own team shots for, opponent shots allowed, home/away, rest, position, league shot environment), NO market features. Expanding
monthly retrain (train sets 18.7k / 27.2k / 29.9k rows for Jan / Feb / Mar). Out-of-sample on its own target it is sound: Jan
holdout NegBin log-lik -1.54 vs -1.55 for the L20 baseline and -1.66 for a constant mean; MAE 1.01 vs 1.03 and 1.18.

Against the market, on 7,007 props with >= 3 books and a model prediction:

| | log-loss | Brier |
|---|---|---|
| consensus | 0.6816 | 0.2443 |
| model | 0.6872 | 0.2470 |
| blend w = 0.1 | 0.68157 | |
| blend w = 0.2 | 0.68168 | |
| blend w = 0.3 | 0.68191 | |
| blend w = 0.5 | 0.68277 | |

The model is WORSE than the market by 0.0056 log-loss; no blend weight beats the market by more than 0.00002 (noise). Blend
regression `y ~ a + b*logit(mkt) + c*(logit(model) - logit(mkt))`, game-clustered: **c = 0.10 (SE 0.11, t = 0.91)**, b = 1.06 (SE
0.08; the market is calibrated). The 95% interval for c is about [-0.11, +0.32]: whatever the model knows beyond the market is, at
most, a minor input. The model's own reliability is poorer than the market's (too extreme: predicted 28% -> realized 37%,
predicted 63% -> realized 60%).

### 2.5 Betting where the model disagrees with consensus (B1-B6), flat 1u

| Strategy | n | ROI | t | vs no-skill (paired, same props) |
|---|---|---|---|---|
| B1 gap >= 5pp, best price | 2,661 | -3.6% | -1.91 | +0.8pp (t 0.45) |
| B2 >= 3pp, best price | 4,216 | -3.8% | -2.51 | +0.5pp (t 0.32) |
| B3 >= 8pp, best price | 1,088 | -3.6% | -1.19 | +1.6pp (t 0.53) |
| B4 >= 5pp, median price | 2,661 | -5.4% | -2.94 | +0.9pp (t 0.51) |
| B5 >= 3pp, median price | 4,216 | -5.7% | -3.83 | +0.6pp (t 0.39) |
| B6 >= 8pp, median price | 1,088 | -5.4% | -1.84 | +1.7pp (t 0.58) |

No-skill reference on the same props (bet both sides, same prices): -4.5% at best price, -6.5% at median. Every model-disagreement
strategy loses money; side selection beats a coin flip by 0.5-1.7pp, never significantly. Edge ladder (best price): 2-4pp -5.0%
(t -2.2), 4-6 -5.1%, 6-10 -4.5%, 10+ +0.6% (n 519, t 0.14) -- the "only the top bucket pays" shape the repo has already learned
to distrust, and at median price the 10+ bucket is still -1.4%. No bucket is significantly positive.

### 2.6 The sog-predictor report's headline strategy (H1)

Unders only at {betmgm, williamhill_us}, blended p = 50% model + 50% {betonlineag, draftkings, fanduel} consensus:

* H1a (EV >= 5%): n = 382, ROI **-7.4%**, t = -1.09. H1b (EV >= 3%): n = 817, ROI **-5.5%**, t = -1.17.
* H1c / H1d (sharp consensus alone, no model): 0 and 1 qualifying bets -- the sharp consensus never shows a +3% EV soft Under
  once the vig is paid.
* All soft-book Unders blind: n = 13,294, ROI -7.6% (t = -6.2).
* Instability: an earlier run of the same code with one fewer feature (before the league-environment feature was added) gave
  H1b = -0.2% on n = 849. A filter whose ROI moves 5 points when a single feature is added is noise, not edge. The report's
  +14.3% / +6.7% did not reproduce; that figure was the best of 102 variants on a small sample, with a model that took market
  lines as inputs.

### 2.7 Variant tally and haircut

Pre-registered ROI tests: 14 (A1-A4, B1-B6, H1a-d); haircut |t| >= 2.7. Exploratory cuts shown: X1, X2, A1 by book (8) / side
(2) / EV bucket (3), B ladders (8), 6 paired tests = 27 more. Model specification variants: 2 (before/after adding the
league-environment feature; identical conclusions: c = 0.07 vs 0.10, B1 -4.5% vs -3.6%). Total looks about 43. **No test
reaches t >= +2.7. The only |t| above 2.7 are NEGATIVE** (B4 t = -2.9, B5 t = -3.8): the model-disagreement bets lose
significantly at median prices, because the market's hold is larger than whatever the model adds.

### 2.8 Verdict

| Question | Answer |
|---|---|
| Is the market well calibrated? | Yes (b = 1.06 +- 0.08; every book ~0.682) |
| Does the model beat the market (log-loss)? | No (0.6872 vs 0.6816) |
| Does the model add information to the market? | Not detectably (c = 0.10 +- 0.11) |
| Do bets on model disagreement make money? | No (-3.6% best price, -5.4% median) |
| Does the repo's Sink A show an edge? | Cannot tell: n = 60, +1.5% +- 13% |
| Does the sog-predictor +14% claim reproduce? | No (-5% to -7%) |

**Verdict: NO_EDGE (model); INSUFFICIENT_N (Sink A). No CLV was computable, so nothing here can be PROMOTE_CANDIDATE.**

## 3. Longer-history model check (our own 2019+ masters)

STATUS AT WRITING: NOT RUN against the quote window. The masters backfill (`nhl-backfill-pftl9`) is chronological and was at
2020 when this was written (~20 min per season); the 2025-26 season, the one that overlaps the quote window and is needed to
compare a long-history model with the market, lands last (ETA ~08:40 UTC 2026-10-14). The 2025-26 box scores are in `nhl_data.db`
already, so as soon as 2019-2024 are in the masters the comparison is one command (section 5, `--history-dir`): features and
training come from the long masters, quotes and outcomes from the DB. Expectations to hold it to: a longer history lowers the
model's own error but cannot change the market's calibration, so the C2 coefficient `c` and the B-strategy ROIs above are the
numbers to beat; a model that cannot raise `c` significantly above 0 with 6 seasons of training has no market-relative edge.

What WAS verified on the masters that exist (2019-20 season, 44k skater rows, shots file attached): the `from_masters` loader
runs end to end on real data (attempts and PP attempts populated for 98.5% of rows), and a model trained on Oct-Dec 2019 beats
the naive L20 rolling mean on Jan-Mar 2020 by a small margin (NegBin log-lik -1.620 vs -1.635, MAE 1.099 vs 1.101). So the
rolling-rate information is what carries the model; the extra features add ~1% log-lik. That is the same story as 2025-26 and is
consistent with "the market already prices rolling shot rates".

(If this section still says NOT RUN, nobody has re-run it yet: `nhl-backfill` had not finished.)

## 4. Things the live system will hit that this study did not

* **The live ParlayAPI SOG feed may be much thinner than this DB.** The study had 8-9 two-sided books per prop (median 6 per
  prop; 88% of props have >= 3). The one real captured NHL event (`tests/fixtures/odds/nhl_props_sample.json`) has `player_shots_on_goal` two-sided
  only from `underdog` (a DFS app, excluded) while `bovada` and `novig` send one-sided `Yes` ladders. If real banked snapshots look
  like that, the scorer will log `NO_CONSENSUS` evidence rows and never flag; Sink A would never fire either. Check
  `OddsAccum/icehockey_nhl/` once a few days are banked.
* **Closing lines are what is missing.** The only way to promote anything is forward-banked snapshots with an open and a close
  (the `odds-bank-*` jobs). Re-run the scorer's evidence rows against the later snapshots to compute CLV.
* **Lineup information.** Books know scratches and line changes by game time; the model only sees rolling history. Voids (600
  here) are refunded, but a player moved from the first to the third line is a pure information gap the market closes and the
  model cannot.

## 5. Reproduce

```bash
PYTHONPATH=. python -m nhl.props.study --db <nhl_data.db> --players-master <NHL/players_master.csv> --json out.json
PYTHONPATH=. python -m nhl.props.study --db <nhl_data.db> --players-master <players_master.csv> --history-dir <dir of local masters + shots_*.csv>   # long-history model
PYTHONPATH=. python -m nhl.props.train --source masters --out-dir local_artifacts/nhl_sog      # needs the masters (GCS read)
PYTHONPATH=. python -m nhl.props.score --model-dir local_artifacts/nhl_sog                     # one LOG-ONLY pass, local CSV
```
