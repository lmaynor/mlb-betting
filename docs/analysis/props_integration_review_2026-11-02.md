# NBA / NFL / NHL props: integration, tracking and audited results (2026-11-02) -- for independent review

Branch `claude/nba-nfl-nhl-props-integration-fcf36e`. Goal as given: get the new props engines up and running, show whether they are profitable, backfill the
data they need, integrate the accumulated odds, track them like the MLB systems, publish to Discord, check mobile, and publish the findings for another model
to review. This document is that hand-off. **Everything below is paper / UNVALIDATED. Nothing here shows a profitable engine.**

## 1. Bottom line

| Question | Answer | Evidence |
|---|---|---|
| Are the new engines profitable? | **No evidence of it, and the NBA picks lose.** | Section 3 |
| Do any beat the market on real lines? | No. NBA / NHL engines are worse than the market alone; NFL hierarchical is better than the old NFL engine on 3 stats but still loses to the market | Section 3, linked reports |
| Are they running? | Locally on live data yes (Section 5); **NOT deployed** (build blocked by the permission layer, Section 8) | Section 5, 8 |
| Is every pick tracked? | Now yes: new ledger system `PROPS`, settlement, CLV, recap split, heartbeat monitor | Section 4 |
| Discord? | Official-tier picks only, via the existing sport alerts channels, labelled paper / unvalidated | Section 4 |
| Mobile? | Rendered at 375px, real problems fixed (props board was unusable), tsc + jest green | Section 6 |

## 2. What was built (all in this branch, nothing outside it)

- `ev/model_picks.py` + `deploy/setup_model_picks.sh` + `tests/test_model_picks.py` (25 tests): selection, ledger, closing-line CLV, Discord routing, heartbeat.
- `mlb/runners/settle_bets.py` (`_winter_sport_stats(system=)`), `mlb_core/notify/discord.py` (`model_stats` recap fields), `mlb_core/alerts/sport_alerts.py`
  (`log_bets=False`, model line in the embed), `mlb/runners/monitor_ops.py` (`_check_model_picks`).
