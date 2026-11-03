# Serving the trained NHL prop models (2026-11-01)

NHL only. Code: `nhl/props/{live_features,trained,serve_trained,eval_serving}.py`, `nhl/props/xgfeat.py` (placeholder rows, expected
starter), `ev/daily_projections.py` (`load_trained`, flag `NHL_TRAINED_PROPS`, default OFF). Nothing is deployed, scheduled or
published; the bundle exists only on a laptop.

## What was missing and what now exists

PR #70's models (XGBoost count:poisson + NegBin alpha) existed only as research walk-forward fits. Now:

| Piece | What it does |
|---|---|
| `live_features.build_live` | Pre-game features for UNPLAYED games: appends one placeholder row per player per game to the builders' inputs and runs the **unchanged training feature code**. |
| `trained.py` | Train one final model per stat, save booster + meta + manifest (`Models/nhl_props/{version}/`, `latest.json`), load, predict. Points = trained goals + assists. |
| `serve_trained.py` | `train` (builds xG tables, frames, fits, saves; publishes only with `--write`) and `score` (next 72 h of games -> `EVSweep/nhl_props/trained_latest.json`, mu / alpha per player x game x stat). |
| Board hookup | `NHL_TRAINED_PROPS=1`: a served prediction replaces the empirical mean and is priced with its own NegBin; rows with none stay on the empirical engine; a file older than 36 h is ignored. |

## Train == serve, verified

- **Real data, exact:** for 6 sampled game dates (2024-11 to 2026-10, including the current season) the placeholder path reproduces every
  `SKATER_FEATURES` / `SAVES_FEATURES` value of every skater and goalie row of the training-path frame (`scripts/nhl_serving_parity.py`;
  e.g. 288 skater + 16 goalie rows on 2026-01-20, 504 + 28 on 2025-02-22; 0 mismatched columns on any date). The last two dates were also
  re-run under the image's pinned **pandas 2.2.3** with the same result.
- Synthetic-league tests pin the same parity in CI, plus: nothing on or after the game date can reach a placeholder, a second upcoming game
  never feeds the first, and the bundle round trip reproduces predictions exactly.
- **A real skew bug was found and guarded.** A bundle trained under xgboost 3.4.1 LOADS under the image's pinned 2.1.4 but predicts
  different numbers (up to 28 saves off). Every artifact now stores a canary (real training rows + its own predictions); `load_bundle`
  refuses a bundle whose canary does not reproduce. The bundle was then retrained under the pinned stack (pandas 2.2.3, numpy 1.26.4,
  xgboost 2.1.4); validation scores matched the 3.4.1 fit to the 3rd decimal (goals -0.4545 vs -0.4547). **Train in the serving
  environment.** (Python 3.9 was the nearest available to the image's 3.11; the repo itself needs >= 3.10, so only the modules that do
  not import the rest of the repo could be run there.)

## Market comparison of what is served (BettingPros 2022-10 -> 2026-10, walk-forward quarterly refits)

Starter assumption: skater models use the EXPECTED opposing starter (most starts in the last 10, backup on a back-to-back), which a live
scorer can know. PR #70 used the actual starter; replacing it changed nothing (goals c 0.38 -> 0.37, assists 0.34 -> 0.34, points 0.19 -> 0.19).

