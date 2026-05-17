import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'

const B = '0.5px solid #1f1f24'
const COL = '90px 70px 1fr 90px 70px 60px 70px 70px'

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
      <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '700px', background: '#111114', borderBottom: B }}>
        {['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Result', 'P&L'].map(h => (
          <div key={h} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{h}</div>
        ))}
      </div>
      {picks.map((bet, i) => {
        const edge = ((bet.model_prob - bet.market_prob) * 100).toFixed(1)
        const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
        const pickLabel = (() => {
          const bt = bet.bet_type ?? ''
          const sys = bet.system
          if (sys === 'F5')   return bt === 'HOME' ? 'F5 Home ML' : bt === 'AWAY' ? 'F5 Away ML' : bt
          if (sys === 'NRFI') {
            if (bt === 'NRFI') return 'NRFI'
            if (bt === 'YRFI') return 'YRFI'
            if (bt === '1I_HOME') return '1st Inn Home'
            if (bt === '1I_AWAY') return '1st Inn Away'
            if (bt === '1I_DRAW') return '1st Inn Draw'
            return bt
          }
          if (sys === 'HR')   return 'HR Yes'
          if (sys === 'K')    return bt.replace('K_OVER_', 'Over ').replace('K_UNDER_', 'Under ') + ' Ks'
          if (sys === 'OUTS') return bt.replace('OUTS_OVER_', 'Over ').replace('OUTS_UNDER_', 'Under ') + ' Outs'
          return bt
        })()
        return (
          <div key={bet.id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '700px', borderBottom: i < picks.length - 1 ? B : undefined, alignItems: 'center' }}>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#71717a' }}>
              {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </div>
            <div style={{ padding: '10px 12px' }}><SystemBadge system={bet.system} /></div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{pickLabel}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
            <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#10b981' }}>{parseFloat(edge) > 0 ? '+' : ''}{edge}%</div>
            <div style={{ padding: '10px 12px' }}><ResultPill result={bet.result} /></div>
            <div style={{ padding: '10px 12px' }}><PnL value={bet.profit} /></div>
          </div>
        )
      })}
    </div>
  )
}
