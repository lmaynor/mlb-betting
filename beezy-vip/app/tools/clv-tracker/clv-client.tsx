'use client'

import { useState, useMemo, useCallback } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import type { CLVDataPoint } from '@/lib/types'
import { B, SYSTEM_COLOR } from '@/lib/tokens'

// ---- constants ---------------------------------------------------------------

const ALL_SYSTEMS = ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'PITCHER_ER', 'BATTER_TB', 'BATTER_HITS', 'BATTER_K', 'SB', 'GAME', 'EV']
const DATE_TABS   = [
  { label: '30D',    days: 30   },
  { label: '60D',    days: 60   },
  { label: '90D',    days: 90   },
  { label: 'Season', days: 210  },
  { label: 'All',    days: 9999 },
]
const RESULT_TABS = ['All', 'Win', 'Loss']

// ---- helpers -----------------------------------------------------------------

function pearsonR(data: { x: number; y: number }[]): number | null {
  const n = data.length
  if (n < 3) return null
  const mx = data.reduce((s, p) => s + p.x, 0) / n
  const my = data.reduce((s, p) => s + p.y, 0) / n
  const num  = data.reduce((s, p) => s + (p.x - mx) * (p.y - my), 0)
  const denX = Math.sqrt(data.reduce((s, p) => s + (p.x - mx) ** 2, 0))
  const denY = Math.sqrt(data.reduce((s, p) => s + (p.y - my) ** 2, 0))
  if (denX === 0 || denY === 0) return null
  return num / (denX * denY)
}

