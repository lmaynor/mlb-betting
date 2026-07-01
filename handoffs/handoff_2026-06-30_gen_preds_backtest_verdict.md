# Handoff -- 2026-06-30 -- gen_preds + backtest harness + the model-vs-line verdict

Built the cross-system prediction + backtest harness the odds-backfill program was
for (generalizes the NRFI edge-bucket method to every system), swept all 6
single-booster systems, and reached a **definitive negative result**: none of them
has a capturable edge vs the market. The apparent ROI is a soft-historical-line
artifact, not skill. Builds on `handoff_2026-06-29_parlay_primary_migration.md`
(odds_history) and the NRFI drift work in `roadmap_2026-06-28_model_improvement.md`.

Branch: `analysis/gen-preds-backtest` (PR open). Nothing deployed; this is analysis
tooling + a finding, not a runtime change.

## TL;DR
- **odds_history data foundation: signed off.** verify_odds_history green on the 9
  system markets: game_pk 100%, player_id ~100%, de-vig 100%, join 89-98% (the
  ~10% prop shortfall = scratched players = legit backtest voids). ATH/OAK alias
  fix confirmed (gpk 100% everywhere).
- **Harness shipped (3 modules):** `gen_preds` (score any system's full historical
  feature table with production artifacts), `backtest_market` (join preds->real
  lines, line-shop, settle, edge-bucket ROI/CLV), `walkforward` (leakage-proof: train
  pre-cutoff, score post-cutoff).
- **Verdict: NO capturable edge on model-vs-line.** All 4 count props (K/OUTS/
  BATTER_HITS/BATTER_TB) walk-forward OOS to +6.7..+10.8% ROI, but with **CLV ~0**
  and profit **only in the 10%+ edge bucket** (middle buckets flat/negative). HR was
  in-sample leakage (+10.7% train -> -0.8% out-of-window). GAME +21% but in-sample &
  no closing lines captured.
- **Root cause of the fake ROI:** historical odds are mostly BettingPros daily
  scrapes (soft/stale). A model looks +EV against a soft line, but by close it has
  corrected -> CLV~0 -> not a price you could actually get. See the docs/solutions
  entry.
- **Pivot identified (not started): line movement / CLV capture** using the 8x/day
  ParlayAPI snapshots (open->close). CLV~0 everywhere IS the finding -- edge, if any,
  is in line movement, not model-vs-line. Needs weeks of forward intraday data.

## What shipped (branch `analysis/gen-preds-backtest`)

| File | Role |
|---|---|
| `mlb/analysis/gen_preds.py` | Score a system's full historical `model_features.csv` with the SAME production artifacts the live runner uses (booster + model_meta feature-list/means/best_iteration/nb_alpha + calibrator). Emits tidy `(system,market,game_pk,game_date,player_id,kind,p_model,mu,nb_alpha,realized)`. `SPECS` covers HR,K,OUTS,BATTER_HITS,BATTER_TB,GAME. `p_over(line,mu,alpha)` = canonical NegBin (matches `run_batter_hits._negbin_p_over`). NRFI/F5 (v18 ensemble) deferred. |
| `mlb/analysis/backtest_market.py` | Join preds -> `odds_history` real lines, drop offshore books, **line-shop the max-edge quote per player-game**, settle vs realized label, bucket ROI/hit/CLV by model edge, optional time-split. `_won`/`_model_prob` handle YES/OVER=homered for hr_yn, HOME/AWAY (or team-abbrev) for game_ml. Accepts a precomputed `preds` frame (for walk-forward). |
| `mlb/analysis/walkforward.py` | Leakage-proof: train a FRESH model on `game_date < cutoff` reusing the system's production training contract verbatim (XGB_PARAMS/features/target/best-iter/NB-alpha), score `>= cutoff`. No production artifact touched, no calibrator (raw OOS lambda). `WF_SYS` = K,OUTS,BATTER_HITS,BATTER_TB (count only; binary WF = TODO). |

## Run recipe (Cloud Shell)
```bash
export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data   # ALWAYS chain this; secret-access flakes
PYTHONPATH=. python3 -m mlb.analysis.gen_preds --system HR --inspect          # verify feature cols
PYTHONPATH=. python3 -m mlb.analysis.backtest_market --system HR --since 2026-04-01 --split 2026-06-01
PYTHONPATH=. python3 -m mlb.analysis.walkforward --system K --cutoff 2026-06-01   # TRUE oos
```

## The evidence (walk-forward = true OOS, cutoff 2026-06-01)
| System | ROI | CLV | edge ladder |
|---|---|---|---|
| K | +10.8% | -0.4% | incoherent; only 10%+ pays |
| OUTS | +10.7% | -0.1% | incoherent; only 10%+ pays |
| BATTER_HITS | +10.7% | -0.2% | 6-10% is -19%, only 10%+ pays |
| BATTER_TB | +6.7% | -0.2% | every bucket <0 except 10%+ |

In-sample (production model) inflated these to +14-15% (HITS/TB) and hid the
leakage because train≈test (both inside the training window).

## How to read a backtest here (so nobody gets fooled again)
1. **ROI vs CLV must AGREE.** Positive ROI + CLV~0/negative = beating a soft line,
   NOT the market. Trust CLV (it's leakage-proof); it's the go/no-go gate.
2. **Edge ladder must be monotonic.** Real edge => ROI rises smoothly with model
   edge. "All profit in the 10%+ bucket, rest flat" = artifact, not ranking skill.
3. **In-sample train/test is not a holdout.** Production models were trained across
   the backtest window; use `walkforward` for the real answer.
4. **CLV is only as good as snapshot density.** Historical BettingPros = ~1/day, so
   entry≈close and CLV is weakly informative there; ROI train->test collapse is the
   stronger leakage signal on the historical window.

## Gotchas fixed this session
- HR realized label is **`hr`** (builder aggs `hr=("hr","max")`), not `hr_flag`
  (that's the registry `tune_target` alias).
- `hr_yn` selections are **OVER/UNDER** (adapters map yes/no->over/under), NOT
  YES/NO. Settling only YES inverted the bet (spurious 88% hit @8.53 decimal).
- `game_ml` sides may be team abbreviations -> resolve via row `home_team`/`away_team`.
- Cloud Shell metadata server flakes ("service account info is missing 'email'"):
  `gcloud auth login` does NOT fix Python clients -- they use ADC. Run
  `gcloud auth application-default login` (or restart the Cloud Shell VM).
- sklearn 1.6.1 (calibrator pickle) vs 1.8.0 (runtime) InconsistentVersionWarning --
  isotonic still loads but exact calibrated values are suspect; pin/re-pickle before
  trusting production calibrated probs.

## Next (pivot, not started)
1. **Open->close CLV / line-movement analyzer** on odds_history using is_open/
   is_closing + the 8x/day ParlayAPI intraday snapshots. Which markets/times/books
   move most; can we bet ahead of the move (positive CLV by construction).
2. Needs forward data to accumulate (intraday only exists from ~late June). Partly a
   "bank data, then analyze" program.
3. Optional: NRFI/F5 v18-ensemble support in gen_preds; binary walk-forward (HR/GAME);
   capture closing lines for game_ml (currently absent -> GAME CLV = nan).
4. Do NOT productionize any system off this harness -- none earned it.
