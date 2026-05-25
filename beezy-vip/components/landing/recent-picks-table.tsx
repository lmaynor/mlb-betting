import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'

const B = '0.5px solid #1f1f24'

const PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
}

// SEED removed -- P0.1: landing now shows real bets or empty state

export async function RecentPicksTable() {
  type Row = { system: string; matchup: string; result: string; profit: number; score: number; tier: ReturnType<typeof scoreTier> }
  let rows: Row[] = []

  try {
    const bets = await apiGetRecentSettled(16)
    rows = bets.slice(0, 8).map(b => {
      const score = beezyscore(b)
      return {
        system:  b.system,
        matchup: b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        result:  b.result ?? 'pending',
        profit:  b.profit ?? 0,
        score,
        tier:    scoreTier(score),
      }
    })
  } catch { /* leave rows empty */ }

  const COL = '52px 52px 52px 1fr 72px'

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
        {rows.length === 0 && (
          <div style={{ padding: '20px 12px', textAlign: 'center', color: 'var(--muted)', fontSize: '12px' }}>
            No settled bets yet &mdash; check back after today&apos;s games.
          </div>
        )}
        {rows.length > 0 && (<>
        {/* Header */}
        <div className="blotter-grid" style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', padding: '8px 12px', background: '#111114', borderBottom: B }}>
          {[['Score', 'left'], ['Result', 'left'], ['System', 'left'], ['Matchup', 'left'], ['P&L', 'right']].map(([h, align]) => (
            <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', textAlign: align as 'left' | 'right' }}>
              {h}
            </div>
          ))}
        </div>

        {rows.map((row, i) => {
          const isWin = row.result === 'win'
          const pill  = PILL[row.system] ?? { bg: '#1f1f24', color: '#a1a1aa', border: B }
          const tColor = TIER_COLOR[row.tier]
          return (
            <div key={i} className="blotter-grid" style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', alignItems: 'center', padding: '9px 12px', borderBottom: i < rows.length - 1 ? B : undefined }}>
              {/* Score + tier */}
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '2px' }}>
                <span className="mono" style={{ fontSize: '14px', fontWeight: 800, color: tColor, lineHeight: 1 }}>{row.score}</span>
                <span className="mono" style={{ fontSize: '7px', fontWeight: 600, letterSpacing: '0.08em', padding: '1px 4px', border: `0.5px solid ${tColor}44`, background: `${tColor}12`, color: tColor }}>
                  {TIER_LABEL[row.tier]}
                </span>
              </div>
              {/* Result pill */}
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 6px', background: isWin ? '#052016' : '#200808', color: isWin ? '#10b981' : '#ef4444', border: isWin ? '0.5px solid #0f6e56' : '0.5px solid #a32d2d', display: 'inline-block' }}>
                  {isWin ? 'WIN' : 'LOSS'}
                </span>
              </div>
              {/* System pill */}
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.04em', padding: '3px 6px', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block' }}>
                  {row.system}
                </span>
              </div>
              <span style={{ fontSize: '12px', color: 'var(--sec)' }}>{row.matchup}</span>
              <span className="mono" style={{ fontSize: '12px', fontWeight: 500, textAlign: 'right', color: isWin ? 'var(--win)' : 'var(--loss)' }}>
                {row.profit >= 0 ? '+' : ''}{(row.profit / 10).toFixed(2)}u
              </span>
            </div>
          )
        })}
        </>)}
      </div>
    </section>
  )
}
