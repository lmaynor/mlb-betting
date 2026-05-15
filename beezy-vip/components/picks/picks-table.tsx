import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'

export function PicksTable({ picks }: { picks: Bet[] }) {
  if (picks.length === 0) {
    return (
      <div className="text-center py-24 border border-[var(--border)]">
        <p className="mono text-muted text-sm">No picks found for the selected filters.</p>
      </div>
    )
  }

  return (
    <div className="border border-[var(--border)] overflow-x-auto">
      {/* Header */}
      <div className="grid grid-cols-8 min-w-[700px] border-b border-[var(--border)] bg-[var(--surface)]">
        {['Date', 'System', 'Game', 'Pick', 'Line', 'Edge', 'Result', 'P&L'].map(h => (
          <div key={h} className="px-4 py-3 mono text-xs tracking-widest uppercase text-muted">
            {h}
          </div>
        ))}
      </div>

      {/* Rows */}
      {picks.map((bet, i) => {
        const edge = ((bet.model_prob - bet.market_prob) * 100).toFixed(1)
        const game = bet.home_team
          ? `${bet.away_team} @ ${bet.home_team}`
          : `Game ${bet.game_pk}`

        return (
          <div
            key={bet.id ?? i}
            className="grid grid-cols-8 min-w-[700px] border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
          >
            <div className="px-4 py-3 mono text-xs text-muted">
              {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </div>
            <div className="px-4 py-3">
              <SystemBadge system={bet.system} />
            </div>
            <div className="px-4 py-3 mono text-xs text-muted truncate">{game}</div>
            <div className="px-4 py-3 mono text-xs text-text">{bet.bet_type}</div>
            <div className="px-4 py-3 mono text-xs text-text">{formatOdds(bet.odds)}</div>
            <div className="px-4 py-3 mono text-xs text-accent">
              {parseFloat(edge) > 0 ? '+' : ''}{edge}%
            </div>
            <div className="px-4 py-3">
              <ResultPill result={bet.result} />
            </div>
            <div className="px-4 py-3">
              <PnL value={bet.profit} />
            </div>
          </div>
        )
      })}
    </div>
  )
}
