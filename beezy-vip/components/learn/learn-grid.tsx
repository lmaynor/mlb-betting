'use client'

import { useState } from 'react'
import Link from 'next/link'
import { cx } from '@/utils/cx'

export interface LearnCard {
  slug: string
  title: string
  category: string
  keyword: string
  description: string
  readTime: number
}

export function LearnGrid({ articles }: { articles: LearnCard[] }) {
  const categories = ['All', ...Array.from(new Set(articles.map(a => a.category)))]
  const [active, setActive] = useState('All')
  const shown = active === 'All' ? articles : articles.filter(a => a.category === active)

  return (
    <>
      {/* Category filter */}
      <div className="mb-6 flex flex-wrap gap-2">
        {categories.map(c => {
          const on = c === active
          return (
            <button
              key={c}
              onClick={() => setActive(c)}
              className={cx('ui-heading button-press-motion cursor-pointer rounded-full border px-3.5 py-[7px] text-mini ',
                on ? 'border-[var(--win-border)] bg-[var(--win-wash)] text-signal' : 'border-separator-border bg-background-primary-default text-text-secondary hover:bg-background-primary-hover')}
            >
              {c}
            </button>
          )
        })}
      </div>

      <div className="grid gap-4 [grid-template-columns:repeat(auto-fill,minmax(300px,1fr))]">
        {shown.map(article => (
          <Link key={article.slug} href={`/learn/${article.slug}`} className="card-hover block rounded-xl border border-separator-border bg-background-primary-default p-5.5 no-underline shadow-card">
            <div className="ui-heading mb-2.5 text-text-secondary">
              {article.category} · {article.keyword} · {article.readTime} min read
            </div>
            <div className="ui-display mb-2 text-[17px] tracking-[-0.01em]">{article.title}</div>
            <div className="times text-body-2-regular text-text-secondary">{article.description}</div>
          </Link>
        ))}
      </div>
    </>
  )
}
