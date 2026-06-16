# Handoff -- 2026-06-16 (Edge cockpit viz polish + enrichment scheduler + ParlayAPI plan)

Session focused on the beezy.fyi **/edge cockpit** and the ParlayAPI accumulator,
following up on the pending items in `handoff_2026-06-16_beezy_platform.md`.

All work is on branch **`edge-cockpit-viz-enrichment`** (PR open). **Nothing is deployed.**
The deploy/scheduler steps require gcloud and must run from **Cloud Shell** (this session
was on the Mac, which has no gcloud and no .env, and the office LAN blocks the odds APIs --
live odds calls only work from Cloud Run).

## Shipped on this branch
1. **Edge cockpit visualizations redesigned for a casual viewer**
   (`beezy-vip/app/edge/edge-client.tsx`). Every chart now leads with a plain-language
   takeaway and is self-explaining (labeled axes, legends, summary-stat chips):
   - Probability track -> labeled 0-100% scale w/ MARKET/MODEL markers
   - Recent form -> value-labeled bars, gutter line-tag, date axis, "cleared in 8/12 (67%) -- trending up"
   - Spray -> infield diamond + LF/CF/RF, distribution takeaway, hit/out legend
   - EV/LA -> labeled axes, launch-angle zones, barrel zone, stat chips (avg EV / hard-hit / sweet-spot / barrels)
   - Velocity -> full pitch names + usage %, leads-with-X takeaway
   - Release point -> height axis (ft), 3B/1B side, mean marker
   - Zone -> HIGH/LOW + LEFT/RIGHT orientation, hottest cell outlined, stepped heat legend (no gradient)
   - Stays within the Dell-1996 catalog system. `npx tsc --noEmit` = 0 errors.
   - VALIDATION CAVEAT: validated visually in a static SVG harness (the Next app can't
     boot locally -- Clerk needs keys). The REAL cockpit render must be checked on the
     Vercel/deploy preview. See memory `reference_beezy_frontend_preview.md`.
2. **OUTS recent-form sparkline bug fix** (`runners/build_edge_enrichment.py`): was
   counting every PA-ending event (walks/hits included) as an out; now sums out-producing
   events with DP/TP multipliers (`OUT_EVENTS_MULT`).
3. **`deploy/setup_edge_enrichment.sh`** (new): create-or-update the `mlb-build-edge-enrichment`
   Job (with Cloud SQL + `mlb-db-url` secret -- it reads picks from Postgres) and wire TWO
   schedulers. TIMING FIX: the prior handoff said "after mlb-refresh-data" (14:00 UTC) --
   that's wrong; enrichment reads TODAY's kelly_triggered picks, which don't exist until the
   16:00/22:00 UTC betting runs. Schedulers are at **16:20 and 22:20 UTC**.

## Cloud Shell runbook (run these next)
```bash
cd ~/mlb-betting && git fetch && git checkout edge-cockpit-viz-enrichment && git pull
./deploy/deploy_service.sh                      # rebuild image w/ OUTS fix + new script

# Edge enrichment: job + 2 schedulers (16:20/22:20 UTC), run once, validate
PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_edge_enrichment.sh
gcloud run jobs execute mlb-build-edge-enrichment --region=us-central1 --project=concrete-crow-445205-m4 --wait
gcloud storage cat gs://concrete-crow-445205-m4-mlb-data/Enrich/edge/$(TZ=America/Chicago date +%F).json | head -c 1500
#   ^ VALIDATE: batter picks should carry spray/ev_la/recent_form; pitchers velo/release/zone.
#     If a player block is empty, fix matching in runners/build_edge_enrichment.py
#     (batters: Stats API id lookup; pitchers: _norm(player) vs _norm(statcast.player_name)).
```

## ParlayAPI -- UPGRADING TO $5 / 20,000 cr TOMORROW (2026-06-17)
Decision: upgrade to the **$5 / 20k-cr-per-month tier tomorrow**. Bank **game lines +
pitcher props + batter props, all games** -- NO `--max-events` cap. Lower the FREQUENCY to
fit budget (full coverage > more time points). SGO keeps running as the live source and
already banks MLB prop snapshots forward (`Odds/sgo/`); ParlayAPI adds Pinnacle/sharp +
closing-line quality and is the essential path for NBA (no SGO there).

