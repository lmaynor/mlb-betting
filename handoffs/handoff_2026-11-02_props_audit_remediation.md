# Handoff -- 2026-11-02 -- remediation of the 2026-11-02 props audit review (code + tests only, NOT deployed)

Branch `claude/review-prs-146-158-c8724c` (worktree `parlay-api-markets-budget-a02d7c`). Nothing deployed, **nothing run against Cloud SQL / GCS**.
Input: an external review of PRs #146-#158 with a paste-in implementation spec. Each claim was checked against the code first; what held, what did not:

## Verified true, fixed
1. **Tracked-tier picks were logged as staked bets** (`ev/model_picks.log_picks`: `stake=1.0, kelly_triggered=True` for BOTH tiers). The recap
   (`settle_bets._winter_sport_stats`, no `kelly_triggered` filter) and `/api/public/picks*` (`kelly_triggered AND stake > 0`) therefore counted
   every tracked row. Now official = staked 1u, tracked = `kelly_triggered=False, stake=0`. Settlement still grades them (the settler's
   `stake or 1.0` is now explicit: zero-stake rows grade on a 1u reference), `capture_closing` still fills CLV (no `kelly_triggered` filter, test pins it),
   `_winter_sport_stats` counts staked rows only, and `scripts/props_forward_audit.py` ROI divides by a 1u reference for zero-stake rows (it would have divided by 0).
2. **Official tier needs `blend_ev` >= 0.025.** `ev/daily_projections.make_row` now exports `p_blend_side` and `blend_ev` (best quote x the market-anchored
   blended posterior). A board with no `blend_ev` fails CLOSED to `tracked` (the spec said "when present"; I chose the stricter reading: a stale-schema board
   cannot mint an official pick). Confluence itself is unchanged (it still uses the raw model, and `model_edge > 0` is true by construction because the side IS the
   model's preferred side, so the filter that does the work is `market_ev > 0` at the best price).
3. **`BLEND_C_BY_SPORT_STAT`**: NBA threes .19 / rebounds .13 / assists .09 / pra .02 / points .02 = the measured `ship_noabs` c (nba_engine_vs_market_2026-11-01.md);
   NHL goals .03 / assists .06 / points .03 = measured (nhl_engine_vs_market_2026-11-01.md).
4. `nba/props/engine_eval.load_master` now folds GS/NO/NY/SA/PHO -> tricodes (test added). `union_events` `games_parlay_only` no longer goes negative (test reproduces -1 with a third feed).
5. `LIVE_ENGINE.summary` (beezy-vip) no longer says the board ignores injuries / teammate absences; the jest assertion was updated.

## Claims in the review that were NOT true / not followed (reverse any of them)
- **"Measured" c for NBA ra .11, pr .06, pa .05, steals .05, blocks .05 and NHL blocks .10, pp_points .05, sog .30, plus a whole NFL table: none of these were measured.** The
  NBA study says BettingPros carries no pr/pa/ra/steals/blocks; sog .30 is the TRAINED model's c; the only measured NFL c is rush_yds +0.25. I kept the review's values for the unmeasured
  NBA/NHL stats as priors (labelled as such in the code comment), used the MEASURED numbers where the study has them (NHL goals/points .03, not the review's .05/.06), and did NOT add an NFL table.
- "Official 4-2, +50.2% ROI" is n=6 (+ 1 NHL loss); not evidence. The +5.0% / +6.3% blend ROIs are the 2022-25 walk-forward study at the MEDIAN price (CIs include zero in 2024/25), not the forward audit; at the best price a no-model line-shopping control beats the blend (best-quote artifact).
- `/ev/games` printing a raw engine id: nothing in `app/` or `components/` renders one in this tree (`gameFeedEngines` is exported but unused). Left alone.
- `public_api.get_today_picks` hard-codes `sport = 'mlb'` and `tweet_drafter` never reads PROPS rows, so the tweet pipeline was never exposed to the tracked rows.
- The public site flood claim is partly overstated: `/api/public/picks*` default to `sport=mlb`; the recap embed (`_winter_sport_stats`) was the live leak.

## To do on deploy (owner)
1. Rebuild the feeds image from a tree containing this, re-provision `daily-projections` (writes `blend_ev`) and `props-model-picks`. The service image is needed for the recap change (`settle_bets` runs from `mlb-betting:latest`).
2. Demote today's rows: `gcloud run jobs execute props-model-picks --region us-central1 --args="-m,ev.model_picks,--fix-tracked-stakes,--dry-run" --wait` (check the count), then again without `--dry-run`. Idempotent. NOT run here, and the `execute --args` override form is untested (the setup script uses the same `--args="-m,..."` form for create/update).
3. Rows already settled keep their result / profit (1u reference); only `kelly_triggered` / `stake` change.

## Verified
`pytest tests` and `beezy-vip`: `npx tsc --noEmit` clean, jest 45 suites. NOT verified: live DB migration, the Cloud Run job, a real board with `blend_ev`, the recap embed on production data.
