# Beezy.VIP — Developer Handoff

*Generated: 2026-05-15*

---

## What this is

A complete Next.js 14 website for Beezy.VIP, a sports betting picks and tools platform backed by live XGBoost ML models. The site is a read-only frontend consuming an existing Postgres database on GCP. The ML backend (Flask on Cloud Run) is not touched — the website talks directly to Postgres via Next.js route handlers.

**Current state:** Pre-launch. Paper mode. 87 settled bets across 5 MLB systems. No real money involved yet.

---

## What's built

### Pages (47 routes)

| Route | Description |
|---|---|
| `/` | Landing page — hero, stats strip, live ticker, models grid, picks table, pricing |
| `/picks` | Filterable picks hub (league/market/date/status) |
| `/picks/mlb` | MLB picks hub with per-system cards |
| `/picks/mlb/[nrfi\|hr\|f5\|k\|outs]` | System-specific pick pages |
| `/results` | Full public results history |
| `/results/[date]` | Results by date with prev/next nav |
| `/tools` | Tools hub |
| `/tools/odds-calculator` | Vig calculator (fully public, client-side) |
| `/tools/kelly-calculator` | Kelly criterion calculator (fully public) |
| `/tools/edge-finder` | Edge finder (model prob gated to Pro) |
| `/tools/nrfi-conditions` | NRFI conditions dashboard (GCS data, model prob gated) |
| `/tools/pitcher-matchups` | Pitcher K dashboard (GCS data, K proj gated) |
| `/tools/bet-tracker` | Personal bet tracker (localStorage, members) |
| `/models` | Models overview — methodology, data sources |
| `/models/[slug]` | Individual model detail — features, calibration, history |
| `/learn` | Learn hub (AI-generated articles) |
| `/learn/[slug]` | Article pages (markdown rendered) |
| `/games/mlb/[date]` | Daily game pages (programmatic SEO) |
| `/pitchers/[name]` | Pitcher K model history (programmatic SEO) |
| `/players/[name]` | Player HR model history (programmatic SEO) |
| `/recap/[date]` | Daily recap (programmatic SEO, auto-generated summary) |
| `/teams/[slug]` | Team model history (12 MLB teams pre-mapped) |
| `/login` | Clerk SignIn |
| `/signup` | Pre-launch waitlist → post-launch Clerk SignUp |
| `/dashboard/picks` | Member picks + Kelly sizing + copy button |
| `/dashboard/history` | Member P&L + per-system breakdown |
| `/dashboard/settings` | Billing, bankroll, notifications |

### API routes (14 endpoints)

| Route | Description |
|---|---|
| `GET /api/picks` | Filterable picks query |
| `GET /api/picks/today` | Today's qualifying picks |
| `GET /api/stats/summary` | Overall + per-system stats |
| `GET /api/tools/odds-calc` | Vig calculation |
| `GET /api/tools/kelly` | Kelly sizing |
| `GET /api/og` | OG image generation (edge runtime) |
| `POST /api/admin/learn/generate` | Trigger article generation |
| `GET /api/cron/refresh-articles` | Weekly cron (Vercel) |
| `POST /api/stripe/checkout` | Create Stripe checkout session |
| `GET /api/stripe/portal` | Redirect to Stripe billing portal |
| `POST /api/webhooks/stripe` | Stripe webhook → sets Clerk tier metadata |
| `POST /api/user/bankroll` | Save bankroll to Clerk metadata |
| `POST /api/db/migrate` | Run DB migrations |
| `GET /api/sitemap.xml` | Dynamic sitemap |

### Libraries (`/lib`)

| File | Contents |
|---|---|
| `db.ts` | Postgres pool, typed queries, all common query functions |
| `odds.ts` | Betting math: implied prob, vig removal, Kelly criterion |
| `auth.ts` | `getUserTier()`, `canAccessFeature()` — reads Clerk metadata |
| `og.ts` | `ogUrl()` helper for OG image URLs |
| `learn-db.ts` | `learn_articles` table CRUD |
| `article-generator.ts` | 10 article specs + Anthropic API call + prompt builder |
| `model-specs.ts` | Full feature lists for all 5 models (NRFI/HR/F5/K/OUTS) |

---

## Tech stack

| Layer | Technology |
|---|---|
| Framework | Next.js 14 (App Router) |
| Styling | Tailwind CSS + CSS variables |
| Database | Postgres (GCP Cloud SQL — read-only) |
| Auth | Clerk |
| Payments | Stripe |
| AI content | Anthropic API (claude-sonnet-4-20250514) |
| Deployment | Vercel |
| OG images | `next/og` (edge runtime) |

---

## Design system

All tokens are in `app/globals.css` as CSS variables and `tailwind.config.ts`:

