import { apiGetRecentSettled as getRecentSettled } from '@/lib/betting-api'
import { formatOdds } from '@/lib/odds'
import { clsx } from 'clsx'

// Seed data used when DB unavailable
const SEED_TICKS = [
  { result: 'W', system: 'NRFI', game: 'NYY @ BOS', pnl: 19.10 },
  { result: 'L', system: 'HR',   game: 'LAD @ SF',  pnl: -10.0 },
  { result: 'W', system: 'K',    game: 'HOU @ TEX', pnl: 23.40 },
  { result: 'W', system: 'F5',   game: 'ATL @ PHI', pnl: 15.20 },
  { result: 'W', system: 'NRFI', game: 'CHC @ MIL', pnl: 18.70 },
  { result: 'L', system: 'OUTS', game: 'SD @ COL',  pnl: -8.50 },
  { result: 'W', system: 'HR',   game: 'NYM @ WSH', pnl: 27.30 },
  { result: 'W', system: 'K',    game: 'SEA @ OAK', pnl: 12.90 },
]

export async function LiveTicker() {
  let ticks = SEED_TICKS

  try {
    const bets = await getRecentSettled(30)
    if (bets.length > 0) {
      ticks = bets.map(b => ({
        result: b.result ?? 'push',
        system: b.system,
        game:   b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        pnl:    b.profit ?? 0,
      }))
    }
  } catch {
    // Use seed data
  }

  // Duplicate for seamless scroll
  const doubled = [...ticks, ...ticks]

  return (
    <section className="border-b border-[var(--border)] bg-[var(--surface)] overflow-hidden py-3">
      <div className="ticker-track">
        {doubled.map((t, i) => (
          <span
            key={i}
            className={clsx(
              'mono text-xs font-semibold tracking-wide whitespace-nowrap px-6 flex items-center gap-2',
              t.result === 'W' ? 'text-win' : t.result === 'L' ? 'text-loss' : 'text-muted'
            )}
          >
            <span>{t.result}</span>
            <span className="text-muted">·</span>
            <span>{t.system}</span>
            <span className="text-muted">·</span>
            <span>{t.game}</span>
            <span className="text-muted">·</span>
            <span>{t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}u</span>
            <span className="text-[var(--border-bright)] px-4">|</span>
          </span>
        ))}
      </div>
    </section>
  )
}
