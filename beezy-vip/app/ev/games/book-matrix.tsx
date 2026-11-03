'use client'

import { BookLogo } from '@/components/ui/book-logo'
import { bookX, type LadderRung } from '@/lib/game-ladder'
import { SHARP_BOOKS, type GameOddsRow } from '@/lib/game-odds'
import { formatOdds, probToAmerican } from '@/lib/odds'
import { bookDisplay } from '@/lib/tokens'
import { GREEN, SKY } from './game-prices-parts'

const MARKET = '#9ca3af'
const sp = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

const GRID = { gridTemplateColumns: 'minmax(84px,132px) minmax(0,1fr) auto' } as const

const fairText = (p: number) => `${formatOdds(probToAmerican(p))} (${(p * 100).toFixed(1)}%)`

/**
 * Every book on one rung, side by side, ordered so FURTHER RIGHT = BETTER PRICE (a higher payout). The two vertical markers are the
 * market fair price (solid grey, a market fact) and our model's fair price (dashed blue, UNVALIDATED), both labelled above the rows
 * with their prices. Each row shows the book, the price on the active side, the other side's price, and that price's EV against the
 * market fair value.
 */
export function BookMatrix({ rung, row, side, sideLabel, otherLabel }: { rung: LadderRung; row: GameOddsRow; side: 'a' | 'b'; sideLabel: string; otherLabel: string }) {
  const rows = rung.dots.slice().sort((x, y) => y.decimal - x.decimal || x.book.localeCompare(y.book))
  if (rows.length === 0) return <div className="text-compact text-text-secondary py-2">No book has a price on this line.</div>

  const fairDec = (p: number | null) => (p != null && p > 0 && p < 1 ? 1 / p : null)
  const dMarket = fairDec(rung.pMarket)
  const dModel = fairDec(rung.pModel)
  const all = [...rows.map(r => r.decimal), ...(dMarket ? [dMarket] : []), ...(dModel ? [dModel] : [])]
  const lo = Math.min(...all)
  const hi = Math.max(...all)
  const xs = (d: number) => `${(bookX(d, lo, hi) * 100).toFixed(2)}%`
  const other = (book: string) => { const q = row.books.find(b => b.book === book); const v = q ? (side === 'a' ? q.b : q.a) : null; return v == null ? null : v }

  return (
    <div data-testid="book-matrix" className="py-3 px-4">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-0.5 mb-1.5 text-[11.5px]" data-testid="matrix-legend">
        {rung.pMarket != null && dMarket && (
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px solid ${MARKET}` }} /><span className="text-text-secondary">Market fair</span><span className="mono text-text-primary">{fairText(rung.pMarket)}</span></span>
        )}
        {rung.pModel != null && dModel && (
          <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0" style={{ borderLeft: `2px dashed ${SKY}` }} /><span style={{ color: SKY }}>Model fair</span><span className="text-text-tertiary">(unvalidated)</span><span className="mono text-text-primary">{fairText(rung.pModel)}</span></span>
        )}
      </div>
      <div className="grid gap-x-3 mb-1 text-[10.5px] text-text-tertiary" style={GRID}>
        <span>Book</span><span className="flex justify-between"><span>&larr; worse price</span><span>better price &rarr;</span></span><span className="text-right">{sideLabel}<span className="hidden sm:inline"> / {otherLabel}</span></span>
      </div>
      {rows.map(d => {
        const meta = bookDisplay(d.book)
        const sharp = SHARP_BOOKS.includes(d.book.toLowerCase())
        const ev = rung.pMarket != null ? rung.pMarket * d.decimal - 1 : null
        const opp = other(d.book)
        const beats = dMarket != null && d.decimal > dMarket
        return (
          <div key={d.book} data-testid="matrix-row" data-book={d.book} data-x={bookX(d.decimal, lo, hi)} className="grid items-center gap-x-3 h-[30px] border-t border-t-[color:var(--color-separator-border)]" style={GRID}>
            <span className="flex items-center gap-1.5 min-w-0">
              <BookLogo book={d.book} size={20} />
              <span className="text-compact font-medium overflow-hidden text-ellipsis whitespace-nowrap" style={{ color: d.offshore ? 'var(--color-text-secondary)' : 'var(--color-text-primary)' }}>{meta.label}</span>
              {sharp && <span className="mono text-[9.5px] font-semibold tracking-fine text-text-secondary border border-[color:var(--color-separator-border)] rounded-chip px-1">SHARP</span>}
              {d.best && <span className="mono text-[9.5px] font-semibold tracking-fine rounded-chip px-1" style={{ color: GREEN, background: 'rgba(87,242,135,0.10)' }}>BEST</span>}
            </span>
            <span className="relative block h-full">
              <span className="absolute left-0 right-0 top-1/2 h-px bg-[color:var(--color-separator-border)]" />
              {dMarket && <span className="absolute top-[5px] bottom-[5px]" style={{ left: xs(dMarket), borderLeft: `2px solid ${MARKET}` }} />}
              {dModel && <span className="absolute top-[5px] bottom-[5px]" style={{ left: xs(dModel), borderLeft: `2px dashed ${SKY}` }} />}
              {dMarket && beats && <span className="absolute top-1/2 h-[3px] -mt-px" style={{ left: xs(dMarket), width: `calc(${xs(d.decimal)} - ${xs(dMarket)})`, background: GREEN, opacity: 0.45 }} />}
              <span className="absolute top-1/2 -mt-[5px] -ml-[5px] size-[10px] rounded-full" style={{ left: xs(d.decimal), background: d.best ? GREEN : beats ? GREEN : '#94a3b8', opacity: d.best || beats ? 1 : 0.85 }} />
            </span>
            <span className="mono text-right text-compact whitespace-nowrap">
              <b className="font-semibold" style={{ color: d.best ? GREEN : 'var(--color-text-primary)' }}>{formatOdds(d.american)}</b>
              <span className="text-text-tertiary hidden sm:inline"> / {opp != null ? formatOdds(opp) : '--'}</span>
              {ev != null && <span className="ml-1.5 text-[11px] font-semibold rounded-chip px-1" style={{ color: ev > 0 ? GREEN : 'var(--color-text-tertiary)', background: ev > 0 ? 'rgba(87,242,135,0.08)' : 'transparent' }}>{sp(ev)}</span>}
            </span>
          </div>
        )
      })}
    </div>
  )
}
