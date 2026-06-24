# Handoff -- 2026-06-24 -- Pillar restructure (mlb/ symmetric with nba/)

Branch: `restructure/pillarize-mlb` (NOT pushed; user reviews + pushes).

## Goal
Make the repo scalable for multi-sport (NBA live, NFL/NHL next). Before this,
NBA was a clean nested package (`nba/`) but MLB was sprawled across the repo root
(`runners/`, `training/`, 8 per-system config dirs). Now both pillars are
symmetric. Chosen option (from 3): **pillarize MLB, keep `mlb_core` name** -- the
`mlb_core` -> `core` rename stays DEFERRED (CONTEXT section 0), lowest risk.

## What changed (code -- all on the branch, verified)
- `runners/`  -> `mlb/runners/`
- `training/` -> `mlb/training/`
- 8 system config dirs -> `mlb/systems/{HR_Pro, NRFI_Pro_System, F5_Pro_System,
  K_Pro_System, OUTS_Pro_System, BATTER_HITS_System, BATTER_TB_System, GAME_Pro_System}`
- Added `mlb/__init__.py`, `mlb/systems/__init__.py`, and `__init__.py` to the 3
  system dirs that lacked one (HR_Pro, NRFI_Pro_System, F5_Pro_System).
- Import rewrites (26 .py files): `from runners.` -> `from mlb.runners.`,
  `from training.` -> `from mlb.training.`, `from <SYS>.config_` ->
  `from mlb.systems.<SYS>.config_`.
- Module-path STRINGS rewritten (consumed by `importlib.import_module`):
  `main.py` builders dict + `mlb_core/registry.py` `builder_module=`/`runner_module=`.
- `Dockerfile`: 10 pillar COPY lines collapsed to `COPY mlb/ ./mlb/`.
- Deploy scripts (8): `setup_model_jobs.sh`, `setup_retrain_*.sh`,
  `setup_edge_enrichment.sh`, `setup_fit_calibrators.sh`, `deploy_service.sh`
  now use `mlb.runners.*` / `mlb.training.*` and compile `mlb/`.
- Declutter: 4 notebooks -> `notebooks/`; 6 one-off ops scripts -> `scripts/`
  (`cleanup_discord`, `debug_ops`, `patch_e10_line_movement`, `setup_discord`,
  `fix_ops_issues*`, `pull_ops_logs`). Kept at root: `main.py`, `setup.py`,
  `tweet_drafter.py` (the last is Docker-COPY'd by tweet jobs).

## Verification (local)
- `python3 -m compileall mlb_core mlb nba main.py` -> OK.
- `importlib.util.find_spec` resolves `mlb.runners.*`, `mlb.training.*`,
  `mlb.systems.*.config_*`.
- pytest: branch == main == `24 failed, 191 passed, 27 errors`. The failures are
  identical on main (local env missing flask/sqlalchemy/xgboost) -> **zero new
  breakage from the move**. `run_outs` is a pre-existing dead registry ref (OUTS
  runs via run_k), unrelated.

## REQUIRED operational follow-ups before/after merge (NOT code -- live system)
The module paths baked into GCP changed. After merging + redeploying:
1. **Rebuild the image**: `./deploy/deploy_service.sh` (slims nccl too; picks up
   the new `mlb.*` paths and `COPY mlb/`).
2. **Re-provision all Cloud Run Jobs** so their `-m` commands point at `mlb.*`:
   - `./deploy/setup_model_jobs.sh` (all retrain/calibrate jobs)
   - `./deploy/setup_edge_enrichment.sh`
   - `./deploy/setup_fit_calibrators.sh`
   - any `setup_retrain_*.sh` still in use
   Until re-provisioned, scheduled jobs fail with `ModuleNotFoundError: runners`/`training`.
3. **Verify** one job manually: `gcloud run jobs execute mlb-build-edge-enrichment
   --region=us-central1 --wait` and a retrain job, then confirm exit 0.
4. The Flask service (`/run`, `/build-features`, etc.) uses in-process imports
   (`from mlb.runners...`) baked into the image -> covered by step 1 alone.

## Notes
- CONTEXT.md updated: section 0 (contract-change flag + pillar symmetry) and the
  section 2 repo map.
- `mlb_core/` deliberately untouched (656 refs across 89 files stay valid).
- This unblocks the future `core/` rename and a shared-vs-MLB split of `mlb_core`
  as separate, lower-risk passes if desired.
