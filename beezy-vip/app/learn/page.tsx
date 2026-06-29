import { ARTICLES } from '@/lib/articles-static'
import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Learn — MLB Betting Guides & Strategy',
  description: 'Data-driven guides on NRFI, Kelly criterion, implied probability, strikeout props, and machine learning models for sports betting.',
}

const B = '1px solid var(--basalt)'
const CATEGORIES = ['All', 'Fundamentals', 'Theory', 'Markets', 'Strategy']

export default function LearnPage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: 'var(--ash)', marginBottom: '6px' }}>Learn</h1>
        <p className="mono" style={{ fontSize: '13px', color: 'var(--fog)' }}>Data-driven betting education. No gut-feel advice.</p>
      </div>

      <div className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', marginBottom: '12px' }}>
        {ARTICLES.length} Articles
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: '0', border: B }}>
        {ARTICLES.map((article, i) => (
          <Link key={article.slug} href={`/learn/${article.slug}`} style={{ display: 'block', padding: '20px', textDecoration: 'none', background: 'var(--carbon)', borderRight: i % 2 === 0 ? B : undefined, borderBottom: i < ARTICLES.length - 2 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', marginBottom: '6px' }}>{article.category} · {article.keyword}</div>
            <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--ash)', marginBottom: '6px' }}>{article.title}</div>
            <div style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: 1.5 }}>{article.description}</div>
          </Link>
        ))}
      </div>
    </div>
  )
}
