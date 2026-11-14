'use client'

import { useState } from 'react'
import { beezyscore, explainScore, scoreTier, TIER_COLOR } from '@/lib/beezy-score'
import { B, SYSTEM_PILL, pickLabel, systemLabel, resolveEvUnderlying, bookText } from '@/lib/tokens'
import { americanToImpliedProb } from '@/lib/odds'
import { isWinterSport, readableNotes } from '@/lib/bet-notes'
import { Matchup } from '@/components/ui/matchup'
import { formatDateKey } from '@/lib/dates'
import type { Bet } from '@/lib/types'

const impliedLabel = (odds: number) => `imp ${(americanToImpliedProb(odds) * 100).toFixed(0)}%`

const PAGE_SIZE = 30
const TABLE_GRID = '72px 72px 112px 128px minmax(310px, 1fr) 76px 72px 88px 72px 70px'
const TABLE_MIN_WIDTH = '1120px'
const PROP_SYSTEMS = new Set(['HR', 'K', 'OUTS', 'BATTER_K', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER', 'SB'])

// An EV row's own `system` is always the pooled "EV" tag, not the market it
// actually alerted on -- resolve to the real market before checking
// PROP_SYSTEMS so a prop-market EV alert (e.g. an HR alert) still gets the
// player-first row layout below instead of always falling back to the
// game-line layout.
function effectiveSystem(bet: Bet): string {
  return bet.system === 'EV' ? resolveEvUnderlying(bet).system : bet.system
}

function ResultPill({ result }: { result: string | null }) {
  const cfg: Record<string, { label: string; color: string; bg: string; border: string }> = {
    win:  { label: 'WIN',  color: 'var(--signal)', bg: 'var(--win-wash)',  border: '1px solid var(--win-border)' },
    loss: { label: 'LOSS', color: 'var(--loss)',   bg: 'var(--loss-wash)', border: '1px solid var(--loss-border)' },
    push: { label: 'PUSH', color: 'var(--color-neutral-300)', bg: 'var(--slate)',     border: '1px solid var(--color-border-button-default)' },
    void: { label: 'VOID', color: 'var(--color-text-secondary)',    bg: 'var(--slate)',     border: '1px solid var(--color-border-button-default)' },
  }
  const r = result?.toLowerCase() ?? ''
  const c = cfg[r] ?? { label: 'Pending', color: 'var(--link)', bg: 'color-mix(in oklab, var(--link) 14%, var(--color-background-full))', border: '1px solid color-mix(in oklab, var(--link) 38%, var(--color-background-full))' }
  return (
    <span className="ui-heading inline-flex items-center py-[3px] px-2 text-micro" style={{
      borderRadius: 'var(--radius-pill)',
      color: c.color,
      background: c.bg,
      border: c.border,
    }}>
      {c.label}
    </span>
  )
}

function PnL({ profit, result, stake }: { profit: number | null; result: string | null; stake?: number | null }) {
  if (profit === null || result === null || result === 'pending' || result === 'void' || result === 'push') {
    return <span className="mono text-text-secondary text-compact">--</span>
  }
  const units = (profit / (stake != null && stake > 0 ? stake : 10)).toFixed(2)
  const pos = profit >= 0
  return (
    <span className="mono text-compact font-semibold" style={{ color: pos ? 'var(--signal)' : 'var(--loss)' }}>
      {pos ? '+' : ''}{units}u
    </span>
  )
}

function CompactScore({ bet, align = 'left' }: { bet: Bet; align?: 'left' | 'center' }) {
  const score = beezyscore(bet)
  const edge = Math.abs(bet.edge ?? 0) < 2 ? (bet.edge ?? 0) : (bet.edge ?? 0) / 100
  const isOfficial = edge >= 0.03 && edge <= 0.10 && score >= 68 && bet.validated !== false
  const isOutlier = edge >= 0.025 && edge <= 0.15 && !isOfficial && bet.system === 'EV'
  const badgeColor = isOfficial ? '#57F287' : isOutlier ? '#38bdf8' : undefined
  const badgeLabel = isOfficial ? 'Official play' : isOutlier ? '+EV OUTLIER' : null

  return (
    <div className="flex flex-col gap-[2px]" title={explainScore(bet)} style={{ alignItems: align === 'center' ? 'center' : 'flex-start' }}>
      <span className="mono text-[16px] font-semibold leading-[1]" style={{ color: badgeColor ?? 'var(--color-neutral-300)' }}>
        {score}
      </span>
      {badgeLabel && badgeColor && (
        <span className="ui-heading text-micro py-[2px] px-[5px] whitespace-nowrap" style={{
          borderRadius: '4px',
          border: `1px solid ${badgeColor}`,
          background: `color-mix(in oklab, ${badgeColor} 14%, var(--color-background-full))`,
          color: badgeColor,
        }}>
          {badgeLabel}
        </span>
      )}
    </div>
  )
}

function fmtOdds(o: number) {
  return o > 0 ? `+${o}` : `${o}`
}

function fmtDate(d: string) {
  return formatDateKey(d, { month: 'short', day: 'numeric' })
}

function fmtEdge(e: number | null | undefined) {
  if (e === null || e === undefined) return '--'
  const pct = Math.abs(e) < 2 ? e * 100 : e
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%`
}

function splitNotes(notes: string | null | undefined) {
  if (!notes) return []
  return notes
    .replaceAll(` ${String.fromCharCode(194, 183)} `, ' / ')
    .replaceAll(String.fromCharCode(183), ' / ')
    .split(' / ')
    .map(s => s.trim())
    .filter(Boolean)
}

function pickDetail(label: string, player: string | null) {
  if (!player) return label
  return label
    .replace(player, '')
    .replace(/^\s*\([^)]+\)\s*/, '')
    .trim()
    .replace(/^[-\s]+/, '')
}

/** A tracked NFL / NBA / NHL bet: graded like any pick, but the model has no tested edge, so it says so on every row. */
function UnvalidatedTag() {
  return <span className="ui-heading text-gold">UNVALIDATED &middot; PAPER</span>
}

function systemDisplay(system: string) {
  return systemLabel(system, true)
}

function pickDescription(bet: Bet, detail: string) {
  if (bet.system === 'HR') return 'Home run prop'
  if (bet.system === 'BATTER_TB') return detail || 'Total bases prop'
  if (bet.system === 'BATTER_HITS') return detail || 'Hits prop'
  if (bet.system === 'SB') return detail || 'Stolen base prop'
  if (bet.system === 'BATTER_K') return detail || 'Batter strikeout prop'
  if (bet.system === 'K') return detail || 'Pitcher strikeout prop'
  if (bet.system === 'OUTS') return detail || 'Pitcher outs prop'
  if (bet.system === 'PITCHER_ER') return detail || 'Earned runs prop'
  if (bet.system === 'NRFI') return 'First inning run market'
  return detail
}

function TableRow({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[effectiveSystem(bet) as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const label = pickLabel(bet)
  const hasProp = PROP_SYSTEMS.has(effectiveSystem(bet))
  const notes = splitNotes(readableNotes(bet.notes))
  const detail = pickDetail(label, bet.player)
  const description = pickDescription(bet, detail)

  return (
    <div className="grid items-center border-b border-b-[color:var(--color-border-table)]" style={{ gridTemplateColumns: TABLE_GRID, minWidth: TABLE_MIN_WIDTH }}>
      <div className="mono py-[11px] px-2.5 text-mini text-text-secondary">
        {fmtDate(bet.game_date)}
      </div>

      <div className="py-[9px] px-2">
        <CompactScore bet={bet} />
      </div>

      <div className="py-2.5 px-1.5">
        <span className="ui-heading inline-flex max-w-[96px] py-[3px] px-2 text-micro overflow-hidden text-ellipsis whitespace-nowrap" style={{
          borderRadius: 'var(--radius-pill)',
          color: pill.color,
          background: pill.bg,
          border: pill.border,
        }}>
          {systemDisplay(effectiveSystem(bet))}
        </span>
      </div>

      <div className="py-2.5 px-1.5">
        <Matchup sport={bet.sport} away={bet.away_team} home={bet.home_team} size={18} fontSize="12px" />
      </div>

      <div className="py-2.5 px-1.5 min-w-0">
        {hasProp && bet.player ? (
          <div className="flex flex-col gap-[3px] min-w-0">
            <span className="text-text-primary overflow-hidden text-ellipsis whitespace-nowrap" style={{ fontSize: '12px', fontWeight: 750 }}>{bet.player}</span>
            <span className="mono text-micro text-neutral-300 leading-[1.4]">
              {description}
            </span>
          </div>
        ) : (
          <div className="flex flex-col gap-[3px] min-w-0">
            <span className="text-text-primary" style={{ fontSize: '12px', fontWeight: 650 }}>{label}</span>
            {description && <span className="mono text-micro text-text-secondary">{description}</span>}
            {isWinterSport(bet.sport) && <UnvalidatedTag />}
          </div>
        )}
        {notes.length > 0 && (
          <div className="mt-1 flex flex-col gap-[1px]">
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} className="flex gap-1 items-start">
                <span className="text-micro shrink-0" style={{ color: pill.color, lineHeight: '14px' }}>{'>'}</span>
                <span className="mono text-micro text-text-secondary" style={{ lineHeight: '14px' }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="py-2.5 px-1.5">
        <div className="mono text-compact font-semibold text-text-primary">{fmtOdds(bet.odds)}</div>
        <div className="mono text-micro text-text-secondary mt-0.5">{impliedLabel(bet.odds)}</div>
      </div>
      <div className="mono py-2.5 px-1.5 text-mini font-semibold text-signal">
        {fmtEdge(bet.edge)}
      </div>
      <div className="mono py-2.5 px-1.5 text-mini text-neutral-300 overflow-hidden text-ellipsis">
        {bet.book ? bookText(bet.book) : '--'}{((bet as { bookCount?: number }).bookCount ?? 1) > 1 ? ` +${(bet as { bookCount?: number }).bookCount! - 1}` : ''}
      </div>
      <div className="py-2.5 px-1.5"><ResultPill result={bet.result} /></div>
      <div className="py-2.5 px-1.5"><PnL profit={bet.profit ?? null} result={bet.result} stake={bet.stake} /></div>
    </div>
  )
}

function BetCard({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[effectiveSystem(bet) as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const label = pickLabel(bet)
  const hasProp = PROP_SYSTEMS.has(effectiveSystem(bet))
  const notes = splitNotes(readableNotes(bet.notes))
  const score = beezyscore(bet)
  const tierColor = TIER_COLOR[scoreTier(score)]
  const detail = pickDetail(label, bet.player)
  const description = pickDescription(bet, detail)

  return (
    <div className="card-hover bg-background-primary-default border border-separator-border rounded-[var(--radius-lg)] overflow-hidden flex flex-col">
      <div className="h-[3px] opacity-[0.85]" style={{ background: tierColor }} />
      <div className="flex flex-col gap-2.5 p-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <span className="ui-heading py-[3px] px-2 text-micro" style={{
            borderRadius: 'var(--radius-pill)',
            color: pill.color,
            background: pill.bg,
            border: pill.border,
          }}>
            {systemDisplay(effectiveSystem(bet))}
          </span>
          <span className="mono text-mini text-text-secondary">{fmtDate(bet.game_date)}</span>
        </div>
        <CompactScore bet={bet} align="center" />
      </div>

      <div><Matchup sport={bet.sport} away={bet.away_team} home={bet.home_team} size={20} fontSize="13px" /></div>

      <div>
        {hasProp && bet.player ? (
          <div className="flex flex-col gap-1">
            <span className="text-[15px] font-semibold text-text-primary">{bet.player}</span>
            <span className="mono text-compact text-neutral-300 leading-[1.45]">
              {description}
            </span>
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            <span className="text-text-primary" style={{ fontSize: '15px', fontWeight: 750 }}>{label}</span>
            {description && <span className="mono text-compact text-text-secondary">{description}</span>}
            {isWinterSport(bet.sport) && <UnvalidatedTag />}
          </div>
        )}
        {notes.length > 0 && (
          <div className="mt-[5px] flex flex-col gap-0.5">
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} className="flex gap-1.5">
                <span className="text-micro shrink-0" style={{ color: pill.color }}>&middot;</span>
                <span className="mono text-micro text-text-secondary">{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid grid-cols-5 gap-2" style={{ alignItems: 'end' }}>
        {[
          { label: 'ODDS', value: fmtOdds(bet.odds), strong: true, sub: impliedLabel(bet.odds) },
          { label: 'EDGE', value: fmtEdge(bet.edge), strong: true, color: 'var(--signal)' },
          { label: 'BOOK', value: bet.book ? bookText(bet.book) : '--' },
        ].map(({ label: l, value, strong, color, sub }) => (
          <div key={l}>
            <div className="mono text-micro text-text-secondary mb-0.5">{l}</div>
            <div className="mono overflow-hidden text-ellipsis" style={{ fontSize: '13px', color: color ?? (strong ? 'var(--color-text-primary)' : 'var(--color-neutral-300)'), fontWeight: strong ? 700 : 500 }}>{value}</div>
            {sub && <div className="mono text-micro text-text-secondary mt-0.5">{sub}</div>}
          </div>
        ))}
        <div><ResultPill result={bet.result} /></div>
        <div><PnL profit={bet.profit ?? null} result={bet.result} stake={bet.stake} /></div>
      </div>
      </div>
    </div>
  )
}

type SortKey = 'date' | 'score' | 'edge' | 'odds' | 'prob'

function sortBets(bets: Bet[], sort: SortKey, dir: 'asc' | 'desc'): Bet[] {
  const mul = dir === 'asc' ? 1 : -1
  return [...bets].sort((a, b) => {
    if (sort === 'score') return mul * (beezyscore(a) - beezyscore(b))
    if (sort === 'edge') return mul * ((a.edge ?? -1) - (b.edge ?? -1))
    if (sort === 'odds') return mul * (a.odds - b.odds)
    if (sort === 'prob') return mul * ((a.model_prob ?? -1) - (b.model_prob ?? -1))
    const d = a.game_date.localeCompare(b.game_date)
    return mul * (d !== 0 ? d : a.id - b.id)
  })
}

interface PicksTableProps {
  bets: Bet[]
  sort?: SortKey
  dir?: 'asc' | 'desc'
}

export function PicksTable({ bets, sort: initSort = 'score', dir: initDir = 'desc' }: PicksTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>(initSort)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(initDir)
  const [page, setPage] = useState(0)

  const sorted = sortBets(bets, sortKey, sortDir)
  const total = sorted.length
  const pages = Math.ceil(total / PAGE_SIZE)
  const visible = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  function handleSort(col: SortKey) {
    if (sortKey === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(col); setSortDir('desc') }
    setPage(0)
  }

  if (total === 0) {
    return (
      <div className="py-12 px-6 text-center text-text-secondary" style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', border: B, borderRadius: 'var(--radius)' }}>
        No picks match the selected filters.
      </div>
    )
  }

  const COLS: { label: string; sortKey?: SortKey }[] = [
    { label: 'DATE', sortKey: 'date' },
    { label: 'Score', sortKey: 'score' },
    { label: 'System' },
    { label: 'Game' },
    { label: 'PICK' },
    { label: 'ODDS', sortKey: 'odds' },
    { label: 'EDGE', sortKey: 'edge' },
    { label: 'BOOK' },
    { label: 'Result' },
    { label: 'P&L' },
  ]

  const sortIndicator = (col: SortKey) =>
    sortKey === col ? (sortDir === 'desc' ? ' v' : ' ^') : ''

  return (
    <>
      <div className="picks-desktop shadow-card bg-background-primary-default overflow-hidden" style={{ border: B, borderRadius: 'var(--radius-lg)' }}>
        <div className="overflow-x-auto" style={{ WebkitOverflowScrolling: 'touch' }}>
          <div className="grid bg-background-secondary-default" style={{ gridTemplateColumns: TABLE_GRID, borderBottom: B, minWidth: TABLE_MIN_WIDTH }}>
            {COLS.map(c => c.sortKey ? (
              <button className="py-2 px-[10px] bg-transparent bg-none border-0 cursor-pointer text-left" key={c.label} onClick={() => handleSort(c.sortKey!)} style={{
                fontSize: '10px',
                fontFamily: 'var(--font-mono)',
                color: sortKey === c.sortKey ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
              }}>
                {c.label}{sortIndicator(c.sortKey)}
              </button>
            ) : (
              <div key={c.label} className="mono py-2 px-2.5 text-micro text-text-secondary">{c.label}</div>
            ))}
          </div>
          {visible.map(bet => <TableRow key={bet.id} bet={bet} />)}
        </div>
      </div>

      <div className="picks-mobile">
        <div className="flex flex-col gap-2.5 py-3 px-0">
          {visible.map(bet => <BetCard key={bet.id} bet={bet} />)}
        </div>
      </div>

      {pages > 1 && (
        <div className="flex items-center justify-between py-3 px-[2px] mt-3" style={{ borderTop: B }}>
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
            className="mono text-mini py-[6px] px-3 bg-transparent"
            style={{ cursor: page === 0 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius)', color: page === 0 ? 'var(--color-border-button-default)' : 'var(--color-text-secondary)' }}>
            Prev
          </button>
          <span className="mono text-mini text-text-secondary">
            {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <button onClick={() => setPage(p => Math.min(pages - 1, p + 1))} disabled={page === pages - 1}
            className="mono text-mini py-[6px] px-3 bg-transparent"
            style={{ cursor: page === pages - 1 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius)', color: page === pages - 1 ? 'var(--color-border-button-default)' : 'var(--color-text-secondary)' }}>
            Next
          </button>
        </div>
      )}
    </>
  )
}