| Token | Value |
|---|---|
| Background | `#080C10` |
| Surface | `#0D1117` |
| Accent / Win | `#00FF87` (terminal green) |
| Loss | `#FF4757` |
| Text | `#F0F6FC` |
| Muted | `#7D8590` |
| Font | Geist (sans) + Geist Mono (data) |
| Border radius | 0px (sharp corners throughout) |

Rules: no gradients, no glow, no blur/glass, no rounded corners, no stock imagery. Numbers are the hero. Data density over decoration.

---

## First deploy steps

```
1. npm install
2. cp .env.example .env.local  →  fill in all vars (see below)
3. Push to GitHub, connect to Vercel
4. Set all env vars in Vercel dashboard
5. Deploy
6. POST /api/db/migrate  (creates learn_articles table, adds league/book columns)
7. POST /api/admin/learn/generate {"all": true}  (generates 10 learn articles, ~$0.50 API cost)
8. Set Discord invite URL in:
   - components/layout/nav.tsx  (line ~65)
   - components/landing/how-it-works.tsx  (DiscordCTA component)
```

---

## Environment variables

```env
# Database (GCP Cloud SQL)
DATABASE_URL=postgresql://user:password@host:5432/beezy

# Anthropic (article generation)
ANTHROPIC_API_KEY=sk-ant-...

# Clerk
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
CLERK_SECRET_KEY=sk_live_...
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/signup
NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL=/dashboard
NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL=/dashboard

# Stripe
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_STARTER=price_...   ← your actual Stripe price IDs
STRIPE_PRICE_PRO=price_...
STRIPE_PRICE_SEASON=price_...

# Admin
ADMIN_SECRET_KEY=choose-a-secret-string

# Cron (Vercel generates this automatically on deploy)
CRON_SECRET=auto-generated

# GCS (for NRFI conditions + pitcher matchup tool data)
GCS_BUCKET_NAME=your-bucket-name

# App
NEXT_PUBLIC_BASE_URL=https://beezy.vip
```

---

## Pre-launch → launch flip

Two files have a `PRE_LAUNCH = true` flag:

1. `app/signup/page.tsx` — controls whether signup shows Discord waitlist or Clerk SignUp
2. `components/landing/pricing.tsx` — controls whether pricing CTAs go to Discord or Stripe checkout

Set both to `false` when the first system clears 200 bets and you're ready to charge.

When `PRE_LAUNCH = false` in pricing, also uncomment `<SignUp />` in `app/signup/page.tsx`.

---

## Stripe setup

1. Create 3 products in Stripe dashboard: Starter ($29/mo), Pro ($79/mo), Season ($499 one-time)
2. Copy the Price IDs into `STRIPE_PRICE_*` env vars
3. Create webhook endpoint: `https://beezy.vip/api/webhooks/stripe`
4. Subscribe to events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
5. Copy webhook signing secret to `STRIPE_WEBHOOK_SECRET`

The webhook sets `privateMetadata.tier` on the Clerk user. Valid values: `starter`, `pro`, `season`. Downgrade sets it to `public`.

---

## Membership gating

`lib/auth.ts` → `getUserTier()` reads `user.privateMetadata.tier` from Clerk.

| Feature | Public | Starter | Pro/Season |
|---|---|---|---|
| First 4 picks | ✓ | ✓ | ✓ |
| All picks | — | 1 system | All |
| Stake sizing | — | — | ✓ |
| Model probability | — | — | ✓ |
| Full results | — | ✓ | ✓ |
| Dashboard | — | — | ✓ |
| CSV export | — | — | ✓ |

Dashboard is protected by middleware (`middleware.ts`) — unauthenticated users redirect to `/login`, non-Pro redirect to `/signup`.

---

## Database

The site reads from your existing `bets` table. Schema it expects:

```sql
bets (
  id              SERIAL PRIMARY KEY,
  system          TEXT,        -- 'NRFI' | 'HR' | 'F5' | 'K' | 'OUTS'
  game_date       DATE,
  game_pk         INTEGER,
  bet_type        TEXT,
  line            INTEGER,     -- American odds e.g. -115
  stake           NUMERIC,
  model_prob      NUMERIC,     -- 0.0–1.0
  implied_prob    NUMERIC,     -- 0.0–1.0
  kelly_triggered BOOLEAN,
  result          TEXT,        -- 'W' | 'L' | 'P' | NULL (pending)
  pnl             NUMERIC,
  created_at      TIMESTAMP
)
```

The migration route (`POST /api/db/migrate`) also adds `league` and `book` columns (default 'MLB' / 'DraftKings') for future multi-league expansion.

The `learn_articles` table is created by the migration:
```sql
learn_articles (
  slug            TEXT PRIMARY KEY,
  title           TEXT,
  body_mdx        TEXT,
  meta_desc       TEXT,
  keyword         TEXT,
  generated_at    TIMESTAMPTZ,
  stats_snapshot  JSONB
)
```

