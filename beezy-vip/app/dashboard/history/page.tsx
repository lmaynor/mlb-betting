import { getPicks, getSummaryStats } from '@/lib/db'
import { apiGetPicks, apiGetStats } from '@/lib/betting-api'
import { StatCard }                   from '@/components/ui/primitives'
import type { Metadata }              from 'next'

export const dynamic = 'force-dynamic'

export const metadata: Metadata = { title: 'History — Dashboard' }
export const revalidate = 300

export default async function DashboardHistoryPage() {
  const [picks, stats] = await Promise.all([
    apiGetPicks({ status: 'settled', limit: 200 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  const totalPnl  = picks.reduce((s, p) => s + (p.profit ?? 0), 0)
  const wins      = picks.filter(p => p.result === 'win').length
  const losses    = picks.filter(p => p.result === 'loss').length
  const winRate   = wins + losses > 0 ? (wins / (wins + losses) * 100).toFixed(1) : '—'

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 border border-[var(--border)] mb-8">
        <StatCard label="Total Bets" value={String(picks.length)} />
        <StatCard label="Win Rate"   value={`${winRate}%`} />
        <StatCard
          label="Total P&L"
          value={`${totalPnl >= 0 ? '+' : ''}${totalPnl.toFixed(1)}u`}
          accent={totalPnl > 0}
        />
        <StatCard label="Systems"    value={String(stats.length)} />
      </div>

      {/* Per-system table */}
      {stats.length > 0 && (
        <div className="border border-[var(--border)] mb-8 overflow-x-auto">
          <div className="grid grid-cols-5 min-w-[500px] border-b border-[var(--border)] bg-[var(--surface)]">
            {['System', 'Bets', 'Win Rate', 'ROI', 'P&L'].map(h => (
              <div key={h} className="px-4 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
            ))}
          </div>
          {stats.map(s => (
            <div key={s.system} className="grid grid-cols-5 min-w-[500px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors">
              <div className="px-4 py-3 mono text-xs font-bold text-accent">{s.system}</div>
              <div className="px-4 py-3 mono text-xs text-text">{s.total_bets}</div>
              <div className="px-4 py-3 mono text-xs text-text">{parseFloat(String(s.win_rate)).toFixed(1)}%</div>
              <div className={`px-4 py-3 mono text-xs font-semibold ${parseFloat(String(s.roi)) > 0 ? 'text-win' : 'text-loss'}`}>
                {parseFloat(String(s.roi)) > 0 ? '+' : ''}{parseFloat(String(s.roi)).toFixed(1)}%
              </div>
              <div className={`px-4 py-3 mono text-sm font-semibold ${parseFloat(String(s.total_pnl)) > 0 ? 'text-win' : 'text-loss'}`}>
                {parseFloat(String(s.total_pnl)) > 0 ? '+' : ''}{parseFloat(String(s.total_pnl)).toFixed(2)}u
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Recent history */}
      <h2 className="text-sm font-extrabold uppercase tracking-wide mb-4">All Settled Bets</h2>
      <div className="border border-[var(--border)] overflow-x-auto">
        <div
          className="grid min-w-[700px] border-b border-[var(--border)] bg-[var(--surface)]"
          style={{ gridTemplateColumns: '0.8fr 0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
        >
          {['Date', 'System', 'Game', 'Pick', 'Line', 'Edge', 'Result', 'P&L'].map(h => (
            <div key={h} className="px-3 py-3 mono text-xs uppercase tracking-widest text-muted">{h}</div>
          ))}
        </div>

        {picks.map((pick, i) => {
          const edge = ((pick.model_prob - pick.market_prob) * 100).toFixed(1)
          return (
            <div
              key={i}
              className="grid min-w-[700px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
              style={{ gridTemplateColumns: '0.8fr 0.8fr 1.2fr 1fr 0.8fr 0.8fr 0.8fr 0.8fr' }}
            >
              <div className="px-3 py-3 mono text-xs text-muted">
                {new Date(pick.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
              </div>
              <div className="px-3 py-3 mono text-xs font-semibold text-accent">{pick.system}</div>
              <div className="px-3 py-3 mono text-xs text-muted">Game {pick.game_pk}</div>
              <div className="px-3 py-3 mono text-xs text-text">{pick.bet_type}</div>
              <div className="px-3 py-3 mono text-xs text-text">
                {pick.odds > 0 ? '+' : ''}{pick.odds}
              </div>
              <div className="px-3 py-3 mono text-xs text-accent">+{edge}%</div>
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
          )
        })}
      </div>
    </div>
  )
}
