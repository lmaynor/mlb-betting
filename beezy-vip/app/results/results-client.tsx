'use client'

import { useState, useMemo } from 'react'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import { B, SYSTEM_COLOR, pickLabel } from '@/lib/tokens'
import type { Bet, SystemStats } from '@/lib/types'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, ComposedChart, Area,
} from 'recharts'

const COL     = '80px 65px 180px 1fr 80px 70px 60px 80px 70px 70px'
const PILL    = SYSTEM_COLOR
const SYSTEMS = ['ALL', 'NRFI', 'HR', 'F5', 'K', 'OUTS']
const RESULTS = ['ALL', 'WIN', 'LOSS', 'VOID']
const GATE    = 200

function Chip({ label, active, color, onClick }: {
  label: string; active: boolean; color?: string; onClick: () => void
}) {
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

  const byDate: Record<string, { edges: number[]; profits: number[]; stakes: number[] }> = {}
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
    const allEdges   = window.flatMap(d => byDate[d].edges)
    const allProfits = window.flatMap(d => byDate[d].profits)
    const allStakes  = window.flatMap(d => byDate[d].stakes)
    const avgEdge    = allEdges.reduce((s, v) => s + v, 0) / allEdges.length
    const totalStaked = allStakes.reduce((s, v) => s + v, 0)
    const roi = totalStaked > 0 ? allProfits.reduce((s, v) => s + v, 0) / totalStaked * 100 : 0
    result.push({ date: dates[i], edge: parseFloat(avgEdge.toFixed(1)), roi: parseFloat(roi.toFixed(1)) })
  }
  return result
}

const fmtDate = (d: string) => { const p = d.split('-'); return `${p[1]}/${p[2]}` }

const PnLTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#111114', border: B, padding: '10px 14px', fontSize: '11px', fontFamily: 'monospace', minWidth: '120px' }}>
      <div style={{ color: '#71717a', marginBottom: '6px' }}>{label}</div>
      {payload.filter((p: any) => p.value != null && !['dd_top', 'dd_bot'].includes(p.dataKey)).map((p: any) => (
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

function exportCSV(bets: Bet[]) {
  const headers = ['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge Range', 'Stake', 'Book', 'Result', 'P&L (units)']
  const edgeBin = (e: number) => e >= 0.10 ? '10%+' : e >= 0.05 ? '5-10%' : e >= 0 ? '0-5%' : '<0%'
  const rows = bets.map(b => {
    const game = b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`
    const pnl  = b.profit != null ? (parseFloat(String(b.profit)) / 10).toFixed(2) : ''
    return [
      b.game_date, b.system, game, pickLabel(b),
      b.odds ?? '', b.edge != null ? edgeBin(b.edge) : '',
      b.stake != null && b.stake > 0 ? b.stake.toFixed(0) : '',
      (b as any).book ?? '', b.result ?? 'pending', pnl,
    ].map(v => `"${String(v).replace(/"/g, '""')}"`).join(',')
  })
  const csv  = [headers.join(','), ...rows].join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url  = URL.createObjectURL(blob)
  const a    = document.createElement('a')
  a.href = url
  a.download = `beezy-results-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

export function ResultsClient({
  initialPicks,
  initialStats,
}: {
  initialPicks: Bet[]
  initialStats: SystemStats[]
}) {
  const [system,  setSystem]  = useState('ALL')
  const [result,  setResult]  = useState('ALL')
  const [sortBy,  setSortBy]  = useState<'date' | 'edge' | 'odds' | 'pnl'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const filtered = useMemo(() => {
    const f = initialPicks.filter(p => {
      if (system !== 'ALL' && p.system !== system) return false
      if (result !== 'ALL' && p.result?.toUpperCase() !== result) return false
      return true
    })
    const dir = sortDir === 'desc' ? -1 : 1
    return [...f].sort((a, b) => {
      if (sortBy === 'date') return dir * a.game_date.localeCompare(b.game_date)
      if (sortBy === 'edge') return dir * ((a.edge ?? 0) - (b.edge ?? 0))
      if (sortBy === 'odds') return dir * ((a.odds ?? 0) - (b.odds ?? 0))
      if (sortBy === 'pnl')  return dir * (parseFloat(String(a.profit ?? 0)) - parseFloat(String(b.profit ?? 0)))
      return 0
    })
  }, [initialPicks, system, result, sortBy, sortDir])

  const { rows: pnlRows, systems: chartSystems } = useMemo(
    () => buildPnLChart(initialPicks, system), [initialPicks, system]
  )
  const edgeRows = useMemo(() => buildEdgeChart(initialPicks), [initialPicks])

  const overall = initialStats.reduce(
    (acc, s) => ({
      bets: acc.bets + parseInt(String(s.total_bets)),
      wins: acc.wins + parseInt(String(s.wins ?? 0)),
      pnl:  acc.pnl  + parseFloat(String(s.total_pnl ?? 0)),
    }),
    { bets: 0, wins: 0, pnl: 0 }
  )
  const winRate = overall.bets > 0 ? (overall.wins / overall.bets * 100).toFixed(1) : '0.0'

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px' }}>Results</h1>
        <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>
          All settled bets &middot; Paper mode &middot; Past performance is not indicative of future results
        </p>
      </div>

      {/* Summary stats strip */}
      <div className="stats-strip" style={{ gridTemplateColumns: 'repeat(4,1fr)', border: B, marginBottom: '24px' }}>
        {[
          { label: 'Total Bets', value: String(overall.bets) },
          { label: 'Win Rate',   value: `${winRate}%` },
          { label: 'Total P&L',  value: `${overall.pnl >= 0 ? '+' : ''}${(overall.pnl / 10).toFixed(1)}u`, color: overall.pnl >= 0 ? '#10b981' : '#ef4444' },
          { label: 'Systems',    value: String(initialStats.length) },
        ].map((s, i) => (
          <div key={s.label} style={{ padding: '20px 24px', borderRight: i < 3 ? B : undefined }}>
            <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>{s.label}</div>
            <div className="mono" style={{ fontSize: '24px', fontWeight: 600, color: s.color ?? '#f5f5f7', lineHeight: 1 }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* P&L Chart */}
      {pnlRows.length > 2 && (
        <div style={{ border: B, marginBottom: '24px', padding: '20px 8px 12px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px 16px' }}>
            <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>
              Cumulative P&L
            </span>
            <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
              {(system === 'ALL' ? ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'ALL'] : [system]).map(s => (
                <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: PILL[s], flexShrink: 0, display: 'inline-block' }} />
                  <span className="mono" style={{ fontSize: '10px', color: '#71717a' }}>{s}</span>
                </span>
              ))}
              <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                <span style={{ width: '8px', height: '6px', background: '#ef444430', display: 'inline-block' }} />
                <span className="mono" style={{ fontSize: '10px', color: '#71717a' }}>drawdown</span>
              </span>
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
              <Area dataKey="dd_top" fill="transparent" stroke="none" legendType="none" />
              <Area dataKey="dd_bot" fill="#ef444420" stroke="none" legendType="none" />
              {system === 'ALL' && chartSystems.map(s => (
                <Line key={s} type="monotone" dataKey={s} stroke={PILL[s]}
                  strokeWidth={1} dot={false} connectNulls strokeOpacity={0.6} />
              ))}
              <Line type="monotone" dataKey="ALL" stroke="#f5f5f7" strokeWidth={2.5} dot={false} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Edge vs ROI chart */}
      {edgeRows.length > 2 && (
        <div style={{ border: B, marginBottom: '24px', padding: '20px 8px 12px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0 20px 16px' }}>
            <div>
              <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>
                Model Edge vs Realized ROI
              </span>
              <span className="mono" style={{ fontSize: '10px', color: '#2a2a31', marginLeft: '8px' }}>7-day rolling</span>
            </div>
            <div style={{ display: 'flex', gap: '16px' }}>
              <span className="mono" style={{ fontSize: '10px', color: '#10b981' }}>-- model edge</span>
              <span className="mono" style={{ fontSize: '10px', color: '#3b82f6' }}>-- realized ROI</span>
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
      {initialStats.length > 0 && (
        <div style={{ border: B, marginBottom: '24px', overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', background: '#111114', borderBottom: B }}>
            {['System', 'Bets', 'Win Rate', 'ROI', 'P&L', 'Avg Edge'].map(h => (
              <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', padding: '9px 12px' }}>{h}</div>
            ))}
          </div>
          {initialStats.map((s, i) => {
            const r   = parseFloat(String(s.roi ?? 0))
            const pnl = parseFloat(String(s.total_pnl ?? 0))
            const pc  = PILL[s.system] ?? '#a1a1aa'
            return (
              <div key={s.system} style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', borderBottom: i < initialStats.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pc }}>{s.system}</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>
                  {s.total_bets}<span style={{ color: '#2a2a31' }}>/{GATE}</span>
                </div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#f5f5f7' }}>{parseFloat(String(s.win_rate ?? 0)).toFixed(1)}%</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: r >= 0 ? '#10b981' : '#ef4444' }}>{r >= 0 ? '+' : ''}{r.toFixed(1)}%</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', fontWeight: 600, color: pnl >= 0 ? '#10b981' : '#ef4444' }}>{pnl >= 0 ? '+' : ''}{(pnl / 10).toFixed(2)}u</div>
                <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#10b981' }}>+{parseFloat(String(s.avg_edge ?? 0)).toFixed(1)}%</div>
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
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginRight: '4px' }}>Sort</span>
          {(['date', 'edge', 'odds', 'pnl'] as const).map(s => (
            <button key={s}
              onClick={() => {
                if (sortBy === s) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
                else { setSortBy(s); setSortDir('desc') }
              }}
              className="mono"
              style={{
                fontSize: '11px', padding: '4px 10px', cursor: 'pointer',
                border: `0.5px solid ${sortBy === s ? '#10b981' : '#1f1f24'}`,
                color: sortBy === s ? '#10b981' : '#71717a',
                background: sortBy === s ? '#10b98112' : 'transparent',
                letterSpacing: '0.04em', textTransform: 'uppercase' as const,
              }}>
              {s}{sortBy === s ? (sortDir === 'desc' ? ' \u2193' : ' \u2191') : ''}
            </button>
          ))}
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span className="mono" style={{ fontSize: '10px', color: '#71717a' }}>{filtered.length} bets</span>
          {filtered.length > 0 && (
            <button onClick={() => exportCSV(filtered)} className="mono" style={{
              fontSize: '10px', padding: '4px 10px', cursor: 'pointer',
              border: '0.5px solid #2a2a31', color: '#71717a', background: 'transparent',
              letterSpacing: '0.05em', textTransform: 'uppercase' as const,
            }}>Export CSV</button>
          )}
        </div>
      </div>

      {/* Bets table */}
      {filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No bets found.</p>
        </div>
      ) : (
        <div style={{ border: B, overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', background: '#111114', borderBottom: B }}>
            {['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Stake', 'Book', 'Result', 'P&L'].map(h => (
              <div key={h} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{h}</div>
            ))}
          </div>
          {filtered.map((bet, i) => {
            const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
            return (
              <div key={(bet as any).id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', borderBottom: i < filtered.length - 1 ? B : undefined, alignItems: 'center' }}>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#71717a' }}>
                  {new Date(bet.game_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                </div>
                <div style={{ padding: '8px 12px' }}><SystemBadge system={bet.system} /></div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickLabel(bet)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#10b981' }}>
                  {bet.edge != null ? (bet.edge >= 0.10 ? '10%+' : bet.edge >= 0.05 ? '5-10%' : bet.edge >= 0 ? '0-5%' : '<0%') : '--'}
                </div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>
                  {bet.stake != null && bet.stake > 0 ? `$${bet.stake.toFixed(0)}` : '--'}
                </div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '10px', color: '#71717a', textTransform: 'capitalize' }}>
                  {(bet as any).book ?? '--'}
                </div>
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
