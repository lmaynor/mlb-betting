# Handoff -- 2026-08-17 -- 2026-08-16 audit remediation: 50/51 done, merged, pushed

The full `docs/audits/2026-08-16_fix_checklist.md` remediation campaign (113 findings,
4 tiers P0-P3) is **done**. All four tiers are merged to `main` and pushed to origin.
Service has been redeployed (user-confirmed). What's **not** confirmed is whether the
retrain/recalibrate half of the post-deploy sequence has actually been run yet -- see
"What 'deployed' does and doesn't cover" below, that's the main thing to check first.

## TL;DR

- **50 of 51 checklist items closed.** `main` is at `78a422a`, clean, in sync with
  `origin/main` (0 ahead/behind), **530 tests passing**. Only one item is still open:
  **E9/B2.4**, and it can't be closed from this sandbox -- see "The one open item" below.
- **Four tier-merge commits**, all `--no-ff` to `main`, in order:
  `24f0c3f` (P0, findings A1-A11) -> `b611089` (P1, Tasks #15-18) -> `c96adba` (P2,
  Tasks #19-20) -> `ecccc7a` (P3, Tasks #21-22). P0 predates this session; P1-P3 (and
  everything below) is this session's work.
- **One more fix landed after P3**, standalone on `main` (not its own tier branch,
  since all tiers were already merged): `b38cb1d`, a real pandas-3.x forward-compat
  bug in `build_game_features.py`, found from the user's own Cloud Shell test run.
  Full story below -- **the home/away logic itself was verified correct**, this was a
  separate, narrower bug.
- **The user said "deployed."** That confirms `deploy/deploy_service.sh` ran (new
  Cloud Run revision live). It does **not** by itself confirm feature rebuilds, retrains,
  recalibration, or `mlb-fit-calibrators` ran -- those are steps 3-6 of the sequence
  below and need their own check.

## What "deployed" does and doesn't cover

I handed the user a 6-step script this session (`reprocess_check.sh`, reproduced in
full below since it only ever lived in a scratchpad, not the repo) covering:

1. `git pull origin main`
2. `./deploy/deploy_service.sh` (rebuild + redeploy the Flask service)
3. Rebuild features for HR, K, GAME, BATTER_HITS, BATTER_TB (the 5 systems whose
   builders/feature lists this session actually touched)
4. Retrain 6 systems (HR, K, OUTS, GAME, BATTER_HITS, BATTER_TB)
5. Recalibrate 5 of those 6 (OUTS self-calibrates inline, no separate job)
6. Refit the prediction-calibrator layer (`mlb-fit-calibrators`, fits on realized bet
   outcomes -- distinct from the isotonic training-time calibrators in step 5, must
   run after ANY retrain)

"Deployed" most naturally reads as step 2 only. **Before doing anything else, check
which steps actually ran:**

```bash
export PROJECT_ID=concrete-crow-445205-m4 REGION=us-central1
# Did the retrain jobs fire, and when?
for job in mlb-retrain-hr-v6 mlb-retrain-k-v1 mlb-retrain-outs-v1 mlb-retrain-game-v1 \
           mlb-retrain-batter-hits mlb-retrain-batter-tb; do
  echo "=== $job ==="
  gcloud run jobs executions list --job="$job" --region="$REGION" --project="$PROJECT_ID" \
    --limit=1 --format="table(name,status,startTime)"
done
# Did mlb-fit-calibrators run after that?
gcloud run jobs executions list --job=mlb-fit-calibrators --region="$REGION" \
  --project="$PROJECT_ID" --limit=1 --format="table(name,status,startTime)"
```

If the retrain executions are older than today, or missing, run the rest of the
sequence. **Full script** (originally verified with `bash -n` + `shellcheck`, one real
bug found and fixed -- an apostrophe inside a `${VAR:?message}` expansion breaks bash's
parser even inside double quotes -- already fixed below):

