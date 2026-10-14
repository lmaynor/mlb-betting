# Handoff -- 2026-10-14 -- NBA player-prop MODEL pillar (paper / LOG-ONLY, nothing deployed)

Branch `feat/nba-props-model` (one PR, draft). Points / rebounds / assists / threes / PRA projection system with an honest
offline evaluation and a pre-registered forward evaluation. **No edge is claimed: no NBA prop odds history exists.** Nothing is
deployed, no Cloud Run job/scheduler/image/DB change was made, nothing was written to GCS, nothing posted to Discord, no
ParlayAPI credits were spent. Code lives under `nba/props/` (+ tests, the evaluation doc, one docs/solutions note). No shared
file was edited; CONTEXT.md is untouched (suggested edits at the bottom).

## What was built
| file | role |
|---|---|
| `nba/props/data.py` | SportsBlaze masters -> played-game rows + team games. ET game days from the schedule dumps (see "Gotchas"). |
| `nba/props/features.py` | 170 leakage-safe features. History becomes *post-game series*; a query row is joined with `merge_asof(..., allow_exact_matches=False)` so it only sees strictly earlier ET days. The same code builds training and live features. |
| `nba/props/dist.py` | NegBin / discretised-Gamma pmf/cdf, CRPS, P(over), variance-vs-mean table, **PIT recalibration** map. |
| `nba/props/train.py` | per-stat XGBoost mean (`count:poisson`) + heteroskedastic spread (small XGBoost on `(y-mu)^2/mu`) + family choice + PIT map, walk-forward by season, artifact writer. Local out dir by default; GCS only behind `--upload` / `score --models-gcs` (never exercised). |
| `nba/props/evaluate.py` | the offline evaluation -> `docs/analysis/nba_props_evaluation_2026-10-14.md` |
| `nba/props/identity.py` | SportsBlaze UUID <-> ESPN id bridge, ambiguity dropped, one-to-one enforced |
| `nba/props/score.py` | LOG-ONLY scorer of the banked props snapshot -> evidence rows |
| `nba/props/forward.py` | grade + CLV vs the closing banked snapshot + verdict (the pre-registered protocol below) |
| tests | `tests/test_nba_props_{features,identity,model,score,forward}.py`, `tests/nba_props_helpers.py`, `tests/fixtures/odds/nba_props_sample.json` (SYNTHETIC; no real NBA props payload existed) |
| `docs/solutions/integration-issues/sportsblaze-master-date-is-utc.md` | the date gotcha below |

Model artifacts follow the CONTEXT s5 contract: `xgb_nba_props_{stat}_v1.json` + `model_meta_nba_props_{stat}_v1.json`
(`version`, `features`, `feature_means`, `best_iteration` = the refit round count, plus `family`, `phi_table`, `pit_table`,
`log_only: true`) and an optional `xgb_nba_props_{stat}_var_v1.json` spread booster. A production-recipe fit was done locally
(train <= 2024-25, validate on 2025-26, refit on both; NegBin won for all five stats; 693-1,489 rounds; ~2.4 MB per booster) and
kept OUT of the repo (`nba/props/.gitignore` ignores `artifacts/`). No lightgbm/new dependency (pins untouched; dev venv was
python 3.14 with pandas 2.3.3 / xgboost 2.1.4 / numpy 2.3 / scipy 1.18 -- closest installable to the 3.11 pins).

## Offline results (full detail: `docs/analysis/nba_props_evaluation_2026-10-14.md`)
Rolling origin, test seasons 2022-2025, regular season, ~103k player-games per stat; the model that scores season T trains on
<= T-2, tunes on T-1, refits on <= T-1.
| stat | model MAE | best naive baseline MAE | gain |
|---|---|---|---|
| points | 4.547 | 4.634 (EWM hl=8) | 1.9% |
| rebounds | 1.882 | 1.912 (minutes-adjusted rate) | 1.6% |
| assists | 1.319 | 1.339 (EWM / min-adj) | 1.5% |
| threes | 0.871 | 0.875 (min-adj) | 0.5% |
| PRA | 5.981 | 6.146 (EWM) | 2.7% |
Gains are small, positive in every season vs EWM, bootstrap CIs exclude zero. 2025-26 alone: points MAE 4.527 / R2 0.531.
Calibration at PROXY lines (the line a naive EWM forecaster would balance at 50/50): ECE 0.004-0.009 after PIT recalibration;
NegBin alone was ~3pp too low near the centre for points/PRA. **Far tail is overconfident** (predicted P(over) >= 0.7 realizes
up to 6-9pp lower for points/PRA). Spread is about right even where minutes are unstable (z_rms 0.96-1.06, 80% coverage
0.76-0.89). Oracle diagnostic: knowing actual minutes would cut MAE by 9% (threes) to 28% (PRA) -- minutes/availability is the
lever the model lacks. Versus Sports-EV-Bot: same range as its README once its MIN>=10 outcome filter is applied (ours 4.73 vs
4.77 points MAE on that filter); its 'directional accuracy' is vs its own rolling L10 median and a naive EWM scores the same.
**None of this is evidence of edge versus prices**; the repo prior (every MLB count model NO_EDGE) stands.

