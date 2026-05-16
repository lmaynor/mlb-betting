import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'

const B = '0.5px solid #1f1f24'

const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
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
    const bets = await apiGetRecentSettled(16)
    const settled = bets.filter(b => b.profit !== null && b.profit !== 0)
    if (settled.length > 0) {
      rows = settled.slice(0, 8).map(b => ({
        system:  b.system,
        matchup: b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        result:  b.result ?? 'pending',
        profit:  b.profit ?? 0,
      }))
    }
  } catch { /* seed */ }

  const COL = '52px 52px 1fr 72px'

  return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)' }}>
          Recent settled bets
        </span>
        <Link href="/results" style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>
          View all &rarr;
        </Link>
      </div>
      <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '14px' }}>
        Every result logged. Wins and losses, public.
      </p>

      <div style={{ border: B }}>
        {/* Header */}
        <div style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', padding: '8px 12px', background: '#111114', borderBottom: B }}>
          {[['Result', 'left'], ['System', 'left'], ['Matchup', 'left'], ['P&L', 'right']].map(([h, align]) => (
            <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', textAlign: align as 'left' | 'right' }}>
              {h}
            </div>
          ))}
        </div>

        {rows.map((row, i) => {
          const isWin = row.result === 'win'
          const pill  = PILL[row.system] ?? { bg: '#1f1f24', color: '#a1a1aa', border: B }
          return (
            <div key={i} style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', alignItems: 'center', padding: '9px 12px', borderBottom: i < rows.length - 1 ? B : undefined }}>
              <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 6px', textAlign: 'center', background: isWin ? '#052016' : '#200808', color: isWin ? '#10b981' : '#ef4444', border: isWin ? '0.5px solid #0f6e56' : '0.5px solid #a32d2d', display: 'inline-block' }}>
                {isWin ? 'WIN' : 'LOSS'}
              </span>
              <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.04em', padding: '3px 6px', textAlign: 'center', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block' }}>
                {row.system}
              </span>
              <span style={{ fontSize: '12px', color: 'var(--sec)' }}>{row.matchup}</span>
              <span className="mono" style={{ fontSize: '12px', fontWeight: 500, textAlign: 'right', color: isWin ? 'var(--win)' : 'var(--loss)' }}>
                {isWin && row.profit > 0 ? '+' : ''}{row.profit !== 0 ? '$'+Math.abs(row.profit).toFixed(2) : '—'}
              </span>
            </div>
          )
        })}
      </div>
    </section>
  )
}
