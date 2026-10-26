'use client'

import { useState } from 'react'
import { bookDisplay, canonBook } from '@/lib/tokens'

/** Book logo from /logos/books/<key>.png; falls back to the colored short-code chip when no PNG is shipped for that book. */
export function BookLogo({ book, size, style }: { book: string; size: number; style?: React.CSSProperties }) {
  const [missing, setMissing] = useState(false)
  const meta = bookDisplay(book)
  if (missing) {
    return <span style={{ fontSize: '10.5px', fontWeight: 700, color: meta.color, background: meta.bg, border: `1px solid ${meta.color}55`, borderRadius: '6px', padding: '1px 6px', ...style }}>{meta.short}</span>
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={`/logos/books/${canonBook(book)}.png`} alt={meta.label} title={meta.label}
      width={size} height={size} loading="lazy" onError={() => setMissing(true)}
      style={{ width: size, height: size, objectFit: 'contain', borderRadius: '4px', verticalAlign: 'middle', ...style }}
    />
  )
}
