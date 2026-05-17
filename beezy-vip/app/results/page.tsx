'use client'

import { useState, useEffect, useMemo } from 'react'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, Area, ComposedChart, ReferenceArea,
} from 'recharts'

const B = '0.5px solid #1f1f24'
const COL = '90px 70px 1fr 90px 70px 60px 70px 70px'
const PILL: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6', K: '#a78bfa', OUTS: '#fb923c', ALL: '#f5f5f7',
}
const SYSTEMS = ['ALL', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
const RESULTS = ['ALL', 'WIN', 'LOSS', 'VOID']
const GATE = 200

function Chip({ label, active, color, onClick }: { label: string; active: boolean; color?: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 12px', fontSize: '11px', fontFamily: 'JetBrains Mono, monospace',
      fontWeight: active ? 600 : 400,
      border: `0.5px solid ${active ? (color ?? '#10b981') : '#2a2a31'}`,
      borderRadius: '4px',
      background: active ? `${color ?? '#10b981'}18` : 'transparent',
      color: active ? (color ?? '#10b981') : '#71717a',
      cursor: 'pointer', letterSpacing: '0.05em', textTransform: 'uppercase' as const,
      transition: 'all 0.15s',
    }}>{label}</button>
  )
}

// Build cumulative P&L chart data from settled bets
function buildChartData(bets: Bet[], activeSystem: string) {
  const settled = bets
    .filter(b => b.result && b.result !== 'void' && b.profit != null)
    .sort((a, b) => a.game_date.localeCompare(b.game_date))

  // Group by date, accumulate per system and all
  const systems = activeSystem === 'ALL' ? ['NRFI', 'HR', 'F5', 'K', 'OUTS'] : [activeSystem]
  const cumulative: Record<string, number> = {}
  systems.forEach(s => cumulative[s] = 0)
  cumulative['ALL'] = 0

  const byDate: Record<string, any> = {}
  const betCounts: Record<string, number> = {}
  systems.forEach(s => betCounts[s] = 0)
  betCounts['ALL'] = 0

  for (const bet of settled) {
    const date = bet.game_date
    if (!byDate[date]) {
      byDate[date] = { date, ...Object.fromEntries(systems.map(s => [s, null])), ALL: null }
      // carry forward previous values
      const dates = Object.keys(byDate).sort()
      if (dates.length > 1) {
        const prev = byDate[dates[dates.length - 2]]
        systems.forEach(s => { byDate[date][s] = prev[s] })
        byDate[date]['ALL'] = prev['ALL']
      }
    }
    const profit = parseFloat(String(bet.profit)) / 10 // convert to units
    cumulative['ALL'] += profit
    betCounts['ALL']++
    if (systems.includes(bet.system)) {
      cumulative[bet.system] += profit
      betCounts[bet.system]++
      byDate[date][bet.system] = cumulative[bet.system]
      byDate[date][`${bet.system}_count`] = betCounts[bet.system]
    }
    byDate[date]['ALL'] = cumulative['ALL']
    byDate[date]['ALL_count'] = betCounts['ALL']
  }

  // Compute drawdown: high water mark for ALL line
  let hwm = 0
  const rows = Object.values(byDate).sort((a: any, b: any) => a.date.localeCompare(b.date))
  for (const row of rows as any[]) {
    const val = row['ALL'] ?? 0
    hwm = Math.max(hwm, val)
    row['hwm'] = hwm
    row['drawdown'] = val < hwm ? val : null
    row['drawdown_top'] = val < hwm ? hwm : null
  }

  return { rows: rows as any[], betCounts, systems }
}

function buildEdgeData(bets: Bet[]) {
  // Rolling 20-bet avg edge vs realized ROI
  const settled = bets
    .filter(b => b.result && b.result !== 'void' && b.profit != null && b.edge != null)
    .sort((a, b) => a.game_date.localeCompare(b.game_date))

  const result = []
  for (let i = 19; i < settled.length; i++) {
    const window = settled.slice(i - 19, i + 1)
    const avgEdge = window.reduce((s, b) => s + (b.edge ?? 0), 0) / 20 * 100
    const totalStaked = window.reduce((s, b) => s + (b.stake ?? 0), 0)
    const totalPnl = window.reduce((s, b) => s + parseFloat(String(b.profit ?? 0)), 0)
    const roi = totalStaked > 0 ? totalPnl / totalStaked * 100 : 0
    result.push({ date: settled[i].game_date, avgEdge: parseFloat(avgEdge.toFixed(2)), roi: parseFloat(roi.toFixed(2)) })
  }
  return result
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#111114', border: B, padding: '10px 14px', fontSize: '11px', fontFamily: 'monospace' }}>
      <div style={{ color: '#71717a', marginBottom: '6px' }}>{label}</div>
      {payload.map((p: any) => p.value != null && (
        <div key={p.dataKey} style={{ color: p.color ?? '#f5f5f7', marginBottom: '2px' }}>
          {p.dataKey}: {p.value > 0 ? '+' : ''}{typeof p.value === 'number' ? p.value.toFixed(2) : p.value}u
        </div>
      ))}
    </div>
  )
}

const EdgeTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#111114', border: B, padding: '10px 14px', fontSize: '11px', fontFamily: 'monospace' }}>
      <div style={{ color: '#71717a', marginBottom: '6px' }}>{label}</div>
      {payload.map((p: any) => p.value != null && (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: '2px' }}>
          {p.dataKey === 'avgEdge' ? 'Model edge' : 'Realized ROI'}: {p.value > 0 ? '+' : ''}{p.value.toFixed(1)}%
        </div>
      ))}
    </div>
  )
}

