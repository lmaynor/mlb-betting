import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { B, SYSTEM_PILL } from '@/lib/tokens'

export async function RecentPicksTable() {
  type Row = {
    system: string
    matchup: string
    result: string
    profit: number
    score: number
    tier: ReturnType<typeof scoreTier>
  }
  let rows: Row[] = []

  try {
    const bets = await apiGetRecentSettled(16)
    rows = bets.slice(0, 8).map(b => {
      const score = beezyscore(b)
      return {
        system: b.system,
        matchup: b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        result: b.result ?? 'pending',
        profit: b.profit ?? 0,
        score,
        tier: scoreTier(score),
      }
    })
  } catch {
    rows = []
  }

  const COL = '58px 56px 58px 1fr 74px'

  return (
    <section style={{ padding: '24px 20px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px', gap: '12px' }}>
        <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)' }}>
          Recent graded plays
        </span>
        <Link href="/results" style={{ fontSize: '11px', color: '#3b82f6', textDecoration: 'none' }}>
          View results
        </Link>
      </div>
      <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '14px' }}>
        A quick receipt trail for the score-ranked card.
      </p>

      <div style={{ border: B, borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {rows.length === 0 && (
          <div style={{ padding: '20px 12px', textAlign: 'center', color: 'var(--muted)', fontSize: '12px' }}>
            No settled bets yet. Check back after today&apos;s games.
          </div>
        )}
        {rows.length > 0 && (
          <>
            <div className="blotter-grid" style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', padding: '8px 12px', background: '#111114', borderBottom: B }}>
              {[['Score', 'left'], ['Result', 'left'], ['System', 'left'], ['Matchup', 'left'], ['P&L', 'right']].map(([h, align]) => (
                <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', textAlign: align as 'left' | 'right' }}>
                  {h}
                </div>
              ))}
            </div>

            {rows.map((row, i) => {
              const isWin = row.result === 'win'
              const pill = SYSTEM_PILL[row.system] ?? SYSTEM_PILL.ALL
              const tColor = TIER_COLOR[row.tier]
              return (
                <div key={i} className="blotter-grid" style={{ display: 'grid', gridTemplateColumns: COL, gap: '10px', alignItems: 'center', padding: '10px 12px', borderBottom: i < rows.length - 1 ? B : undefined, background: '#0d0d11' }}>
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: '2px' }}>
                    <span className="mono" style={{ fontSize: '15px', fontWeight: 850, color: tColor, lineHeight: 1 }}>{row.score}</span>
                    <span className="mono" style={{ fontSize: '7px', fontWeight: 800, letterSpacing: '0.08em', padding: '1px 4px', borderRadius: 'var(--radius-sm)', border: `0.5px solid ${tColor}44`, background: `${tColor}12`, color: tColor }}>
                      {TIER_LABEL[row.tier].replace(' PLAY', '')}
                    </span>
                  </div>
                  <div>
                    <span className="mono" style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.06em', padding: '3px 6px', borderRadius: 'var(--radius-sm)', background: isWin ? '#052016' : '#200808', color: isWin ? '#10b981' : '#ef4444', border: isWin ? '0.5px solid #0f6e56' : '0.5px solid #a32d2d', display: 'inline-block' }}>
                      {isWin ? 'WIN' : 'LOSS'}
                    </span>
                  </div>
                  <div>
                    <span className="mono" style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.04em', padding: '3px 6px', borderRadius: 'var(--radius-sm)', background: pill.bg, color: pill.color, border: pill.border, display: 'inline-block' }}>
                      {row.system}
                    </span>
                  </div>
                  <span style={{ fontSize: '12px', color: 'var(--sec)' }}>{row.matchup}</span>
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 700, textAlign: 'right', color: isWin ? 'var(--win)' : 'var(--loss)' }}>
                    {row.profit >= 0 ? '+' : ''}{(row.profit / 10).toFixed(2)}u
                  </span>
                </div>
              )
            })}
          </>
        )}
      </div>
    </section>
  )
}
