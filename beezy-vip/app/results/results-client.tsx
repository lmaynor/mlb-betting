'use client'

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useState, useMemo } from 'react'
import { useSearchParams } from 'next/navigation'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import { B, SYSTEM_COLOR, pickLabel } from '@/lib/tokens'
import { formatDateKey, siteDateKey } from '@/lib/dates'
import type { Bet, SystemStats } from '@/lib/types'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, ComposedChart, Area,
} from 'recharts'

const COL = '80px 65px 180px 1fr 80px 70px 60px 80px 70px 70px'
const PILL = SYSTEM_COLOR

// All systems grouped by type for filter chips
const SYSTEM_GROUPS = {
  'Game Lines':      ['NRFI', 'F5'],
  'Innings Windows': ['F3', 'F1H', 'F7', 'GAME'],
  'Pitcher Props':   ['K', 'OUTS', 'PITCHER_ER'],
  'Batter Props':    ['HR', 'BATTER_K', 'BATTER_TB', 'BATTER_HITS'],
} as const

// Flat list for the filter chip row: ALL + each system
const ALL_SYSTEMS = ['ALL', ...Object.values(SYSTEM_GROUPS).flat()]

// Chart shows primary trained models + OUTS (best performer) when ALL is
// selected; sub-market proxies are omitted to keep lines legible.
// Each system still appears on its own line when selected individually.
const CHART_SYSTEMS_PRIMARY = ['NRFI', 'F5', 'K', 'OUTS', 'HR']

const RESULTS = ['ALL', 'WIN', 'LOSS', 'VOID']
const GATE    = 200

// Short display labels for system filter chips
const SYSTEM_LABEL: Record<string, string> = {
  ALL:         'ALL',
  NRFI:        'NRFI',
  F5:          'F5',
  F3:          'F3',
  F1H:         '1H',
  F7:          'F7',
  GAME:        'Game',
  K:           'K',
  OUTS:        'Outs',
  PITCHER_ER:  'Pitcher ER',
  HR:          'HR',
  BATTER_K:    'Batter K',
  BATTER_TB:   'Total Bases',
  BATTER_HITS: 'Hits',
}

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

// Group divider label inside the system filter row
function GroupLabel({ label }: { label: string }) {
  return (
    <span style={{
      fontFamily: 'JetBrains Mono, monospace', fontSize: '9px',
      letterSpacing: '0.1em', textTransform: 'uppercase' as const,
      color: '#2a2a31', padding: '0 4px', whiteSpace: 'nowrap' as const,
    }}>{label}</span>
  )
}

