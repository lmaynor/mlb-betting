import { apiGetTodayPicks as getTodayPicks, apiGetStats } from '@/lib/betting-api'
import { kellyStake, formatOdds } from '@/lib/odds'
import { SystemBadge, StatCard, LiveDot, ScoreBadge } from '@/components/ui/primitives'
import { beezyscore } from '@/lib/beezy-score'

function ProbBar({ modelProb, marketProb }: { modelProb: number; marketProb: number }) {
  const modelPct  = (modelProb  * 100).toFixed(1)
  const marketPct = (marketProb * 100).toFixed(1)
  const edgePct   = ((modelProb - marketProb) * 100).toFixed(1)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: '120px' }}>
      <div style={{ position: 'relative', height: '6px', background: '#1f1f24', borderRadius: '3px', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${modelProb * 100}%`, background: '#9ab6c840' }} />
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${modelProb * 100}%`, background: '#b3bd95' }} />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'monospace', fontSize: '10px' }}>
        <span style={{ color: '#b3bd95' }}>{modelPct}% model</span>
        <span style={{ color: '#888890' }}>{marketPct}% implied</span>
        <span style={{ color: '#c0d4a7', fontWeight: 700 }}>+{edgePct}%</span>
      </div>
    </div>
  )
}
import { CopyBetButton }                        from '@/components/ui/copy-bet-button'
import { currentUser }                         from '@clerk/nextjs/server'
import type { Metadata } from 'next'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = { title: "Today's Picks — Dashboard" }
export const revalidate = 60

// Default bankroll — will be replaced by user setting in Session 10
const DEFAULT_BANKROLL = 1000

export default async function DashboardPicksPage() {
  const [picks, stats, user] = await Promise.all([
    getTodayPicks().catch(() => []),
    apiGetStats().then(s => s.overall).catch(() => ({ total_bets: '0', win_rate: '0', roi: '0', avg_edge: '0' })),
    currentUser().catch(() => null),
  ])

  const bankroll = (user?.publicMetadata?.bankroll as number) ?? DEFAULT_BANKROLL

  const pending = picks.filter(p => p.result === null).sort((a, b) => beezyscore(b) - beezyscore(a))
  const settled = picks.filter(p => p.result !== null)

  return (
    <div>
      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-8">
        <StatCard label="Today's Picks" value={String(pending.length)} sub="Pending" />
        <StatCard label="Season ROI"    value={`${parseFloat(String(stats.roi)) > 0 ? '+' : ''}${parseFloat(String(stats.roi)).toFixed(1)}%`} accent />
        <StatCard label="Win Rate"      value={`${parseFloat(String(stats.win_rate)).toFixed(1)}%`} />
        <StatCard label="Settled Bets"  value={String(stats.total_bets)} />
      </div>

      {/* Pending picks */}
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-sm font-extrabold uppercase tracking-wide">Today&apos;s Picks</h2>
        <LiveDot />
      </div>

      {pending.length === 0 ? (
        <div className="border border-[var(--border)] p-12 text-center mb-8">
          <p className="mono text-xs text-muted">No qualifying picks for today yet.</p>
          <p className="mono text-xs text-muted mt-1">Model runs in Central Time after lineups post.</p>
        </div>
      ) : (
        <div className="border border-[var(--border)] mb-8 overflow-x-auto">
          <div
            className="grid min-w-[900px] border-b border-[var(--border)] bg-[var(--surface)]"
            style={{ gridTemplateColumns: '60px 0.8fr 1.2fr 1fr 0.7fr 1.8fr 0.8fr 1fr' }}
          >
            {['Score', 'System', 'Game', 'Pick', 'Line', 'Win Probability', 'Half Kelly', 'Stake ($1k)'].map(h => (
              <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>

          {pending.map((pick, i) => {
            const kelly = kellyStake(bankroll, pick.odds, pick.model_prob)

            return (
              <div
                key={i}
                className="grid min-w-[900px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
                style={{ gridTemplateColumns: '60px 0.8fr 1.2fr 1fr 0.7fr 1.8fr 0.8fr 1fr' }}
              >
                <div className="px-3 py-4 flex items-center justify-center"><ScoreBadge bet={pick} /></div>
                <div className="px-3 py-4"><SystemBadge system={pick.system} /></div>
                <div className="px-3 py-4 mono text-xs text-muted">
                  {pick.away_team && pick.home_team ? `${pick.away_team} @ ${pick.home_team}` : `Game ${pick.game_pk}`}
                </div>
                <div className="px-3 py-4 mono text-xs font-semibold text-text">{pick.bet_type}</div>
                <div className="px-3 py-4 mono text-xs text-text">{formatOdds(pick.odds)}</div>
                <div className="px-3 py-4">
                  <ProbBar modelProb={pick.model_prob} marketProb={pick.market_prob} />
                </div>
                <div className="px-3 py-4 mono text-sm font-bold text-text">
                  {kelly.halfKelly}%
                </div>
                <div className="px-3 py-4 flex items-center gap-2">
                  <span className="mono text-sm font-bold text-win">
                    ${kelly.stakeHalfDollars.toFixed(2)}
                  </span>
                  <CopyBetButton
                    text={`${pick.system} · ${pick.bet_type} · ${formatOdds(pick.odds)} · Stake: $${kelly.stakeHalfDollars.toFixed(2)}`}
                  />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Today's settled */}
      {settled.length > 0 && (
        <>
          <h2 className="text-sm font-extrabold uppercase tracking-wide mb-4">Settled Today</h2>
          <div className="border border-[var(--border)] overflow-x-auto">
            <div
              className="grid min-w-[700px] border-b border-[var(--border)] bg-[var(--surface)]"
              style={{ gridTemplateColumns: '0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr' }}
            >
              {['System', 'Game', 'Pick', 'Line', 'Result', 'P&L'].map(h => (
                <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
              ))}
            </div>
            {settled.map((pick, i) => (
              <div
                key={i}
                className="grid min-w-[700px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
                style={{ gridTemplateColumns: '0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr' }}
              >
                <div className="px-3 py-3"><SystemBadge system={pick.system} /></div>
                <div className="px-3 py-3 mono text-xs text-muted">
                  {pick.away_team && pick.home_team ? `${pick.away_team} @ ${pick.home_team}` : `Game ${pick.game_pk}`}
                </div>
                <div className="px-3 py-3 mono text-xs text-text">{pick.bet_type}</div>
                <div className="px-3 py-3 mono text-xs text-text">{formatOdds(pick.odds)}</div>
                <div className="px-3 py-3">
                  <span className={`mono text-xs font-semibold ${pick.result === 'win' ? 'text-win' : pick.result === 'loss' ? 'text-loss' : 'text-muted'}`}>
                    {pick.result}
                  </span>
                </div>
                <div className="px-3 py-3 mono text-sm font-semibold">
                  <span className={pick.profit !== null && pick.profit >= 0 ? 'text-win' : 'text-loss'}>
                    {pick.profit !== null ? `${pick.profit >= 0 ? '+' : ''}${pick.profit.toFixed(2)}u` : '—'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
