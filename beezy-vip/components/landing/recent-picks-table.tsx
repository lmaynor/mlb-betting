import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { SYSTEM_PILL, pickLabel } from '@/lib/tokens'
import { Matchup } from '@/components/ui/matchup'
import type { Bet } from '@/lib/types'

function resultTone(result: string | null) {
  const r = result?.toLowerCase()
  if (r === 'win')  return { label: 'WIN',     color: 'var(--signal)', bg: 'var(--win-wash)',  border: 'var(--win-border)' }
  if (r === 'loss') return { label: 'LOSS',    color: 'var(--loss)',   bg: 'var(--loss-wash)', border: 'var(--loss-border)' }
  if (r === 'push') return { label: 'PUSH',    color: 'var(--silver)', bg: 'var(--slate)',     border: 'var(--iron)' }
  if (r === 'void') return { label: 'VOID',    color: 'var(--fog)',    bg: 'var(--slate)',     border: 'var(--iron)' }
  return             { label: 'PENDING', color: 'var(--link)',   bg: 'color-mix(in oklab, var(--link) 14%, var(--carbon))', border: 'color-mix(in oklab, var(--link) 38%, var(--carbon))' }
}

function units(profit: number | null, stake: number | null) {
  if (profit == null) return '--'
  const unitSize = stake && stake > 0 ? stake : 10
  return `${profit >= 0 ? '+' : ''}${(profit / unitSize).toFixed(2)}u`
}

function pickText(bet: Bet) {
  const label = pickLabel(bet)
  if (!bet.player) return label
  return label.replace(bet.player, '').trim().replace(/^[-\s]+/, '') || label
}

function ScoreMark({ score, tier }: { score: number; tier: ReturnType<typeof scoreTier> }) {
  const color = TIER_COLOR[tier]
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <span className="mono" style={{ fontSize: '16px', fontWeight: 800, color, lineHeight: 1 }}>{score}</span>
      <span className="dell-heading" style={{ fontSize: '8px', letterSpacing: '0.08em', padding: '2px 6px', borderRadius: 'var(--radius-pill)', border: `1px solid ${color}`, background: `color-mix(in oklab, ${color} 14%, var(--carbon))`, color, whiteSpace: 'nowrap' }}>
        {TIER_LABEL[tier].replace(' PLAY', '')}
      </span>
    </div>
  )
}

function ResultPill({ result }: { result: string | null }) {
  const tone = resultTone(result)
  return (
    <span className="dell-heading" style={{ fontSize: '9px', letterSpacing: '0.06em', padding: '3px 8px', borderRadius: 'var(--radius-pill)', background: tone.bg, color: tone.color, border: `1px solid ${tone.border}`, display: 'inline-flex', width: 'fit-content' }}>
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
    <section style={{ padding: '56px 0 0' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '24px', gap: '16px' }}>
        <div>
          <h2 className="dell-display" style={{ fontSize: '30px', color: 'var(--chalk)' }}>Recent graded plays</h2>
          <p className="times" style={{ fontSize: '15px', color: 'var(--fog)', marginTop: '8px' }}>
            Wins and losses alike, scored before the first pitch &mdash; settled in the open.
          </p>
        </div>
        <Link href="/results" className="times" style={{ fontSize: '14px', fontWeight: 600, color: 'var(--link)', textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}>
          View results &rarr;
        </Link>
      </div>

      <div style={{ border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-card)', overflow: 'hidden', background: 'var(--graphite)' }}>
        {rows.length === 0 && (
          <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px' }}>
            No settled bets yet. Check back after today&apos;s games.
          </div>
        )}
        {rows.length > 0 && (
          <>
            <div className="home-recent-desktop">
            <div style={{ display: 'grid', gridTemplateColumns: COL, gap: '12px', padding: '11px 16px', background: 'var(--obsidian)', borderBottom: '1px solid var(--basalt)' }}>
              {[['Score', 'left'], ['Result', 'left'], ['Game', 'left'], ['Pick', 'left'], ['P&L', 'right']].map(([h, align]) => (
                <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', textAlign: align as 'left' | 'right' }}>
                  {h}
                </div>
              ))}
            </div>

            {rows.map((row, i) => {
              const { bet } = row
              const isPositive = (bet.profit ?? 0) >= 0
              const pill = SYSTEM_PILL[bet.system] ?? SYSTEM_PILL.ALL
              return (
                <div key={bet.id} style={{ display: 'grid', gridTemplateColumns: COL, gap: '12px', alignItems: 'center', padding: '13px 16px', borderBottom: i < rows.length - 1 ? '1px solid #201f22' : undefined }}>
                  <ScoreMark score={row.score} tier={row.tier} />
                  <ResultPill result={bet.result} />
                  <div style={{ minWidth: 0 }}>
                    <Matchup away={bet.away_team} home={bet.home_team} size={16} fontSize="12px" />
                  </div>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
                      <span className="dell-heading" style={{ flexShrink: 0, fontSize: '9px', fontWeight: 600, letterSpacing: '0.04em', padding: '3px 7px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>
                        {bet.system}
                      </span>
                      <span style={{ fontSize: '13px', color: 'var(--ash)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {bet.player ?? pickText(bet)}
                      </span>
                    </div>
                    {bet.player && (
                      <div className="mono" style={{ marginTop: '3px', fontSize: '10px', color: 'var(--fog)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {pickText(bet)}
                      </div>
                    )}
                  </div>
                  <span className="mono" style={{ fontSize: '12px', fontWeight: 700, textAlign: 'right', color: isPositive ? 'var(--signal)' : 'var(--loss)' }}>
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
                  <div key={bet.id} style={{ padding: '14px 16px', borderBottom: i < rows.length - 1 ? '1px solid #201f22' : undefined }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px', marginBottom: '10px' }}>
                      <ScoreMark score={row.score} tier={row.tier} />
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '5px' }}>
                        <ResultPill result={bet.result} />
                        <span className="mono" style={{ fontSize: '12px', fontWeight: 800, color: isPositive ? 'var(--signal)' : 'var(--loss)' }}>
                          {units(bet.profit, bet.stake)}
                        </span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '7px' }}>
                      <span className="dell-heading" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.04em', padding: '3px 7px', borderRadius: 'var(--radius-pill)', background: pill.bg, color: pill.color, border: pill.border }}>
                        {bet.system}
                      </span>
                      <Matchup away={bet.away_team} home={bet.home_team} size={16} fontSize="12px" />
                    </div>
                    <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--ash)', lineHeight: 1.25 }}>
                      {bet.player ?? pickText(bet)}
                    </div>
                    {bet.player && (
                      <div className="mono" style={{ marginTop: '3px', fontSize: '11px', color: 'var(--fog)', lineHeight: 1.35 }}>
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
