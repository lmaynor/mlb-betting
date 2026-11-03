'use client'

import { useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { bookX, edgePos, edgeScale, splitBooks, type LadderRung } from '@/lib/game-ladder'
import { SHARP_BOOKS, type GameOddsRow } from '@/lib/game-odds'
import { formatOdds, probToAmerican } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { GREEN, SKY } from './game-prices-parts'

export const MARKET = '#9ca3af'
export const sp = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`
const SHARP_SET: ReadonlySet<string> = new Set(SHARP_BOOKS)

/**
 * ONE grid for every row: the left and right columns are fixed widths (they were `auto`, so each row sized its own track and the
 * fair line landed at a different x on every row). The two reference lines are drawn once, over the middle column of the whole
 * list, so they are continuous and cannot drift.
 */
export const COLS = 'grid-cols-[var(--ml)_minmax(0,1fr)_var(--mr)]'
export const VARS = '[--ml:104px] [--mr:96px] sm:[--ml:132px] sm:[--mr:172px]'

export const fairText = (p: number) => `${formatOdds(probToAmerican(p))} (${(p * 100).toFixed(1)}%)`

/**
 * Every book on one rung, as flat bars from the market fair price: a bar to the right is a book that beats fair (green), to the left
 * one that does not (grey), so FURTHER RIGHT = BETTER PRICE. The solid line is the market fair price (a market fact), the dashed blue
 * line our model's fair price (UNVALIDATED); both are labelled above. Without a market fair value the bars show the payout instead.
 * Each row also shows the price on the active side, the other side's price, and that price's EV against the market fair value.
 */
export function BookMatrix({ rung, row, side, sideLabel, otherLabel }: { rung: LadderRung; row: GameOddsRow; side: 'a' | 'b'; sideLabel: string; otherLabel: string }) {
  const [all, setAll] = useState(false)
  const sorted = rung.dots.slice().sort((x, y) => y.decimal - x.decimal || x.book.localeCompare(y.book))
  const { shown, rest } = splitBooks(sorted, SHARP_SET, 5)
  const rows = all ? sorted : shown
  if (sorted.length === 0) return <div className="text-compact text-text-secondary py-2">No book has a price on this line.</div>

  const pM = rung.pMarket
  const hasFair = pM != null && pM > 0 && pM < 1
  const evOf = (decimal: number) => (hasFair ? pM! * decimal - 1 : 0)
  // the model's fair price (1 / pModel) as an EV against the market fair price: books to its right are +EV by our model
  const modelEv = hasFair && rung.pModel != null && rung.pModel > 0 ? pM! / rung.pModel - 1 : null
  const scale = edgeScale([...sorted.map(d => evOf(d.decimal)), ...(modelEv != null ? [modelEv] : [])])
  const lo = Math.min(...sorted.map(r => r.decimal))
  const hi = Math.max(...sorted.map(r => r.decimal))
  /** 0..1 along the track for a book: against fair when there is a fair value, else by payout */
  const posOf = (decimal: number) => (hasFair ? edgePos(evOf(decimal), scale) : bookX(decimal, lo, hi))
  const pc = (x: number) => `${(x * 100).toFixed(2)}%`
  const other = (book: string) => { const q = row.books.find(b => b.book === book); const v = q ? (side === 'a' ? q.b : q.a) : null; return v == null ? null : v }

  return (
    <div data-testid="book-matrix" className={`py-3 px-4 ${VARS}`}>
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-0.5 mb-1.5 text-[12px]" data-testid="matrix-legend">
        {hasFair && (
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px solid ${MARKET}` }} /><span className="text-text-secondary">Market fair</span><span className="mono text-text-primary">{fairText(pM!)}</span></span>
        )}
        {rung.pModel != null && hasFair && (
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px dashed ${SKY}` }} /><span style={{ color: SKY }}>Model fair</span><span className="text-text-tertiary">(unvalidated)</span><span className="mono text-text-primary">{fairText(rung.pModel)}</span></span>
        )}
      </div>
      <div className={`grid ${COLS} gap-x-3 mb-1 text-[11px] text-text-tertiary`}>
        <span>Book</span>
        <span className="flex justify-between"><span className="hidden sm:inline">&larr; worse price</span>{hasFair && <span className="text-text-secondary mx-auto sm:mx-0">fair</span>}<span className="hidden sm:inline">better price &rarr;</span></span>
        <span className="text-right">{sideLabel}<span className="hidden sm:inline"> / {otherLabel}</span></span>
      </div>

      <div className="relative">
        {hasFair && (
          <div aria-hidden className={`pointer-events-none absolute inset-y-0 grid ${COLS} gap-x-3 w-full`}>
            <span />
            <span className="relative block h-full" data-testid="matrix-lines">
              <span className="absolute inset-y-0 w-0" data-testid="matrix-fair-line" style={{ left: '50%', borderLeft: `1.5px solid ${MARKET}` }} />
              {modelEv != null && <span className="absolute inset-y-0 w-0" data-testid="matrix-model-line" style={{ left: pc(edgePos(modelEv, scale)), borderLeft: `1.5px dashed ${SKY}` }} />}
            </span>
            <span />
          </div>
        )}
        {rows.map(d => {
          const meta = bookDisplay(d.book)
          const sharp = SHARP_SET.has(d.book.toLowerCase())
          const ev = hasFair ? evOf(d.decimal) : null
          const opp = other(d.book)
          const x = posOf(d.decimal)
          const beats = ev != null && ev > 0
          const clipped = hasFair && Math.abs(ev ?? 0) > scale
          const left = hasFair ? Math.min(0.5, x) : 0.04
          const right = hasFair ? Math.max(0.5, x) : x
          return (
            <div key={d.book} data-testid="matrix-row" data-book={d.book} data-x={x} className={`relative grid ${COLS} items-center gap-x-3 h-[32px] border-t border-t-[color:var(--color-separator-border)]`}>
              <span className="flex items-center gap-1.5 min-w-0">
                <BookLogo book={d.book} size={20} />
                <span className="text-compact font-medium overflow-hidden text-ellipsis whitespace-nowrap" style={{ color: d.offshore ? 'var(--color-text-secondary)' : 'var(--color-text-primary)' }}>{meta.label}</span>
                {sharp && <span className="mono text-[10px] font-semibold tracking-fine text-text-secondary border border-[color:var(--color-separator-border)] rounded-chip px-1">SHARP</span>}
                {d.best && <span className="mono text-[10px] font-semibold tracking-fine rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>BEST</span>}
              </span>
              <span className="relative block h-full">
                <span className="absolute top-1/2 -mt-[6px] h-3 rounded-[3px]" data-testid="matrix-bar"
                  style={{ left: pc(left), width: pc(Math.max(0.006, right - left)), background: beats ? GREEN : '#7d8596', opacity: d.best ? 1 : beats ? 0.7 : 0.4 }} />
                {clipped && <span className="absolute top-1/2 -mt-[7px] text-[11px] leading-none" style={{ left: ev! > 0 ? 'calc(100% - 10px)' : '0', color: '#facc15' }}>{ev! > 0 ? '▶' : '◀'}</span>}
              </span>
              <span className="mono text-right text-compact whitespace-nowrap">
                <b className="font-semibold" style={{ color: d.best ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(d.american)}</b>
                <span className="text-text-tertiary hidden sm:inline"> / {opp != null ? formatOdds(opp) : '--'}</span>
                {ev != null && <span className="ml-1.5 text-[11px] font-semibold rounded-chip px-1" style={{ color: beats ? GREEN : 'var(--color-text-tertiary)', background: beats ? 'rgba(87,242,135,0.08)' : 'transparent' }}>{sp(ev)}</span>}
              </span>
            </div>
          )
        })}
      </div>
      {rest.length > 0 && (
        <button type="button" onClick={() => setAll(a => !a)} aria-expanded={all} data-testid="matrix-more"
          className="mt-1.5 w-full text-left text-[12px] text-text-secondary hover:text-text-primary cursor-pointer bg-transparent border-0 border-t border-t-[color:var(--color-separator-border)] pt-2">
          {all ? 'Show fewer books' : `Show ${rest.length} more ${rest.length === 1 ? 'book' : 'books'}`}
          {!all && <span className="mono text-text-tertiary"> &middot; {formatOdds(rest[0].american)} to {formatOdds(rest[rest.length - 1].american)}</span>}
        </button>
      )}
    </div>
  )
}
