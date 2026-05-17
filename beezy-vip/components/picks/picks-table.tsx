import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'

const B = '0.5px solid #1f1f24'
const COL = '80px 65px 160px 1fr 90px 60px 80px 70px 70px'

export function PicksTable({ picks }: { picks: Bet[] }) {
  if (picks.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
        <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No picks found for the selected filters.</p>
      </div>
    )
  }

  return (
    <div style={{ border: B, overflowX: 'auto' }}>
      <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', background: '#111114', borderBottom: B }}>
        {['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Book', 'Result', 'P&L'].map(h => (
          <div key={h} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{h}</div>
        ))}
      </div>
      {picks.map((bet, i) => {
        const edge = ((bet.model_prob - bet.market_prob) * 100).toFixed(1)
        const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
        const pickLabel = (() => {
          const bt  = bet.bet_type ?? ''
          const sys = bet.system
          const away = bet.away_team ?? ''
          const home = bet.home_team ?? ''
          const player = bet.player ?? ''
          // Derive team abbrev for pitcher/batter props:
          // player field doesn't include team so we guess from position --
          // away_team for away starter, home_team for home starter.
          // We store both and show whichever is shorter (abbrev).
          const teamAbbv = away.length <= 3 ? away : home.length <= 3 ? home : away

          if (sys === 'NRFI') {
            if (bt === 'NRFI')    return 'No Run 1st Inning'
            if (bt === 'YRFI')    return 'Run in 1st Inning'
            if (bt === '1I_HOME') return `${home} 1st Inning Moneyline`
            if (bt === '1I_AWAY') return `${away} 1st Inning Moneyline`
            if (bt === '1I_DRAW') return 'Draw 1st Inning Moneyline'
            return bt
          }
          if (sys === 'F5') {
            if (bt === 'HOME') return `${home} First 5 Innings Moneyline`
            if (bt === 'AWAY') return `${away} First 5 Innings Moneyline`
            return bt
          }
          if (sys === 'HR') {
            return `${player} (${teamAbbv}) to Hit a Home Run`
          }
          if (sys === 'K') {
            const side = bt.startsWith('K_OVER_') ? 'Over' : 'Under'
            const line = bt.replace('K_OVER_', '').replace('K_UNDER_', '')
            return `${player} (${teamAbbv}) ${side} ${line} Strikeouts`
          }
          if (sys === 'OUTS') {
            const side = bt.startsWith('OUTS_OVER_') ? 'Over' : 'Under'
            const line = bt.replace('OUTS_OVER_', '').replace('OUTS_UNDER_', '')
            return `${player} (${teamAbbv}) ${side} ${line} Outs`
          }
          return bt
        })()
        return (
          <div key={bet.id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', borderBottom: i < picks.length - 1 ? B : undefined, alignItems: 'center' }}>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#71717a' }}>
              {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </div>
            <div style={{ padding: '10px 12px' }}><SystemBadge system={bet.system} /></div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{pickLabel}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#10b981' }}>{parseFloat(edge) > 0 ? '+' : ''}{edge}%</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '10px', color: '#71717a', textTransform: 'capitalize' }}>{(bet as any).book ?? '—'}</div>
            <div style={{ padding: '10px 12px' }}><ResultPill result={bet.result} /></div>
            <div style={{ padding: '10px 12px' }}><PnL value={bet.profit} /></div>
          </div>
        )
      })}
    </div>
  )
}