| Market (served model) | n | log loss model / market | blend c (t) | ROI5 at best price | board engine (PR #149): log loss / c |
|---|---|---|---|---|---|
| saves (no-xG) | 7,350 | 0.6879 / 0.6920 | +0.75 (7.3) | +7.2% (3,530) | 0.6919 / +0.48 |
| goals (xG) | 52,783 | 0.4094 / 0.4107 | +0.37 (5.8) | -0.2% (4,039) | 0.4186 / +0.03 |
| assists (xG) | 68,898 | 0.6245 / 0.6238 | +0.34 (6.9) | -0.3% (16,403) | 0.6352 / +0.06 |
| points (goals + assists) | 66,054 | 0.6735 / 0.6709 | +0.175 (3.5) | -1.5% (16,164) | 0.6801 / +0.03 |

- The served models carry real information beyond the market on all four markets; the board engine carries almost none on the skater markets.
  Goals beats the market on log loss; assists and points are slightly behind it but add information when blended.
- **Only saves has a positive ROI, and it is regime-dependent**: c +0.37 / +1.02 / +0.35 and ROI5 +7.2% / +12.2% / -1.5% for 2023 / 24 / 25.
  91% of its disagreement bets are Unders in a market whose over-rate flipped from 0.45 (2023-24) to 0.515 (2025): blind Unders returned
  +3.6% / +4.0% / -6.4%. The model beats blind Unders by roughly 4 / 8 / 5 points, so there is selection skill on top of the bias, but
  the profit is not stable. Prices are the best at any book (optimistic) on close-ish snapshots; there is no closing-line series.
  UNDETERMINED, not a validated edge.
- **Skater markets: information, not money.** They beat the both-sides baseline by 3-6 points but the book's margin absorbs it.
- Points: serving goals + assists scored the same as the separately trained points model (log loss 0.67348 vs 0.67355, c +0.175 vs +0.188)
  and keeps the board's exact additivity law.
- The no-xG skater variants lose a quarter (assists) to a half (points) of their information, so skaters are served with xG; the xG tables
  are built by the same job. Saves keeps the no-xG set (matched the xG set, fewer moving parts).

## A live run (local, nothing published)

`score` on 2026-11-01 built features for 19 games and wrote 2,317 predictions (747 skaters x goals / assists / points, 76 goalie rows: the
two most-used goalies per team); points = goals + assists to 1e-5. With the flag on the board served 176 of 454 rows from the models (the
rest stay empirical); served and empirical means agree on average (saves 23.75 vs 23.81, points 0.626 vs 0.628, correlation 0.85-0.89).
The NHL roster endpoint rate-limited during the run, so the off-roster exclusion was skipped (its designed fallback).

## Deployed 2026-11-02 (jobs only; the board is NOT switched)

| What | State |
|---|---|
| Image | `gcr.io/concrete-crow-445205-m4/mlb-betting-nhl-trained:nhl-12798aa` (digest `sha256:60db6a0bb758...`), built from a clean `origin/main` (the #156 merge) into a DEDICATED repository; no shared tag was touched (`mlb-betting:latest`, `-feeds`, `-ev`, `-bp-crawl` unchanged). The jobs first ran a pre-merge branch build (`nhl-269554b`) and were repointed to the `main` build on 2026-11-02; the published bundle loaded unchanged (canary passed) and scoring / snapshots behaved identically. SHADOW MODE: the jobs write the served-predictions file and the paper-tracking ledger only; the board ignores the file (`NHL_TRAINED_PROPS` unset). |
| Bundle | Trained and published INSIDE that image: `Models/nhl_props/20261102T053829Z` + `latest.json`. Validation scores equal the laptop fit's (goals -0.4546). |
| Jobs / schedulers (Central) | `nhl-trained-train` Sun 08:00 (weekly retrain); `nhl-trained-score` 09:30, 13:30, 17:00 daily (a day with no game leaves the file alone); `nhl-paper-snapshot` :10/:40, 10:00-20:40; `nhl-paper-settle` 07:30. All `max-retries 0`, run as `mlb-betting-sa`, scheduler path proven (empty status, execution started). Pause all: `PAUSE=1 PROJECT_ID=... bash deploy/setup_nhl_trained_jobs.sh`. |
| Measured in the image | train 245 s, peak RSS 2.15 GB; score 159 s, peak RSS 2.17 GB (19 games, 2,317 predictions, same count as the laptop run); sized 4 GiB / 4 cpu. |
| Paper-tracking | First snapshot logged 263 priced props (4 games; goals / assists / points; no saves lines were posted yet). Settlement and the report fill in as games finish: `python -m nhl.props.paper_track report`. |

## When to flip `NHL_TRAINED_PROPS` (NOT done; all must hold)

1. Reliability of the predictions file: >= 6 consecutive scheduled `nhl-trained-score` runs over >= 2 Central days, every one exit 0 with `count > 0` and the file younger than 36 h at each board compute.
2. The first scheduled weekly retrain succeeded and a later score run loaded its bundle (the canary passed in production).
3. The code is in the image `daily-projections` runs: that job uses `mlb-betting-feeds`, built only from a clean `origin/main` by `deploy/build_all_images.sh` (see RUNBOOKS s8 for the blast radius: `SKIP_PROD=1` leaves `mlb-betting:latest` alone), after PR #156 is on `main`.
4. A first read of the paper-tracking report shows nothing broken (settled props accrue, model log loss not wildly off the market).
Then `gcloud run jobs update daily-projections --update-env-vars NHL_TRAINED_PROPS=1`; revert by removing the variable (no redeploy). Until then the board is unchanged and a served file is simply ignored.

## Still open

Forward evidence is accumulating, not concluded; the saves ROI above stays UNDETERMINED until the paper-tracking report (first settled props within days, a meaningful sample in weeks) says otherwise.

## Not modelled (the placeholder rows cannot know)

Confirmed starters, lineups, scratches, injuries, power-play units; and with two upcoming games for a team the second placeholder's
windows include the first (outcome-less) one.
