# Props signal gating, sharp-book check and blend (a, b, c) refit -- 2026-11-02

Input: an external paste-in spec ("master quantitative profitability spec"). Each claim was checked against the code and the study docs.
Code + tests only; nothing deployed, nothing run against Cloud SQL / production GCS (the NBA study data was pulled read-only to a scratch dir).

## What shipped
- `ev/model_picks.py`: **Official tier needs measured signal** (`PROPS_SIGNAL_ALLOWLIST`, `NFL_HIER_SIGNAL`, `NHL_TRAINED_SIGNAL`, `has_measured_signal`) and
  `sharp_agrees is not False`. Failing rows stay `tracked` (stake 0, still settled / closed), so the forward record is not starved.
- `ev/daily_projections.py`: row fields `p_sharp_over`, `n_sharp`, `sharp_agrees` (`sharp_fields`, `SHARP_BOOKS` = pinnacle / novig / prophetx, 1-point tolerance,
  the bet book excluded, fail-open when no sharp book quotes); `blend_p_over` takes `(a, b)` (`BLEND_AB_BY_SPORT_STAT`, EMPTY, clamped to market +/- 8 pts when used);
  `BLEND_C_TRAINED` (NHL goals .37 / assists .34 / points .175 / saves .35) used for rows with `nhl_model == "trained_v1"`.
- `deploy/setup_daily_projections_job.sh`: `NHL_TRAINED_PROPS` is passed through only when the operator sets it (default unset). The script uses `--set-env-vars`
  (full replace), so a flag set via `gcloud ... --update-env-vars` is dropped the next time the script runs without it.
- `scripts/fit_blend_params.py`: refit + out-of-sample check of (a, b, c) from the study's `priced.parquet`.

## Spec claims that did not hold (not followed)
| Claim | Finding |
|---|---|
| Flip `NHL_TRAINED_PROPS=1`; trained models are "profitable" (+7.2% saves) | `nhl_trained_serving_2026-11-01.md` lists four flip conditions; none could be confirmed the day the jobs were deployed. Saves ROI5 is +12.2% / -1.5% by season (UNDETERMINED); goals / assists / points ROI5 at the best price are -0.2% / -0.3% / -1.5% ("information, not money"). Left OFF. |
| Allowlist of stats with c >= 0.08 incl. NHL goals / assists / points, NBA pr / pa / ra, NFL pass_tds / anytime_td | Board-engine NHL c is .03 / .06 / .03; NBA pr / pa / ra, NFL pass_tds / anytime_td have no measured c at all (BettingPros carries no pr / pa / ra lines). Allowlist is only what was measured. |
| `model_edge >= 0.020` and `n_books >= 4` gates | Already in `ev.daily_card.is_confluence_play` (model_edge >= 1.5%, n_books >= 4, score >= 68). |
| The NegBin under-bias "leaks directly into p_over_blend" | With c = .13-.19 the c-only blend inherits c x the gap: < 1 point (test pins it). The bias bites in side selection / confluence on the RAW model, already gated by `blend_ev`. |
| Fitted (a, b, c) fixes it and earns +5-6% ROI | See below: the intercept is tiny and adds ~0.0002 log loss out of sample. The +5.0% / +6.3% are median-price, in-sample-selected study numbers whose CIs include 0 in 2024 / 2025. |
| Train a market-residual model (`nba/props/residual_calibration.json`) | Tested out of sample (below): no gain. Not built. |
| Pinnacle / Novig as a sharp confirmation | Novig / ProphetX quote on BettingPros; Pinnacle never does there (only ParlayAPI may carry it). The check is therefore often vacuous (fail-open). It is unvalidated as a filter. |

## Refit (NBA, 362,777 props, `ship_noabs`, 2021-2025; fit on seasons < 2024, scored on 2024-25)
In-sample (a, b, c, t on c): assists (-0.011, 1.011, .085, 5.7); points (-0.059, 1.283, .017, 1.7); pra (-0.047, 1.341, .016, 1.6);
rebounds (-0.053, 1.087, .132, 9.4); threes (-0.057, 1.051, .190, 8.4). c reproduces the live constants.

| stat | realised over | market mean | c-only blend mean | (a,b,c) blend mean | LL market | LL c-only | LL (a,b,c) |
|---|---|---|---|---|---|---|---|
| rebounds | .480 | .495 | .487 | .472 | .6873 | .6868 | .6867 |
| threes | .465 | .483 | .477 | .461 | .6748 | .6740 | .6738 |
| assists | .486 | .495 | .491 | .491 | .6836 | .6834 | .6833 |

Verdict: **(a, b) NOT adopted** (`BLEND_AB_BY_SPORT_STAT` stays empty). The gain is 0.0002 log loss (noise scale) and the intercept over-corrects the 2024-25 mean
(the realised over rate sits between the c-only and fitted means); it pushes everything toward Unders, the direction the forward audit lost on. The plumbing stays so a future refit with a real
holdout gain can be adopted by editing one table.

Residual features (logit mkt, engine - mkt, (mu - line)/sqrt(mu), n_books; logistic, fit < 2024, scored 2024-25): d log loss vs the (mkt, engine - mkt) form = +7e-5 assists, 0 points, -5e-5 pra,
-9e-5 rebounds, +3e-5 threes. Nothing to build.

Reproduce: `scripts.nba_engine_vs_market` (see its doc) then `python -m scripts.fit_blend_params --priced $S/work/priced.parquet`.
