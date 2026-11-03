'use client'

import { BookLogo } from '@/components/ui/book-logo'
import { LADDER_SUSPECT_EV, type LadderRung } from '@/lib/game-ladder'
import { SHARP_BOOKS } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import { bookDisplay, canonBook } from '@/lib/tokens'
import { GREEN } from './game-prices-parts'

export interface HeatRung { line: number | null; label: string; rung: LadderRung; main: boolean }

const SHARP = new Set(SHARP_BOOKS)
const LABEL_W = 132
const CELL_W = 40

/** Book columns for a set of rungs: sharp references first, then the books that quote the most lines. */
export function heatBooks(rungs: readonly { rung: LadderRung }[]): string[] {
  const n = new Map<string, number>()
  for (const { rung } of rungs) for (const d of rung.dots) n.set(d.book, (n.get(d.book) ?? 0) + 1)
  const rank = (b: string) => { const i = SHARP_BOOKS.indexOf(b.toLowerCase()); return i < 0 ? SHARP_BOOKS.length : i }
  return [...n.keys()].sort((a, b) => rank(a) - rank(b) || (n.get(b)! - n.get(a)!) || a.localeCompare(b))
}

/** One cell's fill: green for a price that beats the market fair value (stronger = bigger), grey for one that does not, amber for a suspiciously large one. */
export function heatFill(ev: number): { background: string; color: string; suspect: boolean } {
  if (ev > LADDER_SUSPECT_EV) return { background: 'rgba(250,204,21,0.7)', color: '#422006', suspect: true }
  if (ev > 0) return { background: `rgba(87,242,135,${Math.min(0.9, 0.16 + ev * 9).toFixed(2)})`, color: '#0b2e17', suspect: false }
  return { background: `rgba(125,133,150,${Math.min(0.5, 0.08 + -ev * 7).toFixed(2)})`, color: 'var(--color-text-secondary)', suspect: false }
}

/**
 * Every shown line against every book, one cell each, shaded by how the price compares with the market fair price: green beats it,
 * grey does not, amber is an unusually large gap. A cell is the book's EV against fair; on a line with no fair value (under 3 books) it
 * is the price instead, unshaded. The best bettable price on each line has a white outline. Clicking a cell or a label selects that
 * line. Scrolls sideways on a phone with the line labels pinned.
 */
export function HeatStrip({ rungs, selected, onSelect }: { rungs: HeatRung[]; selected: number | null; onSelect: (line: number | null) => void }) {
  const books = heatBooks(rungs)
  if (rungs.length === 0 || books.length === 0) return null
  const cols = `${LABEL_W}px repeat(${books.length}, ${CELL_W}px)`
  const minW = LABEL_W + books.length * (CELL_W + 2)
  return (
    <div className="overflow-x-auto" data-testid="heat-strip">
      <div className="grid gap-0.5" style={{ gridTemplateColumns: cols, minWidth: minW }}>
        <div className="sticky left-0 z-[1] bg-[color:var(--color-background-primary-default)]" />
        {books.map(b => (
          <div key={b} className="flex items-end justify-center pb-1" title={bookDisplay(b).label + (SHARP.has(b.toLowerCase()) ? ' (sharp reference)' : '')} data-testid={`heat-head-${canonBook(b)}`}>
            <span className="relative inline-flex"><BookLogo book={b} size={20} />{SHARP.has(b.toLowerCase()) && <span className="absolute -right-0.5 -bottom-0.5 size-[7px] rounded-full border border-[color:var(--color-background-primary-default)]" style={{ background: '#a3a3a3' }} />}</span>
          </div>
        ))}
        {rungs.map(h => {
          const on = h.line === selected
          const sel = () => onSelect(h.line)
          return (
            <HeatRow key={h.label} h={h} on={on} books={books} sel={sel} />
          )
        })}
      </div>
    </div>
  )
}

function HeatRow({ h, on, books, sel }: { h: HeatRung; on: boolean; books: string[]; sel: () => void }) {
  const r = h.rung
  const bestBook = r.best?.book
  return (
    <>
      <button type="button" onClick={sel} aria-pressed={on} data-testid={`heat-row-${h.line ?? 'ml'}`}
        className="sticky left-0 z-[1] flex items-center gap-1.5 text-left text-[12.5px] cursor-pointer border-0 pr-2 bg-[color:var(--color-background-primary-default)]"
        style={{ fontWeight: on ? 600 : 500, color: r.thin ? 'var(--color-text-tertiary)' : 'var(--color-text-primary)' }}>
        <span className="w-[3px] h-4 rounded-[2px]" style={{ background: on ? GREEN : 'transparent' }} />
        <span className="whitespace-nowrap">{h.label}</span>
        {h.main && <span className="mono text-[9px] font-semibold tracking-fine" style={{ color: GREEN }}>MAIN</span>}
        {r.thin && <span className="mono text-[9px] font-semibold tracking-fine text-warn">THIN</span>}
      </button>
      {books.map(b => {
        const d = r.dots.find(x => x.book === b)
        if (!d) return <div key={b} className="h-[30px] rounded-[5px] border border-dashed border-[color:var(--color-separator-border)] opacity-40" />
        const ev = r.pMarket != null ? r.pMarket * d.decimal - 1 : null
        const fill = ev != null ? heatFill(ev) : null
        const isBest = !d.offshore && b === bestBook
        return (
          <button key={b} type="button" onClick={sel} data-testid={`heat-cell-${h.line ?? 'ml'}-${canonBook(b)}`} data-ev={ev ?? ''} data-best={isBest || undefined} data-suspect={fill?.suspect || undefined}
            title={`${bookDisplay(b).label} on ${h.label}: ${formatOdds(d.american)}${ev != null ? ` (${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}% vs fair)` : ' (no fair value on this line)'}`}
            className="mono h-[30px] rounded-[5px] border-0 text-[10.5px] font-medium cursor-pointer"
            style={{
              background: fill?.background ?? 'var(--color-background-secondary-default)',
              color: fill?.color ?? 'var(--color-text-secondary)',
              outline: isBest ? '2px solid var(--color-text-primary)' : on ? '1px solid rgba(87,242,135,0.35)' : 'none',
              outlineOffset: isBest ? '-2px' : '0',
              opacity: r.thin ? 0.7 : 1,
            }}>
            {ev != null ? `${(ev * 100).toFixed(1)}${fill?.suspect ? '?' : ''}` : formatOdds(d.american)}
          </button>
        )
      })}
    </>
  )
}
