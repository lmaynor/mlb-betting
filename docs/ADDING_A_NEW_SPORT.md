# Adding a new sport (cfb, cbb, ufc, epl, pga, ...)

Status of the promise: **the website side is registry-only**. Adding a league needs ZERO new Next.js pages and ZERO new React
components. The **backend is not registry-driven yet**: a few Python lists name the sports explicitly (listed in Step 1), so
a new sport touches those files. Both halves are guarded by tests that fail until the wiring is complete.

Everything below was checked against the repo on 2026-10-16. If a path here stops existing, fix this file.

## What drives the site

| Registry | File | What it defines |
|---|---|---|
| Sports | `beezy-vip/lib/sports.ts` (`SPORT_SLUGS`, `SPORT_PILLARS`) | slug, name, season badge, accent, status, `board` source, markets |
| Systems | `beezy-vip/lib/pick-systems.ts` (`PICK_SYSTEMS`, `getSystemsBySport`) | one entry per market: slug, key, name, `statKey`, distribution family, features |
| Tokens | `beezy-vip/lib/tokens.ts` (`SYSTEM_COLOR`, `SYSTEM_LABEL`, `SYSTEM_LABEL_SHORT`) | badge color, long label, compact chip label |

From those three, with no code change, a sport gets: the sport ribbon (`LIVE_SPORTS`), the `/picks` sport chips, the `/ev` and
`/edge` sport pills, `/picks/<sport>`, `/picks/<sport>/projections`, `/picks/<sport>/<system>`, the Systems footer, the Top 10
candidates, the pricing page league picker and the Single-Sport Pass (`LEAGUES` is `SPORT_SLUGS`), the sitemap and the
`/sports/<sport>` redirect.

`board` in the pillar chooses the data source:
- `projection`: the daily projection board plus live market prices (`GET /api/public/projections`, `/api/public/ev/feed`).
  This is the path for a new sport.
- `tracked`: model picks logged and settled in the `bets` table (MLB only today). A second tracked sport also needs
  `sport` plumbed through `apiGetPicks` / the public picks API (they are MLB-scoped), so start with `projection`.

## The 5 steps

### Step 1 -- Backend data and projection adapter
The Python side lists its sports in these places. Add the sport to each:

1. **Game-log master and loader** -- `mlb_core/data/cockpit_form.py`: a `<SPORT>_STATS` map
   (`stat -> (value columns, usage columns, usage label)`), a `_load_<sport>()` loader returning the per-player index, and an
   entry in `_LOADERS` and `supported_stats()`. Also `GameLogs.table_and_def` / `team_of` in `ev/daily_projections.py`.
2. **Projection engine** -- `ev/daily_projections.py`: `SPORTS`, `PROJECT_STATS[<sport>]` (`stat -> normal | negbin | anytime`),
   `STAT_LABEL`, `HALFLIFE`, `ROTATION_STATS` / `ROTATION_MIN_USAGE`, a branch in `canon_team()`, `ESPN_PATH`, and `load_events()`.
3. **Market keys** -- `ev/props.py`: `STAT_KEYS_BY_SPORT[<sport>]` maps ParlayAPI market keys to the stat keys above.
4. **Public API** -- `mlb/runners/public_api.py`: `_PROJ_SPORTS` and `_COCKPIT_SPORTS` (the endpoints 400 on an unknown sport).
5. The stat keys in `PROJECT_STATS` must match the `statKey`s you register on the site (Step 2). A test reads
   `ev/daily_projections.py` and fails if the two lists differ.

`ev/sports/<sport>.py` is the **Kalshi** market mapper (`SportAdapter` in `ev/sports/base.py`: team codes, series prefix, tie
policy). It is only needed if the sport will be mapped to Kalshi markets; it has no stat map.

A market that cannot be projected from the master (NHL power-play points: no PP-assists column) is registered with `gap`
set, shows a data-gap notice, and is left out of `PROJECT_STATS`. Do not fake a projection.

Check: `PYTHONPATH=. python -m pytest tests/test_daily_projections.py` (extend `DEPTH` / `SPORT_TEAMS` in the depth test),
then `python -m ev.daily_projections --sport <sport> --no-write`.

### Step 2 -- Frontend sport and system registration (`beezy-vip/lib/`)
- `sports.ts`: add the slug to `SPORT_SLUGS` and a `SPORT_PILLARS` entry (`board: 'projection'`, `status: 'live'`, accent, a
  `markets` list whose `system` keys are the systems below).