- `scripts/props_forward_audit.py`: replays the live board archive through the production settler (section 3b).
- `scripts/nba_engine_vs_market.py` + tests + `docs/analysis/nba_engine_vs_market_2026-11-01.md`: the NBA serving engine against 363k real BettingPros props (a subagent's work; I re-ran its tests, I did not re-run the 1,083-day replay).
- Two data/engine fixes found along the way (section 7).
- Frontend (beezy-vip): mobile board, UNVALIDATED labelling, `PROPS` system, `pp_points` live, 38 new tests (a subagent's work; I re-ran `tsc` and the full jest suite).

## 3. Results

### 3a. Real-line studies (historical BettingPros, reference books, one last quote per day, no closing line so no CLV)
- **NBA** (new, this branch; 362,777 props, 2023-24..2025-26, all six variants priced as-of the game date): engine alone log loss 0.7114 (oracle absences) /
  0.7164 (none) vs market 0.6859, worse in every stat and season. Blend coefficient c: rebounds +0.13..0.14, threes +0.19..0.21, assists ~+0.09, points / PRA
  ~+0.02..0.04. New beats old on the same props (-0.0051 oracle, -0.0019 none), but PR #147 added no market-relevant information (pooled c +0.08 both). Median-book
  walk-forward blend ROI +2..8% at edge >= 2-5%, carried by rebounds / threes, fading to +2.5% / +3.0% in 2024 / 2025 with CIs that include zero; the engine's own side
  selection loses (-3.4..-3.7% vs -4.0% blind Under). At the best price a no-model line-shopping control beats the blend (+15.9% vs +8.6..9.2%): a best-quote artifact.
  Verdict UNDETERMINED leaning NO_EDGE.
- **NHL** (`docs/analysis/nhl_engine_vs_market_2026-11-01.md`, earlier session, not re-run): new engine beats the old one but adds no information beyond the market on
  goals / assists / points (c <= 0.06); saves c +0.48 but regime-dependent (-0.23, +0.84, +0.19 by season). ROI at best price -4% on skater markets, +1.5% saves.
- **NFL** (`handoffs/handoff_2026-11-01_nfl_hierarchical_props.md`, earlier session, not re-run): hierarchical better than the shipped engine at book lines for rec_yds,
  rush_yds, pass_tds; the market beats both engines everywhere; allowlist = those three stats.

### 3b. Forward audit of what the live board actually said (`docs/analysis/props_forward_audit_2026-11-02.{json}`)
Method: the board archive keeps each prop's last pre-game state (model price, consensus, best quotes). `scripts/props_forward_audit.py` applies the tracked-pick
selection to it, logs into a throwaway sqlite ledger and settles with the PRODUCTION winter settler. Flat 1u. CI = day-clustered bootstrap. **Engine = `empirical_ewm_v2`,
i.e. the OLD engines (the new ones were not deployed during these days), so this is a baseline for the tracker, not a test of the new engines.** Entry prices are
(near-)closing, the 30-minute gate is waived, CLV cannot be computed here.

| Sport | Days | Settled picks | Hit | Avg model p | ROI [95% CI] | Both-sides control ROI |
|---|---|---|---|---|---|---|
| NBA | 13 (10-20..11-01) | 1,197 | 43.8% | 55.0% | **-8.6% [-17.3, -0.3]** | -4.1% [-5.8, -2.4] |
| NHL | 16 (10-17..11-01) | 1,368 | 41.1% | 46.6% | +0.3% [-7.5, +8.5] | -1.1% [-4.7, +2.4] |
| NFL | 6 game days (10-18..11-01) | 151 (91 pending, 1 void) | 57.0% | 56.0% | +16.1% [+7.2, +34.3] (see caveat) | -8.9% [-15.0, -7.8] |

Reading it honestly:
- NBA: the model claims 55% on its picks and they hit 43.8%. The picks are selected for the largest model-vs-market gap, so this is winner's curse, and it loses more than blind betting.
- NHL: indistinguishable from zero; the model also over-claims (46.6% claimed vs 41.1%).
- **NFL +16% is NOT evidence of edge**: 6 clusters (so the bootstrap CI is unreliable), 151 bets, 79 of them receptions, the same games share outcomes, 91 picks unsettled, and the
  earlier real-line study found the market beats the engine. Do not read it as a result.
- Only 7 official-tier picks existed across all three sports in the window (NBA 6: 4-2; NHL 1: 0-1), too few to say anything.
- By-stat and by-edge-bucket tables are in the JSON; no bucket is monotone and nearly all CIs include zero.

## 4. Tracking, Discord, CLV (how a pick now lives)
selection -> `bets` (system `PROPS`, sport tag, flat 1u, paper, first sighting wins) -> existing winter settler -> CLV from the board archive's last pre-game state (same line only)
-> `#daily-recap` shows `<SPORT> model picks (unvalidated)` separately from the `EV` outlier alerts -> `ModelPicks/last_run.json` heartbeat read by `monitor_ops`.
Selection rule (pre-registered, not tuned): confluence true, real price at a non-offshore non-DFS book, >= 3 books, not edge-capped, >= 30 min to start; official tier =
the repo's own Official Card gate. Discord posts official tier only, through `post_sport_alerts(..., log_bets=False)`.
Live dry run on today's boards (new code, real data, no writes): NBA 45 picks (0 official), NHL 29 (0), NFL 7 (0).

## 5. Do the engines run on live data? (local, no writes, 2026-11-02)
NBA 1,366 rows (987 priced) in 28 s; NHL 718 (348 priced) in 62 s; NFL 695 (339 priced; 141 rows by `hierarchical_v1`, only the allowlisted stats) in 91 s.
Lines came from all three sources (ParlayAPI + BettingPros + BestBook). Feeds fresh: NFL / NBA / NHL masters and hoopR refreshed within the last day.

## 6. Mobile (375x812, real `next dev` render against a fixture API built from the real archive rows)
Fixed: the props board needed 980px (only score and player visible at 375px) -> compact 6-column table with a tap-open detail row; no UNVALIDATED label on the winter boards ->
badge + detail note + cockpit chip; `pp_points` was still "Data gap"; tap targets under 44px; 24px gutters; day strip started off-screen; `PROPS` / winter bet types rendered raw.
Not verified: 390x844, desktop re-render after the CSS changes, a real device / iOS safe area, a winter record view (the frontend has none). Known unfixed: first paint on a phone
shows the desktop filter bar (SSR), `/ev/games` prints a raw engine id, `LIVE_ENGINE.summary` text is stale about injuries. Screenshots are in the session scratchpad only (not committed).

## 7. Bugs and gaps found (each verified in code or data)
1. **NBA live opponent column was never set** (`mlb_core/data/cockpit_form.py` `_load_nba`): the player master has no `opp_abbr`, and spells five teams GS/NO/NY/SA/PHO. The opponent factor and the
   team-makes assist factor were inert on the live board while every replay had them. Fixed (derive opponent from the two teams per `game_id`, fold codes). Measured gain -0.0011 log loss
   (z -6.9). This CHANGES live NBA numbers once deployed. The same raw-code miss remains in `nba/props/engine_eval.py` (opponent factor 1.0 for those five teams): not fixed.
2. **NHL `pp_points` never reached the board**: no sportsbook feed carries the market (ParlayAPI serves `player_power_play_points` only at the DFS apps sleeper / underdog, probed live for 2 credits;
   BettingPros / BestBook carry none) and the stat was not in `ROTATION_STATS`. Now projection-only rows (124, McDavid 0.63/game). It can never be priced or tracked as a bet with this data.
3. `BLEND_C_BY_SPORT_STAT` display constants overstate measured c (assists .18 vs ~.09, PRA .12 vs .02-.04, points .10 vs .02-.03): not edited, owner's call.
4. NHL skater master: preseason rows 2019-2025 and one regular-season game (2026020131, 36 rows) have no power-play columns; the `nhl.data.powerplay` report-only run cannot fill it from the shots master. Negligible; not fixed.
5. `daily_projections` merge summary can print a negative `games_parlay_only` (NFL showed -10): cosmetic counter bug, not fixed.
6. `BetTracker.log_bet` only dedups when `game_pk` is set, which winter bets never have; `ev.model_picks` dedups itself. The existing `EV` winter alerts rely on upstream notify-state instead.

## 8. Deployment state
**Nothing was deployed.** The attempt to build the feeds images (`SKIP_PROD=1 ALLOW_NON_MAIN=1 bash deploy/build_all_images.sh`) was blocked by the session's permission layer and not retried. So:
the new NBA / NHL / NFL engines, the NBA loader fix, `pp_points` rows and the `props-model-picks` job are NOT live; the live boards still run what was deployed on 2026-11-02 (service rev 00351; the
`daily-projections` job has no `NFL_ENGINE` env, so NFL is on the old engine). Verified instead by running the new code locally against live GCS data (section 5) with no writes, and by the full test suites
(pytest 3,093 passed; tsc clean; jest 759 passed). To go live, in order: build the feeds images (command above), `bash deploy/setup_daily_projections_job.sh` (sets `NFL_ENGINE=hierarchical`),
`bash deploy/setup_model_picks.sh`, check `python -m ev.model_picks --sport all --dry-run`, and deploy the Vercel frontend. Retagging prod `mlb-betting:latest` (needed for the recap fields and the
`monitor_ops` heartbeat check) is a separate owner decision because it changes every MLB job's next run; until then the recap's existing `props (unvalidated)` field counts PROPS bets together with `EV` alerts.

## 9. Questions a reviewer should push on
- Is the forward audit's replay faithful to what the job will log, given it uses last-state rather than first-sighting rows? (It cannot reproduce the 30-minute gate.)
- Is `confluence` (model side == market side, both edges > 0) a sound pre-registered filter, or does it just select winner's-curse rows? The NBA hit-rate gap (43.8% vs 55%) suggests the latter.
- Settlement correctness for combo stats (`pra`, `pr`, `pa`, `ra`) and NFL `anytime_td` scope (rush+rec vs return TDs: unverified against book rules).
- The NBA harness joins on the UTC date of BettingPros' `scheduled` (95.9% exact-name match); check the 3,256 same-UTC-date duplicates dropped.
- Whether `tracked`-tier picks (which are never posted) should exist at all, or only add multiplicity.
- What forward sample size is needed before any verdict: with ~1,200 NBA picks per two weeks and a day-clustered SE of ~4.5%, an effect of +5% needs several weeks; CLV (now captured) converges faster.
