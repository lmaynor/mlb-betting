/**
 * @jest-environment node
 *
 * Header-level anonymous traffic identification: the classifier names traffic only by what it
 * declares in its headers (declared bots by name, library/default automation UAs as unknown,
 * browsers as human), and /api/collect logs exactly one structured line per accepted beacon,
 * honoring Sec-GPC and rejecting anything outside the { path, referrer } vocabulary.
 */
import { NextRequest } from 'next/server'
import { classifyAnonTraffic, type HeaderGetter } from '@/lib/anon-traffic'
import { POST } from '@/app/api/collect/route'

function getter(map: Record<string, string>): HeaderGetter {
  const lower = new Map(Object.entries(map).map(([k, v]) => [k.toLowerCase(), v]))
  return { get: (name: string) => lower.get(name.toLowerCase()) ?? null }
}
const ua = (userAgent: string, extra: Record<string, string> = {}) => getter({ 'user-agent': userAgent, ...extra })

const CHROME_WIN = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
const SAFARI_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15'
const SAFARI_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1'
const CHROME_ANDROID_PHONE = 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36'
const CHROME_ANDROID_TABLET = 'Mozilla/5.0 (Linux; Android 13; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
const FIREFOX_LINUX = 'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0'

describe('classifyAnonTraffic: declared bots are named', () => {
  it.each([
    ['Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)', 'Googlebot'],
    ['Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)', 'Bingbot'],
    ['Mozilla/5.0 AppleWebKit/537.36 GPTBot/1.2', 'GPTBot'],
    ['OAI-SearchBot/1.0', 'OAI-SearchBot'],
    ['Mozilla/5.0 ChatGPT-User/1.0', 'ChatGPT-User'],
    ['ClaudeBot/1.0 (+https://www.anthropic.com/claude-bot)', 'ClaudeBot'],
    ['Mozilla/5.0 Claude-User/1.0', 'Claude-User'],
    ['PerplexityBot/1.0 (+https://perplexity.ai/perplexitybot)', 'PerplexityBot'],
    ['Mozilla/5.0 Perplexity-User/1.0', 'Perplexity-User'],
    ['CCBot/2.0 (https://commoncrawl.org/faq/)', 'CCBot'],
    ['Meta-ExternalAgent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/web-crawlers)', 'Meta-ExternalAgent'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_5) AppleWebKit/605.1.15 Applebot/0.1', 'Applebot'],
  ])('%s -> declared_bot %s', (userAgent, bot) => {
    const out = classifyAnonTraffic(ua(userAgent))
    expect(out.kind).toBe('declared_bot')
    expect(out.bot).toBe(bot)
    expect(out.deviceClass).toBe('bot')
  })

  it.each([
    ['Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)', 'generic_bot'],
    ['Mozilla/5.0 (compatible; Baiduspider/2.0)', 'generic_spider'],
    ['SomeCrawler/1.0', 'generic_crawler'],
  ])('generic self-declared automation %s -> %s', (userAgent, bot) => {
    const out = classifyAnonTraffic(ua(userAgent))
    expect(out.kind).toBe('declared_bot')
    expect(out.bot).toBe(bot)
  })
})

describe('classifyAnonTraffic: browsers are human, with a device class', () => {
  it.each([
    [CHROME_WIN, 'Chrome', 'desktop'],
    [SAFARI_MAC, 'Safari', 'desktop'],
    [FIREFOX_LINUX, 'Firefox', 'desktop'],
    [SAFARI_IPHONE, 'Safari', 'mobile'],
    [CHROME_ANDROID_PHONE, 'Chrome', 'mobile'],
    [CHROME_ANDROID_TABLET, 'Chrome', 'tablet'],
  ])('%s -> human %s %s', (userAgent, family, device) => {
    const out = classifyAnonTraffic(ua(userAgent))
    expect(out.kind).toBe('human')
    expect(out.bot).toBeNull()
    expect(out.uaFamily).toBe(family)
    expect(out.deviceClass).toBe(device)
  })

  it('sec-ch-ua-mobile breaks the tie when the UA carries no device signal', () => {
    const bare = 'Mozilla/5.0 AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    expect(classifyAnonTraffic(ua(bare, { 'sec-ch-ua-mobile': '?1' })).deviceClass).toBe('mobile')
    expect(classifyAnonTraffic(ua(bare, { 'sec-ch-ua-mobile': '?0' })).deviceClass).toBe('desktop')
  })
})

