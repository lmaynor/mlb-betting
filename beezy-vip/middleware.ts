import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'

const isProtectedRoute = createRouteMatcher([
  '/dashboard(.*)',
  '/tools/bet-tracker(.*)',
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

export default clerkMiddleware(async (auth, req) => {
  const { userId } = await auth()

  // Geo-check: applies to picks and dashboard pages only -- not legal, tools, or learn
  const path = req.nextUrl.pathname
  const isGatedPath = path.startsWith('/picks') || path.startsWith('/dashboard') || path.startsWith('/signup')
  if (isGatedPath) {
    const geo = isGeoBlocked(req)
    if (geo.blocked) {
      const url = req.nextUrl.clone()
      url.pathname = '/blocked'
      url.searchParams.set('state', geo.state)
      return NextResponse.redirect(url)
    }
  }

  // Redirect authenticated users away from auth pages
  if (isAuthRoute(req) && userId) {
    return NextResponse.redirect(new URL('/dashboard', req.url))
  }

  // Protect dashboard and member routes
  if (isProtectedRoute(req) && !userId) {
    return NextResponse.redirect(new URL('/login', req.url))
  }
})

export const config = {
  matcher: [
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc)(.*)',
  ],
}
