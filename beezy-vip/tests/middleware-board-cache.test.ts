/** @jest-environment node */
/**
 * The canonical picks boards are CDN-cached at the edge ONLY under the exact conditions middleware.ts
 * documents: GET, no query string, tier gating off, no Clerk session cookie, a real sport slug, and not
 * the systems/projections subpaths. Anything else must come back without a shared Cache-Control header.
 * (The matcher test mocks Clerk the same way; here the callback itself is exercised.)
 */
jest.mock('@clerk/nextjs/server', () => ({
  clerkMiddleware: (fn: unknown) => fn,
  createRouteMatcher: () => () => false,
}))

import middleware from '@/middleware'

type Handler = (auth: () => Promise<{ userId: string | null }>, req: unknown) => Promise<Response | undefined>
const handler = middleware as unknown as Handler

function req(path: string, opts: { cookie?: string; method?: string } = {}) {
  return {
    method: opts.method ?? 'GET',
    nextUrl: new URL(`http://localhost${path}`),
    url: `http://localhost${path}`,
    headers: new Headers(),
    cookies: { get: (name: string) => (opts.cookie && name === '__session' ? { name, value: opts.cookie } : undefined) },
  }
}

const cacheControl = async (path: string, opts?: { cookie?: string; method?: string }) =>
  (await handler(async () => ({ userId: null }), req(path, opts)))?.headers.get('cache-control') ?? ''

beforeEach(() => { delete process.env.ENFORCE_TIERS })

describe('board CDN cache (middleware)', () => {
  it.each(['/picks/nfl', '/picks/mlb', '/picks/mlb/sb', '/picks/nba/points'])(
    'caches the canonical board %s',
    async (path) => { expect(await cacheControl(path)).toContain('s-maxage=60') },
  )

  it.each([
    '/picks/nfl?tier=official',   // filtered variants are never shared
    '/picks/nfl/systems',         // already ISR
    '/picks/nfl/projections',     // a redirect, not a board
    '/picks/games',               // not a sport board
    '/picks/history',
    '/picks',                     // Top Signals: not covered by the board rule
    '/ev',
    '/picks/golf',                // not a registered sport
  ])('does not cache %s', async (path) => {
    expect(await cacheControl(path)).not.toContain('s-maxage')
  })

  it('never caches for a signed-in visitor (session cookie present)', async () => {
    expect(await cacheControl('/picks/nfl', { cookie: 'sess_123' })).not.toContain('s-maxage')
  })

  it('never caches a non-GET', async () => {
    expect(await cacheControl('/picks/nfl', { method: 'POST' })).not.toContain('s-maxage')
  })

  it('stops caching the moment tier gating turns on', async () => {
    process.env.ENFORCE_TIERS = '1'
    expect(await cacheControl('/picks/nfl')).not.toContain('s-maxage')
  })
})
