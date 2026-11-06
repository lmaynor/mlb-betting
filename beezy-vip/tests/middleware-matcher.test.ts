/**
 * @jest-environment node
 *
 * Finding 954a0a3a regression (matcher half): /api/slate/* and /api/stats/clv must stay matched by Clerk
 * middleware so the session is available to their handlers' getUserId() checks, while the genuinely public
 * API surface (the /api/stats summary proxies, the OG cards) stays excluded so it costs no middleware run.
 */
jest.mock('@clerk/nextjs/server', () => ({
  clerkMiddleware: (fn: unknown) => fn,
  createRouteMatcher: () => () => false,
}))

import { config } from '@/middleware'

const patterns = (config.matcher as string[]).map(p => new RegExp(`^${p}$`))
const runs = (path: string) => patterns.some(re => re.test(path))

describe('middleware matcher coverage for session-reading API routes', () => {
  it.each([
    '/api/slate/today',
    '/api/stats/clv',
    '/dashboard/picks',
    '/tools/slate',
    '/tools/clv-tracker',
    '/api/picks/today',
    '/picks/mlb',
  ])('middleware runs for %s', (path) => {
    expect(runs(path)).toBe(true)
  })

  it.each([
    '/api/stats',
    '/api/stats/summary',
    '/api/og/games-card',
    '/api/og/picks-card',
    '/api/analytics',
    '/results',
    '/',
    // Self-authenticating routes: the Stripe webhook verifies its signature, the cron checks CRON_SECRET.
    '/api/webhooks/stripe',
    '/api/cron/stripe-reconcile',
    // Purely static public pages: no session read, not geo-gated paths.
    '/learn',
    '/learn/f5-betting-mlb',
    '/about',
    '/legal/privacy',
    '/pricing',
    '/compare/oddsjam-alternative',
    '/blocked',
    '/tools/kelly-calculator',
    '/tools/odds-calculator',
    '/tools/boost-ev',
  ])('middleware stays excluded for the public surface %s', (path) => {
    expect(runs(path)).toBe(false)
  })

  it.each([
    // Member tools stay matched even though other /tools pages are excluded.
    '/tools/bet-tracker',
    '/tools/clv-tracker',
    '/tools/slate',
    '/api/user/bankroll',
    '/api/picks',
    '/api/ev',
    '/login',
    '/signup',
  ])('middleware still runs for %s', (path) => {
    expect(runs(path)).toBe(true)
  })
})
