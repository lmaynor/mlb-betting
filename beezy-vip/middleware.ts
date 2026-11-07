import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { gatingOn } from '@/lib/access'
import { AUTH_DISABLED } from '@/lib/auth-mode'

export const runtime = 'nodejs'

const isProtectedRoute = createRouteMatcher([
  '/dashboard(.*)',
  '/tools/bet-tracker(.*)',
  '/tools/clv-tracker(.*)',
  '/tools/slate(.*)',
  '/api/admin(.*)',
])

const isAuthRoute = createRouteMatcher([
  '/login(.*)',
  '/signup(.*)',
])

// Geo-blocking: read BLOCKED_STATES env var (comma-separated 2-letter US state codes, e.g. "NY,WA").
// Reads the x-vercel-ip-country-region header injected by Vercel's edge network.
// Default is empty string -- blocks nothing until you configure it post legal review.
// NOTE: Vercel Pro plan required for geo headers on production deployments.
function getBlockedStates(): Set<string> {
  const raw = process.env.BLOCKED_STATES ?? ''
  if (!raw.trim()) return new Set()
  return new Set(raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))
}

function isGeoBlocked(req: NextRequest): { blocked: boolean; state: string; country: string } {
  const blockedStates = getBlockedStates()
  if (blockedStates.size === 0) return { blocked: false, state: '', country: '' }

  // Vercel injects these headers at the edge (requires Vercel Pro plan)
  // x-vercel-ip-country-region: US state code e.g. "NY", "CA"
  // x-vercel-ip-country: 2-letter country code e.g. "US"
  const country = req.headers.get('x-vercel-ip-country') ?? ''
  const region  = req.headers.get('x-vercel-ip-country-region') ?? ''

  if (country !== 'US') return { blocked: false, state: region, country }
  if (blockedStates.has(region)) return { blocked: true, state: region, country }
  return { blocked: false, state: region, country }
}

function geoRedirect(req: NextRequest): NextResponse | undefined {
  // Geo-check: applies to picks and dashboard pages only -- not legal, tools, or learn
  const path = req.nextUrl.pathname
  const isGatedPath = path.startsWith('/picks') || path.startsWith('/dashboard') || path.startsWith('/signup')
  if (!isGatedPath) return undefined
  const geo = isGeoBlocked(req)
  if (!geo.blocked) return undefined
  const url = req.nextUrl.clone()
  url.pathname = '/blocked'
  url.searchParams.set('state', geo.state)
  return NextResponse.redirect(url)
}

// The canonical picks boards (/picks/<sport> and /picks/<sport>/<system>, no query string) are the site's
// Fluid CPU hot spot: they render per request (their filters live in searchParams), and anonymous crawlers
// re-hit the same bare URLs all day. While tier gating is OFF their HTML is identical for every viewer (the
// access policy ignores the viewer -- lib/access.ts), so the CDN may cache it briefly. Every condition below
// is a hard requirement, not a preference: GET only, no query string (a filtered variant is never shared),
// gating off (the moment ENFORCE_TIERS=1 lands, per-viewer cuts return and this stops applying), and no
// Clerk session cookie (signed-in visitors always get a fresh per-request render). Geo redirects return
// before this is reached, so blocked states are unaffected. Keep BOARD_SPORTS in step with SPORT_SLUGS in
// lib/sports.ts; 'games'/'history' are not sports and 'systems' is already ISR, 'projections' a redirect.
const BOARD_SPORTS = new Set(['mlb', 'nfl', 'nhl', 'nba', 'soccer'])
function withBoardCdnCache(req: NextRequest, res: NextResponse): NextResponse {
  if (req.method !== 'GET' || gatingOn() || req.nextUrl.search) return res
  const parts = req.nextUrl.pathname.split('/').filter(Boolean)
  if (parts[0] !== 'picks' || parts.length < 2 || parts.length > 3) return res
  if (!BOARD_SPORTS.has(parts[1])) return res
  if (parts.length === 3 && (parts[2] === 'systems' || parts[2] === 'projections')) return res
  if (req.cookies.get('__session')) return res
  res.headers.set('Cache-Control', 'public, s-maxage=60, stale-while-revalidate=300')
  return res
}

// AUTH_DISABLED (lib/auth-mode.ts): no Clerk at all, geo-blocking still applies. Only the chosen branch is evaluated, so
// clerkMiddleware() is never constructed when it is off.
export default AUTH_DISABLED
  ? (req: NextRequest) => geoRedirect(req) ?? withBoardCdnCache(req, NextResponse.next())
  : clerkMiddleware(async (auth, req) => {
      const geo = geoRedirect(req)
      if (geo) return geo

      // userId is used ONLY by the two branches below (signed-in users off the auth pages, anonymous users
      // off the member routes). Resolving the session on every other matched request -- every public picks
      // page and API proxy hit -- was pure per-request cost, so it now happens only on those routes.
      if (isAuthRoute(req) || isProtectedRoute(req)) {
        const { userId } = await auth()

        // Redirect authenticated users away from auth pages
        if (isAuthRoute(req) && userId) {
          return NextResponse.redirect(new URL('/dashboard', req.url))
        }

        // Protect dashboard and member routes
        if (isProtectedRoute(req) && !userId) {
          return NextResponse.redirect(new URL('/login', req.url))
        }
      }

      return withBoardCdnCache(req, NextResponse.next())
    })

export const config = {
  matcher: [
    // Every match here is a separate Node function invocation (billed Active CPU on Hobby), so the public, auth-free
    // surface is excluded: the ISR pages below and the /api routes that never read the viewer. Pages and routes that call
    // getViewer()/getUserTier()/auth(), and anything under the geo-gated /picks, /dashboard and /signup paths, must stay
    // matched. Keep this list in step with the `revalidate` pages.
    // /api/slate/* and /api/stats/clv are NOT excluded: they are the data plane of the login-gated /tools pages and
    // their handlers check the session with getUserId(), which only resolves when clerkMiddleware runs. The rest of
    // /api/stats (the summary proxies) stays excluded: it serves public season aggregates and never reads the viewer.
    // Also excluded: /api/webhooks (the Stripe webhook verifies its own signature) and /api/cron (checks
    // CRON_SECRET itself) -- neither reads a Clerk session, so a middleware run bought them nothing. The purely
    // static public pages (learn, about, legal, pricing, compare, blocked, the three calculator pages) read no
    // session and are not geo-gated paths, so they are excluded too; /picks stays matched because the geo
    // redirect above applies to it. NOTE for whoever merges the anon-traffic PR: keep /api/collect excluded
    // from this same list when it lands -- the two exclusion sets compose.
    '/((?!_next|api/(?:og|stats(?!/clv(?:/|$))|sparkline|analytics|edge-enrich|tools|webhooks|cron)(?:/|$)|results$|analytics$|tools$|tools/(?:boost-ev|kelly-calculator|odds-calculator)$|models(?:/|$)|learn(?:/|$)|about$|legal(?:/|$)|pricing$|compare(?:/|$)|blocked$|$|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest|txt|xml)).*)',
    '/(api(?!/(?:og|stats(?!/clv(?:/|$))|sparkline|analytics|edge-enrich|tools|webhooks|cron)(?:/|$))|trpc)(.*)',
  ],
}
