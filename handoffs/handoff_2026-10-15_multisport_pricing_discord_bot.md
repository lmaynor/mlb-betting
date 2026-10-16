# Handoff -- 2026-10-15 -- multi-sport projections, pricing tiers, Discord routing, beezy-bot

Four parts from one spec. Nothing is deployed. Python 1,925 tests, jest 211 (+7 skipped), `tsc` clean, discord-bot 99 node tests.

## What shipped (code)
1. **Daily projection board** -- `ev/daily_projections.py`, `GET /api/public/projections`, Next proxy `/api/projections`, `/sports/{nfl,nhl,nba}`
   Today tab (dislocations + board, MODEL + MARKET badge) and Models tab (real validation numbers). `lib/sports.ts`: all four pillars `live`.
2. **Pricing** -- Single-Sport $19 / Pro Monthly $39 / Pro Annual $299, `lib/tiers.ts`, checkout + Stripe webhook + both reconcilers, My books filter on /ev.
3. **Discord routing** -- `mlb_core/alerts/discord_routes.py`, Kambi dedup (sugarhouse -> betrivers), deep links on every +EV embed.
4. **`discord-bot/`** -- beezy-bot (see its README).

## Read before trusting any of it
- **The board is NOT the XGBoost models.** Their artifacts are local-only, so the live engine is an empirical estimator (`empirical_ewm_v1`: EWM +
  usage-adjusted rate x opponent factor, NegBin/Normal spread from the master's own variance). No injuries, lineups, minutes, weather, rest or
  teammate redistribution. Every row is `validated:false` and the UI says UNVALIDATED. The studies found NHL SOG NO_EDGE, NBA no edge claimable,
  NFL no edge shown. The sports.ts "live" status and the pricing copy describe a product that exists, not a validated edge. PRE_LAUNCH is still true.
- **Lines may be absent.** `odds-bank-*` schedulers are PAUSED (user decision 2026-10-14), so NHL/NBA boards fall back to rotation rows ("lines have not
  posted"); NFL needs `EVSweep/nfl_props/raw/` payloads from a running sweep. NBA game logs end 2026-06 (SportsBlaze lacks 2026-27): flagged `stale_gamelog`.
- **Badges say "PROP PROJECTIONS", not "PROP MODELS"** (spec wording) because only NHL SOG has a trained model at all and none of them run live.
- **STRONG is 70+** on the pricing explainer (published Beezy Score v2 cut); the spec said 65+ (the retired v1 cut).
- Beezy Score on the board = v2 math with edge = min(model_edge, market_ev) under confluence, half of the single positive edge otherwise, 0 if either
  edge > 15% (winner's-curse cap). Confluence also needs >= 3 books.
- NFL has no headshots (no source). NHL/NBA headshots resolve by name server-side.

## Behaviour changes that bite
- +EV pagers no longer post to #daily-picks. With none of `DISCORD_WEBHOOK_EV_BOOKS` / `_EV_EXCHANGES` / `_ALERTS` / `_OPS` set they only print. Provision
  the channels (`/setup-server` prints the gcloud commands) and `--update-secrets` them onto `mlb-fast-alert` / `mlb-kalshi-alert`.
- `post_error` / ops no longer fall back to the picks webhook. Recap keeps the legacy `DISCORD_WEBHOOK_SUMMARY` name in its chain.
- `tests/test_fast_alert_loop_ev.py` no-description assertion replaced (the embed now carries the action links, per the spec).
- `cockpit_form` gained `anytime_td` (rush+receiving TDs) and loads those columns.

## Not done / needs a human
- Deploy: backend first (new route + routes module), then Vercel. Stripe products/prices + `STRIPE_PRICE_*` envs; Clerk Discord OAuth connection; bot Cloud Run service with
  `BEEZY_BOT_SECRET`, `BEEZY_BOT_SYNC_URL` on Vercel. `/api/stripe/webhook` and `/api/user/discord-link` did not exist; the real webhook is `app/api/webhooks/stripe`.
- discord-bot untested on Node 22 and against a live Discord. `/setup-server` resets channel permission overwrites on every run.
- No Cloud Run job/scheduler runs `python -m ev.daily_projections`; the endpoint computes on demand instead (first request after 90 min is slow).
- `fast_alert_loop`'s scan still counts sugarhouse + betrivers as two books in its consensus (`outlier_scan`); only the alert/log rows are deduped.
