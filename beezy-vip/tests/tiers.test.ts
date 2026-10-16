import { ALL_ACCESS, isAllAccess, isLeague, normalizeTier, priceIdFor, priceToTierMap, tierLabel } from '../lib/tiers'
import { buildRoleSyncPayload, discordIdOf, isSnowflake, syncDiscordRole, syncUserRoles } from '../lib/discord-sync'
import { matchesMyBooks, MY_BOOKS_KEY, parseMyBooks, toggleBook } from '../lib/use-my-books'

describe('tiers', () => {
  it('normalizes unknown metadata to public and keeps the new plan names', () => {
    expect(normalizeTier('pro_annual')).toBe('pro_annual')
    expect(normalizeTier('single_sport')).toBe('single_sport')
    expect(normalizeTier('platinum')).toBe('public')
    expect(normalizeTier(undefined)).toBe('public')
  })
  it('all-access covers the paid tiers that unlock every league, and not single_sport', () => {
    for (const t of ['pro_monthly', 'pro_annual', 'pro', 'season']) expect(isAllAccess(t)).toBe(true)
    for (const t of ['single_sport', 'starter', 'public', null, undefined]) expect(isAllAccess(t as string)).toBe(false)
    expect(ALL_ACCESS).toHaveLength(4)
  })
  it('maps price ids to tiers; pro_monthly falls back to the legacy pro price', () => {
    const env = { STRIPE_PRICE_SINGLE_SPORT: 'p_s', STRIPE_PRICE_PRO_ANNUAL: 'p_a', STRIPE_PRICE_PRO_MONTHLY: 'p_m', STRIPE_PRICE_PRO: 'p_legacy' }
    expect(priceToTierMap(env)).toEqual({ p_s: 'single_sport', p_a: 'pro_annual', p_m: 'pro_monthly', p_legacy: 'pro' })
    expect(priceIdFor('pro_monthly', { STRIPE_PRICE_PRO: 'p_legacy' })).toBe('p_legacy')
    expect(priceIdFor('pro_monthly', { STRIPE_PRICE_PRO_MONTHLY: 'p_m', STRIPE_PRICE_PRO: 'p_legacy' })).toBe('p_m')
    expect(priceIdFor('pro_annual', {})).toBeUndefined()
    expect(priceIdFor('bogus', env)).toBeUndefined()
  })
  it('labels', () => {
    expect(tierLabel('single_sport')).toContain('$19')
    expect(tierLabel('pro_monthly')).toContain('$39')
    expect(tierLabel('pro_annual')).toContain('$299')
    expect(tierLabel('nonsense')).toBe('Free')
    expect(isLeague('nfl')).toBe(true)
    expect(isLeague('cricket')).toBe(false)
  })
})

