import { apiGetTodayPicks as getTodayPicks, apiGetStats } from '@/lib/betting-api'
import { kellyStake, formatOdds } from '@/lib/odds'
import { SystemBadge, StatCard, LiveDot, ScoreBadge } from '@/components/ui/primitives'
import { beezyscore } from '@/lib/beezy-score'

function ProbBar({ modelProb, marketProb }: { modelProb: number; marketProb: number }) {
  const modelPct  = (modelProb  * 100).toFixed(1)
  const marketPct = (marketProb * 100).toFixed(1)
  const edgePct   = ((modelProb - marketProb) * 100).toFixed(1)
  return (
    <div className="flex min-w-[120px] flex-col gap-1">
      <div className="relative h-1.5 overflow-hidden rounded-[3px] bg-chart-track">
        <div className="absolute inset-y-0 left-0" style={{ width: `${modelProb * 100}%`, background: 'color-mix(in oklab, var(--signal) 28%, transparent)' }} />
        <div className="absolute inset-y-0 left-0 bg-signal" style={{ width: `${modelProb * 100}%` }} />
      </div>
      <div className="flex justify-between font-mono text-micro">
        <span className="text-signal">{modelPct}% model</span>
        <span className="text-text-secondary">{marketPct}% implied</span>
        <span className="font-bold text-signal">+{edgePct}%</span>
      </div>
    </div>
  )
}
import { CopyBetButton }                        from '@/components/ui/copy-bet-button'
import { getCurrentUser }                      from '@/lib/auth'
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
    getCurrentUser(),
  ])

  const bankroll = (user?.publicMetadata?.bankroll as number) ?? DEFAULT_BANKROLL

  const pending = picks.filter(p => p.result === null).sort((a, b) => beezyscore(b) - beezyscore(a))
  const settled = picks.filter(p => p.result !== null)

  return (
    <div>
      {/* Stats row */}
      <div className="mb-8 grid grid-cols-2 overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card md:grid-cols-4">
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
        <div className="mb-8 rounded-xl border border-separator-border bg-background-primary-default p-12 text-center shadow-card">
          <p className="mono text-xs text-text-secondary">No qualifying picks for today yet.</p>
          <p className="mono text-xs text-text-secondary mt-1">Model runs in Central Time after lineups post.</p>
        </div>
      ) : (
        <div className="mb-8 overflow-x-auto rounded-xl border border-separator-border bg-background-primary-default shadow-card">
          <div
            className="grid min-w-[900px] border-b border-border-table bg-background-secondary-default"
            style={{ gridTemplateColumns: '60px 0.8fr 1.2fr 1fr 0.7fr 1.8fr 0.8fr 1fr' }}
          >
            {['Score', 'System', 'Game', 'Pick', 'Line', 'Win Probability', 'Half Kelly', 'Stake ($1k)'].map(h => (
              <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-text-secondary">{h}</div>
            ))}
          </div>

          {pending.map((pick, i) => {
            const kelly = kellyStake(bankroll, pick.odds, pick.model_prob)

            return (
              <div
                key={i}
                className="grid min-w-[900px] border-b border-border-table hover:bg-background-primary-hover transition-colors"
                style={{ gridTemplateColumns: '60px 0.8fr 1.2fr 1fr 0.7fr 1.8fr 0.8fr 1fr' }}
              >
                <div className="px-3 py-4 flex items-center justify-center"><ScoreBadge bet={pick} /></div>
                <div className="px-3 py-4"><SystemBadge system={pick.system} /></div>
                <div className="px-3 py-4 mono text-xs text-text-secondary">
                  {pick.away_team && pick.home_team ? `${pick.away_team} @ ${pick.home_team}` : `Game ${pick.game_pk}`}
                </div>
                <div className="px-3 py-4 mono text-xs font-semibold text-text-primary">{pick.bet_type}</div>
                <div className="px-3 py-4 mono text-xs text-text-primary">{formatOdds(pick.odds)}</div>
                <div className="px-3 py-4">
                  <ProbBar modelProb={pick.model_prob} marketProb={pick.market_prob} />
                </div>
                <div className="px-3 py-4 mono text-sm font-bold text-text-primary">
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
          <div className="overflow-x-auto rounded-xl border border-separator-border bg-background-primary-default shadow-card">
            <div
              className="grid min-w-[700px] border-b border-border-table bg-background-secondary-default"
              style={{ gridTemplateColumns: '0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr' }}
            >
              {['System', 'Game', 'Pick', 'Line', 'Result', 'P&L'].map(h => (
                <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-text-secondary">{h}</div>
              ))}
            </div>
            {settled.map((pick, i) => (
              <div
                key={i}
                className="grid min-w-[700px] border-b border-border-table hover:bg-background-primary-hover transition-colors"
                style={{ gridTemplateColumns: '0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr' }}
              >
                <div className="px-3 py-3"><SystemBadge system={pick.system} /></div>
                <div className="px-3 py-3 mono text-xs text-text-secondary">
                  {pick.away_team && pick.home_team ? `${pick.away_team} @ ${pick.home_team}` : `Game ${pick.game_pk}`}
                </div>
                <div className="px-3 py-3 mono text-xs text-text-primary">{pick.bet_type}</div>
                <div className="px-3 py-3 mono text-xs text-text-primary">{formatOdds(pick.odds)}</div>
                <div className="px-3 py-3">
                  <span className={`mono text-xs font-semibold ${pick.result === 'win' ? 'text-win' : pick.result === 'loss' ? 'text-loss' : 'text-text-secondary'}`}>
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
