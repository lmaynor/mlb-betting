# Handoff -- 2026-06-25 -- Stale feature build + HR crash (ops alerts)

Two stacked bugs behind the 2026-06-25 ops alerts ("aborted runs due to
stale/failed feature build in multiple systems" + "HR error ... truth value of a
DataFrame is ambiguous"). Both fixed and verified live.

## Symptoms
- Ops alerts: multiple systems aborting on stale/failed feature build.
- HR runner crashing: `ValueError: The truth value of a DataFrame is ambiguous`,
  surfaced at `main.py:73` (the `run()` call site in `_run_system`).

## Root cause #1 (the real upstream cause): mlb-build-all-features on old paths
The `mlb-build-all-features` Cloud Run Job's baked-in command still pointed at the
pre-pillarize module path:
`/bin/sh -c "python3 -m runners.build_hr_features && ..."` ->
`ModuleNotFoundError: No module named 'runners'`. The `&&` chain aborts on the
first builder, so NO `last_build.json` sentinels get written -> they go stale ->
every runner's stale-build sentinel guard fires and aborts.

Why it was missed: this job's command is **not defined by any committed setup
script** (created manually with `gcloud run jobs`). The 2026-06-24 pillarize
re-provisioning (PR #21) ran setup_model_jobs.sh / setup_edge_enrichment.sh /
setup_fit_calibrators.sh -- none of which touch this job. The prior handoff's
claim "all jobs on mlb.* paths" was therefore wrong.

Fix (manual gcloud, Cloud Shell -- NOT committed):
```
gcloud run jobs update mlb-build-all-features --region=us-central1 \
  --image=gcr.io/concrete-crow-445205-m4/mlb-betting:latest \
  --command=/bin/sh \
  --args='-c,python3 -m mlb.runners.build_hr_features && python3 -m mlb.runners.build_nrfi_features && python3 -m mlb.runners.build_k_features && python3 -m mlb.runners.build_f5_features && python3 -m mlb.runners.build_batter_hits_features && python3 -m mlb.runners.build_batter_tb_features && python3 -m mlb.runners.build_game_features'
```
Verified: execution `mlb-build-all-features-qwxh7` completed 1/1. Sentinels fresh.

## Root cause #2 (HR crash on the abort path): PR #24
`run_hr._fetch_hr_odds` is annotated `-> dict` and its caller does
`if not player_odds:`. Its two abort branches (stale snapshot, stale/failed
feature build) returned `pd.DataFrame()` instead of `{}`. On the exact
stale-build path meant to skip gracefully, `not <DataFrame>` raised the
ambiguous-truth error and crashed HR. HR was the only runner with this shape
(checks live in its dict-returning fetcher); every other runner runs the checks
inside `_build_predictions` (`-> pd.DataFrame`, caller checks `.empty`), so they
aborted cleanly.

Fix: both branches now `return {}`. Added `tests/test_runner_return_types.py`
(AST guard: no `-> dict` runner function returns `pd.DataFrame()`). PR #24 merged.

## Also shipped: scoring after every snapshot (PR #25)
Snapshots are pulled 4x/day but scoring only ran 2x. Added `mlb-betting-afternoon`
(5 19 * * *) and `mlb-betting-pregame` (35 23 * * *) so each odds pull is followed
~5 min later by a `/run`. Safe under strict first-wins dedup
(`(system, game_date, game_pk, bet_type)`): extra runs only log brand-new markets,
never double-log or re-price. Policy: keep first-wins (re-pricing declined).
- `deploy/add_betting_schedulers.sh` (idempotent): clones the live
  mlb-betting-evening job's body + OIDC auth (so the systems list stays correct --
  live body is `1IOU`/... not legacy `NRFI`) and derives each schedule as
  `<snapshot cron> + 5 min`. Provisioned + verified ENABLED.
- `monitor_ops.SCHEDULER_JOBS`: added the 2 new jobs; fixed a pre-existing
  duplicate `mlb-snapshot-pregame` entry.
- CONTEXT.md section 9 updated.

## Verification (live, 02:35Z run of mlb-betting-afternoon)
```
run_nrfi  NRFI: sentinel ok -- ok (age 0.2h) ; NRFI: 0 bets logged
run_hr    HR:   sentinel ok -- ok (age 0.3h) ; HR: 0 bets logged    <- no crash
run_f5    F5:   sentinel ok -- ok (age 0.1h) ; F5: 0 / F1H: 1
run_k     K:    sentinel ok -- ok (age 0.1h) ; K: 3 / PITCHER_ER: 2
```

## Open items / follow-ups
1. **deploy_service.sh** -- recommended but not urgent. The build-job fix made the
   stale-abort path stop firing, so HR no longer hits it; but the HR `return {}`
   fix (PR #24) only reaches the live `:latest`/service after a rebuild. It's the
   safety net for any FUTURE stale build. (Both service and the build job use the
   `:latest` tag, so one `deploy_service.sh` propagates to both.)
2. **Commit a `deploy/setup_build_all_features.sh`** (idempotent, like the other
   setup scripts) so this job's command/image is version-controlled and can't
   silently drift on the next module move. The manual gcloud fix above is the
   exact content it should encode.
3. **Betting /run body omits BATTER_TB and 1I** (pre-existing) -- the live
   morning/evening template body is
   `["1IOU","HR","F5","K","BATTER_HITS","GAME"]`, missing `BATTER_TB`/`1I` that
   are in main.py DEFAULT_RUN_SYSTEMS. The new afternoon/pregame jobs mirror it.
   If those systems should be scored on schedule, update all four job bodies.

## PRs
- #24 fix: HR runner crashed with ambiguous-DataFrame on stale feature build (merged)
- #25 feat: score (/run) after every odds snapshot (merged)
