'use client'

/* eslint-disable @typescript-eslint/no-explicit-any */

import { useState, useMemo } from 'react'
import { useMounted } from '@/lib/use-mounted'
import { useSearchParams } from 'next/navigation'
import Link from 'next/link'
import { SystemBadge, ResultPill, PnL } from '@/components/ui/primitives'
import { formatOdds } from '@/lib/odds'
import { cx } from '@/utils/cx'
import { SYSTEM_COLOR, SYSTEM_LABEL as _SYS_LABEL, pickLabel, bookText } from '@/lib/tokens'
import { Matchup } from '@/components/ui/matchup'
import { formatDateKey, siteDateKey } from '@/lib/dates'
import type { Bet, SystemStats, BankrollPoint } from '@/lib/types'
import { collapseBooks } from '@/lib/dedupe'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, ComposedChart, Area,
} from 'recharts'

const CARD = 'rounded-xl border border-separator-border bg-background-primary-default shadow-card'
const LBL = 'mono text-micro text-text-secondary'
const PILLBTN = 'button-press-motion cursor-pointer rounded-full border px-3 py-1.5 text-mini '
const COL = '80px 65px 180px 1fr 80px 70px 60px 80px 70px 70px'
const PILL = SYSTEM_COLOR

// All systems grouped by type for filter chips
const SYSTEM_GROUPS = {
  'Game Lines':      ['NRFI', 'F5'],
  'Innings Windows': ['F3', 'F1H', 'F7', 'GAME'],
  'Pitcher Props':   ['K', 'OUTS', 'PITCHER_ER'],
  'Batter Props':    ['HR', 'BATTER_K', 'BATTER_TB', 'BATTER_HITS', 'SB'],
  // Pooled +EV alert tracking (fast_alert_loop + kalshi_alert, system="EV")
  // -- not a model, so it doesn't fit any market group above; own group so
  // it's still filterable/chartable instead of only reachable via "ALL".
  '+EV Alerts':      ['EV'],
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
const SYSTEM_LABEL: Record<string, string> = { ALL: 'ALL', ..._SYS_LABEL }

function Chip({ label, active, color, onClick }: {
  label: string; active: boolean; color?: string; onClick: () => void
}) {
  const activeColor = color ?? 'var(--color-neutral-300)'
  return (
    <button onClick={onClick}
      className={cx('mono', PILLBTN, active ? 'font-semibold' : 'border-separator-border bg-background-primary-default font-medium text-text-secondary hover:bg-background-primary-hover')}
      style={active ? { borderColor: activeColor, color: activeColor, background: `color-mix(in oklab, ${activeColor} 16%, var(--color-background-full))` } : undefined}>
      {label}
    </button>
  )
}

function GroupLabel({ label }: { label: string }) {
  return <span className={cx(LBL, 'whitespace-nowrap px-1')}>{label}</span>
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
    const profit = parseFloat(String(bet.profit))
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

const fmtSigned = (v: unknown) => { const n = parseFloat(String(v ?? '')); return isFinite(n) ? `${n >= 0 ? '+' : '-'}${Math.abs(n).toFixed(1)}%` : '--' }
const fmtDate = (d: string) => { const p = d.split('-'); return `${p[1]}/${p[2]}` }
const fmtDisplayDate = (d: string) => formatDateKey(d, { month: 'short', day: 'numeric' })

const PnLTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div className="min-w-[120px] rounded-lg border border-separator-border bg-background-primary-default px-3.5 py-2.5 font-mono text-mini shadow-dropdown">
      <div className="mb-1.5 text-text-secondary">{label}</div>
      {payload
        .filter((p: any) => p.value != null && !['dd_top', 'dd_bot'].includes(p.dataKey))
        .map((p: any) => (
          <div className="mb-[2px]" key={p.dataKey} style={{ color: PILL[p.dataKey] ?? 'var(--color-text-primary)' }}>
            {p.dataKey}: {p.value >= 0 ? '+' : '-'}${Math.abs(p.value).toFixed(0)}
          </div>
        ))}
    </div>
  )
}

const EdgeTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-separator-border bg-background-primary-default px-3.5 py-2.5 font-mono text-mini shadow-dropdown">
      <div className="mb-1.5 text-text-secondary">{label}</div>
      {payload.map((p: any) => (
        <div className="mb-[2px]" key={p.dataKey} style={{ color: p.color }}>
          {p.dataKey === 'edge' ? 'Avg edge' : 'Realized ROI'}: {p.value >= 0 ? '+' : ''}{p.value}%
        </div>
      ))}
    </div>
  )
}

function exportCSV(bets: Bet[]) {
  const headers = ['Date', 'System', 'Game', 'Pick', 'Odds', 'Edge', 'Stake', 'Book', 'Result', 'P&L (units of stake)']
  const rows = bets.map(b => {
    const game = b.home_team ? `${b.away_team} @ ${b.home_team}` : `Game ${b.game_pk}`
    const pnl  = b.profit != null ? (parseFloat(String(b.profit)) / (b.stake != null && b.stake > 0 ? b.stake : 10)).toFixed(2) : ''
    return [
      b.game_date, b.system, game, pickLabel(b),
      b.odds ?? '', b.edge != null ? `${(b.edge * 100).toFixed(1)}%` : '',
      b.stake != null && b.stake > 0 ? b.stake.toFixed(0) : '',
      bookText((b as any).book), b.result ?? 'pending', pnl,
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

function ResultBetCard({ bet, daily }: { bet: Bet; daily?: boolean }) {
  return (
    <div className={cx('card-hover flex flex-col gap-[11px] px-3.5 py-[13px]', CARD)}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <SystemBadge system={bet.system} />
          <span className="mono whitespace-nowrap text-mini text-text-secondary">{fmtDisplayDate(bet.game_date)}</span>
          {daily && <span className="ui-heading text-micro text-link">DAILY CARD</span>}
        </div>
        <ResultPill result={bet.result} />
      </div>

      <div>
        <div className="mb-[5px]"><Matchup away={bet.away_team} home={bet.home_team} size={18} fontSize="12px" /></div>
        <div className="text-headline-bold text-text-primary">{pickLabel(bet)}</div>
      </div>

      <div className="grid grid-cols-4 items-end gap-2.5">
        {[
          { label: 'Odds', value: formatOdds(bet.odds), color: 'var(--color-text-primary)' },
          { label: 'Edge', value: bet.edge != null ? `${bet.edge >= 0 ? '+' : ''}${(bet.edge * 100).toFixed(1)}%` : '--', color: 'var(--signal)' },
          { label: 'Book', value: bet.book ? bookText(bet.book) : '--', color: 'var(--color-neutral-300)' },
        ].map(item => (
          <div key={item.label} className="min-w-0">
            <div className={cx(LBL, 'mb-[3px]')}>{item.label}</div>
            <div className="mono truncate text-xs font-semibold" style={{ color: item.color }}>{item.value}</div>
          </div>
        ))}
        <div>
          <div className={cx(LBL, 'mb-[3px]')}>P&L</div>
          <PnL value={bet.profit} stake={bet.stake} result={bet.result} />
        </div>
      </div>
    </div>
  )
}

export function ResultsClient({
  initialPicks,
  initialStats,
  curve = [],
  dailyPicks = [],
  asOf,
}: {
  initialPicks: Bet[]
  initialStats: SystemStats[]
  curve?: BankrollPoint[]
  dailyPicks?: Bet[]
  /** Central-time date the totals were read, rendered next to them so every figure carries its own date. */
  asOf?: string
}) {
  const sp = useSearchParams()
  const dateFilter = sp.get('date') ?? null
  const [population, setPopulation] = useState<'all' | 'daily'>(() => (sp.get('population') === 'daily' ? 'daily' : 'all'))

  const [system,  setSystem]  = useState('ALL')
  const [result,  setResult]  = useState('ALL')
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [sortBy,  setSortBy]  = useState<'date' | 'edge' | 'odds' | 'pnl'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [page,    setPage]    = useState(0)
  const [onePerProp, setOnePerProp] = useState(true)
  const mounted = useMounted()
  const dailyIds = useMemo(() => new Set(dailyPicks.map(b => b.id)), [dailyPicks])
  const allCount = useMemo(() => collapseBooks(initialPicks).length, [initialPicks])

  const PAGE_SIZE = 30

  // The ledger can list one prop several times (once per book that flagged it). Default to one row per prop;
  // the P&L chart and the per-system table above keep every row, since each book's price was a separate paper bet.
  // Under the Daily Picks population the source is the card plays instead of the whole ledger.
  const source = population === 'daily' ? dailyPicks : initialPicks
  const ledger = useMemo(() => (onePerProp ? collapseBooks(source) : source), [source, onePerProp])

  const countsBySystem = useMemo(() =>
    Object.fromEntries(
      ALL_SYSTEMS.map(s => [s, s === 'ALL' ? ledger.length
        : ledger.filter(p => p.system === s).length])
    ), [ledger])

  const filtered = useMemo(() => {
    const f = ledger.filter(p => {
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
  }, [ledger, system, result, sortBy, sortDir, dateFilter])

  const { rows: pnlRows, systems: chartSystems } = useMemo(
    () => buildPnLChart(initialPicks, system), [initialPicks, system]
  )
  const edgeRows = useMemo(() => buildEdgeChart(filtered), [filtered])
  const pageCount = Math.ceil(filtered.length / PAGE_SIZE)
  const pageIndex = Math.min(page, Math.max(0, pageCount - 1))
  const pageStart = pageIndex * PAGE_SIZE

  // Model systems and the +EV alert pool are different things staked differently (Kelly paper stakes vs a flat $100),
  // so they are summarised separately rather than blended into one headline.
  const sum = (rows: SystemStats[]) => {
    const t = rows.reduce(
      (acc, s) => ({
        bets: acc.bets + parseInt(String(s.total_bets)),
        wins: acc.wins + parseInt(String(s.wins ?? 0)),
        losses: acc.losses + parseInt(String(s.losses ?? 0)),
        pnl:  acc.pnl  + parseFloat(String(s.total_pnl ?? 0)),
      }),
      { bets: 0, wins: 0, losses: 0, pnl: 0 }
    )
    const decided = t.wins + t.losses
    return { ...t, winRate: decided > 0 ? (t.wins / decided * 100).toFixed(1) : '--' }
  }
  const models = sum(initialStats.filter(s => s.system !== 'EV'))
  const ev     = sum(initialStats.filter(s => s.system === 'EV'))
  const money  = (n: number) => `${n >= 0 ? '+' : '-'}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`

  const pagerBtn = 'mono button-press-motion cursor-pointer rounded-lg border border-border-button-default bg-transparent px-3 py-1.5 text-mini text-text-secondary hover:bg-background-primary-hover disabled:cursor-not-allowed disabled:opacity-50'
  const renderPager = (className: string) => (
    <div className={cx('flex items-center justify-between', className)}>
      <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={pageIndex === 0} className={pagerBtn}>Prev</button>
      <span className="mono text-mini text-text-secondary">
        {pageStart + 1}-{Math.min(pageStart + PAGE_SIZE, filtered.length)} of {filtered.length}
      </span>
      <button onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))} disabled={pageIndex >= pageCount - 1} className={pagerBtn}>Next</button>
    </div>
  )

  return (
    <div className="mx-auto max-w-page px-6 py-10">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-title-2-bold text-text-primary">Results</h1>
        <span className="mono text-caption-1-regular text-text-secondary">{filtered.length} bets shown &middot; {asOf ? `as of ${asOf} \u00b7 ` : ''}Central Time &middot; paper</span>
      </div>

      <p className="mb-4 max-w-[90ch] text-body-2-regular text-text-secondary">
        {population === 'daily'
          ? 'Only the plays that made each day\'s Daily Card, at the prices on the card. The same plays by day are in Graded History.'
          : 'Every settled paper bet. Filter to Daily Picks to see only the plays that made each day\'s Daily Card.'}
      </p>

      {/* One inline metric strip: models and +EV alerts are summarised separately. */}
      {population === 'all' && (
        <div className={cx('mono mb-5 flex min-h-[38px] flex-wrap items-center gap-x-5.5 gap-y-1.5 px-3.5 py-2 text-xs text-text-secondary', CARD)}>
          <span><span className="text-text-tertiary">Models</span> {models.bets.toLocaleString()} bets &middot; {models.winRate}% win &middot; <span style={{ color: models.pnl >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{money(models.pnl)}</span> at paper stakes</span>
          <span><span className="text-text-tertiary">+EV alerts</span> {ev.bets.toLocaleString()} graded &middot; {ev.winRate}% win &middot; <span style={{ color: ev.pnl >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{money(ev.pnl)}</span> flat $100</span>
        </div>
      )}

      {/* Season curve: cumulative P&L of the models (paper stakes) vs the +EV alerts (flat $100), from the first graded bet. */}
      {population === 'all' && system === 'ALL' && curve.length > 2 && (
        <div className={cx('mb-5 pb-2 pl-2 pr-4 pt-3.5', CARD)}>
          <div className="flex flex-wrap items-center justify-between gap-3 px-2 pb-2.5">
            <span className={LBL}>Cumulative P&L, full season ($)</span>
            <span className="flex gap-3.5">
              <span className="mono text-micro text-signal">-- +EV alerts, flat $100</span>
              <span className="mono text-micro text-text-primary">-- models, paper stakes</span>
            </span>
          </div>
          {mounted && (
            <ResponsiveContainer width="100%" height={220} minWidth={0} minHeight={220}>
              <LineChart data={curve} margin={{ top: 4, right: 24, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false} tickFormatter={fmtDate} interval="equidistantPreserveStart" minTickGap={56} />
                <YAxis tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }} tickLine={false} axisLine={false} tickFormatter={v => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toLocaleString()}`} width={68} />
                <Tooltip contentStyle={{ background: 'var(--color-background-tertiary-default)', border: '1px solid var(--color-separator-border)', borderRadius: 8, fontSize: 12 }} labelStyle={{ color: 'var(--color-text-secondary)' }} formatter={(v) => money(Number(v))} />
                <ReferenceLine y={0} stroke="#2e3446" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="ev_flat_cum" name="+EV alerts (flat $100)" stroke="#57f287" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="models_cum" name="Models (paper stakes)" stroke="#f3f4f6" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {/* P&L Chart: per system, over the recent rows loaded here */}
      {population === 'all' && system !== 'ALL' && pnlRows.length > 2 && (
        <div className={cx('mb-6 pb-3 pl-2 pr-4 pt-5', CARD)}>
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 pb-4">
            <span className={LBL}>
              Cumulative P&L ($ at paper stakes)
            </span>
            <div className="flex flex-wrap items-center gap-2.5">
              {[...chartSystems, 'ALL'].map(s => (
                <span key={s} className="flex items-center gap-[5px]">
                  <span className="inline-block size-2 shrink-0 rounded-full" style={{ background: PILL[s] ?? 'var(--color-text-primary)' }} />
                  <span className="mono text-micro text-text-secondary">{s}</span>
                </span>
              ))}
              <span className="flex items-center gap-[5px]">
                <span className="inline-block h-1.5 w-2" style={{ background: 'color-mix(in oklab, var(--loss) 30%, transparent)' }} />
                <span className="mono text-micro text-text-secondary">drawdown</span>
              </span>
            </div>
          </div>
          {mounted && (
            <ResponsiveContainer width="100%" height={200} minWidth={0} minHeight={200}>
              <ComposedChart data={pnlRows} margin={{ top: 4, right: 24, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }}
                  tickLine={false} axisLine={false} tickFormatter={fmtDate}
                  interval="equidistantPreserveStart" minTickGap={56} />
                <YAxis tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }}
                  tickLine={false} axisLine={false}
                  tickFormatter={v => `${v >= 0 ? '+' : '-'}$${Math.abs(v).toFixed(0)}`} width={60} />
                <Tooltip content={<PnLTooltip />} />
                <ReferenceLine y={0} stroke="#2e3446" strokeDasharray="3 3" />
                <Area dataKey="dd_top" fill="transparent" stroke="none" legendType="none" />
                <Area dataKey="dd_bot" fill="#ec6a6a26" stroke="none" legendType="none" />
                {chartSystems.map(s => (
                  <Line key={s} type="monotone" dataKey={s} stroke={PILL[s] ?? '#9ca3af'}
                    strokeWidth={1} dot={false} connectNulls strokeOpacity={0.6} />
                ))}
                <Line type="monotone" dataKey="ALL" stroke="#f3f4f6" strokeWidth={2.5} dot={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {/* Edge vs ROI chart */}
      {edgeRows.length > 2 && (
        <div className={cx('mb-6 pb-3 pl-2 pr-4 pt-5', CARD)}>
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 pb-4">
            <div>
              <span className={LBL}>
                Model Edge vs Realized ROI
              </span>
              <span className="mono ml-2 text-micro text-text-secondary">7-day rolling</span>
            </div>
            <div className="flex gap-4">
              <span className="mono text-micro text-signal">-- model edge</span>
              <span className="mono text-micro text-link">-- realized ROI</span>
            </div>
          </div>
          {mounted && (
            <ResponsiveContainer width="100%" height={140} minWidth={0} minHeight={140}>
              <LineChart data={edgeRows} margin={{ top: 4, right: 24, left: 0, bottom: 0 }}>
                <XAxis dataKey="date" tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }}
                  tickLine={false} axisLine={false} tickFormatter={fmtDate}
                  interval="equidistantPreserveStart" minTickGap={56} />
                <YAxis tick={{ fill: '#7d8596', fontSize: 10, fontFamily: 'monospace' }}
                  tickLine={false} axisLine={false}
                  tickFormatter={v => `${v >= 0 ? '+' : ''}${v.toFixed(0)}%`} width={44} />
                <Tooltip content={<EdgeTooltip />} />
                <ReferenceLine y={0} stroke="#2e3446" strokeDasharray="3 3" />
                <Line type="monotone" dataKey="edge" stroke="#57f287" strokeWidth={1.5} dot={false} />
                <Line type="monotone" dataKey="roi"  stroke="#38bdf8" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {/* Per-system stats table -- groups by type with subtle dividers */}
      {population === 'all' && initialStats.length > 0 && (
        <>
          <div className={cx('results-model-desktop mb-6 overflow-x-auto', CARD)}>
            <div className="grid min-w-[500px] grid-cols-6 border-b border-separator-border bg-background-secondary-default">
              {['System', 'Bets', 'Win Rate', 'ROI', 'P&L', 'Avg Edge'].map(h => (
                <div key={h} className={cx(LBL, 'px-3 py-2.5')}>{h}</div>
              ))}
            </div>
            {Object.entries(SYSTEM_GROUPS).flatMap(([groupName, systems]) => {
              const groupStats = systems
                .map(s => initialStats.find(stat => stat.system === s))
                .filter(Boolean) as SystemStats[]
              if (groupStats.length === 0) return []

              return [
                <div key={`hdr-${groupName}`} className="grid min-w-[500px] grid-cols-6 border-b border-separator-border bg-background-full">
                  <div className={cx(LBL, 'col-span-full px-3 py-1.5')}>{groupName}</div>
                </div>,
                ...groupStats.map(s => {
                  const r = s.roi == null ? null : parseFloat(String(s.roi))
                  const pnl = parseFloat(String(s.total_pnl ?? 0))
                  const pc = PILL[s.system] ?? 'var(--color-neutral-300)'
                  return (
                    <div key={s.system} className="grid min-w-[500px] grid-cols-6 items-center border-b border-border-table transition-colors hover:bg-background-primary-hover">
                      <div className="mono px-3 py-2.5 text-mini font-semibold" style={{ color: pc }}>{s.system}</div>
                      <div className="mono px-3 py-2.5 text-mini text-text-primary">
                        {s.total_bets}{parseInt(String(s.total_bets)) < GATE && <span className="text-text-tertiary"> / {GATE}</span>}
                      </div>
                      <div className="mono px-3 py-2.5 text-mini text-text-primary">{parseFloat(String(s.win_rate ?? 0)).toFixed(1)}%</div>
                      <div className="mono px-3 py-2.5 text-mini font-semibold" style={{ color: r == null ? 'var(--color-text-secondary)' : r >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{r == null ? '--' : `${r >= 0 ? '+' : ''}${r.toFixed(1)}%`}</div>
                      <div className="mono px-3 py-2.5 text-mini font-semibold" style={{ color: pnl >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{money(pnl)}</div>
                      <div className="mono px-3 py-2.5 text-mini text-text-primary">{fmtSigned(s.avg_edge)}</div>
                    </div>
                  )
                }),
              ]
            })}
          </div>

          <div className="results-model-mobile mb-6">
            {Object.entries(SYSTEM_GROUPS).map(([groupName, systems]) => {
              const groupStats = systems
                .map(s => initialStats.find(stat => stat.system === s))
                .filter(Boolean) as SystemStats[]
              if (groupStats.length === 0) return null

              return (
                <div key={groupName} className="mb-3.5">
                  <div className={cx(LBL, 'mb-2')}>{groupName}</div>
                  <div className="flex flex-col gap-2">
                    {groupStats.map(s => {
                      const r = s.roi == null ? null : parseFloat(String(s.roi))
                      const pnl = parseFloat(String(s.total_pnl ?? 0))
                      const pc = PILL[s.system] ?? 'var(--color-neutral-300)'
                      return (
                        <div key={s.system} className={cx('px-[13px] py-3', CARD)}>
                          <div className="mb-2.5 flex items-center justify-between gap-3">
                            <span className="mono text-xs font-semibold" style={{ color: pc }}>{s.system}</span>
                            <span className="mono text-mini font-semibold" style={{ color: r == null ? 'var(--color-text-secondary)' : r >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{r == null ? '-- ROI' : `${r >= 0 ? '+' : ''}${r.toFixed(1)}% ROI`}</span>
                          </div>
                          <div className="grid grid-cols-4 gap-2.5">
                            {[
                              ['Bets', parseInt(String(s.total_bets)) < GATE ? `${s.total_bets} / ${GATE}` : String(s.total_bets), 'var(--color-text-primary)'],
                              ['Win', `${parseFloat(String(s.win_rate ?? 0)).toFixed(1)}%`, 'var(--color-text-primary)'],
                              ['P&L', money(pnl), pnl >= 0 ? 'var(--signal)' : 'var(--loss)'],
                              ['Edge', fmtSigned(s.avg_edge), 'var(--color-text-primary)'],
                            ].map(([label, value, color]) => (
                              <div key={label}>
                                <div className={cx(LBL, 'mb-[3px]')}>{label}</div>
                                <div className="mono text-mini font-semibold" style={{ color }}>{value}</div>
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

      {/* System filter chips -- grouped with labels. On a phone they sit behind one Filters row so the ledger starts on the first screen. */}
      <button type="button" aria-expanded={filtersOpen} aria-controls="results-filters" onClick={() => setFiltersOpen(o => !o)}
        className="mono button-press-motion mb-3 flex min-h-[44px] w-full cursor-pointer items-center justify-between rounded-lg border border-separator-border bg-background-primary-default px-3 text-compact text-text-primary md:hidden" data-testid="results-filters-toggle">
        <span>Filters{population !== 'all' || system !== 'ALL' || result !== 'ALL' ? ` \u00b7 ${[population === 'daily' ? 'Daily Picks' : null, system !== 'ALL' ? (SYSTEM_LABEL[system] ?? system) : null, result !== 'ALL' ? result : null].filter(Boolean).join(', ')}` : ''}</span>
        <span aria-hidden>{filtersOpen ? '\u2212' : '+'}</span>
      </button>
      <div id="results-filters" className={cx('mb-4 md:block', filtersOpen ? 'block' : 'hidden')}>
        <div className="mb-2.5 flex flex-wrap items-center gap-1.5">
          <span className={cx(LBL, 'mr-1')}>Population</span>
          <Chip label={`All bets (${allCount})`} active={population === 'all'} onClick={() => { setPopulation('all'); setPage(0) }} />
          <Chip label={`Daily Picks (${dailyPicks.length})`} active={population === 'daily'} onClick={() => { setPopulation('daily'); setPage(0) }} />
        </div>
        <div className="mb-2.5 flex flex-wrap items-center gap-5">

          {/* ALL chip */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cx(LBL, 'mr-1')}>System</span>
            <Chip label={`ALL (${countsBySystem['ALL'] ?? 0})`} active={system === 'ALL'} onClick={() => { setSystem('ALL'); setPage(0) }} />
          </div>

          {/* Grouped system chips */}
          {Object.entries(SYSTEM_GROUPS).map(([groupName, systems]) => (
            <div key={groupName} className="flex flex-wrap items-center gap-1.5">
              <GroupLabel label={groupName} />
              {systems.map(s => (
                <Chip key={s} label={`${SYSTEM_LABEL[s] ?? s}${countsBySystem[s] ? ` (${countsBySystem[s]})` : ''}`} active={system === s}
                  color={PILL[s]} onClick={() => { setSystem(s); setPage(0) }} />
              ))}
            </div>
          ))}
        </div>

        {/* Result + Sort row */}
        <div className="flex flex-wrap items-center gap-5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cx(LBL, 'mr-1')}>Result</span>
            {RESULTS.map(r => (
              <Chip key={r} label={r} active={result === r}
                color={r === 'WIN' ? 'var(--signal)' : r === 'LOSS' ? 'var(--loss)' : r === 'VOID' ? 'var(--color-text-secondary)' : undefined}
                onClick={() => { setResult(r); setPage(0) }} />
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className={cx(LBL, 'mr-1')}>Sort</span>
            {(['date', 'edge', 'odds', 'pnl'] as const).map(s => (
              <button key={s}
                onClick={() => {
                  if (sortBy === s) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
                  else { setSortBy(s); setSortDir('desc') }
                }}
                className={cx('mono', PILLBTN, sortBy === s
                  ? 'border-border-button-hover bg-background-tertiary-default text-text-primary'
                  : 'border-separator-border bg-background-primary-default text-text-secondary hover:bg-background-primary-hover')}>
                {s}{sortBy === s ? (sortDir === 'desc' ? ' v' : ' ^') : ''}
              </button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-3">
            <label className="mono flex cursor-pointer items-center gap-1.5 text-micro text-text-secondary" title="The same prop flagged at several books shows as one row">
              <input type="checkbox" checked={onePerProp} onChange={e => { setOnePerProp(e.target.checked); setPage(0) }} /> One row per prop
            </label>
            <span className="mono text-micro text-text-secondary">{filtered.length} bets</span>
            {filtered.length > 0 && (
              <button onClick={() => exportCSV(filtered)} className="mono button-press-motion cursor-pointer rounded-full border border-separator-border bg-background-primary-default px-2.5 py-1 text-micro text-text-secondary hover:bg-background-primary-hover">Export CSV</button>
            )}
          </div>
        </div>
      </div>

      {/* Bets table */}
      {filtered.length === 0 ? (
        <div className={cx('px-5 py-16 text-center', CARD)}>
          <p className="mono mb-2 text-xs text-text-secondary">No bets match these filters.</p>
          <p className="times mb-3.5 text-caption-1-regular text-text-secondary">Try a different system, result, or date range.</p>
          <button className="mono button-press-motion cursor-pointer rounded-2lg border border-border-button-default bg-transparent px-3.5 py-1.5 text-mini text-text-secondary hover:bg-background-primary-hover" onClick={() => { setSystem('ALL'); setResult('ALL'); setPage(0) }}>
            Reset filters
          </button>
        </div>
      ) : (
        <>
        <div className={cx('results-desktop overflow-x-auto', CARD)}>
          <div className="grid min-w-[860px] border-b border-separator-border bg-background-secondary-default" style={{ gridTemplateColumns: COL }}>
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
              }} className={cx(LBL, 'cursor-pointer border-none bg-transparent px-3 py-2.5 text-left hover:text-text-primary', sortBy === col.key && 'text-text-primary')}>
                {col.label}{sortBy === col.key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
              </button>
            ) : (
              <div key={col.label} className={cx(LBL, 'px-3 py-2.5')}>{col.label}</div>
            ))}
          </div>
          {filtered.slice(pageStart, pageStart + PAGE_SIZE).map((bet, i) => {
            return (
              <div key={bet.id ?? i} className="grid min-w-[860px] items-center border-b border-border-table transition-colors hover:bg-background-primary-hover" style={{ gridTemplateColumns: COL }}>
                <div className="mono px-3 py-2 text-mini text-text-secondary">
                  {fmtDisplayDate(bet.game_date)}
                  {dailyIds.has(bet.id) && <Link href="/picks/history" className="block text-link">Daily Card</Link>}
                </div>
                <div className="px-3 py-2"><SystemBadge system={bet.system} /></div>
                <div className="min-w-0 px-3 py-2"><Matchup away={bet.away_team} home={bet.home_team} size={16} fontSize="11px" /></div>
                <div className="mono truncate px-3 py-2 text-mini text-text-primary">{pickLabel(bet)}</div>
                <div className="mono px-3 py-2 text-mini text-text-primary">{formatOdds(bet.odds)}</div>
                <div className="mono px-3 py-2 text-mini text-signal">
                  {bet.edge != null ? `${bet.edge >= 0 ? '+' : ''}${(bet.edge * 100).toFixed(1)}%` : '--'}
                </div>
                <div className="mono px-3 py-2 text-mini text-text-primary">
                  {bet.stake != null && bet.stake > 0 ? `$${bet.stake.toFixed(0)}` : '--'}
                </div>
                <div className="mono px-3 py-2 text-micro capitalize text-text-secondary">
                  {bet.book ? bookText(bet.book) : '--'}{onePerProp && (bet as { bookCount?: number }).bookCount! > 1 ? ` +${(bet as { bookCount?: number }).bookCount! - 1}` : ''}
                </div>
                <div className="px-3 py-2"><ResultPill result={bet.result} /></div>
                <div className="px-3 py-2"><PnL value={bet.profit} stake={bet.stake} result={bet.result} /></div>
              </div>
            )
          })}
          {/* Pagination */}
          {filtered.length > PAGE_SIZE && renderPager("border-t border-separator-border bg-background-secondary-default px-4 py-3")}
        </div>
        <div className="results-mobile">
          <div className="flex flex-col gap-2.5">
            {filtered.slice(pageStart, pageStart + PAGE_SIZE).map((bet, i) => (
              <ResultBetCard key={bet.id ?? i} bet={bet} daily={dailyIds.has(bet.id)} />
            ))}
          </div>
          {filtered.length > PAGE_SIZE && renderPager("mt-3 px-0.5 py-3")}
        </div>
        </>
      )}
    </div>
  )
}
