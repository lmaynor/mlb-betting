'use client'

import { useState, useMemo, useCallback } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, ReferenceArea,
} from 'recharts'
import type { CLVDataPoint } from '@/lib/types'
import { B, SYSTEM_COLOR } from '@/lib/tokens'

// ---- constants ---------------------------------------------------------------

const ALL_SYSTEMS = ['NRFI', 'HR', 'F5', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'GAME']
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
      padding: '4px 10px', fontSize: '10px', fontFamily: 'JetBrains Mono, monospace',
      fontWeight: active ? 600 : 400,
      border: `0.5px solid ${active ? (color ?? '#10b981') : '#2a2a31'}`,
      background: active ? `${color ?? '#10b981'}18` : 'transparent',
      color: active ? (color ?? '#10b981') : '#52525b',
      cursor: 'pointer', letterSpacing: '0.05em', textTransform: 'uppercase' as const,
      transition: 'all 0.15s',
    }}>{label}</button>
  )
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div style={{ padding: '16px 20px', flex: 1, minWidth: 0 }}>
      <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#52525b', marginBottom: '6px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '22px', fontWeight: 700, color: color ?? '#f5f5f7', lineHeight: 1 }}>{value}</div>
      {sub && <div className="mono" style={{ fontSize: '10px', color: '#3f3f46', marginTop: '4px' }}>{sub}</div>}
    </div>
  )
}

const CustomTooltip = ({ active, payload }: { active?: boolean; payload?: Array<{ payload: CLVDataPoint & { x: number; y: number } }> }) => {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  const sys = p.system
  const color = SYSTEM_COLOR[sys] ?? '#a1a1aa'
  return (
    <div style={{ background: '#111114', border: B, padding: '12px 14px', fontSize: '11px', minWidth: '160px' }}>
      <div className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.08em', color, marginBottom: '8px', textTransform: 'uppercase' }}>{sys}</div>
      <div style={{ color: '#71717a', marginBottom: '6px', fontSize: '10px' }}>{p.game_date}</div>
      <div style={{ display: 'grid', gridTemplateColumns: '70px 1fr', rowGap: '3px' }}>
        <span style={{ color: '#52525b' }}>Model edge</span>
        <span className="mono" style={{ color: '#10b981', fontWeight: 600 }}>{fmt1(p.x)}</span>
        <span style={{ color: '#52525b' }}>CLV</span>
        <span className="mono" style={{ color: p.y >= 0 ? '#10b981' : '#ef4444', fontWeight: 600 }}>{fmt2(p.y)}</span>
        <span style={{ color: '#52525b' }}>Result</span>
        <span className="mono" style={{ color: p.result === 'win' ? '#10b981' : '#ef4444', textTransform: 'uppercase' }}>{p.result}</span>
        <span style={{ color: '#52525b' }}>Open odds</span>
        <span className="mono" style={{ color: '#f5f5f7' }}>{p.opening_odds > 0 ? `+${p.opening_odds}` : p.opening_odds}</span>
      </div>
      {p.player && <div className="mono" style={{ fontSize: '10px', color: '#3f3f46', marginTop: '6px' }}>{p.player}</div>}
    </div>
  )
}

// ---- main component ----------------------------------------------------------