---

## AI article generation

10 articles are pre-specced in `lib/article-generator.ts`:
- what-is-nrfi, kelly-criterion, implied-probability, remove-vig
- mlb-strikeout-props, f5-betting, home-run-props, xgboost-sports-betting
- nrfi-strategy, line-movement

Each article prompt includes live stats from the `bets` table (win rate, ROI, sample size per system), internal links to relevant tools and picks pages, and brand voice rules.

Articles auto-refresh every Sunday at 03:00 UTC via Vercel cron (`vercel.json`).

To regenerate a single article with updated stats:
```bash
curl -X POST https://beezy.vip/api/admin/learn/generate \
  -H "Content-Type: application/json" \
  -H "x-admin-key: YOUR_ADMIN_SECRET_KEY" \
  -d '{"slug": "what-is-nrfi"}'
```

---

## GCS tool data

Two tools pull live feature data from GCS:
- `NRFI conditions`: `gs://{bucket}/NRFI_Pro_System/data/pitcher_start_features.csv`
- `Pitcher matchups`: `gs://{bucket}/K_Pro_System/data/model_features.csv`

Both fail gracefully to seed data if GCS is unavailable or bucket isn't set. Bucket must be publicly readable OR the Next.js server must have GCP credentials.

---

## Model specs

All 5 model feature lists are in `lib/model-specs.ts`. These match the actual Python model features and are shown on `/models/[slug]` detail pages. Update these as models are retrained.

Current versions: NRFI v17, HR v6, F5 v4, K v9, OUTS v3

---

## SEO

- Sitemap at `/sitemap.xml` — auto-includes all learn articles
- `robots.txt` at `/public/robots.txt` — blocks dashboard/api, allows everything else
- OG images generated at `/api/og` (edge runtime, no external deps)
- Structured data: `Article` on recaps, `Person` on pitcher/player pages, `TechArticle` on model pages, `SportsOrganization` on team pages, `ItemList` on game pages
- All programmatic SEO pages (`/games/mlb/[date]`, `/pitchers/[name]`, `/players/[name]`, `/recap/[date]`, `/teams/[slug]`) have `revalidate = 3600`

---

## ISR cache settings

| Page type | Revalidate |
|---|---|
| Landing page | 300s (5 min) |
| Today's picks | 60s |
| Results | 300s |
| Tools (conditions/matchups) | 1800s (30 min) |
| Models, teams, pitchers, players | 3600s (1 hr) |
| Learn articles | 3600s (1 hr) |
| Stats summary | 300s |

---

## Known limitations / future work

- `/picks/mlb/today` is a URL in the PRD but not built as a distinct page — `/picks/mlb?date=today` covers it. Add a redirect if needed.
- Bet tracker uses `localStorage` — data doesn't sync across devices. Server-side persistence would need a `user_bets` table.
- Dashboard picks page shows `Game {game_pk}` instead of team names — the `bets` table doesn't currently store team names. Add a join to a games table or enrich the data at settlement time.
- Teams page (`/teams/[slug]`) covers 12 teams. Expand `TEAM_MAP` in the page file for full 30-team coverage.
- CSV export button is gated to Pro in the access matrix but the UI for it isn't built — add a download handler to the results page.
- `app/signup/page.tsx` has `<SignUp />` commented out — uncomment when `PRE_LAUNCH = false`.

---

## Component structure

```
components/
  landing/
    hero.tsx              Hero section
    stats-strip.tsx       Season ROI/WR/bets/edge strip
    live-ticker.tsx       Scrolling settled bets ticker
    how-it-works.tsx      3-step explainer + Discord CTA
    models-grid.tsx       System cards with live stats
    recent-picks-table.tsx  Blur-gated picks preview
    pricing.tsx           3-tier pricing (PRE_LAUNCH flag)
  layout/
    nav.tsx               Sticky nav with active highlighting
    footer.tsx            Links + disclaimers
  picks/
    filter-bar.tsx        Sticky filter chips (URL-driven)
    picks-table.tsx       Reusable picks table
    system-picks-page.tsx Shared system page (all 5 systems use this)
  ui/
    primitives.tsx        LiveDot, SystemBadge, StatCard, ResultPill, PnL, Button
    bankroll-input.tsx    Bankroll save with Clerk metadata
    checkout-button.tsx   Stripe checkout trigger
    copy-bet-button.tsx   Clipboard copy with fallback
```

---

## Build / deploy commands

```bash
npm run dev       # local dev
npm run build     # production build
npm run lint      # ESLint
npx tsc --noEmit  # type check (should be zero errors)
vercel --prod     # deploy to production
```

---

*This codebase was built against the Beezy.VIP PRD dated 2026-05-15.*
*All 90 files pass TypeScript strict mode with zero errors.*
