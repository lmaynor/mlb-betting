'use client'

import { useState } from 'react'
import Link from 'next/link'

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
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginBottom: '24px' }}>
        {categories.map(c => {
          const on = c === active
          return (
            <button
              key={c}
              onClick={() => setActive(c)}
              className="dell-heading"
              style={{
                fontSize: '11px', letterSpacing: '0.06em', padding: '7px 14px',
                borderRadius: 'var(--radius-pill)', cursor: 'pointer',
                border: on ? '1px solid var(--win-border)' : '1px solid var(--basalt)',
                background: on ? 'var(--win-wash)' : 'var(--graphite)',
                color: on ? 'var(--signal)' : 'var(--silver)',
                transition: 'all var(--dur) var(--ease-out)',
              }}
            >
              {c}
            </button>
          )
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '16px' }}>
        {shown.map(article => (
          <Link key={article.slug} href={`/learn/${article.slug}`} className="card-hover" style={{ display: 'block', padding: '22px', textDecoration: 'none', background: 'var(--graphite)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)' }}>
            <div className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '10px' }}>
              {article.category} · {article.keyword} · {article.readTime} min read
            </div>
            <div className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', marginBottom: '8px', letterSpacing: '-0.01em' }}>{article.title}</div>
            <div className="times" style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55 }}>{article.description}</div>
          </Link>
        ))}
      </div>
    </>
  )
}
