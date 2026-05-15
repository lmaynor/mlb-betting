import { Suspense }     from 'react'
import { PicksTable }   from '@/components/picks/picks-table'
import { StatCard }     from '@/components/ui/primitives'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title:       'Results — Full History',
  description: 'Complete public results for all Beezy.VIP MLB betting systems. Every bet, every result, wins and losses.',
}

export const revalidate = 300

export default async function ResultsPage() {
  const [picks, stats] = await Promise.all([
    getPicks({ status: 'settled', limit: 100 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  const overall = stats.reduce(
    (acc, s) => ({
      bets:  acc.bets  + parseInt(String(s.total_bets)),
      wins:  acc.wins  + parseInt(String(s.wins)),
      pnl:   acc.pnl   + parseFloat(String(s.total_pnl)),
    }),
    { bets: 0, wins: 0, pnl: 0 }
  )

  const winRate = overall.bets > 0 ? (overall.wins / overall.bets * 100).toFixed(1) : '—'

  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-8">
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Results</h1>
        <p className="mono text-xs text-muted">
          All settled bets · Paper mode · Past performance is not indicative of future results
        </p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-8">
        <StatCard label="Total Bets"  value={String(overall.bets)} />
        <StatCard label="Win Rate"    value={`${winRate}%`} />
        <StatCard label="Total P&L"   value={`${overall.pnl >= 0 ? '+' : ''}${overall.pnl.toFixed(1)}u`} accent={overall.pnl > 0} />
        <StatCard label="Systems"     value={String(stats.length)} sub="MLB models" />
      </div>

      {/* Per-system breakdown */}
      {stats.length > 0 && (
        <div className="border border-[var(--border)] mb-8 overflow-x-auto">
          <div className="grid grid-cols-6 min-w-[500px] border-b border-[var(--border)] bg-[var(--surface)]">
            {['System', 'Bets', 'Win Rate', 'ROI', 'P&L', 'Avg Edge'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {stats.map(s => (
            <div key={s.system} className="grid grid-cols-6 min-w-[500px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors">
              <div className="px-4 py-3 mono text-xs font-semibold text-accent">{s.system}</div>
              <div className="px-4 py-3 mono text-xs text-text">{s.total_bets}</div>
              <div className="px-4 py-3 mono text-xs text-text">{parseFloat(String(s.win_rate)).toFixed(1)}%</div>
              <div className={`px-4 py-3 mono text-xs font-semibold ${parseFloat(String(s.roi)) > 0 ? 'text-win' : 'text-loss'}`}>
                {parseFloat(String(s.roi)) > 0 ? '+' : ''}{parseFloat(String(s.roi)).toFixed(1)}%
              </div>
              <div className={`px-4 py-3 mono text-xs font-semibold ${parseFloat(String(s.total_pnl)) > 0 ? 'text-win' : 'text-loss'}`}>
                {parseFloat(String(s.total_pnl)) > 0 ? '+' : ''}{parseFloat(String(s.total_pnl)).toFixed(2)}u
              </div>
              <div className="px-4 py-3 mono text-xs text-accent">
                +{parseFloat(String(s.avg_edge)).toFixed(1)}%
              </div>
            </div>
          ))}
        </div>
      )}

      <PicksTable picks={picks} />
    </div>
  )
}
