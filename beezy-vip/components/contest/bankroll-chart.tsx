'use client'
/**
 * Bankroll over time: one flat line, the starting bankroll as a dashed baseline, and a plain-text summary (start, peak, low, now) beside it
 * so the numbers never depend on reading the picture. SVG attributes use literal hex because var() does not work in them.
 * Points are the moments the bankroll changed (a bet settling, a cash out, an adjustment), evenly spaced: this shows the run, not the clock.
 */
import { fmtMoney } from '@/lib/contest/odds'

export interface HistoryPoint { ts: string; cents: number }
const W = 560, H = 120, PAD = 8

export function BankrollChart({ points, start }: { points: HistoryPoint[]; start: number }) {
  if (points.length < 2) return null
  const vals = points.map(p => p.cents)
  const lo = Math.min(...vals, start), hi = Math.max(...vals, start)
  const span = Math.max(hi - lo, 1)
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (points.length - 1)
  const y = (v: number) => H - PAD - ((v - lo) * (H - 2 * PAD)) / span
  const last = points[points.length - 1].cents
  const tone = last >= start ? '#57f287' : '#ef4444'
  return (
    <section aria-label="Bankroll history" className="flex flex-wrap items-center gap-x-6 gap-y-2" style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, padding: '10px 12px', marginBottom: 10 }}>
      <svg role="img" aria-label={`Bankroll over ${points.length} changes, from ${fmtMoney(points[0].cents)} to ${fmtMoney(last)}`} viewBox={`0 0 ${W} ${H}`} style={{ flex: '1 1 320px', maxWidth: 560, height: 120 }}>
        <line x1={PAD} x2={W - PAD} y1={y(start)} y2={y(start)} stroke="#2e3446" strokeWidth="1" strokeDasharray="4 4" />
        <polyline points={points.map((p, i) => `${x(i)},${y(p.cents)}`).join(' ')} fill="none" stroke={tone} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={x(points.length - 1)} cy={y(last)} r="3" fill={tone} />
      </svg>
      <dl className="mono" style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto auto', columnGap: 16, rowGap: 4, fontSize: 12 }}>
        <dt style={{ color: '#7d8596' }}>Start</dt><dd style={{ margin: 0, textAlign: 'right' }}>{fmtMoney(start)}</dd>
        <dt style={{ color: '#7d8596' }}>Peak</dt><dd style={{ margin: 0, textAlign: 'right' }}>{fmtMoney(Math.max(...vals))}</dd>
        <dt style={{ color: '#7d8596' }}>Low</dt><dd style={{ margin: 0, textAlign: 'right' }}>{fmtMoney(Math.min(...vals))}</dd>
        <dt style={{ color: '#7d8596' }}>Now</dt><dd style={{ margin: 0, textAlign: 'right', color: tone }}>{fmtMoney(last)}</dd>
      </dl>
    </section>
  )
}
