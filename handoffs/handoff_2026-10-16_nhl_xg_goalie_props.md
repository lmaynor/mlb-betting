# Handoff -- 2026-10-16 -- NHL xG + goalie saves / skater goals-assists-points research models

Branch `feat/nhl-xg-goalie-props`, one PR. **Nothing is deployed; no job, scheduler, GCS write, bets-table write, Discord post or
ParlayAPI credit.** Python tests: 1,959 pass (15 new). Full write-up and every table: `docs/analysis/nhl_xg_saves_goals_validation_2026-10-16.md`.

## Request
Use six Downloads archives (nhl-xG-model, nhl-fantasy-predictor, hockey_dfs_optimizer, nhl-performance-model, qrob-nhl-goalie-model,
the-quant-rink) plus MoneyPuck / Natural Stat Trick / Evolving Hockey to build hockey prop models and data infrastructure.

## Read first
- **The three sites are out for a commercial product** (`docs/solutions/conventions/nhl-data-source-licensing.md`): MoneyPuck = non-commercial /
  journalists only ("for other purposes please inquire"); Evolving Hockey bars bots and non-personal use; Natural Stat Trick 403s automated
  fetches (terms unreadable). Nothing of theirs was ingested. We fit our OWN xG on the official-API shots we already hold.
- **Verdict: no promotable edge.** Information beyond the market exists on goals (blend c +0.38, t 5.9), assists (+0.34, 7.0) and saves
  (+0.69, 6.9; regime-dependent); betting the disagreements is ~0% at best book prices for skater stats and -1.0% in 2025-26 for saves.
  Points: no edge. No CLV is computable from BettingPros' one-snapshot-per-day history, so PROMOTE_CANDIDATE is unreachable by construction.
- **The saves headline (+6.7% ROI pooled) is mostly a 2023-25 regime**: books' lines lagged a falling shot volume (over-rate 45%), then
  caught up (51.5% in 2025-26, where the model is slightly worse than the market). Always read per season.
- **xG helps saves not at all** (goalie GSAx is the least persistent thing measured, 0.15 YoY) and skater stats modestly (same sign in all
  three; one observation, not three).

## What shipped
`nhl/xg/{features,model,aggregate,build,evaluate,data}.py`; `nhl/props/{xgfeat,countmodel,bp_eval,eval_xg}.py`; `tests/test_nhl_xg*.py`;
`nhl/props/history.py` TEAM_FIX gained `LA/NJ/SJ/TB`; README + CONTEXT s21 notes; three `docs/solutions/` entries.
xG (own, walk-forward, out-of-season): AUC 0.755 vs 0.720 for a distance+angle logistic, log-loss 0.2185 vs 0.2270 (2024-26 holdout).

## Bugs / gotchas found (all written up in docs/solutions)
1. BettingPros strict `pregame` flag dropped 46% of saves consensus rows, the TRUE closing quotes (10-minute grace window: 52% -> 98% coverage).
   The earlier SOG-vs-market analysis (`bettingpros_model_vs_market_2026-10-15.md`) saw the same loss and had it as "not tested".
2. BettingPros `LA/NJ/SJ/TB` vs API `LAK/NJD/SJS/TBL`; ARI -> UTA.
3. `gsutil -m cp` hangs at 0 bytes on this Mac (Python 3.14); `gcloud storage cp` works in seconds. Quote `gs://` globs (zsh).
4. The qrob-style saves benchmark is clearly worse than the market (log-loss 0.7166 vs 0.6920); the archive's 65% target is a 2-day sample.
5. NOTE for the existing SOG hook (`nhl.props.study.bettingpros_model_vs_market`): it still uses the strict `pregame` flag. It now maps the
   four team codes (TEAM_FIX) but not the slack window; re-run that analysis with `bp_eval.apply_slack` before trusting its coverage.

## Not done / decisions for the user
1. Resume NHL odds banking (paused by an earlier decision) -- the only route to CLV for saves / goals / assists.
2. Whether to deploy a nightly xG table job (`NHL/xg/*.parquet`) or feed `ixg` features into the `empirical_ewm_v1` projection board: separate PR.
3. Whether to ask MoneyPuck for written permission (nothing depends on it).
4. A live scorer needs a starting-goalie confirmation feed; skater models need lineup / PP-unit / injury inputs the market already prices.
5. Richer xG (rush / turnover context from `NHL/raw/pbp/`, shift charts -> on-ice / RAPM) is possible; expect modest gains.

## Local scratch (not in the repo)
Pulled data + outputs were under the session scratchpad (`.../scratchpad/{data,xg,eval}`); everything regenerates from the commands in
section 7 of the analysis doc.
