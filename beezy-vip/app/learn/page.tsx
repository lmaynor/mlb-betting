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
    <div className="mx-auto max-w-[1200px] px-6 py-10">
      <div className="mb-6">
        <h1 className="dell-display mb-2 text-[32px]">Learn</h1>
        <p className="times text-headline-regular text-text-secondary">Data-driven betting education. No gut-feel advice.</p>
      </div>

      <div className="dell-heading mb-4 tracking-[0.12em] text-text-secondary">
        {ARTICLES.length} Articles
      </div>

      <LearnGrid articles={cards} />
    </div>
  )
}
