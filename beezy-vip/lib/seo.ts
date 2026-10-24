import type { Article } from '@/lib/articles-static'
import { plainSummary } from '@/lib/markdown'
import { DISCORD_URL, X_URL } from '@/lib/links'
import { PRE_LAUNCH } from '@/lib/launch'
import { PLANS } from '@/lib/plans'

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
  description:
    'beezy.fyi is a quantitative sports betting analytics site: a +EV odds scanner, player prop projections and a public results ledger for NFL, NBA, NHL and MLB. Not affiliated with PicksBeezy, Breezy HR or beezy.net.',
  disambiguatingDescription: 'Independent sports betting analytics publication at beezy.fyi.',
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

/**
 * Pricing as schema.org offers, built from PLANS so the numbers cannot drift from the page. Pre-launch the plans are
 * not purchasable yet, so availability is PreOrder rather than InStock.
 */
export function pricingLd() {
  const availability = `https://schema.org/${PRE_LAUNCH ? 'PreOrder' : 'InStock'}`
  const offer = (name: string, price: number) => ({
    '@type': 'Offer', name, price: price.toFixed(2), priceCurrency: 'USD', availability, url: `${SITE_URL}/pricing`,
  })
  return {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'beezy.fyi',
    applicationCategory: 'SportsApplication',
    operatingSystem: 'Web',
    url: SITE_URL,
    publisher: { '@id': `${SITE_URL}/#organization` },
    offers: {
      '@type': 'AggregateOffer',
      priceCurrency: 'USD',
      lowPrice: PLANS.single.price.toFixed(2),
      highPrice: PLANS.annual.price.toFixed(2),
      offerCount: 3,
      offers: [
        offer('Single-Sport Pass (per month)', PLANS.single.price),
        offer('All-Access Pro (per month)', PLANS.monthly.price),
        offer('All-Access Pro Annual (per year)', PLANS.annual.price),
      ],
    },
  }
}
