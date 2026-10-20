import type { Article } from '@/lib/articles-static'
import { plainSummary } from '@/lib/markdown'
import { DISCORD_URL, X_URL } from '@/lib/links'

export const SITE_URL = 'https://beezy.fyi'
export const SITE_NAME = 'beezy.fyi'

/** Serialise JSON-LD for an inline <script>: a literal "<" in a string value could otherwise close the tag early. */
export function jsonLdString(data: unknown): string {
  return JSON.stringify(data).replace(/</g, () => '\\' + 'u003c')
}

export const ORGANIZATION_LD = {
  '@context': 'https://schema.org',
  '@type': 'Organization',
  '@id': `${SITE_URL}/#organization`,
  name: SITE_NAME,
  url: SITE_URL,
  logo: `${SITE_URL}/icon-512.png`,
  sameAs: [X_URL, DISCORD_URL],
}

export const WEBSITE_LD = {
  '@context': 'https://schema.org',
  '@type': 'WebSite',
  '@id': `${SITE_URL}/#website`,
  name: SITE_NAME,
  url: SITE_URL,
  description:
    'Quantitative models, prop research and a live +EV terminal across MLB, NFL, NHL and NBA. Paper-tracked, every result public.',
  publisher: { '@id': `${SITE_URL}/#organization` },
}

/** Article schema for a /learn page. The dates are the same ones the page shows in its byline. */
export function articleLd(a: Article) {
  const url = `${SITE_URL}/learn/${a.slug}`
  return {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: a.title,
    description: plainSummary(a.description),
    datePublished: a.datePublished,
    dateModified: a.dateModified,
    mainEntityOfPage: url,
    url,
    articleSection: a.category,
    author: { '@id': `${SITE_URL}/#organization` },
    publisher: { '@id': `${SITE_URL}/#organization` },
  }
}

/** "Oct 19, 2026" in Central Time, the zone every beezy.fyi record is kept in. Pass `now` for tests. */
export function asOfLabel(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric' }).format(now)
}
