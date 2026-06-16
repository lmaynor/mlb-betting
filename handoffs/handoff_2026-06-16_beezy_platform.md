# Handoff -- 2026-06-16 (beezy.fyi platform reframe + NBA pillar + Edge cockpit)

Point-in-time state after a long session that (a) stood up the **NBA pillar**,
(b) built the **/edge** dashboard/cockpit on beezy.fyi, and (c) reframed the whole
project as **beezy.fyi with mlb + nba pillars** (CONTEXT section 0).

## Platform reframe (new -- read CONTEXT section 0)
- The product is **beezy.fyi**, not an MLB-only repo. Sports nest as pillars:
  **mlb** (mature/live, `mlb_core/`, `NBA`-less GCS masters) and **nba** (emerging,
  data-only, `nba/` + `NBA/` prefix).
- Labeling convention going forward: always name the pillar; new code nests by
  sport (`nba/...`); sport-agnostic infra (`mlb_core.storage`, `mlb_core.odds.utils`)
  is reused despite the legacy name. A neutral `core/` rename is DEFERRED.
- Repo name `lmaynor/mlb-betting` is legacy; do not rename.

## Shipped this session (all merged to main, PRs #2-#9)
1. **NBA data pipeline** (#2): `nba/` package -- SportsBlaze client/flatten/masters/
   backfill/refresh. Backfilled 7 seasons (9,186 games / 18,372 team / 315,331 player
   rows) to `NBA/`. Nightly `nba-refresh-data` Job + Scheduler (year-round, no-op
   offseason).
2. **Kaggle deep-history ingest** (#3): `nba-kaggle-ingest` Job -> `NBA/stats_nba/`
   (eoinamoore stats.nba.com dataset). + `storage.upload_file`.
3. **Storage fix** (#4): `mlb_core.storage` resolves `MLB_BASE_DATA` at call time
   (fixed a latent import-order test failure that blocked deploys).
4. **Backtest findings** (#6): NBA game **moneyline is NOT viable** (market AUC
   0.739 > model 0.703; flat backtest -6% to -8%; model adds ~0 over market).
   Pivot to **player props**. See `handoff_nba_backtest_2026-06-15.md`.
5. **ParlayAPI odds accumulator** (#7): chosen live-odds provider (1000 cr/mo free,
   Pinnacle, 32 books). `nba/odds/` -- parlayapi client + extract + accumulator Job
   -> `OddsAccum/{sport}/`. Billing measured: props 1cr/(event x market), game lines
   1cr/market. **No historical props at any tier** -> must forward-accumulate.
6. **The Edge dashboard** (#8) + **cockpit** (#9) on beezy.fyi `/edge`:
   - edge core (model vs de-vig market prob, edge%, Kelly), headshot/identity,
     pitcher matchup (real, via slate endpoint), weather, recent-form sparkline,
     spray, EV/LA, velocity-by-pitch, release-point, strike-zone heatmap.
   - Quick filters: Top-10-today, min-edge slider, team, group tabs.
   - Backend: `/api/public/edge-enrich` (fail-soft) + `runners/build_edge_enrichment.py`
     (nightly precompute; NOT yet wired into the daily chain).
   - Added `pfx_x/pfx_z` + `plate_x/plate_z` to the Statcast pull (forward-only).

## PENDING / verify next session
- **Deploy the Edge backend + populate enrichment** (may be partly done -- VERIFY):
  ```
  cd ~/mlb-betting && git checkout main && git pull && ./deploy/deploy_service.sh
  gcloud run jobs update mlb-build-edge-enrichment --image=gcr.io/concrete-crow-445205-m4/mlb-betting --region=us-central1 --project=concrete-crow-445205-m4
  gcloud run jobs execute mlb-build-edge-enrichment --region=us-central1 --project=concrete-crow-445205-m4 --wait
  gcloud storage cat gs://concrete-crow-445205-m4-mlb-data/Enrich/edge/$(TZ=America/Chicago date +%F).json | head -c 1000
  ```
- **VALIDATE the enrichment fields on a live run** (the one unverified piece):
  batter picks should get `spray`/`ev_la`/`recent_form`; pitcher picks `velo`/
  `release`/`zone`. Statcast identity matching is best-effort (batters via MLB Stats
  API id; pitchers via `player_name`). If empty, fix matching in
  `runners/build_edge_enrichment.py`.
- **Kaggle ingest**: confirm `NBA/stats_nba/last_ingest.json` populated (ran overnight;
  watch for OOM -> bump job memory to 32Gi if FAILED).
- **ParlayAPI accumulator**: MLB props job ran once manually. NOT scheduled yet, and
  the NBA job is not staged. Decide cadence + (optional) $5 tier; wire schedulers.
- **Edge enrichment scheduler**: wire `mlb-build-edge-enrichment` to run nightly
  after `mlb-refresh-data` (not yet scheduled).

## Deferred / backlog
- **NBA Edge cockpit C3**: all-players/teams research directory -- needs a league-wide
  precompute over the 300MB Statcast master -> per-player/team JSON; then directory +
  profile UI. Sizable; not started.
- **NBA props model + backtest**: the path forward, BLOCKED on historical prop odds
  (ParlayAPI has none; forward-accumulate from ~Oct, or buy). Build projection model
  (Kaggle box + feature spec in `nba/BLUEPRINT.md`, NegBin CDF a la BATTER_HITS).
- **NBA game ML**: dead end, do not pursue (documented).
- **core/ refactor**: lift sport-agnostic infra out of `mlb_core` -- deferred.

## Tooling for next session
- **markdown-viewer skills** (`npx skills add markdown-viewer/skills`) -- 14 diagram
  skills (PlantUML/Vega/architecture/mindmap). User authorized; activates next session.
  USE THEM for architecture/diagram passes (e.g. a PlantUML deployment diagram or a
  Vega chart of backtest results). A Mermaid platform diagram exists in chat; consider
  committing `docs/ARCHITECTURE_NBA.md`.
- **Impeccable v3.6.0** installed (user-level) -- active next session; use
  `impeccable live` against a running `/edge` preview for visual fine-tuning.

## Security hygiene
Several credentials appeared in the chat transcript this session and should be
ROTATED: the GitHub PAT (provisioned for the agent), the ParlayAPI key
(`6394e...`, now in Secret Manager `parlay-api-key`), and The Odds API key. Rotate
each and update the corresponding Secret Manager versions.

## Key pointers
- `nba/BLUEPRINT.md` -- full NBA prop-pipeline plan + provenance + feature spec.
- `nba/README.md` -- NBA data dictionary.
- `docs/solutions/integration-issues/sportsblaze-nba.md` -- SportsBlaze quirks.
- CONTEXT section 0 (platform framing) + section 19 (NBA pillar).
- NOTE: api.the-odds-api.com, parlay-api.com, mongoosebets.com are blocked on the
  office LAN (Netskope gambling category) -- live odds calls only work from Cloud Run.
