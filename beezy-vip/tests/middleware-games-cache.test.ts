/** @jest-environment node */
/**
 * Canonical navigation variants of /ev/games (the bare URL, the four ?sport= board forms, ?view=table,
 * and its four ?view=table&sport= forms) are CDN-cached at the edge ONLY under the exact conditions middleware.ts
 * documents (RUL-20261115-crawler-policy): GET, canonical query shapes only, tier gating off, no Clerk
 * session cookie, and the pathname exactly /ev/games. The page is force-dynamic and reads searchParams,
 * so filtered and non-canonical variants are never shared. Both headers are pinned together, same as the board
 * and projections caches: Cache-Control for spec-compliant consumers, CDN-Cache-Control because the force-dynamic
 * render overwrites Cache-Control with a private value while Vercel's edge CDN still obeys CDN-Cache-Control.
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

describe('games CDN cache (middleware)', () => {
  it('caches the bare /ev/games URL', async () => {
    expect(await cacheControl('/ev/games')).toBe('public, s-maxage=60, stale-while-revalidate=300')
    expect(await cdnCacheControl('/ev/games')).toBe('public, s-maxage=60, stale-while-revalidate=300')
  })

  it.each([
    '/ev/games?sport=nba',
    '/ev/games?sport=nhl',
    '/ev/games?sport=ncaab',
    '/ev/games?sport=soccer',
    '/ev/games?view=table',
    '/ev/games?view=table&sport=nba',
    '/ev/games?view=table&sport=nhl',
    '/ev/games?view=table&sport=ncaab',
    '/ev/games?view=table&sport=soccer',
    '/ev/games?sport=nba&view=table',
  ])('caches the canonical navigation variant %s', async (path) => {
    expect(await cacheControl(path)).toBe('public, s-maxage=60, stale-while-revalidate=300')
    expect(await cdnCacheControl(path)).toBe('public, s-maxage=60, stale-while-revalidate=300')
  })

  it.each([
    '/ev/games?view=prices',
    '/ev/games?view=board',
    '/ev/games?sport=all',
    '/ev/games?sport=mlb',
    '/ev/games?game=nba:2026-11-18:BOS@DEN',
    '/ev/games?view=analysis&game=nba:2026-11-18:BOS@DEN',
    '/ev/games?sport=nba&game=nba:2026-11-18:BOS@DEN',
    '/ev/games?sport=nba&sport=nhl',
    '/ev/games?view=table&view=table',
    '/ev/games?view=table&day=yesterday',
    '/ev/games?sport=nba&sort=edge',
    '/ev/games/',
    '/ev',
    '/ev/candidates',
  ])('does not cache %s', async (path) => {
    expect(await cacheControl(path)).not.toContain('s-maxage')
    expect(await cdnCacheControl(path)).not.toContain('s-maxage')
  })

  it('never caches for a signed-in visitor (session cookie present)', async () => {
    expect(await cacheControl('/ev/games', { cookie: 'sess_123' })).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/games', { cookie: 'sess_123' })).not.toContain('s-maxage')
    expect(await cacheControl('/ev/games?sport=nba', { cookie: 'sess_123' })).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/games?sport=nba', { cookie: 'sess_123' })).not.toContain('s-maxage')
  })

  it('never caches a non-GET', async () => {
    expect(await cacheControl('/ev/games', { method: 'POST' })).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/games', { method: 'POST' })).not.toContain('s-maxage')
  })

  it('stops caching the moment tier gating turns on', async () => {
    process.env.ENFORCE_TIERS = '1'
    expect(await cacheControl('/ev/games')).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/games')).not.toContain('s-maxage')
    expect(await cacheControl('/ev/games?view=table&sport=nhl')).not.toContain('s-maxage')
    expect(await cdnCacheControl('/ev/games?view=table&sport=nhl')).not.toContain('s-maxage')
  })
})
