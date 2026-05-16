export const dynamic = 'force-dynamic'

import { PicksTable } from '@/components/picks/picks-table'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Results — Full History',
  description: 'Complete public results for all Beezy.VIP MLB betting systems. Every bet, every result.',
}

const B = '0.5px solid #1f1f24'

const PILL: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6', K: '#a78bfa', OUTS: '#fb923c',
}

export default async function ResultsPage() {
  const [picks, stats] = await Promise.all([
    getPicks({ status: 'settled', limit: 100 }).catch(() => []),
    apiGetStats().then(s => s.bySystem).catch(() => []),
  ])

  const overall = stats.reduce(
    (acc, s) => ({ bets: acc.bets + parseInt(String(s.total_bets)), wins: acc.wins + parseInt(String(s.wins)), pnl: acc.pnl + parseFloat(String(s.total_pnl)) }),
    { bets: 0, wins: 0, pnl: 0 }
  )
  const winRate = overall.bets > 0 ? (overall.wins / overall.bets * 100).toFixed(1) : '0.0'
  const roi     = overall.bets > 0 ? (overall.pnl / (overall.bets * 10) * 100).toFixed(1) : '0.0'

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Results</h1>
        <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>All settled bets · Paper mode · Past performance is not indicative of future results</p>
      </div>

      {/* Summary stats */}
      <div className="stats-strip" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B, marginBottom: '20px' }}>
        {[
          { label: 'Total bets', value: String(overall.bets) },
          { label: 'Win rate',   value: `${winRate}%` },
          { label: 'Total P&L',  value: `${overall.pnl >= 0 ? '+' : ''}${overall.pnl.toFixed(1)}u`, color: overall.pnl >= 0 ? '#10b981' : '#ef4444' },
          { label: 'Systems',    value: String(stats.length) },
        ].map((s, i) => (
          <div key={s.label} style={{ padding: '18px', borderRight: i < 3 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '6px' }}>{s.label}</div>
            <div className="mono" style={{ fontSize: '22px', fontWeight: 600, color: s.color ?? '#f5f5f7', lineHeight: 1 }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Per-system table */}
      {stats.length > 0 && (
        <div style={{ border: B, marginBottom: '20px', overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', background: '#111114', borderBottom: B }}>
            {['System', 'Bets', 'Win Rate', 'ROI', 'P&L', 'Avg Edge'].map(h => (
              <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', padding: '9px 12px' }}>{h}</div>
            ))}
          </div>
          {stats.map((s, i) => {
            const r   = parseFloat(String(s.roi ?? 0))
            const pnl = parseFloat(String(s.total_pnl ?? 0))
            const pc  = PILL[s.system] ?? '#a1a1aa'
            return (
              <div key={s.system} style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', borderBottom: i < stats.length - 1 ? B : undefined }}>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pc }}>{s.system}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>{s.total_bets}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>{parseFloat(String(s.win_rate)).toFixed(1)}%</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: r >= 0 ? '#10b981' : '#ef4444' }}>{r >= 0 ? '+' : ''}{r.toFixed(1)}%</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pnl >= 0 ? '#10b981' : '#ef4444' }}>{pnl >= 0 ? '+' : ''}{pnl.toFixed(2)}u</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#10b981' }}>+{parseFloat(String(s.avg_edge)).toFixed(1)}%</div>
              </div>
            )
          })}
        </div>
      )}

      <PicksTable picks={picks} />
    </div>
  )
}
