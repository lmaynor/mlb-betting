'use client'

import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { AnalyticsDeepDive, SystemStats } from '@/lib/types'
import { apiGetAnalyticsDeepDive } from '@/lib/betting-api'
import { bookDisplay, SYSTEM_COLOR, systemLabel } from '@/lib/tokens'
import { fmtMoney } from '@/lib/ev-math'
import { SPORT_PILLARS } from '@/lib/sports'
import { cx } from '@/utils/cx'

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

const tipStyle = { background: 'var(--color-background-tertiary-default)', border: `1px solid ${GRID}`, borderRadius: 10, fontSize: 12, color: 'var(--color-text-primary)' }
const CARD = 'rounded-xl border border-separator-border bg-background-primary-default shadow-card'

export function AnalyticsClient({ initial, stats }: { initial: AnalyticsDeepDive | null; stats: SystemStats[] }) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
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
    <div className="mx-auto max-w-[1280px] px-6 pb-16 pt-7">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-x-5 gap-y-3">
        <h1 className="text-title-2-bold text-text-primary">{TABS.find(t => t.id === tab)!.label}</h1>
        {loading && <span className="mono text-caption-2-medium text-text-secondary">updating...</span>}
      </div>

      {!data ? (
        <div className={cx(CARD, 'px-5 py-9 text-center text-body-regular text-text-secondary')}>Analytics are unavailable right now.</div>
      ) : tab === 'bankroll' ? (
        <Bankroll data={data} bankroll={bankroll} setBankroll={setBankroll} fraction={fraction} setFraction={setFraction} mounted={mounted} />
      ) : tab === 'calibration' ? (
        <Calibration data={data} system={system} setSystem={setSystem} mounted={mounted} />
      ) : tab === 'books' ? (
        <Books data={data} system={system} setSystem={setSystem} />
      ) : (
        <Health stats={stats} />
      )}
      <p className="mx-0.5 mt-2 text-caption-1-regular text-text-secondary">
        Computed from graded bets in the public ledger. All results are paper: flagged price at flag time, voids excluded from ROI, no slippage or limits.
      </p>
    </div>
  )
}

function Panel({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className={cx(CARD, 'mb-4 px-5 py-4.5')}>
      <h2 className="ui-heading text-text-secondary">{title}</h2>
      {sub && <p className="times mb-3.5 mt-1.5 max-w-[90ch] text-body-2-regular text-text-secondary">{sub}</p>}
      {children}
    </section>
  )
}

function SystemChips({ system, setSystem }: { system: string; setSystem: (s: string) => void }) {
  return (
    <div className="mb-3.5 flex flex-wrap gap-1.5" aria-label="System filter">
      {SYSTEM_CHIPS.map(k => {
        const on = system === k
        const c = SYSTEM_COLOR[k]
        return (
          <button key={k} onClick={() => setSystem(k)} aria-pressed={on}
            className={cx('button-press-motion cursor-pointer rounded-lg border px-2.5 py-1.5 text-caption-1-semibold',
              !on && 'border-separator-border bg-background-secondary-default text-text-secondary hover:bg-background-primary-hover')}
            style={on ? { color: 'var(--color-background-secondary-default)', background: c ?? 'var(--signal)', borderColor: c ?? 'var(--signal)' } : c ? { color: c } : undefined}>
            {k === 'ALL' ? 'ALL SYSTEMS' : systemLabel(k).toUpperCase()}
          </button>
        )
      })}
    </div>
  )
}

// -- 1. Bankroll simulator ------------------------------------------------------