```bash
set -euo pipefail

PROJECT_ID="concrete-crow-445205-m4"
REGION="us-central1"
SERVICE_URL="https://mlb-betting-628109313129.us-central1.run.app"
SITE_API_KEY="$(gcloud secrets versions access latest --secret="site-api-key" --project="$PROJECT_ID")"
REPO_DIR="${REPO_DIR:-$HOME/mlb-betting}"

cd "$REPO_DIR"
git checkout main
git pull origin main

./deploy/deploy_service.sh
echo "Waiting 60s for the new revision to finish rolling out..."
sleep 60

for sys in HR K GAME BATTER_HITS BATTER_TB; do
  echo "=== build-features: $sys ==="
  curl -sf --max-time 1800 -X POST "$SERVICE_URL/build-features" \
    -H "X-API-Key: $SITE_API_KEY" -H "Content-Type: application/json" \
    -d "{\"system\": \"$sys\"}" | tee "/tmp/build_${sys}.json"
  echo
done

RETRAIN_JOBS=(mlb-retrain-hr-v6 mlb-retrain-k-v1 mlb-retrain-outs-v1 mlb-retrain-game-v1 \
              mlb-retrain-batter-hits mlb-retrain-batter-tb)
for job in "${RETRAIN_JOBS[@]}"; do
  echo "=== retrain: $job ==="
  gcloud run jobs execute "$job" --project="$PROJECT_ID" --region="$REGION" --wait
done

CALIBRATE_JOBS=(mlb-calibrate-hr mlb-calibrate-k mlb-calibrate-game \
                mlb-calibrate-batter-hits mlb-calibrate-batter-tb)
for job in "${CALIBRATE_JOBS[@]}"; do
  echo "=== calibrate: $job ==="
  gcloud run jobs execute "$job" --project="$PROJECT_ID" --region="$REGION" --wait
done

gcloud run jobs execute mlb-fit-calibrators --project="$PROJECT_ID" --region="$REGION" --wait

echo "Done. Spot-check via: curl -H \"X-API-Key: \$SITE_API_KEY\" \"$SERVICE_URL/dashboard\""
```

Save it to a file and run with `bash script.sh` -- **don't paste multi-line scripts
directly into an interactive Cloud Shell.** The user hit this exactly: pasting
`set -euo pipefail` line-by-line makes `set -e` govern the whole login session, so any
later nonzero-exit command (even a harmless one) kills the shell (`[exited]`) instead of
just the script. Save-to-file-then-run avoids it.

Job-name source of truth if any of the above ever needs re-deriving: `main.py`'s
`/retrain-weekly` route (`RETRAIN_JOBS`/`CALIBRATE_JOBS` literals, ~line 1637), not
`docs/solutions/conventions/retrain-calibrate-sequence.md` (that doc was itself corrected
this session to match `main.py` -- see commit `6b4ffda`).

## The pandas-3.x fix -- what happened and how it was verified

The user reported a real Cloud Shell test failure and, critically, connected it
themselves to a past home/away-attribution bug fix (finding A4): *"check make sure this
is accurate, we cannot afford a flip flop of home/away."* That combination -- a live
failure plus a correctness fear about money-moving logic -- got the highest scrutiny of
anything this session.

**Two separate questions, kept separate on purpose:**