export default function ResultsPage() {
  const [picks, setPicks] = useState<Bet[]>([])
  const [stats, setStats] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [system, setSystem] = useState('ALL')
  const [result, setResult] = useState('ALL')

  useEffect(() => {
    Promise.all([
      getPicks({ status: 'settled', limit: 500 }).catch(() => []),
      apiGetStats().then(s => s.bySystem).catch(() => []),
    ]).then(([p, s]) => { setPicks(p); setStats(s); setLoading(false) })
  }, [])

  const filtered = useMemo(() => picks.filter(p => {
    if (system !== 'ALL' && p.system !== system) return false
    if (result !== 'ALL' && p.result?.toUpperCase() !== result) return false
    return true
  }), [picks, system, result])

  const { rows: chartRows, betCounts, systems: chartSystems } = useMemo(
    () => buildChartData(picks, system), [picks, system]
  )
  const edgeRows = useMemo(() => buildEdgeData(picks), [picks])

  const overall = stats.reduce(
    (acc, s) => ({ bets: acc.bets + parseInt(String(s.total_bets)), wins: acc.wins + parseInt(String(s.wins)), pnl: acc.pnl + parseFloat(String(s.total_pnl)) }),
    { bets: 0, wins: 0, pnl: 0 }
  )
  const winRate = overall.bets > 0 ? (overall.wins / overall.bets * 100).toFixed(1) : '0.0'

  // Find gate crossing dates per system
  const gateDates: Record<string, string> = {}
  for (const sys of chartSystems) {
    for (const row of chartRows) {
      if ((row[`${sys}_count`] ?? 0) >= GATE && !gateDates[sys]) {
        gateDates[sys] = row.date
      }
    }
  }

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

      {/* P&L Chart */}
      {!loading && chartRows.length > 1 && (
        <div style={{ border: B, marginBottom: '20px', padding: '20px 8px 8px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px', marginBottom: '12px' }}>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>Cumulative P&L (units)</span>
            <div style={{ display: 'flex', gap: '12px' }}>
              {(system === 'ALL' ? ['NRFI','HR','F5','K','OUTS','ALL'] : [system]).map(s => (
                <span key={s} className="mono" style={{ fontSize: '10px', color: PILL[s] }}>— {s}</span>
              ))}
              <span className="mono" style={{ fontSize: '10px', color: '#ef444440' }}>▓ drawdown</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={chartRows} margin={{ top: 4, right: 20, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false}
                tickFormatter={d => { const p = d.split('-'); return `${p[1]}/${p[2]}` }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false}
                tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}u`} width={52} />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="3 3" />

              {/* Drawdown fill */}
              <Area dataKey="drawdown_top" fill="transparent" stroke="none" />
              <Area dataKey="drawdown" fill="#ef444420" stroke="none" />

              {/* Gate markers */}
              {Object.entries(gateDates).map(([sys, date]) => (
                <ReferenceLine key={sys} x={date} stroke={PILL[sys]} strokeDasharray="4 4" strokeOpacity={0.5}
                  label={{ value: `${sys} gate`, fill: PILL[sys], fontSize: 9, fontFamily: 'monospace' }} />
              ))}

              {/* System lines -- dotted when < 20 bets (early noise), solid after */}
              {(system === 'ALL' ? ['NRFI','HR','F5','K','OUTS'] : []).map(s => (
                <Line key={s} type="monotone" dataKey={s} stroke={PILL[s]} strokeWidth={1.5}
                  dot={false} connectNulls strokeDasharray={(betCounts[s] ?? 0) < 20 ? '3 3' : undefined} />
              ))}
              <Line type="monotone" dataKey="ALL" stroke="#f5f5f7" strokeWidth={2}
                dot={false} connectNulls strokeDasharray={(betCounts['ALL'] ?? 0) < 20 ? '3 3' : undefined} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Edge vs ROI chart */}
      {!loading && edgeRows.length > 1 && (
        <div style={{ border: B, marginBottom: '20px', padding: '20px 8px 8px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px', marginBottom: '12px' }}>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>Model edge vs realized ROI (rolling 20 bets)</span>
            <div style={{ display: 'flex', gap: '12px' }}>
              <span className="mono" style={{ fontSize: '10px', color: '#10b981' }}>— model edge</span>
              <span className="mono" style={{ fontSize: '10px', color: '#3b82f6' }}>— realized ROI</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={edgeRows} margin={{ top: 4, right: 20, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false}
                tickFormatter={d => { const p = d.split('-'); return `${p[1]}/${p[2]}` }} interval="preserveStartEnd" />
              <YAxis tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false}
                tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`} width={44} />
              <Tooltip content={<EdgeTooltip />} />
              <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="3 3" />
              <Line type="monotone" dataKey="avgEdge" stroke="#10b981" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="roi" stroke="#3b82f6" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

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
            const atGate = parseInt(String(s.total_bets)) >= GATE
            return (
              <div key={s.system} style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', borderBottom: i < stats.length - 1 ? B : undefined }}>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pc }}>
                  {s.system}{atGate ? ' ✓' : ''}
                </div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>
                  {s.total_bets}<span style={{ color: '#2a2a31' }}>/{GATE}</span>
                </div>
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
          {SYSTEMS.map(s => <Chip key={s} label={s} active={system === s} color={PILL[s]} onClick={() => setSystem(s)} />)}
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

      {/* Bets table */}
      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>Loading...</p>
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No bets found for the selected filters.</p>
        </div>
      ) : (
        <div style={{ border: B, overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '700px', background: '#111114', borderBottom: B }}>
            {['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Result', 'P&L'].map(h => (
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