function Bankroll({ data, bankroll, setBankroll, fraction, setFraction, mounted }: {
  data: AnalyticsDeepDive; bankroll: number; setBankroll: (n: number) => void; fraction: number; setFraction: (n: number) => void; mounted: boolean
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
        <div className="mb-4 flex flex-wrap gap-7">
          <Slider label="STARTING BANKROLL" value={bankroll} min={500} max={25000} step={500} onChange={setBankroll} fmt={v => fmtMoney(v)} />
          <Slider label="KELLY FRACTION" value={fraction} min={0.1} max={0.5} step={0.05} onChange={setFraction} fmt={v => `${v.toFixed(2)}x`} />
        </div>
        <div className="mb-2 flex flex-wrap gap-6">
          <Big label="KELLY ENDING BANKROLL" value={fmtMoney(h.kelly_ending_bankroll)} />
          <Big label="KELLY ROI (ON STAKED)" value={h.kelly_roi_pct == null ? '--' : `${h.kelly_roi_pct > 0 ? '+' : ''}${h.kelly_roi_pct.toFixed(2)}%`} />
          <Big label="FLAT $100 P&L" value={fmtMoney(h.flat_pnl_dollars, true)} />
          <Big label="FLAT ROI" value={h.flat_roi_pct == null ? '--' : `${h.flat_roi_pct > 0 ? '+' : ''}${h.flat_roi_pct.toFixed(2)}%`} />
        </div>
        <div className="h-80">
          {mounted && (
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={320}>
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
          )}
        </div>
        <p className="times mt-2.5 text-caption-1-regular text-text-secondary">
          {h.unique_props_graded.toLocaleString()} unique props behind {h.graded.toLocaleString()} graded alerts: the same prop flagged at several books
          counts several times, which inflates the sample. The model line uses each system&rsquo;s own paper stakes, so its scale differs from the $100 flat line.
        </p>
      </Panel>
    </>
  )
}

function Slider({ label, value, min, max, step, onChange, fmt }: { label: string; value: number; min: number; max: number; step: number; onChange: (n: number) => void; fmt: (n: number) => string }) {
  return (
    <label className="grid min-w-60 gap-1.5">
      <span className="ui-heading flex justify-between text-text-secondary">
        {label}<span className="mono text-text-primary">{fmt(value)}</span>
      </span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(parseFloat(e.target.value))} style={{ accentColor: '#57f287', padding: 0, background: 'transparent', border: 'none' }} />
    </label>
  )
}

function Big({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="ui-heading mb-1 text-text-secondary">{label}</div>
      <div className="mono text-title-1-bold text-text-primary">{value}</div>
    </div>
  )
}

// -- 2. Edge calibration --------------------------------------------------------