This session expanded `PARLAY_PROP_MARKETS["baseball_mlb"]` in `nba/config.py` to 6 markets:
`player_hits, player_total_bases, player_home_runs` (batter, verified) + `player_strikeouts,
player_outs, player_earned_runs` (pitcher, **CANDIDATE keys -- verify before scheduling**).

Budget math: 20,000 / 30 ~= **667 cr/day**. props refresh = `1 + games x markets`; a full
~15-game slate x 6 markets = ~91 cr. game_lines = ~3 cr/refresh (1/market, whole slate).
- Recommended cadence: **every 4 hours** (`0 */4 * * *`) = 6/day -> props 546 + game_lines 18
  = ~564 cr/day -> **~16.9k/mo**, headroom for doubleheaders. (Every 3h ~= 21.8k -> over.)
- NBA offseason until ~Oct -> MLB gets the whole budget through summer. Stage the NBA job
  now; schedule it + re-split the budget in October.
- Watch `credits_remaining` in `OddsAccum/{sport}/latest.json` and tune.

```bash
# STEP 1 -- VERIFY pitcher market keys BEFORE scheduling (one-shot, no scheduler).
PROJECT_ID=concrete-crow-445205-m4 SPORT=baseball_mlb KIND=props bash ./deploy/setup_parlay_accumulator.sh
gcloud run jobs execute parlay-accum-mlb-props --region=us-central1 --project=concrete-crow-445205-m4 --wait
gcloud storage cat gs://concrete-crow-445205-m4-mlb-data/OddsAccum/baseball_mlb/latest.json
#   ^ check best_book_rows > 0 and that K/outs/ER appear. If pitcher markets returned
#     nothing, the keys are wrong -- inspect a raw payload to find the real ones:
#     gcloud storage cat gs://.../OddsAccum/baseball_mlb/raw/<date>/props_<HHMM>.json | python3 -m json.tool | grep '"key"'
#     then fix PARLAY_PROP_MARKETS in nba/config.py, redeploy the image, re-run.

# STEP 2 -- once keys verified, schedule props every 4h (all games, all 6 markets)
PROJECT_ID=concrete-crow-445205-m4 SPORT=baseball_mlb KIND=props \
  SCHEDULE="0 */4 * * *" bash ./deploy/setup_parlay_accumulator.sh

# STEP 3 -- game lines (cheap), same cadence
PROJECT_ID=concrete-crow-445205-m4 SPORT=baseball_mlb KIND=game_lines \
  SCHEDULE="0 */4 * * *" bash ./deploy/setup_parlay_accumulator.sh

# NBA -- stage props job only (no scheduler until October)
PROJECT_ID=concrete-crow-445205-m4 SPORT=basketball_nba KIND=props bash ./deploy/setup_parlay_accumulator.sh
```
(`setup_parlay_accumulator.sh` exists and takes SPORT/KIND/MAX_EVENTS/SCHEDULE; job name is
`parlay-accum-{mlb|nba}-{props|game_lines}`.)

## Still pending / not done
- Deploy everything above (Cloud Shell).
- Validate enrichment field population on a live run (the one unverified piece).
- Validate the redesigned cockpit viz on the Vercel/deploy preview.
- Kaggle ingest sentinel (`NBA/stats_nba/last_ingest.json`) -- still unconfirmed from prior handoff.
- Security: rotate the credentials flagged in the prior handoff (GitHub PAT, ParlayAPI key, Odds API key).

## Deferred / backlog (unchanged)
- NBA props projection model (blocked on historical prop odds -> now forward-accumulating).
- C3 all-players/teams directory.
- core/ refactor.

## Key pointers
- Memory: `reference_beezy_frontend_preview.md` (local preview can't boot -- Clerk),
  `project_edge_enrichment_and_parlay.md` (enrichment timing + ParlayAPI budget).
- `deploy/setup_edge_enrichment.sh`, `deploy/setup_parlay_accumulator.sh`.
- Prior handoff: `handoffs/handoff_2026-06-16_beezy_platform.md`.
