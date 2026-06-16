import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'

export async function LiveTicker() {
  const wins: { system: string; game: string; pnl: number; result: string }[] = []
  const losses: typeof wins = []

  try {
    const bets = await apiGetRecentSettled(200)
    const triggered = bets.filter((b: Bet) => b.kelly_triggered && (b.result === 'win' || b.result === 'loss'))

    for (const b of triggered) {
      const game = b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`
      const entry = { system: b.system, game, pnl: b.profit ?? 0, result: b.result ?? '' }
      if (b.result === 'win' && wins.length < 5) wins.push(entry)
      if (b.result === 'loss' && losses.length < 5) losses.push(entry)
      if (wins.length === 5 && losses.length === 5) break
    }
  } catch {
    // Render nothing if API is unavailable.
  }

  // Interleave W / L so the strip stays visually balanced instead of
  // showing a run of wins followed by a run of losses.
  const ticks: typeof wins = []
  const maxLen = Math.max(wins.length, losses.length)
  for (let i = 0; i < maxLen; i++) {
    if (wins[i])   ticks.push(wins[i])
    if (losses[i]) ticks.push(losses[i])
  }
  if (ticks.length === 0) return null

  const doubled = [...ticks, ...ticks]

  return (
    <div className="live-ticker-shell" style={{ borderBottom: '1px solid #1f1f24', background: '#111114', overflow: 'hidden', padding: '7px 0', display: 'flex', alignItems: 'center', position: 'relative', zIndex: 1 }}>
      {/* Static label -- sits in normal flow; the track scrolls in its own clipped viewport to its right */}
      <div style={{ zIndex: 10, background: '#111114', padding: '0 12px 0 16px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '6px', borderRight: '1px solid #1f1f24' }}>
        <span className="live-dot" />
        <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#888890', whiteSpace: 'nowrap' }}>Recent</span>
      </div>
      {/* Clipping viewport -- the animated track lives inside this, so translateX never reaches the label */}
      <div style={{ flex: 1, minWidth: 0, overflow: 'hidden', position: 'relative' }}>
        <div className="ticker-track">
          {doubled.map((t, i) => {
            const win = t.result === 'win'
            const tone = win ? '#b3bd95' : '#d77a7a'
            // profit is stored in dollars at a $10 unit (1u = 1% of bankroll);
            // divide by 10 to display in units.
            const u = t.pnl / 10
            return (
              <span key={i} className="mono" style={{ fontSize: '11px', color: '#888890', letterSpacing: '0.04em', whiteSpace: 'nowrap', padding: '0 20px', display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ color: tone, fontWeight: 700 }}>{win ? 'W' : 'L'}</span>
                <span style={{ color: '#52525b' }}>/</span>
                <span>{t.system}</span>
                <span style={{ color: '#52525b' }}>/</span>
                <span>{t.game}</span>
                <span style={{ color: '#52525b' }}>/</span>
                <span style={{ color: tone }}>{u >= 0 ? '+' : ''}{u.toFixed(2)}u</span>
                <span style={{ color: '#1f1f24', padding: '0 10px' }}>|</span>
              </span>
            )
          })}
        </div>
      </div>
      <Link
        href="/edge"
        className="mono edge-ribbon-cta"
        style={{ zIndex: 10, background: '#111114', padding: '0 14px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '5px', borderLeft: '1px solid #1f1f24', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#fcc20f', textDecoration: 'none', fontWeight: 700, whiteSpace: 'nowrap' }}
      >
        The Edge <span aria-hidden>&rarr;</span>
      </Link>
    </div>
  )
}
