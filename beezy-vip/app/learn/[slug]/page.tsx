import { getArticle, ARTICLES } from '@/lib/articles-static'
import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import Link from 'next/link'
import { renderMarkdown, plainSummary } from '@/lib/markdown'
import { articleLd, jsonLdString } from '@/lib/seo'

// Where each guide should send a reader next: the free tool or live page that does what the article describes.
const RELATED: Record<string, { href: string; label: string }[]> = {
  'kelly-criterion-sports-betting': [{ href: '/tools/kelly-calculator', label: 'Kelly Calculator' }],
  'implied-probability-calculator': [{ href: '/tools/odds-calculator', label: 'Odds & Vig Calculator' }],
  'how-to-remove-vig-odds': [{ href: '/tools/odds-calculator', label: 'Odds & Vig Calculator' }],
  'odds-boost-ev': [{ href: '/tools/boost-ev', label: 'Boost EV Calculator' }],
  'closing-line-value-clv': [{ href: '/tools/clv-tracker', label: 'CLV Tracker' }],
  'line-movement-sports-betting': [{ href: '/tools/clv-tracker', label: 'CLV Tracker' }],
  'what-is-nrfi-betting': [{ href: '/picks/mlb/nrfi', label: 'NRFI picks' }, { href: '/models/nrfi', label: 'NRFI model' }],
  'nrfi-betting-strategy': [{ href: '/picks/mlb/nrfi', label: 'NRFI picks' }, { href: '/models/nrfi', label: 'NRFI model' }],
  'f5-betting-mlb': [{ href: '/picks/mlb/f5', label: 'F5 picks' }, { href: '/models/f5', label: 'F5 model' }],
  'mlb-home-run-props': [{ href: '/picks/mlb/hr', label: 'HR picks' }, { href: '/models/hr', label: 'HR model' }],
  'mlb-strikeout-props-guide': [{ href: '/picks/mlb/k', label: 'Strikeout picks' }, { href: '/models/k', label: 'Strikeout model' }],
  'machine-learning-sports-betting': [{ href: '/models', label: 'Our models' }, { href: '/results', label: 'Results' }],
}

const B = '1px solid var(--basalt)'

export function generateStaticParams() {
  return ARTICLES.map(a => ({ slug: a.slug }))
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params
  const article = getArticle(slug)
  if (!article) return {}
  return {
    title: article.title,
    description: plainSummary(article.description),
    alternates: { canonical: `/learn/${article.slug}` },
    openGraph: { type: 'article', publishedTime: article.datePublished, modifiedTime: article.dateModified },
  }
}

export default async function ArticlePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params
  const article = getArticle(slug)
  if (!article) notFound()

  // The body starts with its own "# Title"; the page header already shows it, so drop it (one h1 per page).
  const html = renderMarkdown(article.content.replace(/^#\s+.*\n+/, ''))
  const related = RELATED[article.slug] ?? []

  return (
    <div style={{ maxWidth: '780px', margin: '0 auto', padding: '40px 20px' }}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdString(articleLd(article)) }} />
      <div style={{ marginBottom: '32px', paddingBottom: '24px', borderBottom: B }}>
        <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', marginBottom: '10px' }}>
          {article.category}
        </div>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: 'var(--ash)', lineHeight: 1.3 }}>{article.title}</h1>
        <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', marginTop: '10px' }}>
          Published <time dateTime={article.datePublished}>{article.datePublished}</time>
          {' '}&middot; Last updated <time dateTime={article.dateModified}>{article.dateModified}</time>
        </p>
      </div>
      <div
        className="article-body"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <div style={{ marginTop: '40px', paddingTop: '20px', borderTop: B, display: 'grid', gap: '12px' }}>
        {related.length > 0 && (
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13.5px' }}>
            <span style={{ color: 'var(--fog)' }}>Try it:</span>
            {related.map(r => <Link key={r.href} href={r.href} style={{ color: 'var(--link)' }}>{r.label} &rarr;</Link>)}
          </div>
        )}
        <p className="mono" style={{ fontSize: '11px', color: 'var(--fog)', lineHeight: 1.6, margin: 0 }}>
          Educational content, not financial advice. Beezy.FYI results are paper-tracked. 21+. If gambling is a problem, call 1-800-GAMBLER.
        </p>
        <Link href="/learn" style={{ fontSize: '12.5px', color: 'var(--link)' }}>&larr; All guides</Link>
      </div>
    </div>
  )
}
