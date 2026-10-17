'use client'

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { AnalyticsDeepDive, SystemStats } from '@/lib/types'
import { apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { bookDisplay, CARD_STYLE, SYSTEM_COLOR, systemLabel } from '@/lib/tokens'
import { fmtMoney } from '@/lib/ev-math'
import { SPORT_PILLARS } from '@/lib/sports'

type Tab = 'bankroll' | 'calibration' | 'books' | 'health'
const TABS: { id: Tab; label: string }[] = [
  { id: 'bankroll', label: 'BANKROLL SIMULATOR' },
  { id: 'calibration', label: 'EDGE CALIBRATION' },
  { id: 'books', label: 'BOOK LEADERBOARD' },
  { id: 'health', label: 'SYSTEM HEALTH' },
]
const GATE = 200
const STORED_KELLY_FRACTION = 0.25   // fraction already baked into bets.kelly_pct for EV rows
const GRID = '#1e2230'
const AXIS = '#7d8596'
const SYSTEM_CHIPS = ['EV', 'ALL', 'NRFI', 'HR', 'K', 'OUTS', 'F5', 'GAME', 'BATTER_TB', 'BATTER_HITS', 'SB']

const tipStyle = { background: '#171a24', border: `1px solid ${GRID}`, borderRadius: 8, fontSize: 12, color: '#f3f4f6' }

export function AnalyticsClient({ initial, stats }: { initial: AnalyticsDeepDive | null; stats: SystemStats[] }) {
  // The tab lives in the URL (?tab=) and is chosen from the Results section tabs under the nav, so it is linkable.
  const sp = useSearchParams()
  const tabParam = sp.get('tab')
  const tab: Tab = TABS.some(t => t.id === tabParam) ? (tabParam as Tab) : 'bankroll'
  const [data, setData] = useState<AnalyticsDeepDive | null>(initial)
  const [bankroll, setBankroll] = useState(1000)
  const [fraction, setFraction] = useState(STORED_KELLY_FRACTION)
  const [system, setSystem] = useState<string>('EV')
  const [loading, setLoading] = useState(false)
  const first = useRef(true)

  useEffect(() => {
    if (first.current) { first.current = false; return }
    const id = setTimeout(async () => {
      setLoading(true)
      try {
        setData(await apiGetAnalyticsDeepDive(system === 'ALL' ? undefined : system, { bankroll, kellyMult: fraction / STORED_KELLY_FRACTION }))
      } catch { /* keep last data */ } finally { setLoading(false) }
    }, 350)
    return () => clearTimeout(id)
  }, [bankroll, fraction, system])

  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '28px 24px 64px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px 20px', flexWrap: 'wrap', marginBottom: '16px' }}>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0, letterSpacing: '-0.01em' }}>{TABS.find(t => t.id === tab)!.label}</h1>
      {loading && <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>updating...</span>}
      </div>

      {!data ? (
        <div style={{ ...CARD_STYLE, padding: '36px 20px', textAlign: 'center', color: 'var(--fog)' }}>Analytics are unavailable right now.</div>
      ) : tab === 'bankroll' ? (
        <Bankroll data={data} bankroll={bankroll} setBankroll={setBankroll} fraction={fraction} setFraction={setFraction} />
      ) : tab === 'calibration' ? (
        <Calibration data={data} system={system} setSystem={setSystem} />
      ) : tab === 'books' ? (
        <Books data={data} system={system} setSystem={setSystem} />
      ) : (
        <Health stats={stats} />
      )}
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '8px 2px 0' }}>
        Computed from graded bets in the public ledger. All results are paper: flagged price at flag time, voids excluded from ROI, no slippage or limits.
      </p>
    </div>
  )
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section style={{ ...CARD_STYLE, padding: '18px 20px', marginBottom: '16px' }}>
      <h2 className="dell-heading" style={{ fontSize: '11px', color: 'var(--silver)' }}>{title}</h2>
      {sub && <p className="times" style={{ fontSize: '12.5px', color: 'var(--fog)', margin: '6px 0 14px', lineHeight: 1.55, maxWidth: '90ch' }}>{sub}</p>}
      {children}
    </section>
  )
}

function SystemChips({ system, setSystem }: { system: string; setSystem: (s: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '14px' }} aria-label="System filter">
      {SYSTEM_CHIPS.map(k => (
        <button key={k} onClick={() => setSystem(k)} aria-pressed={system === k}
          style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
            color: system === k ? '#090a0f' : (SYSTEM_COLOR[k] ?? 'var(--silver)'), background: system === k ? (SYSTEM_COLOR[k] ?? 'var(--signal)') : 'var(--surface-elevated)', border: `1px solid ${system === k ? (SYSTEM_COLOR[k] ?? 'var(--signal)') : GRID}` }}>
          {k === 'ALL' ? 'ALL SYSTEMS' : systemLabel(k).toUpperCase()}
        </button>
      ))}
    </div>
  )
}