1. **Is the home/away mapping itself correct?** Yes, verified empirically, not just by
   re-reading code. Built a standalone script calling the real, unmodified
   `_identify_starters`/`build_starter_features`/`build_bullpen_features` against two
   synthetic games with fully reversed home/away roles, anchored on Jose Ramirez
   (MLBAM id `608070`, Cleveland his entire career -- already a verified fixture in this
   repo's own `test_id_resolver.py`/`test_parlayapi_to_history.py`). Ran it both before
   and after the fix below, on both pandas 2.3.3 and 3.0.5. All passed, both directions,
   every time. The mapping was never wrong.
2. **Why did the tests fail, then?** A genuine, separate pandas 2.x->3.x breaking
   change. `DataFrame.groupby(col, group_keys=False).apply(callback)` -- pandas 2.x
   passes the grouping column into `callback` (a FutureWarning as far back as 2.2.3,
   the exact version pinned in `requirements.txt`); pandas 3.x drops it. Three sites in
   `build_game_features.py` (`build_starter_features`, `build_bullpen_features`,
   `build_team_offense_features`) returned the callback's result and then, a few lines
   later, did `.drop_duplicates(subset=[..., grouping_col])` -- pandas 3.x raises
   `KeyError(Index([grouping_col]))` there. Reproduced for real in a throwaway
   `pandas==3.0.5` venv (not just theorized) before touching any code.

**Fix**: rewrote all three sites from the whole-frame `.apply()` pattern to per-metric
`.groupby(col)[metric].transform(lambda s: s.shift(1).rolling(window, min_periods=1).mean())`.
`transform()` never had this ambiguity on any pandas version -- it always preserves
every original column by construction. Safe specifically because each site's frame was
already sorted by `[group_col, game_date]` immediately beforehand (verified by reading
the code, not assumed), so the old per-group re-sort inside each callback was a no-op.

**Verification, in order**: (a) reproduced the reported `KeyError` for real pre-fix,
(b) re-ran the Jose Ramirez dual-direction script post-fix on pandas 3.0.5 -- identical
results to pre-fix, (c) added `test_team_offense_features_credits_the_correct_batting_team`
to `tests/test_build_game_features.py` (the one of the three functions with no prior
test at all -- also anchored on Ramirez), (d) full suite green on both pandas versions,
(e) swept `build_nrfi_features.py`/`build_k_features.py` for the same `.apply()` pattern
and confirmed by inspection they don't have the downstream `drop_duplicates` that makes
it dangerous. Commit `b38cb1d`, on `main`.

The throwaway pandas-3.0.5 venv (`/tmp/venv_pandas3`) used to reproduce this was deleted
after use -- if this needs re-reproducing, recreate it fresh (`pip install pandas==3.0.5
scikit-learn scipy xgboost` in a new venv).

## The one open item: E9 / B2.4

```
docs/audits/2026-08-16_fix_checklist.md:71
- [ ] E9 / B2.4  mlb-build-batter-hits-features / mlb-build-game-features sizing named
  in CONTEXT.md s7 but no script provisions either standalone
```

The finding's own remediation is "confirm in GCP directly" -- this sandbox has no
`gcloud`/GCP access, so it was never actionable here, not left unfinished by oversight.
Needs a human (or a Claude session with real `gcloud` access) to run:

```bash
gcloud run jobs describe mlb-build-batter-hits-features --region=us-central1 \
  --project=concrete-crow-445205-m4 2>&1 | head -5
gcloud run jobs describe mlb-build-game-features --region=us-central1 \
  --project=concrete-crow-445205-m4 2>&1 | head -5
```

If either exists: check its sizing against `deploy/setup_model_jobs.sh`'s current
values and reconcile. If neither exists: `CONTEXT.md` section 7's inventory is stale --
prune the two entries (the real build path for both systems is the in-process
`/build-features` Flask route, confirmed by reading `main.py` directly, not a
standalone Cloud Run Job).

## Loose threads (not part of the checklist, don't lose track of these)

- **Retrain/recalibrate confirmation** (see above) -- the single most likely thing to
  be half-done right now. Check execution timestamps before assuming either way.
- **E9/B2.4** above -- needs GCP console/`gcloud` access this environment doesn't have.
- This session's fixes touched training data/feature-list de-duplication for K, GAME,
  OUTS, and BATTER_HITS (imports from each system's `config_*.py` instead of a
  separately-maintained literal that had drifted for GAME and K specifically -- GAME's
  calibrate script was missing 10 features, K's was missing 4). **Retraining picks up
  the corrected feature list; skipping the retrain step leaves the currently-deployed
  model artifacts trained on whatever list was baked in when they were last trained**,
  which for GAME and K was the drifted (wrong) list. This is the concrete reason the
  retrain step above isn't optional cleanup -- it's the actual fix taking effect.
- `deploy/deploy.sh` is now a hard-`exit 1` stub pointing at `deploy/deploy_service.sh`
  as current -- if anything anywhere still references the old script name, it'll fail
  loudly rather than silently deploying stale config. Worth a `grep -rn deploy.sh`
  outside this repo (CI, docs, muscle memory) if deploys start looking odd.
- 4 stale one-off setup scripts were deleted this session (`setup_retrain_job.sh`,
  `setup_retrain_hr_meta.sh`, `setup_retrain_nrfi_v17.sh`, `setup_retrain_k_v1.sh`) --
  confirmed superseded by `main.py`'s real job list before deletion, but flagging in
  case any external doc/bookmark still points at one.
- Two Kalshi scheduler jobs and the `ODDS_PRIMARY` flip mentioned in
  [[project_ops_incident_2026-08-10]] are unrelated to this campaign and already
  resolved -- not reopened by anything here.

## Where the checklist itself stands

`docs/audits/2026-08-16_fix_checklist.md` -- 50/51 checked. Don't re-run the audit from
scratch on pickup; the one line above is genuinely the only gap.
