import { apiGetTodayPicks } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR } from '@/lib/beezy-score'
import { SYSTEM_COLOR } from '@/lib/tokens'

export async function SlateStrip() {
  let picks: Awaited<ReturnType<typeof apiGetTodayPicks>> = []
  try { picks = await apiGetTodayPicks() } catch { /* API unavailable */ }

  if (picks.length === 0) return null

  // Group picks by game (away @ home)
  type GameKey = string
  const byGame = new Map<GameKey, typeof picks>()
  for (const p of picks) {
    const key = p.away_team && p.home_team ? `${p.away_team}@${p.home_team}` : `game-${p.game_pk}`
    if (!byGame.has(key)) byGame.set(key, [])
    byGame.get(key)!.push(p)
  }

  const games = Array.from(byGame.entries())

  return (
    <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch', paddingBottom: '4px' }}>
      <div style={{ display: 'flex', gap: '10px', padding: '12px 16px', minWidth: 'max-content' }}>
        {games.map(([key, gamePicks]) => {
          const first = gamePicks[0]
          const away = first.away_team ?? '?'
          const home = first.home_team ?? '?'
          const hasPick = gamePicks.length > 0
          const topPick = hasPick
            ? gamePicks.reduce((best, p) => beezyscore(p) > beezyscore(best) ? p : best, gamePicks[0])
            : null
          const score = topPick ? beezyscore(topPick) : 0
          const tier  = topPick ? scoreTier(score) : 'watch'
          const color = topPick ? TIER_COLOR[tier] : 'var(--iron)'
          const sysColor = topPick ? (SYSTEM_COLOR[topPick.system] ?? 'var(--silver)') : 'var(--silver)'

          return (
            <div key={key} style={{
              minWidth: '116px', padding: '10px 12px',
              background: 'var(--graphite)',
              border: `1px solid ${hasPick ? `color-mix(in oklab, ${color} 40%, var(--carbon))` : 'var(--basalt)'}`,
              borderRadius: 'var(--radius-lg)',
              display: 'flex', flexDirection: 'column', gap: '6px',
              flexShrink: 0,
            }}>
              <div className="mono" style={{ fontSize: '11px', fontWeight: 600, color: 'var(--ash)', whiteSpace: 'nowrap' }}>
                {away} @ {home}
              </div>
              {hasPick && topPick ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span className="dell-heading" style={{
                    fontSize: '8px', fontWeight: 600, letterSpacing: '0.04em',
                    padding: '2px 6px', background: `color-mix(in oklab, ${sysColor} 16%, var(--carbon))`, color: sysColor,
                    border: `1px solid color-mix(in oklab, ${sysColor} 40%, var(--carbon))`, borderRadius: 'var(--radius-pill)',
                  }}>
                    {topPick.system}
                  </span>
                  <span className="mono" style={{ fontSize: '11px', fontWeight: 700, color }}>
                    {score}
                  </span>
                </div>
              ) : (
                <div className="mono" style={{ fontSize: '9px', color: 'var(--fog)' }}>
                  no pick
                </div>
              )}
              {gamePicks.length > 1 && (
                <div className="mono" style={{ fontSize: '9px', color: 'var(--fog)' }}>
                  +{gamePicks.length - 1} more
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
