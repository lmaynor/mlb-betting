'use client'

import React from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { LockGlyph } from '@/components/ui/lock-glyph'

export interface BookComparisonPeer {
  book: string
  american: number
  barRatio: number
}

export interface BookComparisonBarsProps {
  heroAmerican: number
  heroBook: string
  locked?: boolean
  peers: BookComparisonPeer[]
  footerLeft?: React.ReactNode
  footerRight?: React.ReactNode
}

/**
 * Payout-bars block: hero row at full width in green with book logo or lock glyph when locked,
 * up to 5 peer rows scaled by each peer's barRatio, footer row with left and right slots.
 */
export function BookComparisonBars({
  heroAmerican,
  heroBook,
  locked,
  peers,
  footerLeft,
  footerRight,
}: BookComparisonBarsProps) {
  return (
    <div className="flex flex-col gap-2 pt-1.5 border-t border-t-[color:var(--color-border-table)]">
      {/* Outlier Book Row */}
      <div className="flex items-center gap-2.5">
        <div className="w-8 flex justify-center shrink-0">
          {locked ? (
            <LockGlyph size={12} color="var(--color-text-secondary)" />
          ) : (
            <BookLogo book={heroBook} size={20} />
          )}
        </div>
        <div className="flex-1 h-2 bg-background-tertiary-default rounded-chip overflow-hidden">
          <div style={{ width: '100%', height: '100%', background: '#57F287', borderRadius: '4px' }} />
        </div>
        <div className="mono" style={{ width: '56px', textAlign: 'right', fontSize: '12px', fontWeight: 600, color: '#57F287', flexShrink: 0 }}>
          {heroAmerican > 0 ? `+${heroAmerican}` : `${heroAmerican}`}
        </div>
      </div>

      {/* Peer Books Rows (Up to 5) */}
      {peers.slice(0, 5).map((peer, idx) => (
        <div key={`${peer.book}-${idx}`} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div className="w-8 flex justify-center shrink-0">
            <BookLogo book={peer.book} size={20} />
          </div>
          <div className="flex-1 h-2 bg-background-tertiary-default rounded-chip overflow-hidden">
            <div style={{ width: `${Math.round(peer.barRatio * 100)}%`, height: '100%', background: '#334155', borderRadius: '4px' }} />
          </div>
          <div className="mono w-14 text-right text-[12px] text-neutral-400 shrink-0">
            {peer.american > 0 ? `+${peer.american}` : `${peer.american}`}
          </div>
        </div>
      ))}

      {/* Footer Summary Line */}
      <div className="flex justify-between items-center mt-1 text-mini text-text-secondary">
        <div>{footerLeft}</div>
        <div className="mono text-[#d1d5db] font-semibold">{footerRight}</div>
      </div>
    </div>
  )
}
