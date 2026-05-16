import { getAllArticles } from '@/lib/learn-db'
import { ARTICLE_SPECS }  from '@/lib/article-generator'
import Link               from 'next/link'
import type { Metadata }  from 'next'

export const dynamic  = 'force-dynamic'
export const revalidate = 3600

export const metadata: Metadata = {
  title:       'Learn — MLB Betting Guides & Strategy',
  description: 'Data-driven guides on NRFI, Kelly criterion, implied probability, strikeout props, and machine learning models for sports betting.',
}

const B = '0.5px solid #1f1f24'

export default async function LearnPage() {
  let articles: Array<{ slug: string; title: string; meta_desc: string; keyword: string; generated_at: string; live: boolean }> = []

  try {
    const dbArticles = await getAllArticles()
    const dbSlugs    = new Set(dbArticles.map(a => a.slug))
    const live       = dbArticles.map(a => ({ ...a, live: true }))
    const upcoming   = ARTICLE_SPECS.filter(s => !dbSlugs.has(s.slug)).map(s => ({
      slug: s.slug, title: s.title, meta_desc: `Coming soon: ${s.title}`, keyword: s.keyword, generated_at: '', live: false,
    }))
    articles = [...live, ...upcoming]
  } catch {
    articles = ARTICLE_SPECS.map(s => ({ slug: s.slug, title: s.title, meta_desc: `Guide: ${s.title}`, keyword: s.keyword, generated_at: '', live: false }))
  }

  const live     = articles.filter(a => a.live)
  const upcoming = articles.filter(a => !a.live)

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Learn</h1>
        <p className="mono" style={{ fontSize: '13px', color: '#71717a' }}>Data-driven betting education. No gut-feel advice.</p>
      </div>

      {live.length > 0 && (
        <div style={{ marginBottom: '32px' }}>
          <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '12px' }}>{live.length} Articles</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '0', border: B }}>
            {live.map((article, i) => (
              <Link key={article.slug} href={`/learn/${article.slug}`} style={{ display: 'block', padding: '18px', textDecoration: 'none', background: '#0a0a0c', borderRight: i % 2 === 0 ? B : undefined, borderBottom: i < live.length - 2 ? B : undefined }}>
                <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>{article.keyword}</div>
                <div style={{ fontSize: '13px', fontWeight: 600, color: '#f5f5f7', marginBottom: '4px' }}>{article.title}</div>
                <div style={{ fontSize: '12px', color: '#71717a', lineHeight: 1.5 }}>{article.meta_desc}</div>
                {article.generated_at && (
                  <div className="mono" style={{ fontSize: '10px', color: '#2a2a31', marginTop: '8px' }}>
                    Updated {new Date(article.generated_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </div>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}

      {upcoming.length > 0 && (
        <div>
          <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '12px' }}>Coming Soon</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '0', border: B }}>
            {upcoming.map((article, i) => (
              <div key={article.slug} style={{ padding: '16px', opacity: 0.4, background: '#0a0a0c', borderRight: i % 2 === 0 ? B : undefined, borderBottom: i < upcoming.length - 2 ? B : undefined }}>
                <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '4px' }}>{article.keyword}</div>
                <div style={{ fontSize: '12px', fontWeight: 600, color: '#71717a' }}>{article.title}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
