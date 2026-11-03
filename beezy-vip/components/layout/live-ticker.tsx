import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { collapseBooks } from '@/lib/dedupe'
import { isWinterSport } from '@/lib/bet-notes'
import { SYSTEM_COLOR, pickLabel, systemLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

type Tick = { system: string; label: string; u: number; win: boolean; unvalidated?: boolean }

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
      const tick: Tick = { system: b.system, label: pickLabel(b), u: (b.profit ?? 0) / unit, win: b.result === 'win', unvalidated: isWinterSport(b.sport) }
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
    <div className="live-ticker-shell border-b border-b-[color:var(--color-separator-border)] bg-background-primary-default overflow-hidden py-2 px-0 hidden md:flex items-center relative z-[1]">
      <div className="z-[10] bg-background-primary-default pt-0 pr-3.5 pb-0 pl-5 shrink-0 flex items-center gap-2 border-r border-r-[color:var(--color-separator-border)]">
        <span className="live-dot" />
        <span className="mono ticker-label text-micro text-neutral-300 whitespace-nowrap">Latest results</span>
      </div>
      {/* Clipping viewport: the animated track lives inside it so translateX never reaches the label. */}
      <div className="ticker-viewport flex-1 min-w-0 overflow-hidden relative">
        <div className="ticker-track" style={{ animationDuration: `${duration}s` }}>
          {doubled.map((t, i) => {
            const tone = t.win ? 'var(--signal)' : 'var(--loss)'
            const sysColor = SYSTEM_COLOR[t.system] ?? 'var(--color-neutral-300)'
            return (
              <span key={i} className="mono text-compact text-text-primary whitespace-nowrap py-0 px-4.5 inline-flex items-center gap-[9px]">
                <span className="font-semibold" style={{ color: tone }}>{t.win ? 'W' : 'L'}</span>
                <span className="font-semibold" style={{ color: sysColor }}>{systemLabel(t.system, true)}</span>
                <span>{t.label}</span>
                {t.unvalidated && <span className="text-gold text-micro">UNVALIDATED</span>}
                <span className="font-semibold" style={{ color: tone }}>{t.u >= 0 ? '+' : ''}{t.u.toFixed(2)}u</span>
                <span className="text-separator-border py-0 px-3">|</span>
              </span>
            )
          })}
        </div>
      </div>
      <Link
        href="/results"
        className="mono edge-ribbon-cta z-[10] bg-background-primary-default py-0 px-4 shrink-0 flex items-center gap-[6px] border-l border-l-[color:var(--color-separator-border)] text-micro text-signal no-underline font-semibold whitespace-nowrap"
       
      >
        All results <span aria-hidden>&rarr;</span>
      </Link>
    </div>
  )
}
