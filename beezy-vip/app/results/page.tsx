'use client'

import { useState, useEffect, useMemo } from 'react'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'

const B = '0.5px solid #1f1f24'
const COL = '90px 70px 1fr 90px 70px 60px 70px 70px'

const PILL: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6', K: '#a78bfa', OUTS: '#fb923c',
}

const SYSTEMS = ['ALL', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
const RESULTS = ['ALL', 'WIN', 'LOSS', 'VOID', 'PENDING']

function Chip({ label, active, color, onClick }: { label: string; active: boolean; color?: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '4px 12px',
        fontSize: '11px',
        fontFamily: 'JetBrains Mono, monospace',
        fontWeight: active ? 600 : 400,
        border: `0.5px solid ${active ? (color ?? '#10b981') : '#2a2a31'}`,
        borderRadius: '4px',
        background: active ? `${color ?? '#10b981'}18` : 'transparent',
        color: active ? (color ?? '#10b981') : '#71717a',
        cursor: 'pointer',
        letterSpacing: '0.05em',
        textTransform: 'uppercase' as const,
        transition: 'all 0.15s',
      }}
    >
      {label}
    </button>
  )
}

export default function ResultsPage() {
  const [picks, setPicks]   = useState<Bet[]>([])
  const [stats, setStats]   = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [system, setSystem] = useState('ALL')
  const [result, setResult] = useState('ALL')

  useEffect(() => {
    Promise.all([
      getPicks({ status: 'settled', limit: 500 }).catch(() => []),
      apiGetStats().then(s => s.bySystem).catch(() => []),
    ]).then(([p, s]) => {
      setPicks(p)
      setStats(s)
      setLoading(false)
    })
  }, [])

  const filtered = useMemo(() => picks.filter(p => {
    if (system !== 'ALL' && p.system !== system) return false
    if (result === 'PENDING' && p.result != null) return false
    if (result !== 'ALL' && result !== 'PENDING' && p.result?.toUpperCase() !== result) return false
    return true
  }), [picks, system, result])

  const overall = stats.reduce(
    (acc, s) => ({ bets: acc.bets + parseInt(String(s.total_bets)), wins: acc.wins + parseInt(String(s.wins)), pnl: acc.pnl + parseFloat(String(s.total_pnl)) }),
    { bets: 0, wins: 0, pnl: 0 }
  )
  const winRate = overall.bets > 0 ? (overall.wins / overall.bets * 100).toFixed(1) : '0.0'

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

      {/* Filters */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '24px', alignItems: 'center', marginBottom: '16px' }}>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginRight: '4px' }}>System</span>
          {SYSTEMS.map(s => (
            <Chip key={s} label={s} active={system === s} color={PILL[s]} onClick={() => setSystem(s)} />
          ))}
        </div>
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginRight: '4px' }}>Result</span>
          {RESULTS.map(r => (
            <Chip key={r} label={r} active={result === r}
              color={r === 'WIN' ? '#10b981' : r === 'LOSS' ? '#ef4444' : r === 'VOID' ? '#71717a' : undefined}
              onClick={() => setResult(r)} />
          ))}
        </div>
        <span className="mono" style={{ fontSize: '10px', color: '#71717a', marginLeft: 'auto' }}>
          {loading ? 'loading...' : `${filtered.length} bets`}
        </span>
      </div>

      {/* Picks table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>Loading...</p>
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No picks found for the selected filters.</p>
        </div>
      ) : (
        <div style={{ border: B, overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '700px', background: '#111114', borderBottom: B }}>
            {['Date', 'System', 'Game', 'Pick', 'Line', 'Edge', 'Result', 'P&L'].map(h => (
              <div key={h} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{h}</div>
            ))}
          </div>
          {filtered.map((bet, i) => {
            const edge = ((bet.model_prob - bet.market_prob) * 100).toFixed(1)
            const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
            return (
              <div key={bet.id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '700px', borderBottom: i < filtered.length - 1 ? B : undefined }}>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#71717a' }}>
                  {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div style={{ padding: '10px 12px' }}><SystemBadge system={bet.system} /></div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>{bet.bet_type}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#10b981' }}>{parseFloat(edge) > 0 ? '+' : ''}{edge}%</div>
                <div style={{ padding: '10px 12px' }}><ResultPill result={bet.result} /></div>
                <div style={{ padding: '10px 12px' }}><PnL value={bet.profit} /></div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
