import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { apiGetRecentSettled as getRecentSettled } from '@/lib/betting-api'
import { formatOdds } from '@/lib/odds'
import Link from 'next/link'

const SEED: Array<{
  system: string
  game:   string
  pick:   string
  line:   number
  edge:   number
  result: string
  pnl:    number
}> = [
  { system: 'NRFI', game: 'NYY @ BOS', pick: 'NRFI',         line: -115, edge: 6.2, result: 'W', pnl:  0.87 },
  { system: 'K',    game: 'HOU @ TEX', pick: 'Wheeler O7.5', line: -110, edge: 7.8, result: 'W', pnl:  0.91 },
  { system: 'HR',   game: 'LAD @ SF',  pick: 'Betts HR',     line: +280, edge: 5.1, result: 'L', pnl: -1.00 },
  { system: 'F5',   game: 'ATL @ PHI', pick: 'ATL -0.5',     line: -118, edge: 4.9, result: 'W', pnl:  0.85 },
  { system: 'NRFI', game: 'CHC @ MIL', pick: 'NRFI',         line: -120, edge: 5.5, result: 'W', pnl:  0.83 },
  { system: 'OUTS', game: 'SD @ COL',  pick: 'Snell O17.5',  line: -105, edge: 3.8, result: 'L', pnl: -1.00 },
  { system: 'K',    game: 'SEA @ OAK', pick: 'Gilbert O6.5', line: -115, edge: 8.1, result: 'W', pnl:  0.87 },
  { system: 'HR',   game: 'NYM @ WSH', pick: 'Alonso HR',    line: +230, edge: 6.3, result: 'W', pnl:  2.30 },
]

export async function RecentPicksTable() {
  let rows = SEED

  try {
    const bets = await getRecentSettled(8)
    if (bets.length > 0) {
      rows = bets.map(b => ({
        system: b.system,
        game:   b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`,
        pick:   b.bet_type,
        line:   b.odds,
        edge:   Math.round((b.model_prob - b.market_prob) * 100 * 10) / 10,
        result: b.result ?? 'PENDING',
        pnl:    b.profit ?? 0,
      }))
    }
  } catch { /* seed */ }

  return (
    <section className="max-w-7xl mx-auto px-4 py-20 border-b border-[var(--border)]">
      <div className="flex items-end justify-between mb-8">
        <div>
          <h2 className="text-2xl font-extrabold tracking-tight uppercase mb-2">Recent Picks</h2>
          <p className="text-muted text-xs mono">All results shown. Wins and losses.</p>
        </div>
        <Link
          href="/results"
          className="mono text-xs tracking-widest uppercase text-muted hover:text-accent transition-colors"
        >
          Full History →
        </Link>
      </div>

      <div className="relative border border-[var(--border)]">
        {/* Table header */}
        <div className="grid grid-cols-7 gap-0 border-b border-[var(--border)] bg-[var(--surface)]">
          {['System', 'Game', 'Pick', 'Line', 'Edge', 'Result', 'P&L'].map(h => (
            <div key={h} className="px-4 py-3 mono text-xs tracking-widest uppercase text-muted">
              {h}
            </div>
          ))}
        </div>

        {/* Rows 1-4 — fully visible */}
        {rows.slice(0, 4).map((row, i) => (
          <div
            key={i}
            className="grid grid-cols-7 gap-0 border-b border-[var(--border)] hover:bg-[var(--surface)] transition-colors"
          >
            <div className="px-4 py-3"><SystemBadge system={row.system} /></div>
            <div className="px-4 py-3 mono text-xs text-muted">{row.game}</div>
            <div className="px-4 py-3 mono text-xs text-text">{row.pick}</div>
            <div className="px-4 py-3 mono text-xs text-text">{formatOdds(row.line)}</div>
            <div className="px-4 py-3 mono text-xs text-accent">+{row.edge.toFixed(1)}%</div>
            <div className="px-4 py-3"><ResultPill result={row.result} /></div>
            <div className="px-4 py-3"><PnL value={row.pnl} /></div>
          </div>
        ))}

        {/* Rows 5+ — blurred with gate */}
        <div className="relative">
          {rows.slice(4).map((row, i) => (
            <div
              key={i}
              className="grid grid-cols-7 gap-0 border-b border-[var(--border)] select-none pointer-events-none"
              style={{ filter: 'blur(3px)', opacity: 0.5 }}
            >
              <div className="px-4 py-3"><SystemBadge system={row.system} /></div>
              <div className="px-4 py-3 mono text-xs text-muted">{row.game}</div>
              <div className="px-4 py-3 mono text-xs text-text">{row.pick}</div>
              <div className="px-4 py-3 mono text-xs text-text">{formatOdds(row.line)}</div>
              <div className="px-4 py-3 mono text-xs text-accent">+{row.edge.toFixed(1)}%</div>
              <div className="px-4 py-3"><ResultPill result={row.result} /></div>
              <div className="px-4 py-3"><PnL value={row.pnl} /></div>
            </div>
          ))}

          {/* Overlay */}
          <div className="absolute inset-0 flex items-center justify-center"
            style={{ background: 'linear-gradient(to bottom, transparent 0%, rgba(8,12,16,0.85) 40%, rgba(8,12,16,0.98) 100%)' }}
          >
            <div className="text-center mt-8">
              <p className="text-text font-semibold mb-2">Join to unlock all picks</p>
              <p className="text-muted text-xs mono mb-4">Full history · Stake sizing · Model probability</p>
              <a
                href="/signup"
                className="mono text-xs tracking-widest uppercase px-5 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors"
              >
                Get Access
              </a>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