// -- 1. Bankroll simulator ------------------------------------------------------

function Bankroll({ data, bankroll, setBankroll, fraction, setFraction }: {
  data: AnalyticsDeepDive; bankroll: number; setBankroll: (n: number) => void; fraction: number; setFraction: (n: number) => void
}) {
  const h = data.ev_headline
  const rows = data.bankroll_curve.map(p => ({
    date: p.date.slice(5),
    'EV Kelly': Math.round(bankroll + p.ev_kelly_cum),
    'EV flat $100': Math.round(1000 + p.ev_flat_cum),
    'MLB models (as staked)': Math.round(1000 + p.models_cum),
  }))
  return (
    <>
      <Panel
        title="BANKROLL GROWTH: +EV ALERTS, FLAT VS KELLY"
        sub={`Non-compounding rescale of real graded outcomes: each bet stakes clip(kelly x multiplier, 0, ${(h.kelly_cap * 100).toFixed(0)}%) of the starting bankroll and earns the bet's real profit/stake ratio. Flat and model lines start at $1,000.`}
      >
        <div style={{ display: 'flex', gap: '28px', flexWrap: 'wrap', marginBottom: '16px' }}>
          <Slider label="STARTING BANKROLL" value={bankroll} min={500} max={25000} step={500} onChange={setBankroll} fmt={v => fmtMoney(v)} />
          <Slider label="KELLY FRACTION" value={fraction} min={0.1} max={0.5} step={0.05} onChange={setFraction} fmt={v => `${v.toFixed(2)}x`} />
        </div>
        <div style={{ display: 'flex', gap: '24px', flexWrap: 'wrap', marginBottom: '8px' }}>
          <Big label="KELLY ENDING BANKROLL" value={fmtMoney(h.kelly_ending_bankroll)} />
          <Big label="KELLY ROI (ON STAKED)" value={h.kelly_roi_pct == null ? '--' : `${h.kelly_roi_pct > 0 ? '+' : ''}${h.kelly_roi_pct.toFixed(2)}%`} />
          <Big label="FLAT $100 P&L" value={fmtMoney(h.flat_pnl_dollars, true)} />
          <Big label="FLAT ROI" value={h.flat_roi_pct == null ? '--' : `${h.flat_roi_pct > 0 ? '+' : ''}${h.flat_roi_pct.toFixed(2)}%`} />
        </div>
        <div style={{ height: 320 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={rows} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="gk" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#57f287" stopOpacity={0.3} /><stop offset="100%" stopColor="#57f287" stopOpacity={0} /></linearGradient>
              </defs>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis dataKey="date" stroke={AXIS} tick={{ fontSize: 11 }} minTickGap={24} />
              <YAxis stroke={AXIS} tick={{ fontSize: 11 }} tickFormatter={v => `$${Number(v).toLocaleString()}`} width={64} />
              <Tooltip contentStyle={tipStyle} formatter={(v) => fmtMoney(Number(v))} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Area type="monotone" dataKey="EV Kelly" stroke="#57f287" strokeWidth={2} fill="url(#gk)" dot={false} isAnimationActive={false} />
              <Area type="monotone" dataKey="EV flat $100" stroke="#38bdf8" strokeWidth={1.75} fill="none" dot={false} isAnimationActive={false} />
              <Area type="monotone" dataKey="MLB models (as staked)" stroke="#f59e0b" strokeWidth={1.75} fill="none" dot={false} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '10px', lineHeight: 1.6 }}>
          {h.unique_props_graded.toLocaleString()} unique props behind {h.graded.toLocaleString()} graded alerts: the same prop flagged at several books
          counts several times, which inflates the sample. The model line uses each system&rsquo;s own paper stakes, so its scale differs from the $100 flat line.
        </p>
      </Panel>
    </>
  )
}

function Slider({ label, value, min, max, step, onChange, fmt }: { label: string; value: number; min: number; max: number; step: number; onChange: (n: number) => void; fmt: (n: number) => string }) {
  return (
    <label style={{ display: 'grid', gap: '6px', minWidth: '240px' }}>
      <span className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', display: 'flex', justifyContent: 'space-between' }}>
        {label}<span className="mono" style={{ color: 'var(--chalk)' }}>{fmt(value)}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(parseFloat(e.target.value))} style={{ accentColor: '#57f287', padding: 0, background: 'transparent', border: 'none' }} />
    </label>
  )
}

