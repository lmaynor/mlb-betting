import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { collapseBooks } from '@/lib/dedupe'
import { SYSTEM_COLOR, pickLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

type Tick = { system: string; label: string; u: number; win: boolean }

const PER_SIDE = 6

export async function LiveTicker() {
  const wins: Tick[] = []
  const losses: Tick[] = []

  try {
    // One tick per prop (the same prop flagged at several books is one pick), newest first.
    const bets = collapseBooks(await apiGetRecentSettled(200))
    for (const b of bets as Bet[]) {
      if (!b.kelly_triggered || (b.result !== 'win' && b.result !== 'loss')) continue
      // Units = profit / stake, so a $100 flat EV bet and a $10 model bet both read as 1u risked.
      const unit = b.stake && b.stake > 0 ? b.stake : 10
      const tick: Tick = { system: b.system, label: pickLabel(b), u: (b.profit ?? 0) / unit, win: b.result === 'win' }
      if (tick.win && wins.length < PER_SIDE) wins.push(tick)
      if (!tick.win && losses.length < PER_SIDE) losses.push(tick)
      if (wins.length === PER_SIDE && losses.length === PER_SIDE) break
    }
  } catch {
    // Render nothing if API is unavailable.
  }

  // Interleave W / L so the strip stays balanced instead of a run of wins then a run of losses.
  const ticks: Tick[] = []
  const maxLen = Math.max(wins.length, losses.length)
  for (let i = 0; i < maxLen; i++) {
    if (wins[i])   ticks.push(wins[i])
    if (losses[i]) ticks.push(losses[i])
  }
  if (ticks.length === 0) return null

  const doubled = [...ticks, ...ticks]
  // ~4.5s per item keeps the crawl brisk and readable whatever the item count.
  const duration = Math.max(24, ticks.length * 4.5)

  return (
    <div className="live-ticker-shell" style={{ borderBottom: '1px solid var(--basalt)', background: 'var(--graphite)', overflow: 'hidden', padding: '8px 0', display: 'flex', alignItems: 'center', position: 'relative', zIndex: 1 }}>
      <div style={{ zIndex: 10, background: 'var(--graphite)', padding: '0 14px 0 20px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '8px', borderRight: '1px solid var(--basalt)' }}>
        <span className="live-dot" />
        <span className="mono ticker-label" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--silver)', whiteSpace: 'nowrap' }}>Latest results</span>
      </div>
      {/* Clipping viewport: the animated track lives inside it so translateX never reaches the label. */}
      <div className="ticker-viewport" style={{ flex: 1, minWidth: 0, overflow: 'hidden', position: 'relative' }}>
        <div className="ticker-track" style={{ animationDuration: `${duration}s` }}>
          {doubled.map((t, i) => {
            const tone = t.win ? 'var(--signal)' : 'var(--loss)'
            const sysColor = SYSTEM_COLOR[t.system] ?? 'var(--silver)'
            return (
              <span key={i} className="mono" style={{ fontSize: '12px', color: 'var(--ash)', letterSpacing: '0.02em', whiteSpace: 'nowrap', padding: '0 18px', display: 'inline-flex', alignItems: 'center', gap: '9px' }}>
                <span style={{ color: tone, fontWeight: 700 }}>{t.win ? 'W' : 'L'}</span>
                <span style={{ color: sysColor, fontWeight: 600 }}>{t.system}</span>
                <span>{t.label}</span>
                <span style={{ color: tone, fontWeight: 600 }}>{t.u >= 0 ? '+' : ''}{t.u.toFixed(2)}u</span>
                <span style={{ color: 'var(--basalt)', padding: '0 12px' }}>|</span>
              </span>
            )
          })}
        </div>
      </div>
      <Link
        href="/results"
        className="mono edge-ribbon-cta"
        style={{ zIndex: 10, background: 'var(--graphite)', padding: '0 16px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '6px', borderLeft: '1px solid var(--basalt)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--signal)', textDecoration: 'none', fontWeight: 700, whiteSpace: 'nowrap' }}
      >
        All results <span aria-hidden>&rarr;</span>
      </Link>
    </div>
  )
}
