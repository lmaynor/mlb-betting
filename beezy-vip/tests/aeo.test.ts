import { readFileSync } from 'fs'
import { join } from 'path'
import { ARTICLES } from '../lib/articles-static'
import { articleLd, asOfLabel, jsonLdString, ORGANIZATION_LD } from '../lib/seo'
import { buildSitemapXml, sitemapEntries } from '../lib/sitemap'

type Group = { agents: string[]; allow: string[]; disallow: string[] }

/** Minimal RFC 9309 group parser: consecutive User-agent lines share the rules that follow them. */
function parseRobots(txt: string): Group[] {
  const groups: Group[] = []
  let cur: Group | null = null
  let lastWasAgent = false
  for (const raw of txt.split('\n')) {
    const line = raw.replace(/#.*/, '').trim()
    if (!line) continue
    const [k, ...rest] = line.split(':')
    const key = k.trim().toLowerCase()
    const val = rest.join(':').trim()
    if (key === 'user-agent') {
      if (!cur || !lastWasAgent) { cur = { agents: [], allow: [], disallow: [] }; groups.push(cur) }
      cur.agents.push(val.toLowerCase())
      lastWasAgent = true
    } else if (cur && (key === 'allow' || key === 'disallow')) {
      cur[key].push(val)
      lastWasAgent = false
    }
  }
  return groups
}

const robots = parseRobots(readFileSync(join(__dirname, '../public/robots.txt'), 'utf8'))
const groupFor = (ua: string) => robots.find(g => g.agents.includes(ua.toLowerCase()))

describe('robots.txt', () => {
  const AI_BOTS = ['meta-webindexer', 'Meta-ExternalAgent', 'OAI-SearchBot', 'ChatGPT-User', 'PerplexityBot', 'Claude-SearchBot', 'Claude-User', 'ClaudeBot']

  it.each(AI_BOTS)('%s has a group that allows the site', ua => {
    const g = groupFor(ua)
    expect(g).toBeDefined()
    expect(g!.allow).toContain('/')
  })

  it.each(AI_BOTS)('%s keeps every private path disallowed', ua => {
    const g = groupFor(ua)!
    const star = groupFor('*')!
    expect(g.disallow.length).toBeGreaterThan(0)
    expect([...g.disallow].sort()).toEqual([...star.disallow].sort())
    for (const p of ['/api', '/dashboard', '/login', '/signup', '/tools/slate', '/tools/clv-tracker', '/tools/bet-tracker']) {
      expect(g.disallow).toContain(p)
    }
  })

  it('still serves the OG cards and points at the sitemap', () => {
    expect(groupFor('*')!.allow).toContain('/api/og')
    expect(readFileSync(join(__dirname, '../public/robots.txt'), 'utf8')).toContain('Sitemap: https://beezy.fyi/sitemap.xml')
  })
})

describe('sitemap', () => {
  it('never claims a lastmod it cannot back up', () => {
    const entries = sitemapEntries()
    for (const a of ARTICLES) expect(entries.find(e => e.path === `/learn/${a.slug}`)?.lastmod).toBe(a.dateModified)
    expect(entries.find(e => e.path === '/ev')?.lastmod).toBeUndefined()
    const today = new Date().toISOString().slice(0, 10)
    expect(entries.filter(e => e.lastmod === today && !ARTICLES.some(a => a.dateModified === today))).toEqual([])
  })

  it('is stable between calls and valid xml-ish', () => {
    expect(buildSitemapXml()).toBe(buildSitemapXml())
    expect(buildSitemapXml()).toContain('<loc>https://beezy.fyi/learn/f5-betting-mlb</loc>')
    expect(buildSitemapXml()).not.toContain('changefreq')
  })
})

describe('article dates and schema', () => {
  it.each(ARTICLES.map(a => [a.slug, a] as const))('%s has ordered ISO dates', (_s, a) => {
    expect(a.datePublished).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(a.dateModified).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(a.dateModified >= a.datePublished).toBe(true)
  })

  it('emits matching Article JSON-LD', () => {
    const a = ARTICLES[0]
    const ld = articleLd(a)
    expect(ld['@type']).toBe('Article')
    expect(ld.datePublished).toBe(a.datePublished)
    expect(ld.dateModified).toBe(a.dateModified)
    expect(ld.url).toBe(`https://beezy.fyi/learn/${a.slug}`)
  })

  it('cannot break out of its script tag', () => {
    const s = jsonLdString({ x: '</script><b>' })
    expect(s).not.toContain('<')
    expect(JSON.parse(s).x).toBe('</script><b>')
  })

  it('organization schema links its profiles', () => {
    expect(ORGANIZATION_LD.sameAs.length).toBeGreaterThan(0)
  })
})

describe('asOfLabel', () => {
  it('uses the Central-time calendar day', () => {
    expect(asOfLabel(new Date('2026-10-20T03:30:00Z'))).toBe('Oct 19, 2026')
    expect(asOfLabel(new Date('2026-10-20T12:00:00Z'))).toBe('Oct 20, 2026')
  })
})