function Big({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="dell-heading" style={{ fontSize: '10px', color: 'var(--fog)', marginBottom: '4px' }}>{label}</div>
      <div className="mono" style={{ fontSize: '22px', fontWeight: 700, color: 'var(--chalk)' }}>{value}</div>
    </div>
  )
}

// -- 2. Edge calibration --------------------------------------------------------

function Calibration({ data, system, setSystem }: { data: AnalyticsDeepDive; system: string; setSystem: (s: string) => void }) {
  const rows = data.edge_buckets
  const chart = rows.map(b => ({ bucket: b.bucket, 'Realized hit rate': b.hit_rate, 'Avg model probability': b.avg_model_prob, 'Avg market probability': b.avg_market_prob }))
  return (
    <>
      <Panel title="ADVERSE SELECTION: REALIZED RESULTS BY CLAIMED EDGE" sub="Graded bets grouped by the edge the system claimed at bet time. If real edge exists, realized ROI should hold or rise with claimed edge. If it falls, the biggest apparent edges are model error (the winner's curse).">
        <SystemChips system={system} setSystem={setSystem} />
        <div style={{ height: 260, marginBottom: '14px' }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
              <XAxis dataKey="bucket" stroke={AXIS} tick={{ fontSize: 11 }} />
              <YAxis stroke={AXIS} tick={{ fontSize: 11 }} tickFormatter={v => `${v}%`} domain={[0, 100]} width={44} />
              <Tooltip contentStyle={tipStyle} formatter={(v) => `${Number(v).toFixed(1)}%`} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="Realized hit rate" fill="#57f287" isAnimationActive={false} />
              <Bar dataKey="Avg model probability" fill="#38bdf8" isAnimationActive={false} />
              <Bar dataKey="Avg market probability" fill="#4b5469" isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '640px', fontSize: '13px' }}>
            <thead><tr>{['Edge bucket', 'Bets', 'Hit rate', 'Model prob', 'Market prob', 'ROI', 'Mean CLV'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
            <tbody>
              {rows.map(b => (
                <tr key={b.bucket} style={{ borderBottom: '1px solid #151822' }}>
                  <Td strong>{b.bucket}</Td><Td mono>{b.n}{b.n < 30 && <span style={{ color: 'var(--warn)', marginLeft: '6px', fontSize: '10px' }}>low n</span>}</Td>
                  <Td mono>{b.hit_rate != null ? `${b.hit_rate.toFixed(1)}%` : '--'}</Td>
                  <Td mono>{b.avg_model_prob != null ? `${b.avg_model_prob.toFixed(1)}%` : '--'}</Td>
                  <Td mono>{b.avg_market_prob != null ? `${b.avg_market_prob.toFixed(1)}%` : '--'}</Td>
                  <Td mono color={b.roi_pct == null ? undefined : b.roi_pct >= 0 ? 'var(--signal)' : 'var(--loss)'}>{b.roi_pct != null ? `${b.roi_pct > 0 ? '+' : ''}${b.roi_pct.toFixed(2)}%` : '--'}</Td>
                  <Td mono>{b.mean_clv != null ? `${b.mean_clv > 0 ? '+' : ''}${b.mean_clv.toFixed(2)}%` : 'n/a'}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '10px' }}>
          Mean CLV is the average of each bet's price against the closing price, and a few extreme closes can move it a lot; the CLV Tracker shows the full spread and the median. Buckets under 30 bets are marked low n and should not be read for ROI.
        </p>
      </Panel>
      <Panel title="HOW MODEL PROBABILITIES ARE CONTROLLED">
        <p className="times" style={{ fontSize: '13.5px', color: 'var(--silver)', lineHeight: 1.65, maxWidth: '90ch' }}>
          Model systems pass every probability through an isotonic calibrator fit on realized outcomes,
          then skip any bet whose post-calibration edge still exceeds an edge cap. Both exist because early diagnostics showed the largest claimed edges were mostly
          overconfidence, not value. Calibration is fit on past bets, so treat the chart above as the ongoing test of whether it works.
        </p>
      </Panel>
    </>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="dell-heading" style={{ textAlign: 'left', padding: '9px 12px', fontSize: '10px', color: 'var(--fog)', borderBottom: `1px solid ${GRID}` }}>{children}</th>
}
function Td({ children, mono, strong, color }: { children: React.ReactNode; mono?: boolean; strong?: boolean; color?: string }) {
  return <td className={mono ? 'mono' : undefined} style={{ padding: '9px 12px', color: color ?? (strong ? 'var(--chalk)' : 'var(--silver)'), fontWeight: strong ? 600 : 400 }}>{children}</td>
}

// -- 3. Book leaderboard --------------------------------------------------------

function Books({ data, system, setSystem }: { data: AnalyticsDeepDive; system: string; setSystem: (s: string) => void }) {
  const rows = data.book_leaderboard
  return (
    <Panel title="SPORTSBOOK AND EXCHANGE LEADERBOARD" sub="Where flagged prices came from and how they graded. Bets = how often the book was caught off consensus. Small samples (n under 100) are noise.">
      <SystemChips system={system} setSystem={setSystem} />
      {rows.length === 0 ? <div style={{ color: 'var(--fog)', padding: '20px 0' }}>No graded bets for this selection.</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '680px', fontSize: '13px' }}>
            <thead><tr>{['Book', 'Bets', 'Hit rate', 'ROI', 'Units', 'Mean CLV', 'Avg edge'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
            <tbody>
              {rows.map(r => {
                const b = bookDisplay(r.book)
                return (
                  <tr key={r.book} style={{ borderBottom: '1px solid #151822', opacity: r.n < 100 ? 0.6 : 1 }} title={r.n < 100 ? 'Under 100 bets: too few to read' : undefined}>
                    <Td><span style={{ fontSize: '11.5px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{r.book === 'unknown' ? 'Model (no book)' : b.label}</span>{b.isExchange && <span style={{ marginLeft: '8px', fontSize: '10px', color: 'var(--fog)' }}>exchange</span>}</Td>
                    <Td mono>{r.n.toLocaleString()}</Td>
                    <Td mono>{r.hit_rate != null ? `${r.hit_rate.toFixed(1)}%` : '--'}</Td>
                    <Td mono color={r.roi_pct == null ? undefined : r.roi_pct >= 0 ? 'var(--signal)' : 'var(--loss)'}>{r.roi_pct != null ? `${r.roi_pct > 0 ? '+' : ''}${r.roi_pct.toFixed(2)}%` : '--'}</Td>
                    <Td mono>{r.profit_units > 0 ? '+' : ''}{r.profit_units.toFixed(1)}u</Td>
                    <Td mono>{r.mean_clv != null ? `${r.mean_clv > 0 ? '+' : ''}${r.mean_clv.toFixed(2)}%` : 'n/a'}</Td>
                    <Td mono>{r.avg_edge != null ? `+${r.avg_edge.toFixed(1)}%` : '--'}</Td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

// -- 4. System health ------------------------------------------------------------

function Health({ stats }: { stats: SystemStats[] }) {
  const logOnly = new Set(SPORT_PILLARS.mlb.markets.filter(m => m.status === 'Log-only').map(m => m.system).filter(Boolean) as string[])
  const rows = stats.filter(s => Number(s.total_bets) > 0).sort((a, b) => Number(b.total_bets) - Number(a.total_bets))
  const labelOf = (k: string) => (k === '1IOU' ? '1st Inn O/U' : systemLabel(k))
  return (
    <Panel title="SYSTEM HEALTH" sub="Bets are every graded prediction a system logged, including unstaked log-only ones. Reaching 200 means the sample is big enough to judge, not that the system is profitable.">
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '560px', fontSize: '13px' }}>
          <thead><tr>{['System', 'Status', 'Bets', 'ROI'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
          <tbody>
            {rows.map(s => {
              const n = Number(s.total_bets)
              const roi = s.roi == null ? null : Number(s.roi)
              const isEv = s.system === 'EV'
              const color = SYSTEM_COLOR[s.system] ?? 'var(--silver)'
              const status = isEv ? 'Tracked, not gated'
                : logOnly.has(s.system) ? 'Log-only'
                : n < GATE ? `Paper, ${n}/${GATE} bets`
                : roi != null && roi < 0 ? '200+ bets, negative ROI'
                : '200+ bets, paper'
              const statusColor = isEv || logOnly.has(s.system) || n < GATE ? 'var(--fog)' : roi != null && roi < 0 ? 'var(--loss)' : 'var(--silver)'
              return (
                <tr key={s.system} style={{ borderBottom: '1px solid #151822' }}>
                  <Td><span style={{ color, fontWeight: 700 }}>{labelOf(s.system)}</span></Td>
                  <Td><span style={{ color: statusColor }}>{status}</span></Td>
                  <Td mono>{n.toLocaleString()}</Td>
                  <Td mono color={roi == null ? undefined : roi >= 0 ? 'var(--signal)' : 'var(--loss)'}>{roi == null ? '--' : `${roi > 0 ? '+' : ''}${roi.toFixed(1)}%`}</Td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {rows.length === 0 && <div style={{ color: 'var(--fog)', padding: '12px 0' }}>System stats unavailable.</div>}
      </div>
    </Panel>
  )
}