## Gotchas found (worth a CONTEXT line)
1. **SportsBlaze master `date` is the UTC date of the tip** -- only 24% of games equal the ET game day; 104 team-dates carry two
   games. Rest/B2B/ordering use ET days from `NBA/raw/schedule_{year}.json` tip times (game ids match 9,186/9,186).
   `nba/data/flatten.py` should store the full tip timestamp (additive) so the schedule dumps stop being load-bearing.
2. `team_boxscores_master.csv` has NaN rebounds/turnovers for 2019-2024; team totals are summed from player rows.
3. 2019-20 is truncated at the COVID break (971 of 1,059 regular-season games). 46 rows have played=True with 0:00 minutes.
4. SportsBlaze abbreviations (GS, NO, NY, SA, PHO) differ from tricodes; `PHO` is not resolved by `nba.teams.team_code`
   (handled locally in `nba/props/data.py`, `nba/teams.py` untouched). Two different players are both "Jaylin Williams".
5. Bridge coverage today: 505 of 629 ESPN-crosswalk players bridge to SportsBlaze history; the 124 dropped are all `unknown`
   (2026 draftees / no SportsBlaze history); zero ambiguous.
6. `OddsAccum/basketball_nba/historical/` (written 2026-10-14 06:45 UTC by something else) holds preseason game/quarter totals
   and a few FanDuel/novig player rows in a different flattened shape; not used.
7. A first proxy line (floor(mean)+0.5) sat ~4-5pp off the median for skewed counts and produced implausibly strong hit-rate
   ladders; it was replaced by a naive-forecaster-median line and the change is disclosed in the evaluation doc.

## How minutes / injuries are handled (no lineup or injury data exists in this PR)
* **Widen**: the spread booster takes the full feature set (minutes volatility, recent absences, days since last game, new team,
  rollover, ...) and the predictive variance is fitted on out-of-sample residuals; z_rms and coverage are reported by bucket.
* **Abstain** (rows are scored and logged but can never be flagged): `THIN_HISTORY` (< 15 games), `VOLATILE_MINUTES` (std10 >= 7.5),
  `RECENT_ABSENCES` (>= 3 of last 10 team games missed), `RETURNING_FROM_ABSENCE` (>= 5 team games since last played),
  `MINUTES_TREND_SHIFT` (L5/L20 outside 0.70-1.30), `NEW_TEAM`. Thresholds are heuristics, unvalidated.
* Other guards mirror the sweep: >= 3 two-sided real books, EV in [3%, 15%] (`EV_TOO_HIGH` also when |p_model - p_consensus| > 0.15),
  MAD of book fair probabilities <= 0.06, >= 20 min before tip, snapshot <= 45 min old (via the sweep's own `read_latest_props`),
  DFS apps excluded, partial-crosswalk events not scored, alternate ladders skipped.
* **Follow-up data source: ESPN injuries endpoint** `site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries` -- verified
  reachable 2026-10-14 (HTTP 200, 30 teams / 136 entries, statuses Out / Day-To-Day with return dates; athlete id is in
  `athlete.links[].href` `/id/<espn_id>/`, the same id space as the crosswalk). Not built. It would need a historical
  inactive/availability feed to TRAIN with (not available), so it can first be used as a live abstain/exclude rule.

## Live scorer behaviour (verified on real history + synthetic prices; plumbing only)
* `python -m nba.props.score --local-dir ... --models-dir ... --events-file ...` on an opening-night event (2026-10-23)
  refuses all 36 groups with `STALE_HISTORY` (newest box score 2026-06-13, 132 days back). `--allow-rollover` scores them
  (history carries over, the season-to-date features reset) but every row is `ROLLOVER_HISTORY` and unflaggable -- offseason
  `missed_l10`/minutes windows are stale (e.g. a player hurt late last spring looks like a returning player).
* A replay event inside the history ran end to end (180 rows; mix of FLAG / EV_TOO_HIGH / LOW_EV / ABSTAIN_*). Prices were made up.
* Evidence schema is a superset of the sweep's `book_outliers` CSV, so `ev/grade_props.py` (`first_sightings` -> `grade_frame`
  -> `summarize`) grades it unchanged (tested). Extra columns: `p_model`, `mu`, `var`, `family`, `edge_vs_cons`, `edge_vs_book`,
  `p_cons_over`, `sb_player_id`, `abstain`, `rollover`, `history_last_date`, `n_played`.