- `pick-systems.ts`: one `PickSystem` per market (>= 5 to pass the contract test), with `sport`, `statKey`, `family`, `features`.
- `tokens.ts`: `SYSTEM_COLOR`, `SYSTEM_LABEL` and `SYSTEM_LABEL_SHORT` for each system key.
- `prop-model-specs.ts`: an entry per stat if a study exists (otherwise add a plain "unvalidated" entry; the footer prints `--`).
- Optional: team logos / headshots -- `python scripts/build_sport_assets.py` and `lib/sport-assets.ts` (`HEADSHOT_SPORTS`).

Because `/picks/[sport]`, `/picks/[sport]/projections` and `/picks/[sport]/[system]` are data-driven, the ribbon, `SportTabs`,
`FilterBar`, `SportProjectionsBoard`, `SystemsFooter` and every system page go live with no new pages.

### Step 3 -- Tier and paywall gating
Nothing to edit: `lib/tiers.ts` `LEAGUES` and `lib/access.ts` derive from `SPORT_SLUGS`, so the sport is in the Single-Sport
Pass selector, `/picks` / `/edge` gating and the Stripe checkout metadata as soon as Step 2 lands. Two follow-ups outside the
repo: create the Stripe price metadata if the pass copy should name the league, and confirm `PRE_LAUNCH` stays true.

### Step 4 -- Discord bot and webhooks
- `discord-bot/src/serverSetup.js`: add the `<Sport> Pass` role to `SPORT_PASS_ROLES` / `ROLE_SPECS`, a `<sport>-card` channel in
  the DAILY MODEL CARDS category, and a webhook row (`discord-webhook-card-<sport>` -> `DISCORD_WEBHOOK_CARD_<SPORT>`).
- `mlb_core/alerts/discord_routes.py`: `webhook_for("card", sport=...)` already reads `DISCORD_WEBHOOK_CARD_<SPORT>`, so only the
  env var and secret are new.
- Run `/setup-server` in Discord (idempotent; it resets channel permission overwrites each run) and apply the gcloud
  secret commands it prints.

### Step 5 -- Cloud Run jobs and credit budget
- `deploy/setup_odds_banking.sh`: add the `SCHEDULES` rows (`tag|sport_key|kind|cron`).
- `deploy/setup_props_alerts.sh`: add `props-alerts-<sport>` and `props-grade-<sport>` rows; `ev/sweep_props_alerts.py` `SPORTS`
  maps the sport to its banked-odds key (the CLI `--sport` choices come from it).
- `nba/config.py`: `PARLAY_PROP_MARKETS[<sport_key>]` (core markets), `PARLAY_PROP_MARKETS_ALT` (opt-in tier) and
  `ODDS_BANK_DEFAULT_BUDGET`; the monthly cap is enforced by `nba/odds/credit_ledger.py`.
- **Every market costs 1 ParlayAPI credit per event per run, whether or not any book returns it, and the pool is shared**
  (20k/month plan). Adding a sport or a market is a budget decision, not a wishlist; the ledger refuses a sport with no budget.
- Run `deploy/build_all_images.sh` and re-provision the jobs (RUNBOOKS.md s8; a job's command is baked into GCP).

## The guard rails (what fails CI)
- `beezy-vip/tests/sport-onboarding.test.ts` loops over `SPORT_SLUGS` and asserts: a live pillar exists, >= 5 systems, every
  system has a color, label, description, family and features and resolves by `getSportSystemBySlug(sport, slug)`,
  `sportHref(sport) === '/picks/<sport>'`, the sport is in `LEAGUES` and `LIVE_SPORTS`, pillar markets and listed systems
  match one-to-one, and the site's stat keys equal the Python engine's `PROJECT_STATS`.
- `beezy-vip/tests/ia.test.ts` fails if a ribbon link or a system page has no route.
- `tests/test_daily_projections.py` runs every projectable stat of every sport through the engine.

## Not covered by any test (check by hand)
- The Python sport tuples in Step 1.4 and the odds-banking / alerts job rows in Step 5.
- Whether the sport has a headshot source and team logos.
- Whether the ParlayAPI market keys you mapped are the ones books actually return (probe `/v1/markets` first).
