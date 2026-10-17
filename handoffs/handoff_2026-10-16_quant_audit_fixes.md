# Handoff -- 2026-10-16 -- quant-audit fixes (grace window, NHL codes, blend/shrink helpers, NBA rollover)

Branch `feat/quant-audit-fixes` (own PR; NOT pushed to main). Source: an external 7-point "quant audit" spec. Every point was checked
against the repo and real GCS data before coding; what held, what did not, and what was left out are all below. Nothing is deployed.

## What changed (code)
| Area | Change |
|---|---|
| `mlb_core/odds/bettingpros_loader.py` | `grace_minutes` (default 0 = unchanged) on `consensus_from_long` / `build_market_consensus`; `DEFAULT_GRACE_MINUTES` (10 for NBA/NHL/NFL); CLI `scripts/join_bettingpros_history.py --grace-minutes N|auto` |
| `nhl/props/history.py`, `study.py` | `canon_team()` (BettingPros `LA/NJ/SJ/TB` -> `LAK/NJD/SJS/TBL`; ARI/UTA dated); `attach_outcomes` retries a UNIQUE +-1 day match |
| `mlb_core/risk/calibration.py` | `shrink_edge()` (knee 6%, tail slope 0.35, cap 10%) and `market_blend_prob()` with `BLEND_C` (NBA points .07 / reb .19 / ast .18 / 3pm .28 / pra .05, NHL sog .30; anything else c=0 = the market) |
| `mlb_core/odds/model_vs_market.py` | `blend_price_test()` / `render_price_test()`: walk-forward by season, Brier/log-loss blend vs market vs model, flat-stake ROI with date-clustered bootstrap CIs at >=3/4/5%, with `market` (line-shopping) and `model` rules as controls. Hooked into `nba.props.history_join.market_report` and `scripts/nhl_sog_market_eval.py` |
| `beezy-vip/lib/ev-math.ts` | `shrinkEdge()`; `kellySizing` shrinks a positive edge over the price before sizing (`shrink=false` restores the raw stake) |
| `beezy-vip/lib/beezy-score.ts` | model-only HR score capped at 64 (LEAN) unless >= 4 books back it |
| `nba/props/score.py`, `config.py` | `allow_rollover="auto"` (CLI default): rollover is automatic only for event days <= Oct 1 + 35 days; `--no-auto-rollover` restores the refusal. Direct `check_history` callers keep the strict default |
| `nba/props/features.py`, `nba/data/espn_injuries.py` | live availability accepts `{id: P(absent)}`; `absence_weights_by_team()` (Out 1.0 / Doubtful .75 / Questionable .35 / Day-To-Day .25). Training path and set-based live path unchanged |
| `scripts/pooled_shift_probe.py` | `--mode outcome`: fits RMS((actual - modal line)/line) per stat from nflverse outcomes |

## Findings from real data (2026-10-16)
- **2025 NFL: the join keys are fine; the quote timestamps are the problem.** 46/46 resolved 2025 passing-yards consensus rows matched a model row on
  `(gsis_id, game_date)`. But in 2024 `updated` is the last genuine change (median ~6 h before kickoff) while in 2025 ~90% of stamps sit 1-5 minutes
  AFTER the scheduled start (p90 ~5.4 min) -- a freeze/close stamp. The strict `pregame` filter therefore discards nearly the whole 2025 close. The spec's
  NFL grace of 2 min would still lose most of them; 10 min recovers nearly all. The doc's earlier "`no_model_row` dominates 2025" was NOT reproduced on this
  sample (single weeks, one market). Re-run the full NFL build with `--grace-minutes auto` to get real coverage.
- **NHL `no_game`: confirmed, cause found.** BettingPros emits Kalshi-style `LA/NJ/SJ/TB` (+ `ARI` in 2022-24); the game log has `LAK/NJD/SJS/TBL`, `ARI`
  through 2024-04 and `UTA` from 2024-09. The old blanket `ARI -> UTA` also mislabelled 411 real Coyotes games.
- NHL/NBA grace: the same 1-5 minute stamp pattern was reported for all three sports in the original analysis; not re-measured here for NBA/NHL.

