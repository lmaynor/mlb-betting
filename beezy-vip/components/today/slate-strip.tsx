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
          const color = topPick ? TIER_COLOR[tier] : 'var(--color-border-button-default)'
          const sysColor = topPick ? (SYSTEM_COLOR[topPick.system] ?? 'var(--color-neutral-300)') : 'var(--color-neutral-300)'

          return (
            <div key={key} style={{
              minWidth: '116px', padding: '10px 12px',
              background: 'var(--color-background-primary-default)',
              border: `1px solid ${hasPick ? `color-mix(in oklab, ${color} 40%, var(--color-background-full))` : 'var(--color-separator-border)'}`,
              borderRadius: 'var(--radius-lg)',
              display: 'flex', flexDirection: 'column', gap: '6px',
              flexShrink: 0,
            }}>
              <div className="mono text-[11px] font-semibold text-text-primary whitespace-nowrap">
                {away} @ {home}
              </div>
              {hasPick && topPick ? (
                <div className="flex items-center gap-[6px]">
                  <span className="dell-heading" style={{
                    fontSize: '10px', fontWeight: 600, letterSpacing: '0.04em',
                    padding: '2px 6px', background: `color-mix(in oklab, ${sysColor} 16%, var(--color-background-full))`, color: sysColor,
                    border: `1px solid color-mix(in oklab, ${sysColor} 40%, var(--color-background-full))`, borderRadius: 'var(--radius-pill)',
                  }}>
                    {topPick.system}
                  </span>
                  <span className="mono" style={{ fontSize: '11px', fontWeight: 700, color }}>
                    {score}
                  </span>
                </div>
              ) : (
                <div className="mono text-[10px] text-text-secondary">
                  no pick
                </div>
              )}
              {gamePicks.length > 1 && (
                <div className="mono text-[10px] text-text-secondary">
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