function fmt1(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(1)}%` }
function fmt2(n: number) { return `${n >= 0 ? '+' : ''}${n.toFixed(2)}%` }

// ---- sub-components ---------------------------------------------------------

function Chip({ label, active, color, onClick }: {
  label: string; active: boolean; color?: string; onClick: () => void
}) {
  return (
    <button onClick={onClick} style={{
      padding: '4px 10px', fontSize: '10px', fontFamily: 'var(--font-mono), monospace',
      fontWeight: active ? 600 : 400,
      border: `1px solid ${active ? (color ?? 'var(--color-neutral-300)') : 'var(--color-separator-border)'}`,
      background: active ? `color-mix(in oklab, ${color ?? 'var(--color-neutral-300)'} 16%, var(--color-background-full))` : 'var(--color-background-primary-default)',
      color: active ? (color ?? 'var(--color-neutral-300)') : 'var(--color-neutral-300)',
      cursor: 'pointer', letterSpacing: '0.05em', textTransform: 'uppercase' as const,
      transition: 'all 0.15s',
    }}>{label}</button>
  )
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="py-4 px-5 flex-1 min-w-0">
      <div className="mono text-[10px] tracking-[0.1em] uppercase text-text-secondary mb-1.5">{label}</div>
      <div className="mono" style={{ fontSize: '22px', fontWeight: 700, color: color ?? 'var(--color-text-primary)', lineHeight: 1 }}>{value}</div>
      {sub && <div className="mono text-[10px] text-text-secondary mt-1">{sub}</div>}
    </div>
  )
}

const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: CLVDataPoint & { x: number; y: number } }> }) => {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  const sys = p.system
  const color = SYSTEM_COLOR[sys] ?? 'var(--color-neutral-300)'
  return (
    <div style={{ background: 'var(--color-background-primary-default)', border: B, padding: '12px 14px', fontSize: '11px', minWidth: '160px' }}>
      <div className="mono" style={{ fontSize: '10px', fontWeight: 600, letterSpacing: '0.08em', color, marginBottom: '8px', textTransform: 'uppercase' }}>{sys}</div>
      <div className="text-text-secondary mb-1.5 text-[10px]">{p.game_date}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '70px 1fr', rowGap: '3px' }}>
        <span className="text-text-secondary">Model edge</span>
        <span className="mono text-[var(--signal)] font-semibold">{fmt1(p.x)}</span>
        <span className="text-text-secondary">CLV</span>
        <span className="mono" style={{ color: p.y >= 0 ? 'var(--signal)' : 'var(--loss)', fontWeight: 600 }}>{fmt2(p.y)}</span>
        <span className="text-text-secondary">Result</span>
        <span className="mono" style={{ color: p.result === 'win' ? 'var(--signal)' : 'var(--loss)', textTransform: 'uppercase' }}>{p.result}</span>
        <span className="text-text-secondary">Open odds</span>
        <span className="mono text-text-primary">{p.opening_odds > 0 ? `+${p.opening_odds}` : p.opening_odds}</span>
      </div>
      {p.player && <div className="mono text-[10px] text-text-secondary mt-1.5">{p.player}</div>}
    </div>
  )
}

// The API serialises Postgres numerics as strings ("9.80"); summing those concatenated into NaN. Coerce once at ingest
// and drop anything non-finite so every downstream mean/correlation/axis works on real numbers.
function normalizeClv(rows: CLVDataPoint[]): CLVDataPoint[] {
  return rows
    .map(p => ({ ...p, model_edge_pct: Number(p.model_edge_pct), clv_pct: Number(p.clv_pct) }))
    .filter(p => Number.isFinite(p.model_edge_pct) && Number.isFinite(p.clv_pct))
}

function percentile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))
  return sorted[i]
}

// ---- main component ----------------------------------------------------------

export function CLVClient({ initial }: { initial: CLVDataPoint[] }) {
  const [days,            setDays]            = useState(90)
  const [activeSystems,   setActiveSystems]   = useState<string[]>([])   // empty = all
  const [resultFilter,    setResultFilter]    = useState('All')
  const [data,            setData]            = useState<CLVDataPoint[]>(() => normalizeClv(initial))
  const [loading,         setLoading]         = useState(false)
  const [infoOpen,        setInfoOpen]        = useState(false)

  // Re-fetch when date range changes
  const fetchData = useCallback(async (d: number) => {
    setLoading(true)
    try {
      const res  = await fetch(`/api/stats/clv?days=${d}`)
      if (res.ok) {
        const json = await res.json()
        setData(normalizeClv((json.data ?? []) as CLVDataPoint[]))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  function handleDays(d: number) {
    setDays(d)
    void fetchData(d)
  }

  function toggleSystem(sys: string) {
    setActiveSystems(prev =>
      prev.includes(sys) ? prev.filter(s => s !== sys) : [...prev, sys]
    )
  }

  // Client-side filters (system + result)
  const filtered = useMemo(() => {
    let d = data
    if (activeSystems.length > 0) d = d.filter(p => activeSystems.includes(p.system))
    if (resultFilter === 'Win')  d = d.filter(p => p.result === 'win')
    if (resultFilter === 'Loss') d = d.filter(p => p.result === 'loss')
    return d
  }, [data, activeSystems, resultFilter])

  // Per-system scatter series
  const systems = activeSystems.length > 0 ? activeSystems : ALL_SYSTEMS
  const bySystem = useMemo(() =>
    systems.map(sys => ({
      sys,
      color: SYSTEM_COLOR[sys] ?? 'var(--color-neutral-300)',
      points: filtered
        .filter(p => p.system === sys)
        .map(p => ({ ...p, x: p.model_edge_pct, y: p.clv_pct })),
    })).filter(s => s.points.length > 0),
  [filtered, systems])

  // Summary stats
  const stats = useMemo(() => {
    if (filtered.length === 0) return null
    const meanCLV = filtered.reduce((s, p) => s + p.clv_pct, 0) / filtered.length
    const pctPos  = filtered.filter(p => p.clv_pct > 0).length / filtered.length * 100
    const r       = pearsonR(filtered.map(p => ({ x: p.model_edge_pct, y: p.clv_pct })))
    const sortedClv = filtered.map(p => p.clv_pct).sort((x, y) => x - y)
    const medianCLV = sortedClv[Math.floor(sortedClv.length / 2)]
    return { meanCLV, medianCLV, pctPos, r, n: filtered.length }
  }, [filtered])

  // Axis domain (dynamic + padded)
  const xVals = filtered.map(p => p.model_edge_pct)
  const yVals = filtered.map(p => p.clv_pct)
  // Axes span the 1st-99th percentile so a few extreme closes cannot squash the other 98% of points flat.
  const xs = [...xVals].sort((a, b) => a - b)
  const ys = [...yVals].sort((a, b) => a - b)
  const xMin  = xVals.length ? Math.floor(Math.min(0, percentile(xs, 0.01)) - 2) : -5
  const xMax  = xVals.length ? Math.ceil(percentile(xs, 0.99) + 2) : 20
  const yMin  = yVals.length ? Math.floor(percentile(ys, 0.01) - 3) : -20
  const yMax  = yVals.length ? Math.ceil(percentile(ys, 0.99) + 3) : 20

  return (
    <div className="max-w-[1200px] mx-auto py-10 px-5">

      {/* Header */}
      <div className="flex items-baseline justify-between gap-3 flex-wrap mb-4">
        <h1 title="Each dot is a settled pick: edge at bet time (x) against closing line value (y)." className="text-[20px] font-bold text-text-primary m-0">CLV Tracker</h1>
        <span className="mono text-[12px] text-text-secondary">Edge at bet time vs closing line value &middot; axes show the 1st-99th percentile</span>
      </div>

      {/* Filter bar */}
      <div style={{ border: B, padding: '14px 16px', marginBottom: '16px', display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center' }}>
        {/* Date range */}
        <div className="flex gap-1 items-center">
          <span className="mono text-[10px] text-text-secondary mr-1.5 tracking-[0.08em] uppercase">Range</span>
          {DATE_TABS.map(t => (
            <Chip key={t.days} label={t.label} active={days === t.days} onClick={() => handleDays(t.days)} />
          ))}
        </div>
        {/* Result filter */}
        <div className="flex gap-1 items-center">
          <span className="mono text-[10px] text-text-secondary mr-1.5 tracking-[0.08em] uppercase">Result</span>
          {RESULT_TABS.map(r => (
            <Chip key={r} label={r} active={resultFilter === r} onClick={() => setResultFilter(r)} />
          ))}
        </div>
        {loading && <span className="mono" style={{ fontSize: '10px', color: 'var(--color-text-secondary)', marginLeft: 'auto' }}>Loading...</span>}
      </div>

      {/* System chips */}
      <div className="flex gap-1.5 flex-wrap mb-4">
        {ALL_SYSTEMS.map(sys => (
          <Chip
            key={sys}
            label={sys}
            color={SYSTEM_COLOR[sys]}
            active={activeSystems.length === 0 || activeSystems.includes(sys)}
            onClick={() => toggleSystem(sys)}
          />
        ))}
        {activeSystems.length > 0 && (
          <button onClick={() => setActiveSystems([])} style={{ fontSize: '10px', fontFamily: 'var(--font-mono), monospace', color: 'var(--color-text-secondary)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 6px' }}>
            clear
          </button>
        )}
      </div>

      {/* Scatter chart */}
      <div style={{ border: B, background: 'var(--color-background-full)', marginBottom: '1px' }}>
        {filtered.length === 0 ? (
          <div className="h-[420px] flex items-center justify-center">
            <div className="text-center">
              <div className="mono text-[12px] text-text-secondary mb-2">No CLV data for this filter</div>
              <div className="mono text-[11px] text-text-secondary">CLV is captured as picks approach game time.</div>
            </div>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={460}>
            <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e2230" strokeOpacity={0.6} />

              {/* Quadrant shading */}
              <ReferenceArea x1={0} x2={xMax} y1={0} y2={yMax} fill="#57f28712" stroke="none" />
              <ReferenceArea x1={xMin} x2={0} y1={yMin} y2={0} fill="#ec6a6a12" stroke="none" />

              <XAxis
                dataKey="x"
                type="number"
                domain={[xMin, xMax]}
                allowDataOverflow
                name="Model Edge"
                unit="%"
                tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'ui-monospace, monospace' }}
                tickLine={false}
                axisLine={{ stroke: '#2e3446' }}
                label={{ value: 'Model Edge %', position: 'insideBottom', offset: -10, fill: '#7d8596', fontSize: 10, fontFamily: 'ui-monospace, monospace' }}
              />
              <YAxis
                dataKey="y"
                type="number"
                domain={[yMin, yMax]}
                allowDataOverflow
                name="CLV"
                unit="%"
                tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'ui-monospace, monospace' }}
                tickLine={false}
                axisLine={{ stroke: '#2e3446' }}
                width={52}
                label={{ value: 'CLV %', angle: -90, position: 'insideLeft', fill: '#7d8596', fontSize: 10, fontFamily: 'ui-monospace, monospace', dx: 14 }}
              />

              <ReferenceLine x={0} stroke="#2e3446" strokeDasharray="4 2" />
              <ReferenceLine y={0} stroke="#2e3446" strokeDasharray="4 2" />

              <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3', stroke: '#2e3446' }} />

              {bySystem.map(({ sys, color, points }) => (
                <Scatter
                  key={sys}
                  name={sys}
                  data={points}
                  fill={color}
                  fillOpacity={0.75}
                  strokeWidth={0}
                />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Quadrant labels (outside chart, below) */}
      <div style={{ border: B, borderTop: 'none', display: 'flex', justifyContent: 'space-between', padding: '6px 16px', marginBottom: '16px' }}>
        <span className="mono" style={{ fontSize: '10px', color: 'var(--signal)60', letterSpacing: '0.06em' }}>TOP RIGHT: edge + positive CLV -- model is finding real value</span>
        <span className="mono" style={{ fontSize: '10px', color: 'var(--loss)60', letterSpacing: '0.06em' }}>BOTTOM LEFT: no edge, negative CLV</span>
      </div>

      {/* System legend */}
      {bySystem.length > 0 && (
        <div className="flex gap-4 flex-wrap mb-4 pl-1">
          {bySystem.map(({ sys, color }) => (
            <span key={sys} className="flex items-center gap-[5px]">
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
              <span className="mono text-[10px] text-text-secondary">{sys}</span>
            </span>
          ))}
        </div>
      )}

      {/* Summary stat cards */}
      {stats && (
        <div style={{ border: B, display: 'flex', flexWrap: 'wrap', marginBottom: '16px' }}>
          <StatCard
            label="Mean CLV"
            value={fmt2(stats.meanCLV)}
            sub={`average per bet; median ${fmt2(stats.medianCLV)}`}
            color={stats.meanCLV >= 0 ? 'var(--signal)' : 'var(--loss)'}
          />
          <div  className="w-[1px] bg-[var(--color-separator-border)] shrink-0"/>
          <StatCard
            label="Positive CLV"
            value={`${stats.pctPos.toFixed(0)}%`}
            sub="of bets beat the closing line"
            color={stats.pctPos >= 50 ? 'var(--signal)' : 'var(--warn)'}
          />
          <div  className="w-[1px] bg-[var(--color-separator-border)] shrink-0"/>
          <StatCard
            label="Correlation (r)"
            value={stats.r !== null ? stats.r.toFixed(2) : '--'}
            sub="edge vs CLV correlation"
            color={stats.r !== null && stats.r > 0.2 ? 'var(--signal)' : 'var(--color-text-primary)'}
          />
          <div  className="w-[1px] bg-[var(--color-separator-border)] shrink-0"/>
          <StatCard
            label="Sample"
            value={`n=${stats.n}`}
            sub="settled bets with closing data"
          />
        </div>
      )}

      {/* Info panel */}
      <div style={{ border: B }}>
        <button
          onClick={() => setInfoOpen(o => !o)}
          style={{ width: '100%', background: 'none', border: 'none', cursor: 'pointer', padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
        >
          <span className="mono text-[10px] tracking-[0.08em] uppercase text-text-secondary">What is CLV?</span>
          <span className="mono text-[12px] text-text-secondary">{infoOpen ? '-' : '+'}</span>
        </button>
        {infoOpen && (
          <div style={{ padding: '0 16px 16px', borderTop: B }}>
            <p className="times text-[12px] text-text-secondary leading-[1.65] mt-3 mb-2">
              Closing line value (CLV) measures how much better Beezy&apos;s opening line was vs. the closing price at kickoff. When the model predicts NRFI at -115 and the line closes at -128, the edge was real -- sharp money agreed, moving the line in the same direction.
            </p>
            <p className="times text-[12px] text-text-secondary leading-[1.65]">
              Positive mean CLV over a large sample is the strongest indicator that a betting model is genuinely finding inefficiencies rather than riding variance. It&apos;s measured independent of outcomes -- a bet can have positive CLV and lose, or negative CLV and win.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
