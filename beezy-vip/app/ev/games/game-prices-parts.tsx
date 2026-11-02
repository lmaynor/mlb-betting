'use client'

import { useEffect, useRef, useState } from 'react'
import { BookLogo } from '@/components/ui/book-logo'
import { fmtMoney } from '@/lib/ev-math'
import { startLabel, type GameBookColumn, type GamePriceRow, type GameSidePrice } from '@/lib/game-odds'
import { formatOdds } from '@/lib/odds'
import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'

export const HAIR = 'var(--color-separator-border)'
export const GREEN = '#57F287'
export const SKY = '#38bdf8'

export function signedPct(x: number, digits = 1): string {
  return `${x >= 0 ? '+' : ''}${(x * 100).toFixed(digits)}%`
}
export function pctText(p: number | null): string {
  return p == null ? '--' : `${(p * 100).toFixed(1)}%`
}
export function usd(x: number): string {
  return `$${x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/** Clipboard write with the textarea fallback for pages without clipboard permission; false when both fail. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch { return false }
  }
}

// ------------------------------------------------------------------------------------------------------- small controls

export function Select({ id, label, value, onChange, options }: { id: string; label: string; value: string; onChange: (v: string) => void; options: { v: string; l: string }[] }) {
  return (
    <div className="flex gap-[8px] items-center">
      <label htmlFor={id} className="ui-heading text-text-secondary">{label}</label>
      <select id={id} value={value} onChange={e => onChange(e.target.value)}
        style={{ background: 'var(--color-background-secondary-default)', color: 'var(--color-text-primary)', border: `1px solid ${HAIR}`, borderRadius: '6px', height: '32px', padding: '0 8px', fontSize: '12px', maxWidth: '190px' }}>
        {options.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
      </select>
    </div>
  )
}

export function Pill({ children, active, onClick, title }: { children: React.ReactNode; active: boolean; onClick: () => void; title?: string }) {
  return (
    <button type="button" onClick={onClick} title={title} aria-pressed={active}
      style={{
        fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, letterSpacing: '0.03em', padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
        color: active ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', background: active ? 'var(--color-background-tertiary-default)' : 'transparent', border: `1px solid ${active ? 'var(--color-border-button-hover)' : 'transparent'}`,
      }}>
      {children}
    </button>
  )
}

export function PillGroup({ children, label }: { children: React.ReactNode; label: string }) {
  return <div role="group" aria-label={label} style={{ display: 'inline-flex', flexWrap: 'wrap', gap: '2px', padding: '3px', background: 'var(--color-background-secondary-default)', border: `1px solid ${HAIR}`, borderRadius: '8px', maxWidth: '100%' }}>{children}</div>
}

export function Toggle({ label, checked, onChange, title }: { label: string; checked: boolean; onChange: (v: boolean) => void; title?: string }) {
  return (
    <label title={title} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12px', color: checked ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', cursor: 'pointer' }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ cursor: 'pointer', accentColor: 'var(--signal)' }} />
      {label}
    </label>
  )
}

/** A button that opens a checklist of books (a popover closed by outside click or Escape). `emptyMeansAll`: none ticked = every book. */
export function BookPicker({ label, books, selected, onChange, presets, note, emptyMeansAll }: {
  label: string
  books: GameBookColumn[]
  selected: string[]
  onChange: (next: string[]) => void
  presets: { label: string; keys: string[] }[]
  note: string
  emptyMeansAll?: boolean
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false) }
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  const on = new Set(selected)
  const toggle = (k: string) => onChange(on.has(k) ? selected.filter(x => x !== k) : [...selected, k])
  const small = { padding: '3px 8px', fontSize: '11px' } as const
  return (
    <div ref={box} className="relative">
      <button type="button" onClick={() => setOpen(v => !v)} aria-expanded={open} className="btn btn-ghost" style={{ padding: '5px 11px', fontSize: '11.5px', height: '32px' }}>{label}</button>
      {open && (
        <div role="group" aria-label={label} style={{ position: 'absolute', zIndex: 30, top: '36px', left: 0, width: '270px', ...SURFACE_CARD, padding: '10px', display: 'grid', gap: '8px' }}>
          <div className="flex gap-[5px] flex-wrap">
            {presets.map(p => <button key={p.label} type="button" className="btn btn-ghost" style={small} onClick={() => onChange(p.keys)}>{p.label}</button>)}
            <button type="button" className="btn btn-ghost" style={small} onClick={() => onChange(emptyMeansAll ? [] : books.map(b => b.key))}>All</button>
            {!emptyMeansAll && <button type="button" className="btn btn-ghost" style={small} onClick={() => onChange([])}>None</button>}
          </div>
          <div className="grid gap-[2px] max-h-[260px] overflow-y-auto">
            {books.map(b => (
              <label key={b.key} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '3px 6px', fontSize: '12px', color: on.has(b.key) ? 'var(--color-text-primary)' : 'var(--color-neutral-300)', cursor: 'pointer' }}>
                <input type="checkbox" checked={on.has(b.key)} onChange={() => toggle(b.key)} style={{ cursor: 'pointer', accentColor: 'var(--signal)' }} />
                <BookLogo book={b.key} size={20} />
                <span className="flex-1 overflow-hidden text-ellipsis whitespace-nowrap">{bookDisplay(b.key).label}{b.offshore ? ' (offshore)' : ''}</span>
                <span className="mono text-[10.5px] text-text-secondary">{b.n}</span>
              </label>
            ))}
          </div>
          <span className="text-[11px] text-text-secondary leading-[1.45]">{note}</span>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------------- table pieces

function isBest(s: GameSidePrice, book: string, offshore: boolean): boolean {
  return !offshore && s.american != null && s.ties.includes(book)
}

function BestCell({ s }: { s: GameSidePrice }) {
  if (s.book == null || s.american == null) return <td className="odds-ev"><span className="mono odds-sub">&ndash;</span></td>
  return (
    <td className="odds-ev">
      <div className="flex items-center gap-[8px]">
        <BookLogo book={s.book} size={24} />
        <div>
          <div className="mono text-[13px] font-bold text-text-primary">{formatOdds(s.american)}</div>
          <div className="mono odds-sub" title={`${s.label} at ${bookDisplay(s.book).label}`}>{s.label} &middot; {bookDisplay(s.book).short}</div>
        </div>
      </div>
    </td>
  )
}

function PriceCell({ r, a, b, offshore, book }: { r: GamePriceRow; a: number | null; b: number | null; offshore: boolean; book: string }) {
  const side = (am: number | null, s: GameSidePrice, arbBook: string | undefined) => {
    const best = isBest(s, book, offshore)
    const arb = r.arbLive && arbBook === book
    return (
      <div>
        <span className="mono" style={{
          display: 'inline-block', lineHeight: 1.35, padding: '0 4px', borderRadius: '3px', fontSize: '12px',
          fontWeight: best || arb ? 800 : 500, color: best || arb ? GREEN : offshore ? 'var(--color-text-secondary)' : 'var(--color-neutral-300)',
          outline: arb ? `1px solid ${GREEN}` : 'none', outlineOffset: '1px',
        }}>{am == null ? '\u2013' : formatOdds(am)}</span>
      </div>
    )
  }
  return (
    <td className="odds-cell" style={{ borderLeft: `1px solid ${HAIR}` }} data-offshore={offshore ? 'true' : undefined}>
      {a == null && b == null ? <span className="mono odds-sub">&ndash;</span> : (
        <>{side(a, r.a, r.arb?.bestOverBook)}{side(b, r.b, r.arb?.bestUnderBook)}</>
      )}
    </td>
  )
}

function SideLine({ s, children }: { s: GameSidePrice; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-[6px] whitespace-nowrap">
      <span className="mono odds-sub inline-block min-w-[58px]">{s.label}</span>
      {children}
    </div>
  )
}

/** One game x market x line: the matchup, best prices, fair value, EV and one narrow column per book. */
export function PriceTableRow({ r, columns, bankroll, copied, onCopy }: {
  r: GamePriceRow; columns: GameBookColumn[]; bankroll: number; copied: boolean; onCopy: (r: GamePriceRow) => void
}) {
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const evTop = r.a.ev != null && r.b.ev != null ? (r.a.ev >= r.b.ev ? r.a : r.b) : (r.a.ev != null ? r.a : r.b.ev != null ? r.b : null)
  const stake = evTop && evTop.ev != null && evTop.ev > 0 ? evTop.stake : 0
  const noFair = r.a.pFair == null && r.b.pFair == null
  const evColor = (s: GameSidePrice) => (s.ev != null && s.ev > 0 ? GREEN : 'var(--color-text-secondary)')
  return (
    <>
      <tr data-row="game-prices" data-arb={r.arbLive ? 'true' : undefined}>
        <td className="odds-prop" style={r.arbLive ? { borderLeft: `3px solid ${GREEN}` } : undefined}>
          <div className="text-[13px] font-semibold text-text-primary leading-[1.3]">{r.matchup}</div>
          <div className="mono odds-sub">
            {r.row.sport.toUpperCase()} &middot; {startLabel(r.row.commence)} &middot; {r.marketLabel === 'Moneyline' ? 'ML' : r.lineLabel}
          </div>
        </td>
        <BestCell s={r.a} />
        <BestCell s={r.b} />
        <td className="odds-ev">
          {noFair ? (
            <>
              <div className="mono text-[13px] font-bold text-text-secondary">--</div>
              <div className="mono odds-sub" title="Fewer than 3 reference books priced this line both ways; a fair value needs 3">{Math.max(r.a.nRef, r.b.nRef)} of 3 bk</div>
            </>
          ) : (
            <>
              <div className="mono" style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--color-text-primary)' }} title={`${r.a.label} fair ${r.a.fairAmerican == null ? '--' : formatOdds(r.a.fairAmerican)}`}>{pctText(r.a.pFair)}</div>
              <div className="mono" style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--color-text-primary)' }} title={`${r.b.label} fair ${r.b.fairAmerican == null ? '--' : formatOdds(r.b.fairAmerican)}`}>{pctText(r.b.pFair)}</div>
            </>
          )}
        </td>
        <td className="odds-ev">
          {noFair ? <div className="mono text-[13px] font-bold text-text-secondary">--</div> : (
            <>
              <SideLine s={r.a}><span className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color: evColor(r.a) }}>{r.a.ev == null ? '--' : signedPct(r.a.ev)}</span></SideLine>
              <SideLine s={r.b}><span className="mono" style={{ fontSize: '12.5px', fontWeight: 700, color: evColor(r.b) }}>{r.b.ev == null ? '--' : signedPct(r.b.ev)}</span></SideLine>
            </>
          )}
        </td>
        <td className="text-right">
          <div className="mono" style={{ fontSize: '12px', color: stake > 0 ? 'var(--color-text-primary)' : 'var(--color-text-secondary)' }} title={`1/4 Kelly on a ${fmtMoney(bankroll)} bankroll`}>{stake > 0 ? fmtMoney(stake) : '--'}</div>
        </td>
        {columns.map(c => {
          const q = byBook.get(c.key)
          return <PriceCell key={c.key} r={r} a={q ? q.a : null} b={q ? q.b : null} offshore={c.offshore} book={c.key} />
        })}
      </tr>
      {r.arbLive && r.arb && (
        <tr data-row="game-arb">
          <td colSpan={6 + columns.length} style={{ background: 'rgba(87, 242, 135, 0.06)', borderLeft: `3px solid ${GREEN}` }}>
            <ArbLine r={r} copied={copied} onCopy={onCopy} />
          </td>
        </tr>
      )}
    </>
  )
}

/** The locked-profit line of a live arbitrage: both legs, the split of $100, the net margin and a Copy hedge button. */
export function ArbLine({ r, copied, onCopy }: { r: GamePriceRow; copied: boolean; onCopy: (r: GamePriceRow) => void }) {
  const a = r.arb
  if (!a) return null
  return (
    <div className="flex items-center gap-y-[6px] gap-x-[14px] flex-wrap text-[11.5px]">
      <span className="mono" style={{ fontWeight: 700, color: GREEN }} title={a.feeLeg ? `Gross ${a.arbPct.toFixed(2)}%, net of Kalshi's taker fee ${a.netArbPct.toFixed(2)}%` : 'No fee on either leg'}>
        ARB +{a.netArbPct.toFixed(2)}%{a.feeLeg ? ' NET OF KALSHI FEE' : ''}
      </span>
      <span className="mono text-text-primary">{r.a.label} {formatOdds(a.bestOverAm)} {bookDisplay(a.bestOverBook).label} {usd(a.stakeOverPer100)}</span>
      <span className="mono text-text-primary">{r.b.label} {formatOdds(a.bestUnderAm)} {bookDisplay(a.bestUnderBook).label} {usd(a.stakeUnderPer100)}</span>
      <span className="mono" style={{ color: GREEN }}>+{usd(a.guaranteedProfitPer100)} locked on $100</span>
      <button type="button" className="btn btn-ghost" onClick={() => onCopy(r)} style={{ padding: '1px 8px', fontSize: '10.5px' }}>{copied ? 'Copied' : 'Copy hedge'}</button>
    </div>
  )
}

