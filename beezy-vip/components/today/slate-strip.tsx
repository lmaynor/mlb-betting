import { apiGetTodayPicks } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR } from '@/lib/beezy-score'

const SYSTEM_COLOR: Record<string, string> = {
  NRFI: '#b3bd95', HR: '#e6915d', F5: '#9ab6c8',
  K: '#8c9ae0', OUTS: '#e6915d', BATTER_HITS: '#8c9ae0',
}

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
      <div style={{ display: 'flex', gap: '8px', padding: '10px 16px', minWidth: 'max-content' }}>
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
          const color = topPick ? TIER_COLOR[tier] : '#2a2a31'
          const sysColor = topPick ? (SYSTEM_COLOR[topPick.system] ?? '#888890') : '#888890'

          return (
            <div key={key} style={{
              minWidth: '100px', padding: '8px 10px',
              background: hasPick ? `${sysColor}0d` : '#111114',
              border: `1px solid ${hasPick ? `${color}66` : '#1f1f24'}`,
              borderRadius: 0,
              display: 'flex', flexDirection: 'column', gap: '4px',
              flexShrink: 0,
            }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', fontWeight: 600, color: '#f5f5f7', whiteSpace: 'nowrap' }}>
                {away} @ {home}
              </div>
              {hasPick && topPick ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: '8px', fontWeight: 700,
                    padding: '1px 5px', background: `${sysColor}20`, color: sysColor,
                    border: `1px solid ${sysColor}66`, borderRadius: 0,
                  }}>
                    {topPick.system}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', fontWeight: 700, color }}>
                    {score}
                  </span>
                </div>
              ) : (
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '9px', color: '#3a3a48' }}>
                  no pick
                </div>
              )}
              {gamePicks.length > 1 && (
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '8px', color: '#52525b' }}>
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