## Forward evaluation -- decided by data, not hope (pre-registered; do not tune after looking)
1. Score every banked props snapshot: `python -m nba.props.score --gcs [--models-gcs]` (evidence -> `EVProps/nba_model/{ET day}/model_props_*.csv`).
2. After the day's games are final: `python -m nba.props.forward --day YYYY-MM-DD` (read-only): grades first sightings with
   `ev/grade_props.nba_actuals` (ESPN, by ESPN id), attaches CLV from the last banked snapshot strictly before tip
   (`mlb_core.odds.utils.clv_pct_from_prices`, same book/same side; also EV-at-close vs the closing consensus), builds candidates.
3. Population: `flag=True` rows only, first sighting per (game, player, stat, point, side, book), line-shopped to the highest-model-EV
   quote per (game, player, stat, point, side) -- the shape `mlb/analysis/backtest_market.backtest` produces.
4. Verdict = `backtest_market.verdict(cand)` verbatim: low-edge (edge <= `LOW_EDGE_MAX` 0.06) CLV must clear
   `clv_verdict` (mean >= +2.0%, t > 2.0) with >= `BAKEOFF_MIN_N` (30) CLV-matched low-edge bets for an early read; edge ladder
   monotonic (not "only the 10%+ bucket pays"); 10%+ bucket CLV not significantly negative -> `PROMOTE_CANDIDATE`, else `NO_EDGE`,
   else `INSUFFICIENT_N`. The paper -> live (T17) bar is `clv_verdict`'s own: >= 100 bets with a closing line, mean CLV >= +2%, t > 2,
   plus hit rate within 3pp of mean model probability, over >= 2 weeks of slates. ROI is reported, never the gate.
5. Caveats written into the protocol: closing = last bank before tip, not the true close (CLV is only as good as snapshot density);
   rows are correlated (stats of one player-game, many books) so n counts quotes -- cluster by player-game before trusting a t-stat;
   thin books (~3-4 real two-sided books pre-game) make consensus itself noisy.
Until >= 2 weeks of banked slates exist the answer is UNDETERMINED.

## Deploy path (NOT done)
Train locally -> `python -m nba.props.train --local-dir ... --out-dir ... [--upload]` (uploads to `Models/NBA/props/`); a Cloud
Run Job running `python -m nba.props.score --gcs --models-gcs` a few minutes after each `odds-bank-*` props run, on the
`mlb-betting-feeds` image (CONTEXT s21 image rule; `Dockerfile` already copies `nba/`). No `deploy/setup_*.sh` was written
(shared dir). Stays OUT of the MLB registry/monitors (s19). Needs fresh history (the nightly `nba-refresh-data` job).

## Decisions for the user
1. **SportsBlaze 2026-27**: the canary checks 2026-10-23. If the season is not there, the scorer correctly refuses and NO forward
   evidence accrues. Options: build an ESPN box-score history appender (`ev/grade_props.nba_actuals` already parses ESPN summaries;
   needs the bridge to key by SportsBlaze UUID or a parallel ESPN-keyed master), or accept `--allow-rollover` (log-only, never flagged).
2. Whether to spend an engineer-day on the ESPN injuries live abstain rule before the first scored slates.
3. Whether to provision the scorer job now (zero credits; it reads the bank) or wait for >= 1 week of banked props.

## Not verified
Anything on Cloud Run; behaviour on a real banked NBA props payload (fixture is synthetic, built from the documented shapes);
`--gcs` / `--upload` / `--models-gcs` paths against real GCS; whether any of this has positive EV.

## Suggested CONTEXT edits (lead)
s19: add `nba/props/` (paper model pillar, log-only) and the UTC-date gotcha; s21: add the scorer/forward-eval commands and
the verdict criteria above; s15 gotcha: "SportsBlaze `date` is UTC".