function Calibration({ data, system, setSystem, mounted }: { data: AnalyticsDeepDive; system: string; setSystem: (s: string) => void; mounted: boolean }) {
  const rows = data.edge_buckets
  const chart = rows.map(b => ({ bucket: b.bucket, 'Realized hit rate': b.hit_rate, 'Avg model probability': b.avg_model_prob, 'Avg market probability': b.avg_market_prob }))
  return (
    <>
      <Panel title="ADVERSE SELECTION: REALIZED RESULTS BY CLAIMED EDGE" sub="Graded bets grouped by the edge the system claimed at bet time. If real edge exists, realized ROI should hold or rise with claimed edge. If it falls, the biggest apparent edges are model error (the winner's curse).">
        <SystemChips system={system} setSystem={setSystem} />
        <div className="mb-3.5 h-[260px]">
          {mounted && (
            <ResponsiveContainer width="100%" height="100%" minWidth={0} minHeight={260}>
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
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-body-2-regular">
            <thead><tr>{['Edge bucket', 'Bets', 'Hit rate', 'Model prob', 'Market prob', 'ROI', 'Mean CLV'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
            <tbody>
              {rows.map(b => (
                <tr key={b.bucket} className="border-b border-border-table transition-colors hover:bg-background-primary-hover">
                  <Td strong>{b.bucket}</Td><Td mono>{b.n}{b.n < 30 && <span className="text-[var(--warn)] ml-1.5 text-[10px]">low n</span>}</Td>
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
        <p className="times mt-2.5 text-caption-1-regular text-text-secondary">
          Mean CLV is the average of each bet's price against the closing price, and a few extreme closes can move it a lot; the CLV Tracker shows the full spread and the median. Buckets under 30 bets are marked low n and should not be read for ROI.
        </p>
      </Panel>
      <Panel title="HOW MODEL PROBABILITIES ARE CONTROLLED">
        <p className="times max-w-[90ch] text-body-regular text-text-secondary">
          Model systems pass every probability through an isotonic calibrator fit on realized outcomes,
          then skip any bet whose post-calibration edge still exceeds an edge cap. Both exist because early diagnostics showed the largest claimed edges were mostly
          overconfidence, not value. Calibration is fit on past bets, so treat the chart above as the ongoing test of whether it works.
        </p>
      </Panel>
    </>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return <th className="ui-heading border-b border-separator-border bg-background-secondary-default px-3 py-2.5 text-left text-text-secondary">{children}</th>
}
function Td({ children, mono, strong, color }: { children: React.ReactNode; mono?: boolean; strong?: boolean; color?: string }) {
  return <td className={cx('px-3 py-2.5', mono && 'mono', strong ? 'font-semibold text-text-primary' : 'text-text-secondary')} style={color ? { color } : undefined}>{children}</td>
}

// -- 3. Book leaderboard --------------------------------------------------------

function Books({ data, system, setSystem }: { data: AnalyticsDeepDive; system: string; setSystem: (s: string) => void }) {
  const rows = data.book_leaderboard
  return (
    <Panel title="SPORTSBOOK AND EXCHANGE LEADERBOARD" sub="Where flagged prices came from and how they graded. Bets = how often the book was caught off consensus. Small samples (n under 100) are noise.">
      <SystemChips system={system} setSystem={setSystem} />
      {rows.length === 0 ? <div className="py-5 text-text-secondary">No graded bets for this selection.</div> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] border-collapse text-body-2-regular">
            <thead><tr>{['Book', 'Bets', 'Hit rate', 'ROI', 'Units', 'Mean CLV', 'Avg edge'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
            <tbody>
              {rows.map(r => {
                const b = bookDisplay(r.book)
                return (
                  <tr key={r.book} className={cx('border-b border-border-table transition-colors hover:bg-background-primary-hover', r.n < 100 && 'opacity-60')} title={r.n < 100 ? 'Under 100 bets: too few to read' : undefined}>
                    <Td><span style={{ fontSize: '11.5px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '3px 8px' }}>{r.book === 'unknown' ? 'Model (no book)' : b.label}</span>{b.isExchange && <span className="ml-2 text-[10px] text-text-secondary">exchange</span>}</Td>
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
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] border-collapse text-body-2-regular">
          <thead><tr>{['System', 'Status', 'Bets', 'ROI'].map(h => <Th key={h}>{h}</Th>)}</tr></thead>
          <tbody>
            {rows.map(s => {
              const n = Number(s.total_bets)
              const roi = s.roi == null ? null : Number(s.roi)
              const isEv = s.system === 'EV'
              const color = SYSTEM_COLOR[s.system] ?? 'var(--color-neutral-300)'
              const status = isEv ? 'Tracked, not gated'
                : logOnly.has(s.system) ? 'Log-only'
                : n < GATE ? `Paper, ${n}/${GATE} bets`
                : roi != null && roi < 0 ? '200+ bets, negative ROI'
                : '200+ bets, paper'
              const statusColor = isEv || logOnly.has(s.system) || n < GATE ? 'var(--color-text-secondary)' : roi != null && roi < 0 ? 'var(--loss)' : 'var(--color-neutral-300)'
              return (
                <tr key={s.system} className="border-b border-border-table transition-colors hover:bg-background-primary-hover">
                  <Td><span style={{ color, fontWeight: 700 }}>{labelOf(s.system)}</span></Td>
                  <Td><span style={{ color: statusColor }}>{status}</span></Td>
                  <Td mono>{n.toLocaleString()}</Td>
                  <Td mono color={roi == null ? undefined : roi >= 0 ? 'var(--signal)' : 'var(--loss)'}>{roi == null ? '--' : `${roi > 0 ? '+' : ''}${roi.toFixed(1)}%`}</Td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {rows.length === 0 && <div className="py-3 text-text-secondary">System stats unavailable.</div>}
      </div>
    </Panel>
  )
}
