# MLB Betting Systems

Five paper-mode MLB betting systems running daily on GCP. Models score today's slate twice daily, post qualifying bets to Discord, and settle automatically each morning.

| System | Target | Market |
|---|---|---|
| NRFI Pro v17 | P(no run in inning 1) | DK NRFI/YRFI O/U + 1st inning 3-way ML |
| HR Pro v6 | P(batter hits HR) | DK HR yes/no props |
| F5 Pro v5 | P(home wins first 5 innings) | DK F5 moneyline |
| K Pro v1 | E[pitcher strikeouts] | DK strikeout O/U |
| OUTS | E[pitcher outs recorded] | DK pitcher outs O/U |

All systems are paper-mode only until each clears a 200-settled-bet gate.

## Structure

```
mlb_core/         Shared package: storage, odds utils, data pulls, bet tracker, risk
runners/          Daily jobs: feature builds, scoring runs, settlement, monitoring
deploy/           Deploy scripts and runbooks
training/         Model retrain pipeline
tests/            pytest suite
HR_Pro/           HR model config and data
NRFI_Pro_System/  NRFI model config and data
F5_Pro_System/    F5 model config and data
K_Pro_System/     K/OUTS model config and data
```

## Setup

```bash
pip install -e .
pytest tests/
```

## Deploy

```bash
./deploy/deploy_service.sh
```

Builds the Docker image, deploys to Cloud Run, and runs a smoke test. Always use this script — it preserves the Cloud SQL binding and auto-stamps `CONTEXT.md`.

## Data

GCS bucket: `concrete-crow-445205-m4-mlb-data`. Local fallback via `MLB_BASE_DATA` env var. Data is not tracked in Git.

## Docs

- `CONTEXT.md` — architecture, contracts, conventions (source of truth)
- `SCHEDULER_CONTEXT.md` — Cloud Scheduler job inventory
- `SGO_CONTEXT.md` — SportsGameOdds API reference
- `MODELS_CONTEXT.md` — per-model feature and training notes
- `SETTLEMENT_CONTEXT.md` — settlement logic and debugging
