'use client'

import { useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { bookX, edgePos, edgeScale, LADDER_SUSPECT_EV, type LadderDot, type LadderRung } from '@/lib/game-ladder'
import { SHARP_BOOKS, type GameOddsRow } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { BookMatrix, COLS, fairText, MARKET, sp, VARS } from './book-matrix'
import { GREEN, SKY } from './game-prices-parts'

const SHARP = new Set(SHARP_BOOKS)
const pc = (x: number) => `${(x * 100).toFixed(2)}%`

/**
 * The selected line, short: the three best prices you can bet, any sharp reference book, and one summary row for everyone else
 * ("11 other books, -4.6% to -0.6%"). Bars run from the market fair price (the solid line): green to the right beats it, grey to the
 * left does not, so further right is a better price. The dashed blue line is our model's fair price (UNVALIDATED). "Every book" opens
 * the full list.
 */
export function BestVsRest({ rung, row, side, sideLabel, otherLabel }: { rung: LadderRung; row: GameOddsRow; side: 'a' | 'b'; sideLabel: string; otherLabel: string }) {
  const [full, setFull] = useState(false)
  const sorted = rung.dots.slice().sort((x, y) => y.decimal - x.decimal || x.book.localeCompare(y.book))
  if (sorted.length === 0) return <div className="text-compact text-text-secondary py-2">No book has a price on this line.</div>

  const bettable = sorted.filter(d => !d.offshore)
  const top = bettable.slice(0, 3)
  const topSet = new Set(top.map(d => d.book))
  const sharp = sorted.filter(d => SHARP.has(d.book.toLowerCase()) && !topSet.has(d.book))
  const shownSet = new Set([...top, ...sharp].map(d => d.book))
  const rest = sorted.filter(d => !shownSet.has(d.book))

  const pM = rung.pMarket
  const hasFair = pM != null && pM > 0 && pM < 1
  const evOf = (decimal: number) => (hasFair ? pM! * decimal - 1 : 0)
  const modelEv = hasFair && rung.pModel != null && rung.pModel > 0 ? pM! / rung.pModel - 1 : null
  const scale = edgeScale([...sorted.map(d => evOf(d.decimal)), ...(modelEv != null ? [modelEv] : [])])
  const lo = Math.min(...sorted.map(d => d.decimal))
  const hi = Math.max(...sorted.map(d => d.decimal))
  const posOf = (decimal: number) => (hasFair ? edgePos(evOf(decimal), scale) : bookX(decimal, lo, hi))
  const other = (book: string) => { const q = row.books.find(b => b.book === book); const v = q ? (side === 'a' ? q.b : q.a) : null; return v == null ? null : v }

  const Row = ({ d, muted }: { d: LadderDot; muted?: boolean }) => {
    const ev = hasFair ? evOf(d.decimal) : null
    const x = posOf(d.decimal)
    const beats = ev != null && ev > 0
    const left = hasFair ? Math.min(0.5, x) : 0.04
    const right = hasFair ? Math.max(0.5, x) : x
    const opp = other(d.book)
    return (
      <div data-testid="bvr-row" data-book={d.book} data-x={x} className={`relative grid ${COLS} items-center gap-x-3 h-[32px]`}>
        <span className="flex items-center gap-1.5 min-w-0">
          <BookLogo book={d.book} size={20} />
          <span className="text-compact overflow-hidden text-ellipsis whitespace-nowrap" style={{ fontWeight: d.best ? 600 : 500, color: muted ? 'var(--color-text-secondary)' : 'var(--color-text-primary)' }}>{bookDisplay(d.book).label}</span>
          {d.best && <span className="mono text-[10px] font-semibold rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>BEST</span>}
          {muted && <span className="mono text-[10px] font-semibold text-text-secondary border border-[color:var(--color-separator-border)] rounded-chip px-1">Sharp</span>}
        </span>
        <span className="relative block h-full">
          <span className="absolute top-1/2 -mt-[4px] h-2 rounded-[2px]" data-testid="bvr-bar" style={{ left: pc(left), width: pc(Math.max(0.006, right - left)), background: ev != null && ev > LADDER_SUSPECT_EV ? '#facc15' : beats ? GREEN : '#7d8596', opacity: d.best ? 0.95 : beats ? 0.55 : 0.3 }} />
        </span>
        <span className="mono text-right text-compact whitespace-nowrap">
          <b className="font-semibold" style={{ color: d.best ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(d.american)}</b>
          <span className="text-text-tertiary hidden sm:inline"> / {opp != null ? formatOdds(opp) : '--'}</span>
          {ev != null && <span className="ml-1.5 text-[11px] font-semibold rounded-chip px-1" style={{ color: beats ? GREEN : 'var(--color-text-tertiary)', background: beats ? 'rgba(87,242,135,0.08)' : 'transparent' }}>{sp(ev)}</span>}
        </span>
      </div>
    )
  }

  const restEvs = rest.map(d => evOf(d.decimal))
  const restLo = Math.min(...restEvs), restHi = Math.max(...restEvs)

  return (
    <div data-testid="best-vs-rest" className={`py-3 px-4 ${VARS}`}>
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-0.5 mb-1.5 text-[12px]" data-testid="matrix-legend">
        {hasFair && <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px solid ${MARKET}` }} /><span className="text-text-secondary">Market fair</span><span className="mono text-text-primary">{fairText(pM!)}</span></span>}
        {hasFair && rung.pModel != null && <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px dashed ${SKY}` }} /><span style={{ color: SKY }}>Model fair</span><span className="text-text-tertiary">(unvalidated)</span><span className="mono text-text-primary">{fairText(rung.pModel)}</span></span>}
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
            <span className="relative block h-full">
              <span className="absolute inset-y-0 w-0" data-testid="matrix-fair-line" style={{ left: '50%', borderLeft: `1.5px solid ${MARKET}` }} />
              {modelEv != null && <span className="absolute inset-y-0 w-0" data-testid="matrix-model-line" style={{ left: pc(edgePos(modelEv, scale)), borderLeft: `1.5px dashed ${SKY}` }} />}
            </span>
            <span />
          </div>
        )}
        {top.map(d => <Row key={d.book} d={d} />)}
        {sharp.map(d => <Row key={d.book} d={d} muted />)}
        {rest.length > 0 && (
          <div data-testid="bvr-rest" className={`relative grid ${COLS} items-center gap-x-3 h-[32px] border-t border-t-[color:var(--color-separator-border)]`}>
            <span className="text-compact text-text-secondary">{rest.length} other {rest.length === 1 ? 'book' : 'books'}</span>
            <span />
            <span className="mono text-right text-compact text-text-secondary whitespace-nowrap">
              {hasFair ? `${sp(restLo)} to ${sp(restHi)}` : `${formatOdds(rest[0].american)} to ${formatOdds(rest[rest.length - 1].american)}`}
            </span>
          </div>
        )}
      </div>
      {sorted.length > top.length + sharp.length && (
        <button type="button" onClick={() => setFull(f => !f)} aria-expanded={full} data-testid="bvr-full"
          className="mt-1.5 w-full text-left text-[12px] text-text-secondary hover:text-text-primary cursor-pointer bg-transparent border-0 border-t border-t-[color:var(--color-separator-border)] pt-2">
          {full ? 'Hide the full list' : `Every book (${sorted.length})`}
        </button>
      )}
      {full && <div className="-mx-4 mt-1"><BookMatrix rung={rung} row={row} side={side} sideLabel={sideLabel} otherLabel={otherLabel} /></div>}
    </div>
  )
}