/** Mobile: one card per game x market x line, with every book's price behind a disclosure. */
export function PriceCard({ r, columns, copied, onCopy }: {
  r: GamePriceRow; columns: GameBookColumn[]; copied: boolean; onCopy: (r: GamePriceRow) => void
}) {
  const byBook = new Map(r.row.books.map(b => [b.book, b]))
  const side = (s: GameSidePrice) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 0', borderTop: `1px solid ${HAIR}` }}>
      {s.book ? <BookLogo book={s.book} size={22} /> : <span  className="w-[22px]"/>}
      <span className="flex-1 text-[12.5px] text-text-primary font-semibold">{s.label}</span>
      <span className="mono text-[13px] font-bold text-text-primary">{s.american == null ? '--' : formatOdds(s.american)}</span>
      <span className="mono text-[11px] text-text-secondary min-w-[48px] text-right">{pctText(s.pFair)}</span>
      <span className="mono" style={{ fontSize: '12px', fontWeight: 700, minWidth: '52px', textAlign: 'right', color: s.ev != null && s.ev > 0 ? GREEN : 'var(--color-text-secondary)' }}>{s.ev == null ? '--' : signedPct(s.ev)}</span>
    </div>
  )
  return (
    <div data-card="game-prices" style={{ ...SURFACE_CARD, padding: '10px 12px', borderLeft: r.arbLive ? `3px solid ${GREEN}` : '1px solid var(--color-separator-border)' }}>
      <div className="flex justify-between gap-[8px] items-baseline">
        <span className="text-[13.5px] font-bold text-text-primary">{r.matchup}</span>
        <span className="mono text-[11px] text-text-secondary">{startLabel(r.row.commence)}</span>
      </div>
      <div className="mono text-[11px] text-text-secondary mb-[4px]">{r.row.sport.toUpperCase()} &middot; {r.marketLabel}{r.row.market === 'ml' ? '' : ` ${r.lineLabel}`}</div>
      {side(r.a)}
      {side(r.b)}
      {r.arbLive && <div style={{ paddingTop: '6px', borderTop: `1px solid ${HAIR}` }}><ArbLine r={r} copied={copied} onCopy={onCopy} /></div>}
      <details style={{ marginTop: '6px' }}>
        <summary style={{ fontSize: '11.5px', color: 'var(--link)', cursor: 'pointer' }}>All books</summary>
        <table className="w-full border-collapse mt-[4px]">
          <tbody>
            {columns.filter(c => byBook.has(c.key)).map(c => {
              const q = byBook.get(c.key)!
              return (
                <tr key={c.key}>
                  <td style={{ padding: '3px 0', fontSize: '12px', color: c.offshore ? 'var(--color-text-secondary)' : 'var(--color-neutral-300)' }}>{bookDisplay(c.key).label}{c.offshore ? ' (offshore)' : ''}</td>
                  <td className="mono" style={{ textAlign: 'right', fontSize: '12px', color: isBest(r.a, c.key, c.offshore) ? GREEN : 'var(--color-neutral-300)', fontWeight: isBest(r.a, c.key, c.offshore) ? 800 : 400 }}>{q.a == null ? '\u2013' : formatOdds(q.a)}</td>
                  <td className="mono" style={{ textAlign: 'right', fontSize: '12px', color: isBest(r.b, c.key, c.offshore) ? GREEN : 'var(--color-neutral-300)', fontWeight: isBest(r.b, c.key, c.offshore) ? 800 : 400 }}>{q.b == null ? '\u2013' : formatOdds(q.b)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </details>
    </div>
  )
}
