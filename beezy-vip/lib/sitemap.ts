import { ARTICLES } from '@/lib/articles-static'
import { PICK_SYSTEMS } from '@/lib/pick-systems'
import { SPORT_SLUGS } from '@/lib/sports'
import { SITE_URL } from '@/lib/seo'

export interface SitemapEntry {
  path: string
  /** ISO date. Only set when the page has a real modification date; live data pages omit it rather than claim "now". */
  lastmod?: string
}

const STATIC_PATHS = [
  '/', '/picks', '/picks/games', '/picks/history', '/ev', '/edge', '/analytics', '/cheat-sheet', '/pricing', '/about', '/compare/oddsjam-alternative',
  '/tools', '/tools/boost-ev', '/tools/odds-calculator', '/tools/kelly-calculator', '/results', '/models', '/learn',
]

export function sitemapEntries(): SitemapEntry[] {
  // Pick-system and model pages come from the registry so a new system is listed automatically.
  const sportPaths = SPORT_SLUGS.flatMap(sport => [`/picks/${sport}`, `/picks/${sport}/projections`, `/picks/${sport}/systems`])
  const systemPaths = PICK_SYSTEMS.map(s => `/picks/${s.sport}/${s.slug}`)
  const learn: SitemapEntry[] = ARTICLES.map(a => ({ path: `/learn/${a.slug}`, lastmod: a.dateModified }))
  // The /learn index changes when any article does.
  const learnIndex = ARTICLES.map(a => a.dateModified).sort().pop()
  return [
    ...[...STATIC_PATHS, ...sportPaths, ...systemPaths].map((path): SitemapEntry => (path === '/learn' ? { path, lastmod: learnIndex } : { path })),
    ...learn,
  ]
}

/** changefreq and priority are omitted on purpose: major crawlers ignore them, and lastmod is only worth sending when true. */
export function buildSitemapXml(entries: SitemapEntry[] = sitemapEntries()): string {
  const urls = entries.map(e => `  <url>\n    <loc>${SITE_URL}${e.path}</loc>${e.lastmod ? `\n    <lastmod>${e.lastmod}</lastmod>` : ''}\n  </url>`)
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls.join('\n')}\n</urlset>`
}