describe('discord role sync', () => {
  const ID = '123456789012345678'
  it('validates snowflakes and builds the bot payload', () => {
    expect(isSnowflake(ID)).toBe(true)
    expect(isSnowflake('abc')).toBe(false)
    expect(isSnowflake('1'.repeat(30))).toBe(false)
    expect(buildRoleSyncPayload({ discordUserId: 'nope', tier: 'pro_monthly' })).toBeNull()
    expect(buildRoleSyncPayload({ discordUserId: ID, email: 'a@b.co', tier: 'single_sport', sport: 'nba', clerkUserId: 'u1' }))
      .toEqual({ discord_user_id: ID, email: 'a@b.co', tier: 'single_sport', sport_choice: 'nba', clerk_user_id: 'u1' })
    expect(buildRoleSyncPayload({ discordUserId: ID, tier: 'pro_annual', sport: 'curling' })?.sport_choice).toBeNull()
  })
  it('skips (never throws) without a linked account or config', async () => {
    expect(await syncDiscordRole(null, { url: 'http://x', secret: 's' })).toMatchObject({ ok: false, skipped: true })
    const p = buildRoleSyncPayload({ discordUserId: ID, tier: 'public' })
    expect(await syncDiscordRole(p, { url: '', secret: '' })).toMatchObject({ ok: false, skipped: true })
  })
  it('posts with the shared secret header and treats 404 (not in server) as fine', async () => {
    const calls: { url: string; init: RequestInit }[] = []
    const mk = (status: number) => (async (url: string, init: RequestInit) => { calls.push({ url, init }); return { ok: status < 300, status } }) as unknown as typeof fetch
    const p = buildRoleSyncPayload({ discordUserId: ID, tier: 'pro_monthly' })
    expect(await syncDiscordRole(p, { url: 'https://bot/internal/sync-role', secret: 'shh', fetchImpl: mk(200) })).toEqual({ ok: true, status: 200 })
    expect(calls[0].init.method).toBe('POST')
    expect((calls[0].init.headers as Record<string, string>)['X-Beezy-Secret']).toBe('shh')
    expect(JSON.parse(calls[0].init.body as string).tier).toBe('pro_monthly')
    expect(await syncDiscordRole(p, { url: 'u', secret: 's', fetchImpl: mk(404) })).toEqual({ ok: true, status: 404 })
    expect(await syncDiscordRole(p, { url: 'u', secret: 's', fetchImpl: mk(500) })).toMatchObject({ ok: false, status: 500 })
    const boom = (async () => { throw new Error('down') }) as unknown as typeof fetch
    expect(await syncDiscordRole(p, { url: 'u', secret: 's', fetchImpl: boom })).toMatchObject({ ok: false, reason: 'down' })
  })
  it('syncUserRoles reads the Discord id and sport choice from Clerk private metadata', async () => {
    let sent: Record<string, unknown> = {}
    const f = (async (_u: string, init: RequestInit) => { sent = JSON.parse(init.body as string); return { ok: true, status: 200 } }) as unknown as typeof fetch
    const user = { privateMetadata: { discordUserId: ID, sportChoice: 'nhl' }, emailAddresses: [{ emailAddress: 'm@x.io' }] }
    await syncUserRoles(user, 'user_1', 'single_sport', undefined, { url: 'u', secret: 's', fetchImpl: f })
    expect(sent).toMatchObject({ discord_user_id: ID, email: 'm@x.io', tier: 'single_sport', sport_choice: 'nhl', clerk_user_id: 'user_1' })
    expect(await syncUserRoles({ privateMetadata: {} }, 'u', 'pro', undefined, { url: 'u', secret: 's' })).toMatchObject({ skipped: true })
  })
  it('takes the Discord id only from a verified Discord OAuth account', () => {
    expect(discordIdOf([{ provider: 'oauth_google', providerUserId: ID }])).toBeNull()
    expect(discordIdOf([{ provider: 'oauth_discord', providerUserId: ID }])).toBe(ID)
    expect(discordIdOf([{ provider: 'discord', externalId: ID }])).toBe(ID)
    expect(discordIdOf([{ provider: 'oauth_discord', providerUserId: 'not-a-snowflake' }])).toBeNull()
    expect(discordIdOf(undefined)).toBeNull()
  })
})

describe('my books', () => {
  it('uses the documented storage key and survives garbage', () => {
    expect(MY_BOOKS_KEY).toBe('beezy_my_books')
    expect(parseMyBooks('["DraftKings","fanduel","fanduel"," "]')).toEqual(['draftkings', 'fanduel'])
    expect(parseMyBooks('not json')).toEqual([])
    expect(parseMyBooks('{"a":1}')).toEqual([])
    expect(parseMyBooks(null)).toEqual([])
  })
  it('empty selection means no filter; otherwise only the chosen books', () => {
    expect(matchesMyBooks('draftkings', [])).toBe(true)
    expect(matchesMyBooks('DraftKings', ['draftkings'])).toBe(true)
    expect(matchesMyBooks('novig', ['draftkings'])).toBe(false)
  })
  it('toggle adds and removes', () => {
    expect(toggleBook([], 'FanDuel')).toEqual(['fanduel'])
    expect(toggleBook(['fanduel', 'betmgm'], 'fanduel')).toEqual(['betmgm'])
  })
})
