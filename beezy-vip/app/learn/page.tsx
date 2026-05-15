import { getAllArticles } from '@/lib/learn-db'
import { ARTICLE_SPECS }  from '@/lib/article-generator'
import Link               from 'next/link'
import type { Metadata }  from 'next'

export const metadata: Metadata = {
  title:       'Learn — MLB Betting Guides & Strategy',
  description: 'Data-driven guides on NRFI, Kelly criterion, implied probability, strikeout props, and machine learning models for sports betting.',
}

export const revalidate = 3600  // 1 hour

export default async function LearnPage() {
  // Get generated articles from DB, fall back to spec list
  let articles: Array<{
    slug:         string
    title:        string
    meta_desc:    string
    keyword:      string
    generated_at: string
    live:         boolean
  }> = []

  try {
    const dbArticles = await getAllArticles()
    const dbSlugs    = new Set(dbArticles.map(a => a.slug))

    // Live articles (from DB)
    const live = dbArticles.map(a => ({ ...a, live: true }))

    // Upcoming (from spec, not yet generated)
    const upcoming = ARTICLE_SPECS
      .filter(s => !dbSlugs.has(s.slug))
      .map(s => ({
        slug:         s.slug,
        title:        s.title,
        meta_desc:    `Coming soon: ${s.title}`,
        keyword:      s.keyword,
        generated_at: '',
        live:         false,
      }))

    articles = [...live, ...upcoming]
  } catch {
    // Fall back to spec list only
    articles = ARTICLE_SPECS.map(s => ({
      slug:         s.slug,
      title:        s.title,
      meta_desc:    `Guide: ${s.title}`,
      keyword:      s.keyword,
      generated_at: '',
      live:         false,
    }))
  }

  const live     = articles.filter(a => a.live)
  const upcoming = articles.filter(a => !a.live)

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-12">
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Learn</h1>
        <p className="mono text-sm text-muted">
          Data-driven betting education. No gut-feel advice.
        </p>
      </div>

      {live.length > 0 && (
        <>
          <p className="mono text-xs uppercase tracking-widest text-muted mb-4">
            {live.length} Articles
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-12">
            {live.map(article => (
              <Link
                key={article.slug}
                href={`/learn/${article.slug}`}
                className="group block p-5 bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--border-bright)] transition-colors"
              >
                <p className="mono text-xs text-accent uppercase tracking-widest mb-2">
                  {article.keyword}
                </p>
                <h2 className="font-bold text-sm mb-2 group-hover:text-accent transition-colors">
                  {article.title}
                </h2>
                <p className="text-muted text-xs leading-relaxed line-clamp-2">
                  {article.meta_desc}
                </p>
                {article.generated_at && (
                  <p className="mono text-xs text-[var(--border-bright)] mt-3">
                    Updated {new Date(article.generated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </p>
                )}
              </Link>
            ))}
          </div>
        </>
      )}

      {upcoming.length > 0 && (
        <>
          <p className="mono text-xs uppercase tracking-widest text-muted mb-4">
            Coming Soon
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {upcoming.map(article => (
              <div
                key={article.slug}
                className="p-5 bg-[var(--surface)] border border-[var(--border)] opacity-50"
              >
                <p className="mono text-xs text-muted uppercase tracking-widest mb-2">
                  {article.keyword}
                </p>
                <h2 className="font-bold text-sm text-muted">{article.title}</h2>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