function buildPnLChart(bets: Bet[], activeSystem: string) {
  const settled = bets
    .filter(b => b.result && b.result !== 'void' && b.profit != null)
    .sort((a, b) => a.game_date.localeCompare(b.game_date))

  // When a specific system is selected, show only that system + ALL aggregate.
  // When ALL is selected, show only primary chart systems to keep lines legible.
  const systems = activeSystem === 'ALL'
    ? CHART_SYSTEMS_PRIMARY
    : [activeSystem]

  const cum: Record<string, number> = {}
  systems.forEach(s => cum[s] = 0)
  cum['ALL'] = 0

  const byDate: Record<string, any> = {}
  for (const bet of settled) {
    const date = bet.game_date
    if (!byDate[date]) {
      const dates = Object.keys(byDate).sort()
      const prev = dates.length ? byDate[dates[dates.length - 1]] : {}
      byDate[date] = {
        date, ALL: prev.ALL ?? 0,
        ...Object.fromEntries(systems.map(s => [s, prev[s] ?? 0])),
      }
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
    const window      = dates.slice(i - 6, i + 1)
    const allEdges    = window.flatMap(d => byDate[d].edges)
    const allProfits  = window.flatMap(d => byDate[d].profits)
    const allStakes   = window.flatMap(d => byDate[d].stakes)
    const avgEdge     = allEdges.reduce((s, v) => s + v, 0) / allEdges.length
    const totalStaked = allStakes.reduce((s, v) => s + v, 0)
    const roi = totalStaked > 0
      ? allProfits.reduce((s, v) => s + v, 0) / totalStaked * 100
      : 0
    result.push({
      date: dates[i],
      edge: parseFloat(avgEdge.toFixed(1)),
      roi:  parseFloat(roi.toFixed(1)),
    })
  }
  return result
}

const fmtDate = (d: string) => { const p = d.split('-'); return `${p[1]}/${p[2]}` }
const fmtDisplayDate = (d: string) => formatDateKey(d, { month: 'short', day: 'numeric' })

const PnLTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: '#111114', border: B, padding: '10px 14px', fontSize: '11px', fontFamily: 'monospace', minWidth: '120px' }}>
      <div style={{ color: '#71717a', marginBottom: '6px' }}>{label}</div>
      {payload
        .filter((p: any) => p.value != null && !['dd_top', 'dd_bot'].includes(p.dataKey))
        .map((p: any) => (
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
  a.download = `beezy-results-${siteDateKey()}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

function ResultBetCard({ bet }: { bet: Bet }) {
  const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`

  return (
    <div className="card-hover" style={{
      border: B,
      borderRadius: 'var(--radius)',
      background: '#0d0d12',
      boxShadow: 'var(--shadow-card)',
      padding: '13px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: '11px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', minWidth: 0 }}>
          <SystemBadge system={bet.system} />
          <span className="mono" style={{ fontSize: '11px', color: '#71717a', whiteSpace: 'nowrap' }}>
            {fmtDisplayDate(bet.game_date)}
          </span>
        </div>
        <ResultPill result={bet.result} />
      </div>

      <div>
        <div className="mono" style={{ fontSize: '12px', color: '#a1a1aa', marginBottom: '5px' }}>{game}</div>
        <div style={{ fontSize: '15px', lineHeight: 1.35, fontWeight: 750, color: '#f5f5f7' }}>{pickLabel(bet)}</div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '10px', alignItems: 'end' }}>
        {[
          { label: 'Odds', value: formatOdds(bet.odds), color: '#f5f5f7' },
          { label: 'Edge', value: bet.edge != null ? `${bet.edge >= 0 ? '+' : ''}${(bet.edge * 100).toFixed(1)}%` : '--', color: '#10b981' },
          { label: 'Book', value: bet.book ?? '--', color: '#a1a1aa' },
        ].map(item => (
          <div key={item.label} style={{ minWidth: 0 }}>
            <div className="mono" style={{ fontSize: '8px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '3px' }}>{item.label}</div>
            <div className="mono" style={{ fontSize: '12px', fontWeight: 700, color: item.color, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.value}</div>
          </div>
        ))}
        <div>
          <div className="mono" style={{ fontSize: '8px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '3px' }}>P&L</div>
          <PnL value={bet.profit} />
        </div>
      </div>
    </div>
  )
}

export function ResultsClient({
  initialPicks,
  initialStats,
}: {
  initialPicks: Bet[]
  initialStats: SystemStats[]
}) {
  const sp = useSearchParams()
  const dateFilter = sp.get('date') ?? null

  const [system,  setSystem]  = useState('ALL')
  const [result,  setResult]  = useState('ALL')
  const [sortBy,  setSortBy]  = useState<'date' | 'edge' | 'odds' | 'pnl'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [page,    setPage]    = useState(0)

  const PAGE_SIZE = 30

  const countsBySystem = useMemo(() =>
    Object.fromEntries(
      ALL_SYSTEMS.map(s => [s, s === 'ALL' ? initialPicks.length
        : initialPicks.filter(p => p.system === s).length])
    ), [initialPicks])

  const filtered = useMemo(() => {
    const f = initialPicks.filter(p => {
      if (dateFilter && p.game_date !== dateFilter) return false
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
  }, [initialPicks, system, result, sortBy, sortDir, dateFilter])

  const { rows: pnlRows, systems: chartSystems } = useMemo(
    () => buildPnLChart(initialPicks, system), [initialPicks, system]
  )
  const edgeRows = useMemo(() => buildEdgeChart(filtered), [filtered])
  const pageCount = Math.ceil(filtered.length / PAGE_SIZE)
  const pageIndex = Math.min(page, Math.max(0, pageCount - 1))
  const pageStart = pageIndex * PAGE_SIZE

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
          All settled bets &middot; Central Time &middot; Paper mode &middot; Past performance is not indicative of future results
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
              {[...chartSystems, 'ALL'].map(s => (
                <span key={s} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: PILL[s] ?? '#f5f5f7', flexShrink: 0, display: 'inline-block' }} />
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
              {chartSystems.map(s => (
                <Line key={s} type="monotone" dataKey={s} stroke={PILL[s] ?? '#a1a1aa'}
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

      {/* Per-system stats table -- groups by type with subtle dividers */}
      {initialStats.length > 0 && (
        <>
          <div className="results-model-desktop" style={{ border: B, marginBottom: '24px', overflowX: 'auto' }}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px', background: '#111114', borderBottom: B }}>
              {['System', 'Bets', 'Win Rate', 'ROI', 'P&L', 'Avg Edge'].map(h => (
                <div key={h} className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', padding: '9px 12px' }}>{h}</div>
              ))}
            </div>
            {Object.entries(SYSTEM_GROUPS).flatMap(([groupName, systems]) => {
              const groupStats = systems
                .map(s => initialStats.find(stat => stat.system === s))
                .filter(Boolean) as SystemStats[]
              if (groupStats.length === 0) return []

              return [
                <div key={`hdr-${groupName}`} style={{
                  display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px',
                  borderBottom: B, background: '#0a0a0c',
                }}>
                  <div className="mono" style={{
                    gridColumn: '1 / -1', padding: '6px 12px',
                    fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase',
                    color: '#2a2a31',
                  }}>{groupName}</div>
                </div>,
                ...groupStats.map(s => {
                  const r = parseFloat(String(s.roi ?? 0))
                  const pnl = parseFloat(String(s.total_pnl ?? 0))
                  const pc = PILL[s.system] ?? '#a1a1aa'
                  return (
                    <div key={s.system} style={{
                      display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', minWidth: '500px',
                      borderBottom: B, alignItems: 'center',
                    }}>
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
                }),
              ]
            })}
          </div>

          <div className="results-model-mobile" style={{ marginBottom: '24px' }}>
            {Object.entries(SYSTEM_GROUPS).map(([groupName, systems]) => {
              const groupStats = systems
                .map(s => initialStats.find(stat => stat.system === s))
                .filter(Boolean) as SystemStats[]
              if (groupStats.length === 0) return null

              return (
                <div key={groupName} style={{ marginBottom: '14px' }}>
                  <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginBottom: '8px' }}>{groupName}</div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    {groupStats.map(s => {
                      const r = parseFloat(String(s.roi ?? 0))
                      const pnl = parseFloat(String(s.total_pnl ?? 0))
                      const pc = PILL[s.system] ?? '#a1a1aa'
                      return (
                        <div key={s.system} style={{ border: B, borderRadius: 'var(--radius)', background: '#0d0d12', padding: '12px 13px' }}>
                          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px', gap: '12px' }}>
                            <span className="mono" style={{ fontSize: '12px', fontWeight: 800, color: pc }}>{s.system}</span>
                            <span className="mono" style={{ fontSize: '11px', fontWeight: 800, color: r >= 0 ? '#10b981' : '#ef4444' }}>{r >= 0 ? '+' : ''}{r.toFixed(1)}% ROI</span>
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '10px' }}>
                            {[
                              ['Bets', `${s.total_bets}/${GATE}`, '#f5f5f7'],
                              ['Win', `${parseFloat(String(s.win_rate ?? 0)).toFixed(1)}%`, '#f5f5f7'],
                              ['P&L', `${pnl >= 0 ? '+' : ''}${(pnl / 10).toFixed(2)}u`, pnl >= 0 ? '#10b981' : '#ef4444'],
                              ['Edge', `+${parseFloat(String(s.avg_edge ?? 0)).toFixed(1)}%`, '#10b981'],
                            ].map(([label, value, color]) => (
                              <div key={label}>
                                <div className="mono" style={{ fontSize: '8px', color: '#71717a', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '3px' }}>{label}</div>
                                <div className="mono" style={{ fontSize: '11px', fontWeight: 700, color }}>{value}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* System filter chips -- grouped with labels */}
      <div style={{ marginBottom: '16px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'center', marginBottom: '10px' }}>

          {/* ALL chip */}
          <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginRight: '4px' }}>System</span>
            <Chip label={`ALL (${countsBySystem['ALL'] ?? 0})`} active={system === 'ALL'} onClick={() => { setSystem('ALL'); setPage(0) }} />
          </div>

          {/* Grouped system chips */}
          {Object.entries(SYSTEM_GROUPS).map(([groupName, systems]) => (
            <div key={groupName} style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
              <GroupLabel label={groupName} />
              {systems.map(s => (
                <Chip key={s} label={`${SYSTEM_LABEL[s] ?? s}${countsBySystem[s] ? ` (${countsBySystem[s]})` : ''}`} active={system === s}
                  color={PILL[s]} onClick={() => { setSystem(s); setPage(0) }} />
              ))}
            </div>
          ))}
        </div>

        {/* Result + Sort row */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
            <span className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a', marginRight: '4px' }}>Result</span>
            {RESULTS.map(r => (
              <Chip key={r} label={r} active={result === r}
                color={r === 'WIN' ? '#10b981' : r === 'LOSS' ? '#ef4444' : r === 'VOID' ? '#71717a' : undefined}
                onClick={() => { setResult(r); setPage(0) }} />
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
                {s}{sortBy === s ? (sortDir === 'desc' ? ' v' : ' ^') : ''}
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
      </div>

      {/* Bets table */}
      {filtered.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '60px 20px', border: B }}>
          <p className="mono" style={{ fontSize: '12px', color: '#71717a' }}>No bets found.</p>
        </div>
      ) : (
        <>
        <div className="results-desktop" style={{ border: B, overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', background: '#111114', borderBottom: B }}>
            {([
              { label: 'Date',   key: 'date' as const },
              { label: 'System' },
              { label: 'Game' },
              { label: 'Pick' },
              { label: 'Odds',   key: 'odds' as const },
              { label: 'Edge',   key: 'edge' as const },
              { label: 'Stake' },
              { label: 'Book' },
              { label: 'Result' },
              { label: 'P&L',    key: 'pnl' as const },
            ] as Array<{ label: string; key?: 'date'|'edge'|'odds'|'pnl' }>).map(col => col.key ? (
              <button key={col.label} onClick={() => {
                if (sortBy === col.key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
                else { setSortBy(col.key!); setSortDir('desc') }
                setPage(0)
              }} className="mono" style={{
                padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase',
                color: sortBy === col.key ? '#f5f5f7' : '#71717a',
                background: 'none', border: 'none', cursor: 'pointer', textAlign: 'left',
              }}>
                {col.label}{sortBy === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
              </button>
            ) : (
              <div key={col.label} className="mono" style={{ padding: '9px 12px', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#71717a' }}>{col.label}</div>
            ))}
          </div>
          {filtered.slice(pageStart, pageStart + PAGE_SIZE).map((bet, i) => {
            const game = bet.home_team ? `${bet.away_team} @ ${bet.home_team}` : `Game ${bet.game_pk}`
            return (
              <div key={bet.id ?? i} style={{ display: 'grid', gridTemplateColumns: COL, minWidth: '860px', borderBottom: B, alignItems: 'center' }}>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#71717a' }}>
                  {fmtDisplayDate(bet.game_date)}
                </div>
                <div style={{ padding: '8px 12px' }}><SystemBadge system={bet.system} /></div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{game}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickLabel(bet)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>{formatOdds(bet.odds)}</div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#10b981' }}>
                  {bet.edge != null ? `${bet.edge >= 0 ? '+' : ''}${(bet.edge * 100).toFixed(1)}%` : '--'}
                </div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '11px', color: '#f5f5f7' }}>
                  {bet.stake != null && bet.stake > 0 ? `$${bet.stake.toFixed(0)}` : '--'}
                </div>
                <div className="mono" style={{ padding: '8px 12px', fontSize: '10px', color: '#71717a', textTransform: 'capitalize' }}>
                  {bet.book ?? '--'}
                </div>
                <div style={{ padding: '8px 12px' }}><ResultPill result={bet.result} /></div>
                <div style={{ padding: '8px 12px' }}><PnL value={bet.profit} /></div>
              </div>
            )
          })}
          {/* Pagination */}
          {filtered.length > PAGE_SIZE && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderTop: B, background: '#0a0a0c' }}>
              <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={pageIndex === 0}
                className="mono" style={{ fontSize: '11px', padding: '5px 12px', cursor: pageIndex === 0 ? 'default' : 'pointer', border: B, background: 'transparent', color: pageIndex === 0 ? '#2a2a31' : '#71717a' }}>
                Prev
              </button>
              <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>
                {pageStart + 1}-{Math.min(pageStart + PAGE_SIZE, filtered.length)} of {filtered.length}
              </span>
              <button onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}
                disabled={pageIndex >= pageCount - 1}
                className="mono" style={{ fontSize: '11px', padding: '5px 12px', cursor: pageIndex >= pageCount - 1 ? 'default' : 'pointer', border: B, background: 'transparent', color: pageIndex >= pageCount - 1 ? '#2a2a31' : '#71717a' }}>
                Next
              </button>
            </div>
          )}
        </div>
        <div className="results-mobile">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            {filtered.slice(pageStart, pageStart + PAGE_SIZE).map((bet, i) => (
              <ResultBetCard key={bet.id ?? i} bet={bet} />
            ))}
          </div>
          {filtered.length > PAGE_SIZE && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 2px', marginTop: '12px' }}>
              <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={pageIndex === 0}
                className="mono" style={{ fontSize: '11px', padding: '6px 12px', cursor: pageIndex === 0 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius-sm)', background: 'transparent', color: pageIndex === 0 ? '#2a2a31' : '#71717a' }}>
                Prev
              </button>
              <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>
                {pageStart + 1}-{Math.min(pageStart + PAGE_SIZE, filtered.length)} of {filtered.length}
              </span>
              <button onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}
                disabled={pageIndex >= pageCount - 1}
                className="mono" style={{ fontSize: '11px', padding: '6px 12px', cursor: pageIndex >= pageCount - 1 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius-sm)', background: 'transparent', color: pageIndex >= pageCount - 1 ? '#2a2a31' : '#71717a' }}>
                Next
              </button>
            </div>
          )}
        </div>
        </>
      )}
    </div>
  )
}
