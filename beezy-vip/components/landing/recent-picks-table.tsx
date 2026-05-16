import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'

const SYSTEM_PILL: Record<string, string> = {
  NRFI: 'background:#052016;color:#10b981;border:.5px solid #0f6e56',
  HR:   'background:#1c1207;color:#f59e0b;border:.5px solid #854f0b',
  F5:   'background:#040e1c;color:#3b82f6;border:.5px solid #185fa5',
  K:    'background:#0e0718;color:#a78bfa;border:.5px solid #534ab7',
  OUTS: 'background:#1a0d05;color:#fb923c;border:.5px solid #9a3412',
}

const SEED = [
  { system: 'NRFI', matchup: 'NYY @ BOS', result: 'win',  profit:  12.40 },
  { system: 'OUTS', matchup: 'STL @ OAK', result: 'loss', profit: -22.43 },
  { system: 'K',    matchup: 'LAD @ SF',  result: 'win',  profit:  18.20 },
  { system: 'OUTS', matchup: 'WSH @ CIN', result: 'loss', profit: -19.20 },
  { system: 'OUTS', matchup: 'SEA @ HOU', result: 'win',  profit:  27.46 },
  { system: 'K',    matchup: 'SEA @ HOU', result: 'loss', profit: -41.45 },
  { system: 'F5',   matchup: 'ATL @ NYM', result: 'win',  profit:   9.80 },
  { system: 'HR',   matchup: 'TEX @ MIN', result: 'loss', profit: -10.00 },
]

export async function RecentPicksTable() {
  let rows = SEED

  try {
    const bets = await apiGetRecentSettled(8)
    if (bets.length > 0) {
      rows = bets.map(b => ({
        system:  b.system,
        matchup: b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        result:  b.result ?? 'pending',
        profit:  b.profit ?? 0,
      }))
    }
  } catch { /* seed */ }

  return (
    <section className="px-5 py-8 border-b" style={{ borderColor: 'var(--border)' }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-1">
        <span className="mono text-[10px] tracking-widest uppercase" style={{ color: 'var(--muted)' }}>
          Recent settled bets
        </span>
        <Link href="/results" className="text-xs" style={{ color: 'var(--info)' }}>
          View all &rarr;
        </Link>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--muted)' }}>
        Every result logged. Wins and losses, public.
      </p>

      {/* Blotter */}
      <div className="border" style={{ borderColor: 'var(--border)' }}>
        {/* Column header */}
        <div
          className="grid grid-cols-[52px_52px_1fr_72px] gap-2 px-3 py-2 border-b"
          style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}
        >
          {['Result', 'System', 'Matchup', 'Units'].map((h, i) => (
            <div
              key={h}
              className="mono text-[9px] tracking-widest uppercase"
              style={{ color: 'var(--muted)', textAlign: i === 3 ? 'right' : 'left' }}
            >
              {h}
            </div>
          ))}
        </div>

        {/* Rows */}
        {rows.map((row, i) => {
          const isWin = row.result === 'win'
          const pillStyle = SYSTEM_PILL[row.system] ?? 'background:#1f1f24;color:#a1a1aa'
          return (
            <div
              key={i}
              className="grid grid-cols-[52px_52px_1fr_72px] gap-2 items-center px-3 py-2.5 border-b"
              style={{
                borderColor: 'var(--border)',
                borderBottomWidth: i === rows.length - 1 ? 0 : undefined,
              }}
            >
              {/* Result pill */}
              <span
                className="mono text-[9px] font-semibold tracking-wider px-1.5 py-0.5 text-center inline-block"
                style={
                  isWin
                    ? { background: '#052016', color: '#10b981', border: '.5px solid #0f6e56' }
                    : { background: '#200808', color: '#ef4444', border: '.5px solid #a32d2d' }
                }
              >
                {isWin ? 'WIN' : 'LOSS'}
              </span>

              {/* System pill */}
              <span
                className="mono text-[9px] font-semibold tracking-wider px-1.5 py-0.5 text-center inline-block"
                style={Object.fromEntries(
                  pillStyle.split(';').filter(Boolean).map(s => {
                    const [k, v] = s.split(':')
                    return [k.trim().replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v.trim()]
                  })
                )}
              >
                {row.system}
              </span>

              {/* Matchup */}
              <span className="text-xs" style={{ color: 'var(--sec)' }}>
                {row.matchup}
              </span>

              {/* Units */}
              <span
                className="mono text-xs font-medium text-right"
                style={{ color: isWin ? 'var(--win)' : 'var(--loss)' }}
              >
                {isWin ? '+' : ''}{row.profit.toFixed(2)}u
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
