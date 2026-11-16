/** @jest-environment node */
/**
 * Bare /ev/projections is CDN-cached at the edge ONLY under the exact conditions middleware.ts documents
 * (RUL-20261115-crawler-policy): GET, no query string, tier gating off, no Clerk session cookie, and the
 * pathname exactly /ev/projections. The page is force-dynamic and reads searchParams, so parameterized
 * variants are never shared -- they are also disallowed to crawlers in public/robots.txt. Both headers are
 * pinned together, same as the board cache: Cache-Control for spec-compliant consumers, CDN-Cache-Control
 * because the force-dynamic render overwrites Cache-Control with a private value while Vercel's edge CDN
 * still obeys CDN-Cache-Control.
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

const cdnCacheControl = async (path: string, opts?: { cookie?: string; method?: string }) =>
  (await handler(async () => ({ userId: null }), req(path, opts)))?.headers.get('cdn-cache-control') ?? ''

beforeEach(() => { delete process.env.ENFORCE_TIERS })

describe('projections CDN cache (middleware)', () => {
  it('caches the bare /ev/projections URL', async () => {
    expect(await cacheControl('/ev/projections')).toBe('public, s-maxage=60, stale-while-revalidate=300')
    expect(await cdnCacheControl('/ev/projections')).toBe('public, s-maxage=60, stale-while-revalidate=300')
  })

  it.each([
    '/ev/projections?league=nba',   // parameterized variants are never shared
    '/ev/projections?date=2026-11-05&game=nba:2026-11-18:BOS@DEN',
    '/ev/projections/',             // trailing slash is a different pathname
    '/ev',                          // the EV terminal is not covered by this rule
    '/ev/games',
  ])('does not cache %s', async (path) => {
    expect(await cacheControl(path)).not.toContain('s-maxage')
    expect(await cdnCacheControl(path)).not.toContain('s-maxage')
  })

  it('never caches for a signed-in visitor (session cookie present)', async () => {
    expect(await cacheControl('/ev/projections', { cookie: 'sess_123' })).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/projections', { cookie: 'sess_123' })).not.toContain('s-maxage')
  })

  it('never caches a non-GET', async () => {
    expect(await cacheControl('/ev/projections', { method: 'POST' })).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/projections', { method: 'POST' })).not.toContain('s-maxage')
  })

  it('stops caching the moment tier gating turns on', async () => {
    process.env.ENFORCE_TIERS = '1'
    expect(await cacheControl('/ev/projections')).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/projections')).not.toContain('s-maxage')
  })
})