## Spec claims that did NOT hold (do not repeat them)
- "`P_blend` strictly beats both market and model on Brier and log-loss": **never tested**. The doc only shows a significant blend coefficient c, which the doc
  itself calls "detectable, not yet shown tradable". NHL SOG priced at the best Under was ROI -0.08% (CI -1.2..+1.1). `blend_price_test` now exists to
  test it; **it has only been run on synthetic data** (the real NBA / NHL consensus builds take hours). Treat `BLEND_C` as a prior, not a result; c was
  fitted on the same test rows it describes.
- NFL join keys (season/week vs gameday), see above. The join is on `(gsis_id, ET game date)`, as designed.
- "NBA training counts healthy bench DNP-CDs as missing": the rotation is already restricted to players who played in the team's last 5 games AND average
  >= 10 min over their trailing 10; training and live share ONE function (`_group_candidates`). The real skew is that training knows who actually sat while live only
  sees the injury report -- that is what the probability weights address. The spec's thresholds (16.0 vs 18.0 min, 3-of-5 games) contradict each other and
  have no evidence behind them; defaults were NOT changed. A/B them with the walk-forward before touching `ROTATION_MIN_L10`.
- "Hard-fails STALE_HISTORY with no rollover": a rollover already existed (opt-in). The EWM "0.75 prior-season weight" was not added: the L10/EWM windows roll
  into the new season by construction as games accrue.
- Edge shrinkage vs Beezy Score: STRONG-tier EV alerts realised +25.8% flat ROI (n=918) on the same data that shows the 12%+ bucket is overconfident, so the
  score formula was left alone; only SIZING shrinks.

## Not done (needs a decision or a long run)
1. **Trained artifacts into `ev/daily_projections.py`** -- not attempted. `Models/{NFL,NBA,NHL}/` does not exist in GCS, each sport has a different feature/scoring
   path, and showing unvalidated models on the public board contradicts the handoff_2026-10-15 decision. If wanted: train + explicit `--upload`, then one
   adapter per sport behind a flag, every row still `validated:false`.
2. **ESPN NBA box-score fallback in `nba/data/refresh.py`** (Opening Night is 2026-10-20). The auto-rollover covers the first ~5 weeks of reads, but the
   history does not advance until SportsBlaze (or a fallback) supplies 2026-27 box scores. `ev/grade_props.nba_actuals` already parses ESPN summaries.
3. **Real-data runs**: NBA/NHL consensus rebuild with `--grace-minutes auto`, the blend price test on each, the NFL full rebuild, and the outcome-based
   `POOL_SD_FRAC` fit across the whole history (a first pass is in the PR description).
4. `POOL_SD_FRAC` in `ev/props.py` was NOT edited (see below).

## Outcome-based sd fraction (`pooled_shift_probe.py --mode outcome`, 2023-09..2026-02, grace 10 min, 14,899 consensus rows joined to nflverse)
RMS of (actual - modal line) / line, by line tercile (low / mid / high), against the production constant:
| stat | n | bias | RMS | MAD-based | terciles | production |
|---|---|---|---|---|---|---|
| pass_yds | 1,199 | +0.003 | 0.336 | 0.321 | 0.40 / 0.30 / 0.30 | 0.22 |
| rush_yds | 3,902 | +0.171 | 1.057 | 0.773 | 1.53 / 0.83 / 0.57 | 0.65 |
| rec_yds | 8,875 | +0.270 | 1.254 | 0.904 | 1.77 / 1.04 / 0.71 | 0.55 |

Read this before changing anything: (1) the form `sd = frac * line` does NOT fit -- the fraction falls 2-2.5x from the low to the high tercile for rush/rec, so no
single constant is right; (2) rush/rec are right-skewed (mean above the median line: bias +17% / +27%), so a total-sd number overstates the LOCAL density around the
line, which is what `shift_p_over` needs (dP/dpoint near the line); (3) pass_yds has only 1,199 rows, so the QB population is thin (resolution/stat-row drops were
not broken out). The right follow-up is to fit the local slope directly (e.g. isotonic/logistic P(over) vs distance from the modal line on these rows) or let the
pooled shift use a line-dependent sd, then A/B pooled vs exact consensus on outcomes. Production stays as is until then.
