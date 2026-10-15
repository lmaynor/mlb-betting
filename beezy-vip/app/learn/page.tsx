import { ARTICLES } from '@/lib/articles-static'
import { plainSummary } from '@/lib/markdown'
import { LearnGrid, type LearnCard } from '@/components/learn/learn-grid'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Learn',
  description: 'Guides on odds and vig, Kelly sizing, closing line value, and MLB markets like NRFI, F5, strikeouts and home runs.',
}

// ~200 wpm reading speed, min 1 minute.
function readTime(content: string): number {
  const words = content.trim().split(/\s+/).filter(Boolean).length
  return Math.max(1, Math.round(words / 200))
}

export default function LearnPage() {
  const cards: LearnCard[] = ARTICLES.map(a => ({
    slug: a.slug,
    title: a.title,
    category: a.category,
    keyword: a.keyword,
    description: plainSummary(a.description, 140),
    readTime: readTime(a.content),
  }))

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 className="dell-display" style={{ fontSize: '32px', color: 'var(--chalk)', marginBottom: '8px' }}>Learn</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>Data-driven betting education. No gut-feel advice.</p>
      </div>

      <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: 'var(--fog)', marginBottom: '16px' }}>
        {ARTICLES.length} Articles
      </div>

      <LearnGrid articles={cards} />
    </div>
  )
}
