import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { B, SYSTEM_PILL, TEAM_ABBREV, pickLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

function resultTone(result: string | null) {
  const r = result?.toLowerCase()
  // Dell tint palette: sage=win, salmon=loss, steel=push/void, sky=pending
  if (r === 'win')  return { label: 'WIN',     color: '#b3bd95', bg: '#1a2218', border: '#8e9e78' }
  if (r === 'loss') return { label: 'LOSS',    color: '#d77a7a', bg: '#2a1818', border: '#b05050' }
  if (r === 'push') return { label: 'PUSH',    color: '#a5b8c0', bg: '#131a1e', border: '#7a9aa5' }
  if (r === 'void') return { label: 'VOID',    color: '#888890', bg: '#101014', border: '#3f3f46' }
  return             { label: 'PENDING', color: '#9ab6c8', bg: '#131e24', border: '#6a8fa0' }
}

function units(profit: number | null, stake: number | null) {
  if (profit == null) return '--'
  const unitSize = stake && stake > 0 ? stake : 10
  return `${profit >= 0 ? '+' : ''}${(profit / unitSize).toFixed(2)}u`
}

function matchup(bet: Bet) {
  const away = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team
  const home = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team
  return away && home ? `${away} @ ${home}` : `Game ${bet.game_pk}`
}

function pickText(bet: Bet) {
  const label = pickLabel(bet)
  if (!bet.player) return label
  return label.replace(bet.player, '').trim().replace(/^[-\s]+/, '') || label
}

function ScoreMark({ score, tier }: { score: number; tier: ReturnType<typeof scoreTier> }) {
  const color = TIER_COLOR[tier]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
      <span className="mono" style={{ fontSize: '16px', fontWeight: 850, color, lineHeight: 1 }}>{score}</span>
      <span className="dell-heading" style={{ fontSize: '8px', letterSpacing: '0.08em', padding: '2px 5px', border: `1px solid ${color}`, background: `${color}18`, color, whiteSpace: 'nowrap' }}>
        {TIER_LABEL[tier].replace(' PLAY', '')}
      </span>
    </div>
  )
}

function ResultPill({ result }: { result: string | null }) {
  const tone = resultTone(result)
  return (
    <span className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.06em', padding: '3px 7px', background: tone.bg, color: tone.color, border: `1px solid ${tone.border}`, display: 'inline-flex', width: 'fit-content' }}>
      {tone.label}
    </span>
  )
}

export async function RecentPicksTable() {
  type Row = {
    bet: Bet
    score: number
    tier: ReturnType<typeof scoreTier>
  }
  let rows: Row[] = []

  try {
    const bets = await apiGetRecentSettled(16)
    rows = bets.slice(0, 8).map(b => {
      const score = beezyscore(b)
      return {
        bet: b,
        score,
        tier: scoreTier(score),
      }
    })
  } catch {
    rows = []
  }

  const COL = '112px 78px minmax(116px, 0.7fr) minmax(260px, 1.4fr) 72px'

  return (
    <section style={{ padding: '40px 20px 32px', borderBottom: B }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: '4px', gap: '12px' }}>
        <span className="dell-display" style={{ fontSize: '14px', color: 'var(--text)' }}>
          Recent Graded Plays
        </span>
        <Link href="/results" style={{ fontSize: '11px', fontFamily: 'Arial, Helvetica, sans-serif', fontWeight: 700, color: '#9999ff', textDecoration: 'underline' }}>
          View results
        </Link>
      </div>
      <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '14px' }}>
        Recent picks graded in the open — wins and losses alike, scored before the first pitch.
      </p>

      <div style={{ border: B, borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)', overflow: 'hidden' }}>
        {rows.length === 0 && (
          <div style={{ padding: '20px 12px', textAlign: 'center', color: 'var(--muted)', fontSize: '12px' }}>
            No settled bets yet. Check back after today&apos;s games.
          </div>
        )}
        {rows.length > 0 && (
          <>
            <div className="home-recent-desktop">
            <div style={{ display: 'grid', gridTemplateColumns: COL, gap: '12px', padding: '8px 12px', background: '#111114', borderBottom: B }}>
              {[['Score', 'left'], ['Result', 'left'], ['Game', 'left'], ['Pick', 'left'], ['P&L', 'right']].map(([h, align]) => (
                <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--muted)', textAlign: align as 'left' | 'right' }}>
                  {h}
                </div>
              ))}
            </div>

            {rows.map((row, i) => {
              const { bet } = row
              const isPositive = (bet.profit ?? 0) >= 0
              const pill = SYSTEM_PILL[bet.system] ?? SYSTEM_PILL.ALL
              return (
                <div key={bet.id} style={{ display: 'grid', gridTemplateColumns: COL, gap: '12px', alignItems: 'center', padding: '11px 12px', borderBottom: i < rows.length - 1 ? B : undefined, background: '#0d0d11' }}>
                  <ScoreMark score={row.score} tier={row.tier} />
                  <ResultPill result={bet.result} />
                  <div className="mono" style={{ fontSize: '12px', color: '#a1a1aa', minWidth: 0 }}>
                    {matchup(bet)}
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '7px', minWidth: 0 }}>
                      <span className="mono" style={{ flexShrink: 0, fontSize: '9px', fontWeight: 800, letterSpacing: '0.04em', padding: '3px 6px', borderRadius: 'var(--radius-sm)', background: pill.bg, color: pill.color, border: pill.border }}>
                        {bet.system}
                      </span>
                      <span style={{ fontSize: '12px', color: '#f5f5f7', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {bet.player ?? pickText(bet)}
                      </span>
                    </div>
                    {bet.player && (
                      <div className="mono" style={{ marginTop: '3px', fontSize: '10px', color: '#888890', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {pickText(bet)}
                      </div>
                    )}
                  </div>
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 700, textAlign: 'right', color: isPositive ? '#b3bd95' : '#d77a7a' }}>
                    {units(bet.profit, bet.stake)}
                  </span>
                </div>
              )
            })}
            </div>

            <div className="home-recent-mobile" style={{ display: 'none' }}>
              {rows.map((row, i) => {
                const { bet } = row
                const pill = SYSTEM_PILL[bet.system] ?? SYSTEM_PILL.ALL
                const isPositive = (bet.profit ?? 0) >= 0
                return (
                  <div key={bet.id} style={{ padding: '12px', borderBottom: i < rows.length - 1 ? B : undefined, background: '#0d0d11' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px', marginBottom: '10px' }}>
                      <ScoreMark score={row.score} tier={row.tier} />
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '5px' }}>
                        <ResultPill result={bet.result} />
                        <span className="mono" style={{ fontSize: '12px', fontWeight: 800, color: isPositive ? '#b3bd95' : '#d77a7a' }}>
                          {units(bet.profit, bet.stake)}
                        </span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '7px' }}>
                      <span className="mono" style={{ fontSize: '9px', fontWeight: 800, letterSpacing: '0.04em', padding: '3px 6px', borderRadius: 'var(--radius-sm)', background: pill.bg, color: pill.color, border: pill.border }}>
                        {bet.system}
                      </span>
                      <span className="mono" style={{ fontSize: '12px', color: '#a1a1aa' }}>{matchup(bet)}</span>
                    </div>
                    <div style={{ fontSize: '14px', fontWeight: 780, color: '#f5f5f7', lineHeight: 1.25 }}>
                      {bet.player ?? pickText(bet)}
                    </div>
                    {bet.player && (
                      <div className="mono" style={{ marginTop: '3px', fontSize: '11px', color: '#888890', lineHeight: 1.35 }}>
                        {pickText(bet)}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </section>
  )
}
