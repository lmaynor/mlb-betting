'use client'

import { useEffect, useRef, useState } from 'react'
import { rungEdgeRange, type LadderRung } from '@/lib/game-ladder'
import { GREEN, SKY } from './game-prices-parts'

export interface ChartRung { line: number | null; label: string; rung: LadderRung }

const MARKET = '#9ca3af'
const BAND = '#7d8596'
const PROB_H = 132
const GAP = 32
const EDGE_H = 116
const LABEL_H = 46
const PAD = { l: 44, r: 8, t: 10 }
const H = PAD.t + PROB_H + GAP + EDGE_H + LABEL_H
/** The edge panel never zooms past this, so one stale quote cannot flatten every other bar. A clipped bar gets an arrow. */
const EDGE_CAP = 0.12

/** The width of an element in px, kept current with a ResizeObserver (a fixed fallback where there is none, e.g. jsdom). */
function useWidth(fallback = 640): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement | null>(null)
  const [w, setW] = useState(fallback)
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(es => { const x = Math.round(es[0]?.contentRect.width ?? 0); if (x > 0) setW(x) })
    ro.observe(el)
    setW(Math.round(el.getBoundingClientRect().width) || fallback)
    return () => ro.disconnect()
  }, [fallback])
  return [ref, w]
}

const pct = (p: number) => `${Math.round(p * 100)}%`
const evText = (x: number) => `${x >= 0 ? '+' : ''}${(x * 100).toFixed(1)}%`

/**
 * The alternate-line ladder for one market and one side, in two panels that share their columns.
 *  Top: probability by line. Market fair (solid grey), our projection (dashed blue) and the best price's break-even (green dot). A
 *  dot below a curve is a price better than that curve says.
 *  Bottom: every line's books against the market fair price. Zero is the fair price; each bar runs from the worst book to the best,
 *  green above zero (books that beat fair), grey below it. This is where the value is, so it gets the room: books that agree
 *  collapse to a sliver around zero, an outlier stands out.
 * The selected line shows the gaps in the top panel: green = best price below the market fair value (a real price edge), blue =
 * best price below our model (UNVALIDATED). Clicking a column selects it.
 */
