'use client'

import { useState, useEffect, useMemo } from 'react'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { apiGetPicks as getPicks, apiGetStats } from '@/lib/betting-api'
import { formatOdds } from '@/lib/odds'
import type { Bet } from '@/lib/db'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, ComposedChart, Area,
} from 'recharts'

const B = '0.5px solid #1f1f24'
const COL = '80px 70px 1fr 180px 70px 60px 70px 70px'
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
    }}>{label}</button>
  )
}

function pickLabel(bet: Bet): string {
  const bt   = bet.bet_type ?? ''
  const sys  = bet.system
  const away = bet.away_team ?? ''
  const home = bet.home_team ?? ''
  const player = bet.player ?? ''
  const team = away.length <= 3 ? away : home.length <= 3 ? home : away

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
  if (sys === 'HR')   return `${player} (${team}) to Hit a Home Run`
  if (sys === 'K') {
    const side = bt.startsWith('K_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('K_OVER_', '').replace('K_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Strikeouts`
  }
  if (sys === 'OUTS') {
    const side = bt.startsWith('OUTS_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('OUTS_OVER_', '').replace('OUTS_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Outs`
  }
  return bt
}

function buildPnLChart(bets: Bet[], activeSystem: string) {
  const settled = bets
    .filter(b => b.result && b.result !== 'void' && b.profit != null)
    .sort((a, b) => a.game_date.localeCompare(b.game_date))

  const systems = activeSystem === 'ALL' ? ['NRFI', 'HR', 'F5', 'K', 'OUTS'] : [activeSystem]
  const cum: Record<string, number> = {}
  systems.forEach(s => cum[s] = 0)
  cum['ALL'] = 0

  const byDate: Record<string, any> = {}

  for (const bet of settled) {
    const date = bet.game_date
    if (!byDate[date]) {
      const dates = Object.keys(byDate).sort()
      const prev = dates.length ? byDate[dates[dates.length - 1]] : {}
      byDate[date] = { date, ALL: prev.ALL ?? 0, ...Object.fromEntries(systems.map(s => [s, prev[s] ?? 0])) }
    }
    const profit = parseFloat(String(bet.profit)) / 10
    cum['ALL'] += profit
    byDate[date]['ALL'] = parseFloat(cum['ALL'].toFixed(2))
    if (systems.includes(bet.system)) {
      cum[bet.system] += profit
      byDate[date][bet.system] = parseFloat(cum[bet.system].toFixed(2))
    }
  }

  let hwm = 0
  const rows = Object.values(byDate).sort((a: any, b: any) => a.date.localeCompare(b.date))
  for (const row of rows as any[]) {
    const val = row['ALL'] ?? 0
    hwm = Math.max(hwm, val)
    row['dd_top'] = val < hwm ? hwm : null
    row['dd_bot'] = val < hwm ? val : null
  }
  return { rows: rows as any[], systems }
}

function buildEdgeChart(bets: Bet[]) {
  const settled = bets
    .filter(b => b.result && b.result !== 'void' && b.profit != null && b.edge != null)
    .sort((a, b) => a.game_date.localeCompare(b.game_date))

  // Group by date, weekly rolling
  const byDate: Record<string, { edges: number[], profits: number[], stakes: number[] }> = {}
  for (const bet of settled) {
    const d = bet.game_date
    if (!byDate[d]) byDate[d] = { edges: [], profits: [], stakes: [] }
    byDate[d].edges.push((bet.edge ?? 0) * 100)
    byDate[d].profits.push(parseFloat(String(bet.profit ?? 0)))
    byDate[d].stakes.push(bet.stake ?? 0)
  }

  const dates = Object.keys(byDate).sort()
  const result = []
  for (let i = 6; i < dates.length; i++) {
    const window = dates.slice(i - 6, i + 1)
    const allEdges = window.flatMap(d => byDate[d].edges)
    const allProfits = window.flatMap(d => byDate[d].profits)
    const allStakes = window.flatMap(d => byDate[d].stakes)
    const avgEdge = allEdges.reduce((s, v) => s + v, 0) / allEdges.length
    const totalStaked = allStakes.reduce((s, v) => s + v, 0)
    const roi = totalStaked > 0 ? allProfits.reduce((s, v) => s + v, 0) / totalStaked * 100 : 0
    result.push({
      date: dates[i],
      edge: parseFloat(avgEdge.toFixed(1)),
      roi:  parseFloat(roi.toFixed(1)),
    })
  }
  return result
}

const fmtDate = (d: string) => { const p = d.split('-'); return `${p[1]}/${p[2]}` }

const PnLTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#111114', border: B, padding: '10px 14px', fontSize: '11px', fontFamily: 'monospace', minWidth: '120px' }}>
      <div style={{ color: '#71717a', marginBottom: '6px' }}>{label}</div>
      {payload.filter((p: any) => p.value != null && !['dd_top','dd_bot'].includes(p.dataKey)).map((p: any) => (
        <div key={p.dataKey} style={{ color: PILL[p.dataKey] ?? '#f5f5f7', marginBottom: '2px' }}>
          {p.dataKey}: {p.value >= 0 ? '+' : ''}{p.value.toFixed(1)}u
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
      {payload.map((p: any) => (
        <div key={p.dataKey} style={{ color: p.color, marginBottom: '2px' }}>
          {p.dataKey === 'edge' ? 'Avg edge' : 'Realized ROI'}: {p.value >= 0 ? '+' : ''}{p.value}%
        </div>
      ))}
    </div>
  )
}

export default function ResultsPage() {
  const [picks, setPicks]     = useState<Bet[]>([])
  const [stats, setStats]     = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [system, setSystem]   = useState('ALL')
  const [result, setResult]   = useState('ALL')

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

  const { rows: pnlRows, systems: chartSystems } = useMemo(() => buildPnLChart(picks, system), [picks, system])
  const edgeRows = useMemo(() => buildEdgeChart(picks), [picks])

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

      {/* Summary stats strip */}
      <div className="stats-strip" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B, marginBottom: '24px' }}>
        {[
          { label: 'Total Bets', value: String(overall.bets) },
          { label: 'Win Rate',   value: `${winRate}%` },
          { label: 'Total P&L',  value: `${overall.pnl >= 0 ? '+' : ''}${overall.pnl.toFixed(1)}u`, color: overall.pnl >= 0 ? '#10b981' : '#ef4444' },
          { label: 'Systems',    value: String(stats.length) },
        ].map((s, i) => (
          <div key={s.label} style={{ padding: '20px 24px', borderRight: i < 3 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>{s.label}</div>
            <div className="mono" style={{ fontSize: '24px', fontWeight: 600, color: s.color ?? '#f5f5f7', lineHeight: 1 }}>{loading ? '—' : s.value}</div>
          </div>
        ))}
      </div>

      {/* P&L Chart */}
      {!loading && pnlRows.length > 2 && (
        <div style={{ border: B, marginBottom: '24px', padding: '20px 8px 12px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px 16px' }}>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>
              Cumulative P&L
            </span>
            <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
              {system === 'ALL' && ['NRFI','HR','F5','K','OUTS'].map(s => (
                <span key={s} className="mono" style={{ fontSize: '10px', color: PILL[s] }}>— {s}</span>
              ))}
              <span className="mono" style={{ fontSize: '10px', color: '#f5f5f7', fontWeight: 600 }}>— ALL</span>
              <span className="mono" style={{ fontSize: '10px', color: '#ef444460' }}>▓ drawdown</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <ComposedChart data={pnlRows} margin={{ top: 4, right: 24, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }}
                tickLine={false} axisLine={false} tickFormatter={fmtDate}
                interval={Math.floor(pnlRows.length / 6)} />
              <YAxis tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }}
                tickLine={false} axisLine={false}
                tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}u`} width={52} />
              <Tooltip content={<PnLTooltip />} />
              <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="3 3" />

              {/* Drawdown shading */}
              <Area dataKey="dd_top" fill="transparent" stroke="none" legendType="none" />
              <Area dataKey="dd_bot" fill="#ef444420" stroke="none" legendType="none" />

              {/* System lines — only when ALL selected */}
              {system === 'ALL' && chartSystems.map(s => (
                <Line key={s} type="monotone" dataKey={s} stroke={PILL[s]}
                  strokeWidth={1} dot={false} connectNulls strokeOpacity={0.6} />
              ))}

              {/* ALL line — always prominent */}
              <Line type="monotone" dataKey="ALL" stroke="#f5f5f7"
                strokeWidth={2.5} dot={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Edge vs ROI chart */}
      {!loading && edgeRows.length > 2 && (
        <div style={{ border: B, marginBottom: '24px', padding: '20px 8px 12px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px 16px' }}>
            <div>
              <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>
                Model Edge vs Realized ROI
              </span>
              <span className="mono" style={{ fontSize: '10px', color: '#2a2a31', marginLeft: '8px' }}>7-day rolling</span>
            </div>
            <div style={{ display: 'flex', gap: '16px' }}>
              <span className="mono" style={{ fontSize: '10px', color: '#10b981' }}>— model edge</span>
              <span className="mono" style={{ fontSize: '10px', color: '#3b82f6' }}>— realized ROI</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={edgeRows} margin={{ top: 4, right: 24, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }}
                tickLine={false} axisLine={false} tickFormatter={fmtDate}
                interval={Math.floor(edgeRows.length / 6)} />
              <YAxis tick={{ fill: '#71717a', fontSize: 10, fontFamily: 'monospace' }}
                tickLine={false} axisLine={false}
                tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`} width={44} />
              <Tooltip content={<EdgeTooltip />} />
              <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="3 3" />
              <Line type="monotone" dataKey="edge" stroke="#10b981" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="roi"  stroke="#3b82f6" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Per-system table */}
      {!loading && stats.length > 0 && (
        <div style={{ border: B, marginBottom: '24px', overflowX: 'auto' }}>
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
              <div key={s.system} style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', borderBottom: i < stats.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pc }}>{s.system}</div>
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
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'center', marginBottom: '16px' }}>
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
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No bets found.</p>
        </div>
      ) : (
        <div style={{ border: B, overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '780px', background: '#111114', borderBottom: B }}>
            {['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Result', 'P&L'].map(h => (
              <div key={h} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{h}</div>
            ))}
          </div>
          {filtered.map((bet, i) => {
            const edge = ((bet.model_prob - bet.market_prob) * 100).toFixed(1)
            const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
            return (
              <div key={bet.id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '780px', borderBottom: i < filtered.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#71717a' }}>
                  {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div style={{ padding: '8px 12px' }}><SystemBadge system={bet.system} /></div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickLabel(bet)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#10b981' }}>{parseFloat(edge) > 0 ? '+' : ''}{edge}%</div>
                <div style={{ padding: '8px 12px' }}><ResultPill result={bet.result} /></div>
                <div style={{ padding: '8px 12px' }}><PnL value={bet.profit} /></div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