export function CLVClient({ initial }: { initial: CLVDataPoint[] }) {
  const [days,            setDays]            = useState(90)
  const [activeSystems,   setActiveSystems]   = useState<string[]>([])   // empty = all
  const [resultFilter,    setResultFilter]    = useState('All')
  const [data,            setData]            = useState<CLVDataPoint[]>(initial)
  const [loading,         setLoading]         = useState(false)
  const [infoOpen,        setInfoOpen]        = useState(false)

  // Re-fetch when date range changes
  const fetchData = useCallback(async (d: number) => {
    setLoading(true)
    try {
      const res  = await fetch(`/api/stats/clv?days=${d}`)
      if (res.ok) {
        const json = await res.json()
        setData((json.data ?? []) as CLVDataPoint[])
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
      color: SYSTEM_COLOR[sys] ?? '#a1a1aa',
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
    return { meanCLV, pctPos, r, n: filtered.length }
  }, [filtered])

  // Axis domain (dynamic + padded)
  const xVals = filtered.map(p => p.model_edge_pct)
  const yVals = filtered.map(p => p.clv_pct)
  const xMin  = xVals.length ? Math.floor(Math.min(...xVals) - 2) : -5
  const xMax  = xVals.length ? Math.ceil(Math.max(...xVals)  + 2) : 20
  const yMin  = yVals.length ? Math.floor(Math.min(...yVals) - 3) : -20
  const yMax  = yVals.length ? Math.ceil(Math.max(...yVals)  + 3) : 20

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>

      {/* Header */}
      <div style={{ marginBottom: '28px' }}>
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#10b981', marginBottom: '6px' }}>Tools -- Pro</p>
        <h1 style={{ fontSize: '20px', fontWeight: 600, color: '#f5f5f7', marginBottom: '6px', letterSpacing: '-0.01em' }}>CLV + Edge Correlation</h1>
        <p style={{ fontSize: '13px', color: '#71717a' }}>Each dot is a settled Beezy pick. X = model edge at bet time. Y = closing line value. Positive CLV in the top-right quadrant means the model consistently finds real market inefficiencies.</p>
      </div>

      {/* Filter bar */}
      <div style={{ border: B, padding: '14px 16px', marginBottom: '16px', display: 'flex', flexWrap: 'wrap', gap: '12px', alignItems: 'center' }}>
        {/* Date range */}
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', color: '#3f3f46', marginRight: '6px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>Range</span>
          {DATE_TABS.map(t => (
            <Chip key={t.days} label={t.label} active={days === t.days} onClick={() => handleDays(t.days)} />
          ))}
        </div>
        {/* Result filter */}
        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
          <span className="mono" style={{ fontSize: '9px', color: '#3f3f46', marginRight: '6px', letterSpacing: '0.08em', textTransform: 'uppercase' }}>Result</span>
          {RESULT_TABS.map(r => (
            <Chip key={r} label={r} active={resultFilter === r} onClick={() => setResultFilter(r)} />
          ))}
        </div>
        {loading && <span className="mono" style={{ fontSize: '10px', color: '#3f3f46', marginLeft: 'auto' }}>Loading...</span>}
      </div>

      {/* System chips */}
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '16px' }}>
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
          <button onClick={() => setActiveSystems([])} style={{ fontSize: '10px', fontFamily: 'JetBrains Mono, monospace', color: '#52525b', background: 'none', border: 'none', cursor: 'pointer', padding: '4px 6px' }}>
            clear
          </button>
        )}
      </div>

      {/* Scatter chart */}
      <div style={{ border: B, background: '#0a0a0c', marginBottom: '1px' }}>
        {filtered.length === 0 ? (
          <div style={{ height: '420px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <div style={{ textAlign: 'center' }}>
              <div className="mono" style={{ fontSize: '12px', color: '#3f3f46', marginBottom: '8px' }}>No CLV data for this filter</div>
              <div className="mono" style={{ fontSize: '11px', color: '#27272a' }}>CLV is captured as picks approach game time.</div>
            </div>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={460}>
            <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f1f24" strokeOpacity={0.6} />

              {/* Quadrant shading */}
              <ReferenceArea x1={0} x2={xMax} y1={0} y2={yMax} fill="#10b98106" stroke="none" />
              <ReferenceArea x1={xMin} x2={0} y1={yMin} y2={0} fill="#ef444406" stroke="none" />

              <XAxis
                dataKey="x"
                type="number"
                domain={[xMin, xMax]}
                name="Model Edge"
                unit="%"
                tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                tickLine={false}
                axisLine={{ stroke: '#2a2a31' }}
                label={{ value: 'Model Edge %', position: 'insideBottom', offset: -10, fill: '#3f3f46', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
              />
              <YAxis
                dataKey="y"
                type="number"
                domain={[yMin, yMax]}
                name="CLV"
                unit="%"
                tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                tickLine={false}
                axisLine={{ stroke: '#2a2a31' }}
                width={52}
                label={{ value: 'CLV %', angle: -90, position: 'insideLeft', fill: '#3f3f46', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', dx: 14 }}
              />

              <ReferenceLine x={0} stroke="#2a2a31" strokeDasharray="4 2" />
              <ReferenceLine y={0} stroke="#2a2a31" strokeDasharray="4 2" />

              <Tooltip content={<CustomTooltip />} cursor={{ strokeDasharray: '3 3', stroke: '#2a2a31' }} />

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
        <span className="mono" style={{ fontSize: '9px', color: '#10b98160', letterSpacing: '0.06em' }}>TOP RIGHT: edge + positive CLV -- model is finding real value</span>
        <span className="mono" style={{ fontSize: '9px', color: '#ef444460', letterSpacing: '0.06em' }}>BOTTOM LEFT: no edge, negative CLV</span>
      </div>

      {/* System legend */}
      {bySystem.length > 0 && (
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', marginBottom: '16px', paddingLeft: '4px' }}>
          {bySystem.map(({ sys, color }) => (
            <span key={sys} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: color, display: 'inline-block', flexShrink: 0 }} />
              <span className="mono" style={{ fontSize: '10px', color: '#71717a' }}>{sys}</span>
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
            sub="avg closing line value per bet"
            color={stats.meanCLV >= 0 ? '#10b981' : '#ef4444'}
          />
          <div style={{ width: '0.5px', background: '#1f1f24', flexShrink: 0 }} />
          <StatCard
            label="Positive CLV"
            value={`${stats.pctPos.toFixed(0)}%`}
            sub="of bets beat the closing line"
            color={stats.pctPos >= 50 ? '#10b981' : '#f59e0b'}
          />
          <div style={{ width: '0.5px', background: '#1f1f24', flexShrink: 0 }} />
          <StatCard
            label="Correlation (r)"
            value={stats.r !== null ? stats.r.toFixed(2) : '--'}
            sub="edge vs CLV correlation"
            color={stats.r !== null && stats.r > 0.2 ? '#10b981' : '#f5f5f7'}
          />
          <div style={{ width: '0.5px', background: '#1f1f24', flexShrink: 0 }} />
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
          <span className="mono" style={{ fontSize: '10px', letterSpacing: '0.08em', textTransform: 'uppercase', color: '#52525b' }}>What is CLV?</span>
          <span className="mono" style={{ fontSize: '12px', color: '#3f3f46' }}>{infoOpen ? '-' : '+'}</span>
        </button>
        {infoOpen && (
          <div style={{ padding: '0 16px 16px', borderTop: B }}>
            <p style={{ fontSize: '12px', color: '#71717a', lineHeight: 1.65, marginTop: '12px', marginBottom: '8px' }}>
              Closing line value (CLV) measures how much better Beezy&apos;s opening line was vs. the closing price at kickoff. When the model predicts NRFI at -115 and the line closes at -128, the edge was real -- sharp money agreed, moving the line in the same direction.
            </p>
            <p style={{ fontSize: '12px', color: '#71717a', lineHeight: 1.65 }}>
              Positive mean CLV over a large sample is the strongest indicator that a betting model is genuinely finding inefficiencies rather than riding variance. It&apos;s measured independent of outcomes -- a bet can have positive CLV and lose, or negative CLV and win.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