describe('classifyAnonTraffic: undeclared automation is unknown, never named', () => {
  it.each([
    ['python-requests/2.31.0', 'python-requests'],
    ['curl/8.4.0', 'curl'],
    ['axios/1.6.0', 'axios'],
    ['Go-http-client/1.1', 'Go-http-client'],
    ['okhttp/4.12.0', 'okhttp'],
  ])('%s -> unknown_automation (%s)', (userAgent, family) => {
    const out = classifyAnonTraffic(ua(userAgent))
    expect(out.kind).toBe('unknown_automation')
    expect(out.bot).toBeNull()
    expect(out.uaFamily).toBe(family)
  })

  it('a missing or empty user-agent is unknown_automation', () => {
    for (const headers of [getter({}), ua('')]) {
      const out = classifyAnonTraffic(headers)
      expect(out.kind).toBe('unknown_automation')
      expect(out.bot).toBeNull()
      expect(out.uaFamily).toBe('unknown')
      expect(out.deviceClass).toBe('unknown')
    }
  })
})

describe('classifyAnonTraffic: geo and language come from headers, first tag only', () => {
  it('extracts and normalizes country, region, and primary language', () => {
    const out = classifyAnonTraffic(ua(CHROME_WIN, {
      'x-vercel-ip-country': 'us',
      'x-vercel-ip-country-region': 'il',
      'accept-language': 'en-US,en;q=0.9,fr;q=0.8',
    }))
    expect(out.country).toBe('US')
    expect(out.region).toBe('IL')
    expect(out.language).toBe('en-US')
  })

  it('returns nulls when the headers are absent', () => {
    const out = classifyAnonTraffic(ua(CHROME_WIN))
    expect(out.country).toBeNull()
    expect(out.region).toBeNull()
    expect(out.language).toBeNull()
  })
})

describe('POST /api/collect', () => {
  let infoSpy: jest.SpyInstance
  beforeEach(() => {
    infoSpy = jest.spyOn(console, 'info').mockImplementation(() => {})
  })
  afterEach(() => {
    infoSpy.mockRestore()
  })

  function call(body: unknown, headers: Record<string, string> = {}, rawBody?: string) {
    return POST(new NextRequest('http://localhost/api/collect', {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: rawBody ?? JSON.stringify(body),
    }))
  }
  const loggedEvent = () => JSON.parse(infoSpy.mock.calls[0][0] as string) as Record<string, unknown>

  it('logs one structured line for a human page view and returns 204', async () => {
    const res = await call(
      { path: '/pricing', referrer: 'https://www.google.com/search?q=beezy+picks' },
      { 'user-agent': CHROME_WIN, 'x-vercel-ip-country': 'US', 'x-vercel-ip-country-region': 'IL', 'accept-language': 'en-US,en;q=0.9' },
    )
    expect(res.status).toBe(204)
    expect(infoSpy).toHaveBeenCalledTimes(1)
    const event = loggedEvent()
    expect(event).toMatchObject({
      event: 'anon_traffic',
      kind: 'human',
      bot: null,
      uaFamily: 'Chrome',
      deviceClass: 'desktop',
      country: 'US',
      region: 'IL',
      language: 'en-US',
      path: '/pricing',
      referrerHost: 'www.google.com',
    })
    expect(typeof event.ts).toBe('string')
    // Host only: no referrer path/query and no raw user-agent ever reach the log.
    const line = infoSpy.mock.calls[0][0] as string
    expect(line).not.toContain('q=beezy')
    expect(line).not.toContain('Mozilla')
  })

  it('classifies a declared bot UA in the logged event', async () => {
    const res = await call({ path: '/ev', referrer: null }, { 'user-agent': 'ClaudeBot/1.0' })
    expect(res.status).toBe(204)
    expect(loggedEvent()).toMatchObject({ kind: 'declared_bot', bot: 'ClaudeBot', deviceClass: 'bot', referrerHost: null })
  })

  it('drops the event silently when Sec-GPC is 1', async () => {
    const res = await call({ path: '/pricing', referrer: null }, { 'user-agent': CHROME_WIN, 'sec-gpc': '1' })
    expect(res.status).toBe(204)
    expect(infoSpy).not.toHaveBeenCalled()
  })

  it('rejects non-JSON, malformed, and out-of-vocabulary bodies without logging', async () => {
    expect((await call(null, { 'content-type': 'text/plain' }, '{"path":"/x"}')).status).toBe(415)
    expect((await call(null, {}, 'not json')).status).toBe(400)
    expect((await call({ path: 'https://evil.example/x', referrer: null })).status).toBe(400)
    expect((await call({ path: '/x', referrer: null, email: 'a@example.com' })).status).toBe(400)
    expect((await call({ referrer: null })).status).toBe(400)
    expect(infoSpy).not.toHaveBeenCalled()
  })

  it('rejects oversized bodies cheaply', async () => {
    const res = await call({ path: '/x', referrer: `https://example.com/${'a'.repeat(3000)}` })
    expect(res.status).toBe(413)
    expect(infoSpy).not.toHaveBeenCalled()
  })

  it('strips query strings from the collected path', async () => {
    const res = await call({ path: '/ev?token=secret', referrer: null }, { 'user-agent': CHROME_WIN })
    expect(res.status).toBe(204)
    expect(loggedEvent().path).toBe('/ev')
    expect(infoSpy.mock.calls[0][0] as string).not.toContain('secret')
  })
})
