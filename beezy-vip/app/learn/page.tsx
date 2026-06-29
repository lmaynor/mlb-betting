import { ARTICLES } from '@/lib/articles-static'
import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Learn — MLB Betting Guides & Strategy',
  description: 'Data-driven guides on NRFI, Kelly criterion, implied probability, strikeout props, and machine learning models for sports betting.',
}

export default function LearnPage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '28px' }}>
        <h1 className="dell-display" style={{ fontSize: '32px', color: 'var(--chalk)', marginBottom: '8px' }}>Learn</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>Data-driven betting education. No gut-feel advice.</p>
      </div>

      <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '16px' }}>
        {ARTICLES.length} Articles
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '16px' }}>
        {ARTICLES.map((article) => (
          <Link key={article.slug} href={`/learn/${article.slug}`} className="card-hover" style={{ display: 'block', padding: '22px', textDecoration: 'none', background: 'var(--graphite)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>{article.category} · {article.keyword}</div>
            <div className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', marginBottom: '8px', letterSpacing: '-0.01em' }}>{article.title}</div>
            <div className="times" style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55 }}>{article.description}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}
