import { apiGetRecentSettled } from '@/lib/betting-api'

export async function LiveTicker() {
  let wins: { system: string; game: string; pnl: number; result: string }[] = []
  let losses: typeof wins = []

  try {
    const bets = await apiGetRecentSettled(200)
    const triggered = bets.filter((b: any) => b.kelly_triggered && (b.result === 'win' || b.result === 'loss'))

    for (const b of triggered) {
      const game = b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`
      const entry = { system: b.system, game, pnl: b.profit ?? 0, result: b.result ?? '' }
      if (b.result === 'win' && wins.length < 5) wins.push(entry)
      if (b.result === 'loss' && losses.length < 5) losses.push(entry)
      if (wins.length === 5 && losses.length === 5) break
    }
  } catch {
    // no-op — render nothing if API unavailable
  }

  const ticks = [...wins, ...losses]
  if (ticks.length === 0) return null

  const doubled = [...ticks, ...ticks]

  return (
    <div style={{ borderBottom: '0.5px solid #1f1f24', background: '#111114', overflow: 'hidden', padding: '8px 0', display: 'flex', alignItems: 'center' }}>
      {/* Pinned label */}
      <div style={{ position: 'sticky', left: 0, zIndex: 10, background: '#111114', padding: '0 14px 0 16px', flexShrink: 0, display: 'flex', alignItems: 'center', gap: '6px', borderRight: '0.5px solid #1f1f24' }}>
        <span className="live-dot" />
        <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#52525b', whiteSpace: 'nowrap' }}>Recent Picks</span>
      </div>
      <div className="ticker-track" style={{ flex: 1, overflow: 'hidden' }}>
        {doubled.map((t, i) => (
          <span key={i} className="mono" style={{ fontSize: '11px', color: '#71717a', letterSpacing: '0.04em', whiteSpace: 'nowrap', padding: '0 24px', display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
            <span>{t.result === 'win' ? 'W' : 'L'}</span>
            <span style={{ color: '#3d3d42' }}>·</span>
            <span>{t.system}</span>
            <span style={{ color: '#3d3d42' }}>·</span>
            <span>{t.game}</span>
            <span style={{ color: '#3d3d42' }}>·</span>
            <span>{t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}u</span>
            <span style={{ color: '#2a2a30', padding: '0 12px' }}>|</span>
          </span>
        ))}
      </div>
      </div>
  )
}
