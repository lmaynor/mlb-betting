# Runbooks

_Last updated: 2026-06-24 (pillar restructure: mlb/ paths)_

Common manual actions and operational workflows for `lmaynor/mlb-betting`.
CONTEXT.md is the contract; this is the cookbook. If something here
contradicts CONTEXT.md, CONTEXT.md wins -- update this file.

---

## Table of contents

1. [Overview](#1-overview)
2. [Claude Code + Cloud Shell workflow](#2-claude-code--cloud-shell-workflow)
3. [Common manual actions (code fragments)](#3-common-manual-actions-code-fragments)
4. [Social media content pipeline](#4-social-media-content-pipeline)
5. [When to update this file](#5-when-to-update-this-file)
6. [Odds provider (ParlayAPI / SGO)](#6-odds-provider-parlayapi-primary--sgo-inning-fallback)

---

## 1. Overview

This document holds the operational fragments that used to live in `CONTEXT.md`:
the Claude-Code-to-Cloud-Shell deploy flow, the curl/gcloud cookbook, and the
social media pipeline (OG cards, tweet drafter, Discord cards). CONTEXT.md
references this file by name from §17 Pointers; keep them in sync.

---

## 2. Claude Code + Cloud Shell workflow

Claude Code runs on the **local Mac** at `/Users/lmaynor/mlb-betting`.
Deployment happens from **Cloud Shell** at `~/mlb-betting`.
These are two separate git clones. Changes written by Claude Code must reach
Cloud Shell before they can be deployed.

### Normal flow (Claude Code session -> deploy)

```
1. Claude Code edits files on Mac, commits locally
2. git push origin main   (from Mac -- works via macOS keychain)
3. Cloud Shell: git pull && ./deploy/deploy_service.sh
4. Cloud Shell: ./deploy/setup_betting_schedulers.sh
5. Cloud Shell: ./deploy/setup_model_jobs.sh
```

If `git push` fails from Mac (no credential helper):
```bash
# Generate a PAT at github.com -> Settings -> Developer settings -> Fine-grained tokens
# Scope: Contents read+write on lmaynor/mlb-betting
git remote set-url origin https://lmaynor:<PAT>@github.com/lmaynor/mlb-betting.git
git push origin main
```

### If you need to edit a file directly on Cloud Shell

Use `sed -i` for targeted replacements -- do NOT use nano/vim for multi-line
patches (copy-paste corruption). Always verify with `grep` after:

```bash
sed -i 's|OLD_STRING|NEW_STRING|' ~/mlb-betting/path/to/file.py
grep -n "NEW_STRING" ~/mlb-betting/path/to/file.py
```

Then commit from Cloud Shell:
```bash
cd ~/mlb-betting
git add path/to/file.py
git commit -m "fix: description"
./deploy/deploy_service.sh   # deploy stamps CONTEXT.md + pushes + builds + deploys
```

**Do not commit on both Mac and Cloud Shell without syncing first** -- diverged
branches require a merge or rebase before deploy will push cleanly.

### Dockerfile COPY rule -- pillar dirs are copied wholesale

Since the 2026-06-24 pillar restructure the Dockerfile copies whole packages
(`mlb_core/`, `nba/`, `mlb/`) rather than one COPY line per system dir. Anything
under `mlb/` (runners/, training/, systems/) is included automatically -- only a
brand-new top-level package would need a new COPY line.

When adding a new system, put its config dir under `mlb/systems/FOO_System/`
(with `__init__.py`) -- it is then included by the existing `COPY mlb/`, no
Dockerfile change required.

Current COPY lines in Dockerfile:
```
mlb_core/ nba/ mlb/ main.py tweet_drafter.py setup.py
```
(`mlb/` contains `runners/`, `training/`, and `systems/{HR_Pro, NRFI_Pro_System,
F5_Pro_System, K_Pro_System, OUTS_Pro_System, BATTER_HITS_System,
BATTER_TB_System, GAME_Pro_System}/`.)

`BATTER_TB` now has a dedicated `BATTER_TB_System/` package, feature table,
model artifact, and lambda calibrator. It no longer uses the HR proxy artifact.

### Proxy gotchas

- Always start proxy in the **foreground** (no `&`) in one tab, curl from a second tab
- **Stale proxy returns Google 404** on `/healthz` -- kill and restart after any deploy
- **Public-URL GET /healthz ALWAYS returns Google's "Error 404 (Not Found)!!1"** --
  verified 2026-07-06: the request never reaches Cloud Run (request logs empty;
  other GET routes like /robots.txt pass through fine). Google's edge intercepts
  this path on run.app URLs. NOT an outage signal -- health-check via the proxy
  only. Schedulers/POST routes/the site are unaffected.
- Port already in use: `pkill -f "run services proxy"` then restart on a new port
- `sleep N && curl` in the same command races with `&` proxy startup -- run separately

```bash
# Tab 1
pkill -f "run services proxy"
gcloud run services proxy mlb-betting --region=us-central1 --port=8081

# Tab 2
curl -s http://localhost:8081/healthz   # must return {"status":"ok"} before anything else
```

### git identity on Mac (one-time)

Claude Code commits require git identity. If `git commit` fails with
"Author identity unknown":
```bash
git config --global user.email "lmaynor@users.noreply.github.com"
git config --global user.name "lmaynor"
```

---

## 3. Common manual actions (code fragments)

### Deploy

```bash
cd ~/mlb-betting
./deploy/deploy_service.sh
```

### Start Cloud Run proxy for curl tests

```bash
# Foreground in Tab 1 -- do NOT use & here
gcloud run services proxy mlb-betting --region=us-central1 --port=8081
# Tab 2: verify before any curl
curl -s http://localhost:8081/healthz
```

### Trigger a scheduler job immediately

```bash
gcloud scheduler jobs run mlb-settle             --location=us-central1
gcloud scheduler jobs run mlb-betting-morning    --location=us-central1
gcloud scheduler jobs run mlb-betting-evening    --location=us-central1
gcloud scheduler jobs run mlb-build-all-features --location=us-central1
gcloud scheduler jobs run mlb-refresh-data       --location=us-central1
gcloud scheduler jobs run mlb-monitor            --location=us-central1
gcloud scheduler jobs run mlb-monitor-ops        --location=us-central1
```

### Manually trigger an endpoint

```bash
curl -s -X POST http://localhost:8081/settle | python3 -m json.tool
curl -s -X POST http://localhost:8081/monitor | python3 -m json.tool
curl -s -X POST http://localhost:8081/monitor-ops | python3 -m json.tool
curl -s -X POST http://localhost:8081/snapshot-odds | python3 -m json.tool

curl -s -X POST http://localhost:8081/run \
  -H "Content-Type: application/json" \
  -d '{"systems":["HR","1IOU","F5","K","BATTER_HITS","BATTER_TB","GAME","1I"],"run_type":"morning"}' | python3 -m json.tool

curl -s -X POST http://localhost:8081/build-all-features \
  -H "Content-Type: application/json" \
  -d '{"systems":["HR","NRFI","K","F5","BATTER_HITS","BATTER_TB","GAME"],"continue_on_error":true}' | python3 -m json.tool
```

`1I` does not need a separate feature build today; it uses NRFI half-inning
features. `BATTER_TB` has its own feature build and model artifact.

`BATTER_HITS` and `BATTER_TB` require confirmed MLB lineup rows for live
candidate generation. If lineups are not posted yet, they skip the unsafe
historical-team fallback and may return fewer or zero bets. This is intentional:
do not force a batter prop run by matching old feature rows to today's teams.

### Delete bets and re-run clean

```bash
# Delete all bets for a date
curl -s -X POST http://localhost:8081/reset-bets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)" \
  -d '{"date": "2026-05-20"}' | python3 -m json.tool

# Delete one system only
curl -s -X POST http://localhost:8081/reset-bets \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)" \
  -d '{"date": "2026-05-20", "system": "NRFI"}' | python3 -m json.tool

# Re-run after reset
gcloud scheduler jobs run mlb-snapshot-evening --location=us-central1
sleep 10
gcloud scheduler jobs run mlb-betting-evening --location=us-central1
```

### Run all scheduled jobs manually in order (full daily cycle)

```bash
gcloud scheduler jobs run mlb-refresh-data --location=us-central1
sleep 30
gcloud scheduler jobs run mlb-build-all-features --location=us-central1
sleep 60
gcloud scheduler jobs run mlb-monitor-ops --location=us-central1
gcloud scheduler jobs run mlb-settle --location=us-central1
gcloud scheduler jobs run mlb-monitor --location=us-central1
```

### Secrets -- read, create, update

```bash
# Read a secret (cat -A shows invisible newlines)
gcloud secrets versions access latest --secret=discord-bot-token \
  --project=concrete-crow-445205-m4 | cat -A

# Create a new secret
echo -n "VALUE" | gcloud secrets create SECRET_NAME \
  --data-file=- --project=concrete-crow-445205-m4

# Update an existing secret
echo -n "VALUE" | gcloud secrets versions add SECRET_NAME \
  --data-file=- --project=concrete-crow-445205-m4
```

### Wire a secret to Cloud Run

```bash
gcloud run services update mlb-betting \
  --region=us-central1 \
  --project=concrete-crow-445205-m4 \
  --update-secrets=ENV_VAR_NAME=secret-name:latest
```

### Add a Cloud Scheduler job

```bash
gcloud scheduler jobs create http JOB_NAME \
  --location=us-central1 \
  --schedule="CRON" \
  --uri="https://mlb-betting-628109313129.us-central1.run.app/ENDPOINT" \
  --message-body='{}' \
  --headers="Content-Type=application/json" \
  --oidc-service-account-email="scheduler-invoker@concrete-crow-445205-m4.iam.gserviceaccount.com" \
  --oidc-token-audience="https://mlb-betting-628109313129.us-central1.run.app" \
  --attempt-deadline=300s
```

Max `attempt-deadline`: 1800s.

### Update active market schedulers

```bash
cd ~/mlb-betting
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_betting_schedulers.sh
```

This updates/creates the four `/run` jobs (morning/afternoon/evening/pregame,
paired ~5 min after each odds snapshot) with the authoritative system list:
`HR, 1IOU, F5, K, BATTER_HITS, BATTER_TB, GAME, 1I`. `setup_betting_schedulers.sh`
is the single source of truth for these jobs -- do not use any other script
to touch `mlb-betting-morning`/`-afternoon`/`-evening`/`-pregame`
(`setup_active_market_schedulers.sh`, which duplicated this with different,
stale cron times, was deleted 2026-08-17 -- see
docs/audits/2026-08-16_cloud_efficiency_and_profitability_review.md finding A7).

### Create a new Cloud Run Job

```bash
gcloud run jobs create JOB_NAME \
  --image gcr.io/concrete-crow-445205-m4/mlb-betting:latest \
  --region us-central1 \
  --command python3 \
  --args="-m" --args="training.MODULE_NAME" \
  --memory 4Gi --cpu 2 \
  --set-secrets MLB_DB_URL=mlb-db-url:latest,DISCORD_WEBHOOK_URL=discord-webhook-url:latest \
  --set-env-vars MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data,GCP_PROJECT=concrete-crow-445205-m4 \
  --set-cloudsql-instances concrete-crow-445205-m4:us-central1:mlb-betting-db \
  --service-account mlb-betting-sa@concrete-crow-445205-m4.iam.gserviceaccount.com \
  --task-timeout 7200 --max-retries 1
```

Task timeouts by job category: retrain 7200s, calibrate 1800s, build 3600s, tweet 300s.
If job already exists, use `gcloud run jobs update JOB_NAME` with the same flags.

### Create/update all model jobs

```bash
cd ~/mlb-betting
PROJECT_ID=concrete-crow-445205-m4 ./deploy/setup_model_jobs.sh
```

This configures retrain/calibrate jobs for NRFI, HR, F5, K, OUTS, GAME,
BATTER_HITS, and BATTER_TB.

### Trigger a Cloud Run Job (retrain/calibrate)

```bash
# Always run calibrate immediately after retrain

# NRFI: runner uses v18 models (xgb_pitcher_v18.json etc.) -- retrain via mlb-retrain-nrfi-v18.
# If the job doesn't exist yet, create it once (copy image from v17 job):
#   IMAGE=$(gcloud run jobs describe mlb-retrain-nrfi-v17 --region=us-central1 --format='value(spec.template.spec.containers[0].image)')
#   gcloud run jobs create mlb-retrain-nrfi-v18 --image $IMAGE \
#     --args="-m,mlb.training.retrain_nrfi_v18" --region=us-central1 --task-timeout=7200s \
#     --set-env-vars="$(gcloud run jobs describe mlb-retrain-nrfi-v17 --region=us-central1 --format='value(spec.template.spec.containers[0].env)')"
gcloud run jobs execute mlb-retrain-nrfi-v18  --region=us-central1
gcloud run jobs execute mlb-calibrate-nrfi    --region=us-central1

gcloud run jobs execute mlb-retrain-hr-v6     --region=us-central1
gcloud run jobs execute mlb-calibrate-hr      --region=us-central1

gcloud run jobs execute mlb-retrain-f5-v5     --region=us-central1
gcloud run jobs execute mlb-calibrate-f5      --region=us-central1

# K and OUTS share model_features.csv -- rebuild K features first, then retrain both
gcloud run jobs execute mlb-retrain-k-v1      --region=us-central1
gcloud run jobs execute mlb-calibrate-k       --region=us-central1
gcloud run jobs execute mlb-retrain-outs-v1   --region=us-central1
# (no calibrate job for OUTS -- calibrator is fit inside retrain_outs_v1.py)

gcloud run jobs execute mlb-retrain-game-v1   --region=us-central1
gcloud run jobs execute mlb-calibrate-game    --region=us-central1

gcloud run jobs execute mlb-retrain-batter-hits   --region=us-central1
gcloud run jobs execute mlb-calibrate-batter-hits --region=us-central1

gcloud run jobs execute mlb-retrain-batter-tb     --region=us-central1
gcloud run jobs execute mlb-calibrate-batter-tb   --region=us-central1
```

After `BATTER_TB` retrain/calibrate, run a TB-only smoke test after lineups are
posted:

```bash
curl -s -X POST https://mlb-betting-628109313129.us-central1.run.app/run \
  -H "Content-Type: application/json" \
  -d '{"systems":["BATTER_TB"],"run_type":"morning"}' | python3 -m json.tool
```

Sanity check that every returned player belongs to the listed game. The runner
also enforces `SGO event_id == feature game_pk` before logging a bet.

### Discord bot scripts

```bash
TOKEN=$(gcloud secrets versions access latest \
  --secret=discord-bot-token --project=concrete-crow-445205-m4)

# One-time server setup
python3 ~/mlb-betting/setup_discord.py --token "$TOKEN"

# Clean up junk roles
python3 ~/mlb-betting/cleanup_discord.py --token "$TOKEN"
```

### Run model health check

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)
curl -s "https://mlb-betting-628109313129.us-central1.run.app/model-health" \
  -H "X-API-Key: $KEY" | python3 -m json.tool
```

### Backfill Statcast pitch data

```bash
# Takes a dates list -- NOT start_date/end_date
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-statcast" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"dates":["2026-05-19","2026-05-20"]}' \
  | python3 -m json.tool
```

### Backfill Savant leaderboards

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)

# Backfill all 6 datasets for a year range (slow -- 15-25 min per year)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-savant" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_year":2024,"end_year":2026,"force":false}' \
  | python3 -m json.tool

# Backfill a single dataset
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-savant" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"dataset":"exit_velocity_barrels","start_year":2026,"end_year":2026,"force":true}' \
  | python3 -m json.tool

# Rebuild a corrupted master WITHOUT re-fetching Savant (instant, year files must be intact):
# All years return -1 (skipped/cached) and master is rebuilt from them. total_rows=0 is correct.
curl -s -X POST http://localhost:8081/backfill-savant \
  -H "Content-Type: application/json" \
  -d '{"dataset":"pitch_arsenals"}' \
  | python3 -m json.tool
# Verify: gsutil cat gs://BUCKET/Statcast/savant_pitch_arsenals_master.csv | wc -l  (expect ~8600+)
```

Note: there is **no Cloud Run Job** named `savant-backfill`. Use the `/backfill-savant` endpoint
only (direct URL with API key, or via proxy). The proxy times out after ~60s -- use the direct
URL with `X-API-Key` for full multi-dataset backfills that take >60s.

### Backfill weather/scoring/umpires

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)

# Weather only (most common gap)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-data" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_date": "2026-04-01", "end_date": "2026-05-19", "systems": ["weather"]}' \
  | python3 -m json.tool

# All three
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/backfill-data" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $KEY" \
  -d '{"start_date": "2026-04-01", "systems": ["weather", "scoring", "umpires"]}' \
  | python3 -m json.tool
```

### Retrain OUTS model

```bash
KEY=$(gcloud secrets versions access latest --secret=site-api-key --project=concrete-crow-445205-m4)

# Must rebuild K features first (adds starter_outs column)
curl -s -X POST "https://mlb-betting-628109313129.us-central1.run.app/build-features" \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" \
  -d '{"system":"K"}' | python3 -m json.tool

# Run retrain
gcloud run jobs execute mlb-retrain-outs-v1 --region=us-central1 --wait
```

### Run Optuna hyperparameter tuning

```bash
pip install optuna --break-system-packages
cd ~/mlb-betting
# Run after E02-E07 complete and models are clean
python -m mlb.training.tune_hyperparams --system NRFI --n-trials 50
python -m mlb.training.tune_hyperparams --system K    --n-trials 50
python -m mlb.training.tune_hyperparams --system F5   --n-trials 50
python -m mlb.training.tune_hyperparams --system HR   --n-trials 50
```

### Refresh player headshots (full reconciliation)

```bash
cd ~/mlb-betting && git pull
pip install "rembg[cpu]" -q
python3 scripts/process_headshots.py --refresh-map --all
git add beezy-vip/public/headshots/*.png beezy-vip/public/headshots/player_map.json
git commit -m "headshots: refresh player map and process missing"
git push
```

### Commit CONTEXT.md / RUNBOOKS.md

```bash
cd ~/mlb-betting
git add CONTEXT.md RUNBOOKS.md
git commit -m "docs: update CONTEXT.md and RUNBOOKS.md"
git push
```

---

## 4. Social media content pipeline

_Last updated: 2026-05-29_

### Overview

Automated daily content pipeline that builds Twitter/X following and converts
followers to beezy.fyi members and Discord joiners. Three OG image cards
served from Vercel edge runtime + two Cloud Run jobs that generate tweet
drafts via Gemini and post picks cards to Discord.

Twitter handle: @beezy_fyi
Discord invite: discord.gg/HfMYCmbmE
Site: https://beezy.fyi
Public API: https://api.beezy.fyi

### OG image cards (Vercel edge, @vercel/og)

Three card routes, all at `beezy-vip/app/api/og/`:

| Route | File | Purpose | Dimensions |
|---|---|---|---|
| /api/og/picks-card | picks-card/route.tsx | All systems top-5 by edge | 1200x675 |
| /api/og/games-card | games-card/route.tsx | F5/NRFI game picks with team gradients | 900px dynamic height |
| /api/og/props-card | props-card/route.tsx | HR/K/OUTS player props with headshots | 900px dynamic height |

All three:
- Pull live data from Cloud Run public API at render time (cache: no-store)
- Use BETTING_API_URL + BETTING_API_KEY env vars (server-only, already in Vercel)
- Use NEXT_PUBLIC_BASE_URL for self-referencing assets (logos, headshots)
- Every div with multiple children must have explicit display:flex -- @vercel/og hard requirement
- No conditional null returns inside JSX -- use empty string checks instead
- Card title text: BEEZY.FYI (all caps, matches site header)

Team color gradients: All 30 MLB teams hardcoded as `{ p: "R,G,B", s: "R,G,B", slug: "xxx" }`
in each card route. Primary color fades to secondary to `#0e0e11`.
Source: official MLB RGB values documented in session 2026-05-23.

Games card: Shows away/home team logos side by side. Gradient uses featured
team colors (home for HOME bet, away for AWAY bet).

Props card: Shows team logo + circular player headshot. Headshot lookup:
1. player field from API (e.g. "Gerrit Cole")
2. Convert to key: `.toLowerCase().replace(/ /g, "_")` -> "gerrit_cole"
3. Look up MLBAM ID in player_map.json
4. Render `img src="/headshots/{id}.jpg"`

Pick label convention (must match `lib/tokens.ts` at all times):
- NRFI -> "No Run 1st Inning"
- YRFI -> "Run in 1st Inning"
- HOME (F5) -> "F5 Home ML"
- AWAY (F5) -> "F5 Away ML"
- K_OVER_7.5 -> "Over 7.5 Ks"
- OUTS_UNDER_14.5 -> "Under 14.5 Outs"
- HR -> "HR Yes"

### Static assets

Team logos: `beezy-vip/public/logos/{abbrev}.png` (30 files).
Downloaded from cdn.ssref.net. Abbrevs: ari, atl, bal, bos, chc, cws, cin,
cle, col, det, hou, kc, laa, lad, mia, mil, min, nym, nyy, oak, phi, pit,
sd, sf, sea, stl, tb, tex, tor, wsh.

Player headshots: `beezy-vip/public/headshots/{slug}.png` (1240+ files, bg-removed).
Keys are normalized slugs (see CONTEXT.md §13 Headshot system).
`player_map.json` maps slug -> MLBAM integer ID (1272 entries, full 2026 roster).
To refresh: see "Refresh player headshots" in §3 above.

### Cloud Run jobs

Provisioning (both jobs + both schedules, idempotent):
`PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_tweet_jobs.sh`
(added 2026-09-02 -- previously these were hand-created with no checked-in
script at all, see docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md).

| Job | Schedule | TWEET_MODE | What it does |
|---|---|---|---|
| `mlb-tweet-picks` | 17:00 UTC (noon ET) | picks | Games card to Discord + tweet draft to Typefully |
| `mlb-tweet-recap` | 10:00 UTC (5am ET) | recap | Recap tweet draft to Typefully |

Script: `tweet_drafter.py` in repo root. Included in Docker image via
`COPY tweet_drafter.py .` in Dockerfile.

Gemini: `gemini-2.0-flash` free tier (1500 req/day).
Key in Secret Manager as `gemini-api-key`.
Free tier requires key from a project with NO billing account linked.
Get from aistudio.google.com -- create fresh project, no billing.

Typefully: Free tier = 15 scheduled tweets/month (~3-4/week).
Key in Secret Manager as `typefully-api-key`. Typefully disabled v1
API-key access entirely (confirmed live 2026-09-02, every push 403'd with
"API v1 access via API keys is disabled"); migrated to v2 the same day and
confirmed working end to end with the SAME key (no rotation needed,
despite Typefully's own docs saying v1 keys can't be used with v2) -- see
docs/solutions/integration-issues/typefully-api-v1-sunset.md.
Drafts endpoint: POST `https://api.typefully.com/v2/social-sets/{social_set_id}/drafts`
(`social_set_id` resolved at runtime via `GET /v2/social-sets`). Payload
needs `platforms.x.enabled: true` -- Typefully's own migration-guide
example omits it, but the real API 422s without it.
Delete unused variants after choosing -- all 3 count against the 15/month limit.

Secrets on both jobs:
- `SITE_API_KEY = site-api-key:latest`
- `GEMINI_API_KEY = gemini-api-key:latest`
- `TYPEFULLY_API_KEY = typefully-api-key:latest`
- `DISCORD_WEBHOOK_URL = discord-webhook-url:latest`

Env vars on both jobs:
- `BEEZY_API_URL=https://api.beezy.fyi`
- `BEEZY_SITE_URL=https://beezy.fyi`

**`TWEET_MODE` is a real per-job env var baked into each Job resource --
it is NOT passed by the scheduler.** Both `mlb-tweet-picks-schedule` and
`mlb-tweet-recap-schedule` call `.../jobs/{name}:run` with no HTTP body at
all (verified via `gcloud scheduler jobs describe ... --format="yaml(httpTarget)"`,
2026-09-02) -- the "TWEET_MODE" column in the table above documents INTENT,
not a mechanism. Set it explicitly on each job:
`gcloud run jobs update mlb-tweet-recap --region=us-central1 --update-env-vars=TWEET_MODE=recap`
(picks doesn't strictly need it set since `tweet_drafter.py` defaults to
`"picks"`, but relying on the default silently is exactly how this job broke
-- see docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md).

Scheduler jobs (already created):
- `mlb-tweet-picks-schedule` -- `0 17 * * *`
- `mlb-tweet-recap-schedule` -- `0 10 * * *`

### Rationale / notes wiring

`mlb_core/rationale.py` has rules for all 5 core systems (HR, NRFI, K, OUTS, F5).
`build_rationale(row_dict, system)` returns up to 3 phrases joined by `" . "`.

Wiring status as of 2026-05-23:
- HR: wired (lazy import inside scoring function in run_hr.py)
- NRFI: wired
- F5: wired 2026-05-23 -- replaced JSON debug dict with build_rationale output
- K: wired 2026-05-23 -- added notes= kwarg to log_bet call
- OUTS: wired via K runner (market="OUTS" passed to build_rationale)

Notes field shows as italic subtext in `picks-table.tsx` and all three OG cards.

Gotcha: F5 previously stored a JSON dict in notes (internal debug data).
Bets before 2026-05-23 have JSON strings not rationale phrases in notes.
Frontend null-guards this -- old JSON strings show as-is. Not worth backfilling.

### Brand voice (Twitter)

- Confident but not loud. Data-first. Let numbers talk.
- Transparency is the brand -- post highest-edge pick win or lose
- Never cherry-pick winners after the fact
- Occasionally explain WHY the edge exists (1 sentence max)
- No hype, no LOCK, no CASH IT
- Think Bloomberg terminal meets someone who actually knows what they are doing
- Always include beezy.fyi in at least one variant
- Always include discord.gg/HfMYCmbmE in at least one variant

### Website domain wiring

The public site is `https://beezy.fyi`; `https://www.beezy.fyi` redirects to
the apex. Vercel should keep:
- `NEXT_PUBLIC_BASE_URL=https://beezy.fyi`
- `BETTING_API_URL=https://api.beezy.fyi`

The Cloud Run public API should keep Secret Manager `site-origin` set to:
`https://beezy.fyi,https://www.beezy.fyi`

The social Cloud Run jobs should keep:
- `BEEZY_API_URL=https://api.beezy.fyi`
- `BEEZY_SITE_URL=https://beezy.fyi`

One-shot Cloud Shell update after domain or tweet job code changes:

```bash
PROJECT_ID="concrete-crow-445205-m4"
REGION="us-central1"
IMAGE="gcr.io/$PROJECT_ID/mlb-betting:latest"

gcloud config set project "$PROJECT_ID"

echo -n "https://beezy.fyi,https://www.beezy.fyi" | \
  gcloud secrets versions add site-origin --data-file=-

gcloud run services update mlb-betting \
  --region "$REGION" \
  --update-secrets SITE_ORIGIN=site-origin:latest

gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID"

# NOTE: --update-env-vars merges; --set-env-vars REPLACES the entire env
# list and will silently wipe TWEET_MODE (see
# docs/solutions/runtime-errors/cloud-run-job-set-env-vars-wipes-existing.md).
# Prefer deploy/setup_tweet_jobs.sh over this snippet where possible -- it's
# the source-controlled, idempotent version of the same provisioning.
for JOB in mlb-tweet-picks mlb-tweet-recap; do
  gcloud run jobs update "$JOB" \
    --region "$REGION" \
    --image "$IMAGE" \
    --update-env-vars BEEZY_API_URL=https://api.beezy.fyi,BEEZY_SITE_URL=https://beezy.fyi
done

curl -i https://api.beezy.fyi/healthz
curl -i https://beezy.fyi/api/stats/summary
```

---

## 5. When to update this file

- New manual command worth saving -> §3
- Deploy workflow change (Mac vs Cloud Shell, Dockerfile, PAT, proxy) -> §2
- New card route added or redesigned -> §4 OG image cards
- Headshots refreshed (update date in §4) -> §3 + §4
- Typefully replaced with another tool -> §4 Cloud Run jobs
- Gemini API key rotated or provider changed -> §4 Cloud Run jobs
- Domain/API wiring changes -> §4 Website domain wiring
- Rationale wiring status changes -> §4 Rationale / notes wiring

**Don't put architecture or contracts here.** That belongs in CONTEXT.md.
**Don't put point-in-time state here.** That belongs in `handoffs/`.

---

## 6. Odds provider (ParlayAPI primary / SGO inning fallback)

Live odds come from ParlayAPI (covered markets) merged with SGO (inning markets),
written SGO-shaped to `Odds/sgo/latest.json`. Controlled by env `ODDS_PRIMARY`
on the `mlb-betting` service. Architecture/contracts: CONTEXT.md s8.

```bash
PROJECT_ID=concrete-crow-445205-m4; REGION=us-central1

# Check which provider is live
gcloud run services describe mlb-betting --region=$REGION \
  --format="yaml(spec.template.spec.containers[0].env)" | grep -A1 ODDS_PRIMARY

# Flip to ParlayAPI (cutover) / back to SGO (rollback) -- no redeploy
gcloud run services update mlb-betting --region=$REGION --update-env-vars ODDS_PRIMARY=parlay
gcloud run services update mlb-betting --region=$REGION --update-env-vars ODDS_PRIMARY=sgo

# Schedulers: 8-job ParlayAPI cadence (post-cutover) vs 2 legacy SGO jobs (pre-cutover)
PROJECT_ID=$PROJECT_ID ./deploy/add_snapshot_schedulers.sh             # 8 jobs
LEGACY=1 PROJECT_ID=$PROJECT_ID ./deploy/add_snapshot_schedulers.sh    # 2 SGO jobs
gcloud scheduler jobs list --location=$REGION | grep snapshot          # expect exactly 8 post-cutover

# Shadow test (never touches live latest.json)
SERVICE_URL=$(gcloud run services describe mlb-betting --region=$REGION --format='value(status.url)')
TOKEN=$(gcloud auth print-identity-token)
curl -s -X POST "$SERVICE_URL/snapshot-odds" -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"parlay","out_prefix":"Odds/sgo/_shadow"}' | python3 -m json.tool

# Credit tally (implicit guard; x-requests-remaining header is blind)
BUCKET=$(gcloud secrets versions access latest --secret=mlb-gcs-bucket)
gsutil cat "gs://$BUCKET/OddsAccum/baseball_mlb/_credits/$(date -u +%Y-%m).json"
```

Gotchas:
- Pre-cutover (`ODDS_PRIMARY=sgo`), EVERY snapshot does a full SGO fetch -- do NOT
  register the 8-job cadence while on sgo or you blow the SGO free tier. Use LEGACY=1.
- Secret `parlay-api-key` must be bound (deploy_service.sh does this). ParlayAPI is
  reachable from Cloud Run only (office LAN blocks gambling sites).
- To exclude a book from best-line: add it to `OFFSHORE_BOOKS` in `mlb_core/odds/sgo.py`.
