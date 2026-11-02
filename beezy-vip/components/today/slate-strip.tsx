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
    <div className="overflow-x-auto pb-1" style={{ WebkitOverflowScrolling: 'touch' }}>
      <div className="flex gap-[10px] py-3 px-4" style={{ minWidth: 'max-content' }}>
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
            <div className="min-w-[116px] py-[10px] px-3 bg-background-primary-default flex flex-col gap-[6px] shrink-0" key={key} style={{
              border: `1px solid ${hasPick ? `color-mix(in oklab, ${color} 40%, var(--color-background-full))` : 'var(--color-separator-border)'}`,
              borderRadius: 'var(--radius-lg)',
            }}>
              <div className="mono text-mini font-semibold text-text-primary whitespace-nowrap">
                {away} @ {home}
              </div>
              {hasPick && topPick ? (
                <div className="flex items-center gap-1.5">
                  <span className="ui-heading text-micro font-semibold tracking-fine py-[2px] px-[6px]" style={{
                    background: `color-mix(in oklab, ${sysColor} 16%, var(--color-background-full))`,
                    color: sysColor,
                    border: `1px solid color-mix(in oklab, ${sysColor} 40%, var(--color-background-full))`,
                    borderRadius: 'var(--radius-pill)',
                  }}>
                    {topPick.system}
                  </span>
                  <span className="mono" style={{ fontSize: '11px', fontWeight: 700, color }}>
                    {score}
                  </span>
                </div>
              ) : (
                <div className="mono text-micro text-text-secondary">
                  no pick
                </div>
              )}
              {gamePicks.length > 1 && (
                <div className="mono text-micro text-text-secondary">
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