export function LadderChart({ rungs, selected, onSelect, sideLabel }: { rungs: ChartRung[]; selected: number | null; onSelect: (line: number | null) => void; sideLabel: string }) {
  const [ref, W] = useWidth()
  const n = rungs.length
  if (n === 0) return null

  const ys: number[] = []
  for (const { rung } of rungs) {
    if (rung.best) ys.push(rung.best.y)
    if (rung.pMarket != null) ys.push(rung.pMarket)
    if (rung.pModel != null) ys.push(rung.pModel)
  }
  const lo = ys.length ? Math.max(0, Math.floor((Math.min(...ys) - 0.05) * 20) / 20) : 0
  const hi = ys.length ? Math.min(1, Math.ceil((Math.max(...ys) + 0.05) * 20) / 20) : 1
  const span = Math.max(0.1, hi - lo)
  const pw = W - PAD.l - PAD.r
  const colW = pw / n
  const X = (i: number) => PAD.l + colW * (i + 0.5)
  const Y = (p: number) => PAD.t + (1 - (p - lo) / span) * PROB_H
  const narrow = colW < 58
  const ticks = [0, 1, 2].map(i => lo + (span * i) / 2)

  // edge panel domain: the data, never narrower than +/-3% and never wider than the cap
  const ranges = rungs.map(c => rungEdgeRange(c.rung))
  const allLo = Math.min(-0.03, ...ranges.map(r => (r ? r.lo : 0)))
  const allHi = Math.max(0.03, ...ranges.map(r => (r ? r.hi : 0)))
  const eLo = Math.max(-EDGE_CAP, allLo * 1.15)
  const eHi = Math.min(EDGE_CAP, allHi * 1.15)
  const EY0 = PAD.t + PROB_H + GAP
  const EY = (v: number) => EY0 + (1 - (Math.min(eHi, Math.max(eLo, v)) - eLo) / (eHi - eLo)) * EDGE_H
  const hasEdge = ranges.some(Boolean)
  const barW = Math.min(26, Math.max(8, colW * 0.34))
  const eTicks = [eHi, 0, eLo]

  const curve = (pick: (r: LadderRung) => number | null) => {
    const pts = rungs.map((c, i) => ({ x: X(i), p: pick(c.rung) })).filter((p): p is { x: number; p: number } => p.p != null)
    return pts.length >= 2 ? pts.map(p => `${p.x},${Y(p.p)}`).join(' ') : null
  }
  const market = curve(r => r.pMarket)
  const model = curve(r => r.pModel)
  const selIdx = rungs.findIndex(c => c.line === selected)
  const frameH = PROB_H + GAP + EDGE_H + LABEL_H - 6

  return (
    <div ref={ref} className="w-full" data-testid="ladder-chart">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${sideLabel}: probability by line, and each book's price against the market fair price.`} className="block max-w-full">
        {ticks.map(t => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={Y(t)} y2={Y(t)} stroke="var(--color-separator-border)" />
            <text x={PAD.l - 7} y={Y(t) + 4} textAnchor="end" fontSize="11" fill="var(--color-text-tertiary)">{pct(t)}</text>
          </g>
        ))}

        {selIdx >= 0 && (
          <rect x={X(selIdx) - colW / 2 + 2} y={PAD.t} width={colW - 4} height={frameH} rx={8} fill="rgba(87,242,135,0.05)" stroke={GREEN} strokeOpacity={0.7} strokeWidth={1.5} data-testid="ladder-selected" />
        )}
        {market && <polyline points={market} fill="none" stroke={MARKET} strokeWidth={2} strokeLinejoin="round" />}
        {model && <polyline points={model} fill="none" stroke={SKY} strokeWidth={2} strokeDasharray="5 4" strokeLinejoin="round" />}
        {rungs.map((c, i) => {
          if (i !== selIdx) return null
          const r = c.rung
          const bw = 12
          return (
            <g key="gaps">
              {r.modelGap && <rect x={X(i) + 9} y={Y(r.modelGap.to)} width={bw} height={Math.max(1, Y(r.modelGap.from) - Y(r.modelGap.to))} fill={SKY} fillOpacity={0.28} data-testid="gap-model"><title>Our model is above the best price (unvalidated)</title></rect>}
              {r.marketGap && <rect x={X(i) - 9 - bw} y={Y(r.marketGap.to)} width={bw} height={Math.max(1, Y(r.marketGap.from) - Y(r.marketGap.to))} fill={GREEN} fillOpacity={0.35} data-testid="gap-market"><title>Best price is below the market fair value</title></rect>}
            </g>
          )
        })}
        {rungs.map((c, i) => c.rung.best && (
          <circle key={`${c.label}-best`} cx={X(i)} cy={Y(c.rung.best.y)} r={4.5} fill={GREEN} stroke="var(--color-background-primary-default)" strokeWidth={2} data-testid="best-dot" data-y={c.rung.best.y}>
            <title>{`${c.rung.best.book} ${c.rung.best.american > 0 ? '+' : ''}${c.rung.best.american}: break-even ${(c.rung.best.y * 100).toFixed(1)}%`}</title>
          </circle>
        ))}

        {hasEdge && (
          <g data-testid="edge-panel">
            <text x={PAD.l + 4} y={EY0 - 12} fontSize="11" fill="var(--color-text-secondary)">Books against the market fair price</text>
            {eTicks.map((t, ti) => (
              <g key={ti}>
                <line x1={PAD.l} x2={W - PAD.r} y1={EY(t)} y2={EY(t)} stroke={t === 0 ? MARKET : 'var(--color-separator-border)'} strokeWidth={t === 0 ? 1.5 : 1} strokeDasharray={t === 0 ? undefined : '2 4'} />
                <text x={PAD.l - 7} y={EY(t) + 4} textAnchor="end" fontSize="11" fill={t === 0 ? 'var(--color-text-secondary)' : 'var(--color-text-tertiary)'}>{t === 0 ? 'fair' : evText(t)}</text>
              </g>
            ))}
            {rungs.map((c, i) => {
              const e = ranges[i]
              if (!e) return null
              const above = e.hi > 0
              const top = EY(e.hi)
              const bottom = EY(e.lo)
              const zero = EY(0)
              const clipped = e.hi > eHi
              return (
                <g key={`${c.label}-edge`} opacity={c.rung.thin ? 0.45 : 1}>
                  {e.lo < 0 && <rect x={X(i) - barW / 2} y={zero} width={barW} height={Math.max(1, bottom - zero)} rx={3} fill={BAND} fillOpacity={0.35} />}
                  {above && <rect x={X(i) - barW / 2} y={top} width={barW} height={Math.max(1.5, zero - top)} rx={3} fill={c.rung.suspect ? '#facc15' : GREEN} fillOpacity={c.rung.suspect ? 0.55 : 0.75} />}
                  <rect x={X(i) - barW / 2} y={top} width={barW} height={Math.max(1.5, bottom - top)} fill="transparent" data-testid="book-range" data-ev-lo={e.lo} data-ev-hi={e.hi} />
                  {clipped && <text x={X(i)} y={EY0 + 9} textAnchor="middle" fontSize="11" fill="#facc15">{'▲'}</text>}
                  {c.rung.best && e.best != null && <circle cx={X(i)} cy={EY(e.best)} r={3.5} fill={e.best > 0 ? GREEN : MARKET} stroke="var(--color-background-primary-default)" strokeWidth={1.5} />}
                  <title>{`${c.rung.nBooks} books: ${evText(e.lo)} to ${evText(e.hi)} against the market fair price`}</title>
                </g>
              )
            })}
          </g>
        )}

        {rungs.map((c, i) => {
          const r = c.rung
          const on = c.line === selected
          const evColor = r.thin || r.suspect ? '#facc15' : r.evMarket != null && r.evMarket > 0 ? GREEN : 'var(--color-text-tertiary)'
          const ly = PAD.t + PROB_H + GAP + EDGE_H + 18
          return (
            <g key={`${c.label}-${i}`}>
              <text x={X(i)} y={ly} textAnchor="middle" fontSize={narrow ? 11 : 12.5} fontWeight={on ? 600 : 500} fill={r.thin ? 'var(--color-text-tertiary)' : 'var(--color-text-primary)'}>{narrow ? c.label.split(' ').pop() : c.label}</text>
              <text x={X(i)} y={ly + 18} textAnchor="middle" fontSize={narrow ? 11 : 12.5} fontWeight={600} fill={evColor} data-testid="ladder-ev">{r.thin ? 'thin' : r.evMarket != null ? `${evText(r.evMarket)}${r.suspect ? '?' : ''}` : '--'}</text>
              <rect x={X(i) - colW / 2} y={PAD.t} width={colW} height={H - PAD.t} fill="transparent" style={{ cursor: 'pointer' }} role="button" tabIndex={0} aria-pressed={on} aria-label={`${c.label}: ${r.nBooks} books`}
                data-testid={`rung-${c.line ?? 'ml'}`} onClick={() => onSelect(c.line)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(c.line) } }} />
            </g>
          )
        })}
      </svg>
      <div className="text-[11.5px] text-text-tertiary mt-0.5">
        Top: chance of covering, best price as the green dot. Bottom: each bar spans every book against the market fair price; green beats it. &ldquo;thin&rdquo; = under 3 books, &ldquo;?&rdquo; = unusually large gap.
      </div>
    </div>
  )
}
