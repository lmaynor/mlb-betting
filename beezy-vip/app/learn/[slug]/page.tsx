import { getArticle, ARTICLES } from '@/lib/articles-static'
import { notFound } from 'next/navigation'
import type { Metadata } from 'next'
import Link from 'next/link'
import { renderMarkdown, plainSummary } from '@/lib/markdown'
import { articleLd, jsonLdString } from '@/lib/seo'

// Where each guide should send a reader next: the live page that does what the article describes.
const RELATED: Record<string, { href: string; label: string }[]> = {
  'what-is-nrfi-betting': [{ href: '/picks/mlb/nrfi', label: 'NRFI picks' }, { href: '/models/nrfi', label: 'NRFI model' }],
  'nrfi-betting-strategy': [{ href: '/picks/mlb/nrfi', label: 'NRFI picks' }, { href: '/models/nrfi', label: 'NRFI model' }],
  'f5-betting-mlb': [{ href: '/picks/mlb/f5', label: 'F5 picks' }, { href: '/models/f5', label: 'F5 model' }],
  'mlb-home-run-props': [{ href: '/picks/mlb/hr', label: 'HR picks' }, { href: '/models/hr', label: 'HR model' }],
  'mlb-strikeout-props-guide': [{ href: '/picks/mlb/k', label: 'Strikeout picks' }, { href: '/models/k', label: 'Strikeout model' }],
  'machine-learning-sports-betting': [{ href: '/models', label: 'Our models' }, { href: '/results', label: 'Results' }],
}

const B = '1px solid var(--color-separator-border)'

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
    <div className="max-w-[780px] mx-auto py-10 px-5">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdString(articleLd(article)) }} />
      <div className="mb-8 pb-6" style={{ borderBottom: B }}>
        <div className="mono text-micro text-text-secondary mb-2.5">
          {article.category}
        </div>
        <h1 className="text-[24px] font-bold text-text-primary leading-[1.3]">{article.title}</h1>
        <p className="mono text-mini text-text-secondary mt-2.5">
          Published <time dateTime={article.datePublished}>{article.datePublished}</time>
          {' '}&middot; Last updated <time dateTime={article.dateModified}>{article.dateModified}</time>
        </p>
      </div>
      <div
        className="article-body"
        dangerouslySetInnerHTML={{ __html: html }}
      />
      <div className="mt-10 pt-5 grid gap-3" style={{ borderTop: B }}>
        {related.length > 0 && (
          <div className="flex gap-4 flex-wrap text-[14px]">
            <span className="text-text-secondary">Try it:</span>
            {related.map(r => <Link className="text-link" key={r.href} href={r.href}>{r.label} &rarr;</Link>)}
          </div>
        )}
        <p className="mono text-mini text-text-secondary leading-[1.6] m-0">
          Educational content, not financial advice. Beezy.FYI results are paper-tracked. 21+. If gambling is a problem, call 1-800-GAMBLER.
        </p>
        <Link className="text-[13px] text-link" href="/learn">&larr; All guides</Link>
      </div>
    </div>
  )
}
