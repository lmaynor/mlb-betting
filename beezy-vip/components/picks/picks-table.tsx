'use client'

import { useState } from 'react'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { B, SYSTEM_PILL, TEAM_ABBREV, pickLabel } from '@/lib/tokens'
import { formatDateKey } from '@/lib/dates'
import { getPickSystemByKey } from '@/lib/pick-systems'
import type { Bet } from '@/lib/types'

const PAGE_SIZE = 30
const TABLE_GRID = '72px 72px 112px 96px minmax(330px, 1fr) 76px 72px 88px 72px 70px'
const TABLE_MIN_WIDTH = '1120px'
const PROP_SYSTEMS = new Set(['HR', 'K', 'OUTS', 'BATTER_K', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER'])

function ResultPill({ result }: { result: string | null }) {
  const cfg: Record<string, { label: string; color: string; bg: string; border: string }> = {
    win:  { label: 'WIN',  color: 'var(--signal)', bg: 'var(--win-wash)',  border: '1px solid var(--win-border)' },
    loss: { label: 'LOSS', color: 'var(--loss)',   bg: 'var(--loss-wash)', border: '1px solid var(--loss-border)' },
    push: { label: 'PUSH', color: 'var(--silver)', bg: 'var(--slate)',     border: '1px solid var(--iron)' },
    void: { label: 'VOID', color: 'var(--fog)',    bg: 'var(--slate)',     border: '1px solid var(--iron)' },
  }
  const r = result?.toLowerCase() ?? ''
  const c = cfg[r] ?? { label: 'PENDING', color: 'var(--link)', bg: 'color-mix(in oklab, var(--link) 14%, var(--carbon))', border: '1px solid color-mix(in oklab, var(--link) 38%, var(--carbon))' }
  return (
    <span className="dell-heading" style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '3px 8px',
      borderRadius: 'var(--radius-pill)',
      fontSize: '9px',
      letterSpacing: '0.06em',
      color: c.color,
      background: c.bg,
      border: c.border,
    }}>
      {c.label}
    </span>
  )
}

function PnL({ profit, result }: { profit: number | null; result: string | null }) {
  if (profit === null || result === null || result === 'pending') {
    return <span className="mono" style={{ color: 'var(--fog)', fontSize: '12px' }}>--</span>
  }
  const units = (profit / 10).toFixed(1)
  const pos = profit >= 0
  return (
    <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: pos ? 'var(--signal)' : 'var(--loss)' }}>
      {pos ? '+' : ''}{units}u
    </span>
  )
}

function CompactScore({ bet, align = 'left' }: { bet: Bet; align?: 'left' | 'center' }) {
  const score = beezyscore(bet)
  const tier = scoreTier(score)
  const color = TIER_COLOR[tier]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: align === 'center' ? 'center' : 'flex-start', gap: '2px' }}>
      <span className="mono" style={{ fontSize: '16px', fontWeight: 800, color, lineHeight: 1 }}>
        {score}
      </span>
      <span className="dell-heading" style={{
        fontSize: '7px',
        letterSpacing: '0.08em',
        padding: '2px 5px',
        borderRadius: 'var(--radius-pill)',
        border: `1px solid ${color}`,
        background: `color-mix(in oklab, ${color} 14%, var(--carbon))`,
        color,
        whiteSpace: 'nowrap',
      }}>
        {TIER_LABEL[tier].replace(' PLAY', '')}
      </span>
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

function systemDisplay(system: string) {
  return getPickSystemByKey(system)?.shortName ?? system
}

function pickDescription(bet: Bet, detail: string) {
  if (bet.system === 'HR') return 'Home run prop'
  if (bet.system === 'BATTER_TB') return detail || 'Total bases prop'
  if (bet.system === 'BATTER_HITS') return detail || 'Hits prop'
  if (bet.system === 'BATTER_K') return detail || 'Batter strikeout prop'
  if (bet.system === 'K') return detail || 'Pitcher strikeout prop'
  if (bet.system === 'OUTS') return detail || 'Pitcher outs prop'
  if (bet.system === 'PITCHER_ER') return detail || 'Earned runs prop'
  if (bet.system === 'NRFI') return 'First inning run market'
  return detail
}

function TableRow({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label = pickLabel(bet)
  const hasProp = PROP_SYSTEMS.has(bet.system)
  const notes = splitNotes(bet.notes)
  const detail = pickDetail(label, bet.player)
  const description = pickDescription(bet, detail)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: TABLE_GRID,
      alignItems: 'center',
      borderBottom: '1px solid #201f22',
      minWidth: TABLE_MIN_WIDTH,
    }}>
      <div className="mono" style={{ padding: '11px 10px', fontSize: '11px', color: 'var(--fog)' }}>
        {fmtDate(bet.game_date)}
      </div>

      <div style={{ padding: '9px 8px' }}>
        <CompactScore bet={bet} />
      </div>

      <div style={{ padding: '10px 6px' }}>
        <span className="dell-heading" style={{
          display: 'inline-flex',
          maxWidth: '96px',
          padding: '3px 8px',
          borderRadius: 'var(--radius-pill)',
          fontSize: '9px',
          letterSpacing: '0.06em',
          color: pill.color,
          background: pill.bg,
          border: pill.border,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {systemDisplay(bet.system)}
        </span>
      </div>

      <div className="mono" style={{ padding: '10px 6px', fontSize: '12px', color: 'var(--silver)' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div style={{ padding: '10px 6px', minWidth: 0 }}>
        {hasProp && bet.player ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 }}>
            <span style={{ fontSize: '12px', fontWeight: 750, color: 'var(--ash)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{bet.player}</span>
            <span className="mono" style={{ fontSize: '10px', color: 'var(--silver)', lineHeight: 1.4 }}>
              {description}
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 }}>
            <span style={{ fontSize: '12px', color: 'var(--ash)', fontWeight: 650 }}>{label}</span>
            {description && <span className="mono" style={{ fontSize: '10px', color: 'var(--fog)' }}>{description}</span>}
          </div>
        )}
        {notes.length > 0 && (
          <div style={{ marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '1px' }}>
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} style={{ display: 'flex', gap: '4px', alignItems: 'flex-start' }}>
                <span style={{ color: pill.color, fontSize: '8px', lineHeight: '14px', flexShrink: 0 }}>{'>'}</span>
                <span className="mono" style={{ fontSize: '9px', color: 'var(--fog)', lineHeight: '14px' }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mono" style={{ padding: '10px 6px', fontSize: '12px', fontWeight: 700, color: 'var(--ash)' }}>
        {fmtOdds(bet.odds)}
      </div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', fontWeight: 700, color: 'var(--signal)' }}>
        {fmtEdge(bet.edge)}
      </div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', color: 'var(--silver)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {bet.book ?? '--'}
      </div>
      <div style={{ padding: '10px 6px' }}><ResultPill result={bet.result} /></div>
      <div style={{ padding: '10px 6px' }}><PnL profit={bet.profit ?? null} result={bet.result} /></div>
    </div>
  )
}

function BetCard({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label = pickLabel(bet)
  const hasProp = PROP_SYSTEMS.has(bet.system)
  const notes = splitNotes(bet.notes)
  const score = beezyscore(bet)
  const tierColor = TIER_COLOR[scoreTier(score)]
  const detail = pickDetail(label, bet.player)
  const description = pickDescription(bet, detail)

  return (
    <div className="card-hover" style={{
      background: 'var(--graphite)',
      border: '1px solid var(--basalt)',
      borderRadius: 'var(--radius-lg)',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    }}>
      <div style={{ height: '3px', background: tierColor, opacity: 0.85 }} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          <span className="dell-heading" style={{
            padding: '3px 8px',
            borderRadius: 'var(--radius-pill)',
            fontSize: '9px',
            letterSpacing: '0.06em',
            color: pill.color,
            background: pill.bg,
            border: pill.border,
          }}>
            {systemDisplay(bet.system)}
          </span>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>{fmtDate(bet.game_date)}</span>
        </div>
        <CompactScore bet={bet} align="center" />
      </div>

      <div className="mono" style={{ fontSize: '13px', color: 'var(--silver)' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div>
        {hasProp && bet.player ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '15px', fontWeight: 800, color: 'var(--ash)' }}>{bet.player}</span>
            <span className="mono" style={{ fontSize: '12px', color: 'var(--silver)', lineHeight: 1.45 }}>
              {description}
            </span>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <span style={{ fontSize: '15px', fontWeight: 750, color: 'var(--ash)' }}>{label}</span>
            {description && <span className="mono" style={{ fontSize: '12px', color: 'var(--fog)' }}>{description}</span>}
          </div>
        )}
        {notes.length > 0 && (
          <div style={{ marginTop: '5px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} style={{ display: 'flex', gap: '6px' }}>
                <span style={{ color: pill.color, fontSize: '9px', flexShrink: 0 }}>&middot;</span>
                <span className="mono" style={{ fontSize: '9px', color: 'var(--fog)' }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: '8px', alignItems: 'end' }}>
        {[
          { label: 'ODDS', value: fmtOdds(bet.odds), strong: true },
          { label: 'EDGE', value: fmtEdge(bet.edge), strong: true, color: 'var(--signal)' },
          { label: 'BOOK', value: bet.book ?? '--' },
        ].map(({ label: l, value, strong, color }) => (
          <div key={l}>
            <div className="mono" style={{ fontSize: '9px', color: 'var(--fog)', letterSpacing: '0.08em', marginBottom: '2px' }}>{l}</div>
            <div className="mono" style={{ fontSize: '13px', color: color ?? (strong ? 'var(--ash)' : 'var(--silver)'), fontWeight: strong ? 700 : 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
          </div>
        ))}
        <div><ResultPill result={bet.result} /></div>
        <div><PnL profit={bet.profit ?? null} result={bet.result} /></div>
      </div>
      </div>
    </div>
  )
}

type SortKey = 'date' | 'score' | 'edge' | 'odds'

function sortBets(bets: Bet[], sort: SortKey, dir: 'asc' | 'desc'): Bet[] {
  const mul = dir === 'asc' ? 1 : -1
  return [...bets].sort((a, b) => {
    if (sort === 'score') return mul * (beezyscore(a) - beezyscore(b))
    if (sort === 'edge') return mul * ((a.edge ?? -1) - (b.edge ?? -1))
    if (sort === 'odds') return mul * (a.odds - b.odds)
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
      <div style={{ padding: '48px 24px', textAlign: 'center', color: 'var(--fog)', fontFamily: 'var(--font-mono)', fontSize: '12px', border: B, borderRadius: 'var(--radius)' }}>
        No picks match the selected filters.
      </div>
    )
  }

  const COLS: { label: string; sortKey?: SortKey }[] = [
    { label: 'DATE', sortKey: 'date' },
    { label: 'SCORE', sortKey: 'score' },
    { label: 'SYSTEM' },
    { label: 'GAME' },
    { label: 'PICK' },
    { label: 'ODDS', sortKey: 'odds' },
    { label: 'EDGE', sortKey: 'edge' },
    { label: 'BOOK' },
    { label: 'RESULT' },
    { label: 'P&L' },
  ]

  const sortIndicator = (col: SortKey) =>
    sortKey === col ? (sortDir === 'desc' ? ' v' : ' ^') : ''

  return (
    <>
      <div className="picks-desktop" style={{ border: B, borderRadius: 'var(--radius-lg)', boxShadow: 'var(--shadow-card)', background: 'var(--graphite)', overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          <div style={{
            display: 'grid',
            gridTemplateColumns: TABLE_GRID,
            borderBottom: B,
            minWidth: TABLE_MIN_WIDTH,
            background: 'var(--obsidian)',
          }}>
            {COLS.map(c => c.sortKey ? (
              <button key={c.label} onClick={() => handleSort(c.sortKey!)} style={{
                padding: '8px 10px',
                fontSize: '9px',
                fontFamily: 'var(--font-mono)',
                letterSpacing: '0.1em',
                color: sortKey === c.sortKey ? 'var(--ash)' : 'var(--fog)',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                textAlign: 'left',
              }}>
                {c.label}{sortIndicator(c.sortKey)}
              </button>
            ) : (
              <div key={c.label} className="mono" style={{ padding: '8px 10px', fontSize: '9px', letterSpacing: '0.1em', color: 'var(--fog)' }}>{c.label}</div>
            ))}
          </div>
          {visible.map(bet => <TableRow key={bet.id} bet={bet} />)}
        </div>
      </div>

      <div className="picks-mobile">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', padding: '12px 0' }}>
          {visible.map(bet => <BetCard key={bet.id} bet={bet} />)}
        </div>
      </div>

      {pages > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 2px', borderTop: B, marginTop: '12px' }}>
          <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
            className="mono"
            style={{ fontSize: '11px', padding: '6px 12px', cursor: page === 0 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius)', background: 'transparent', color: page === 0 ? 'var(--iron)' : 'var(--fog)' }}>
            Prev
          </button>
          <span className="mono" style={{ fontSize: '11px', color: 'var(--fog)' }}>
            {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <button onClick={() => setPage(p => Math.min(pages - 1, p + 1))} disabled={page === pages - 1}
            className="mono"
            style={{ fontSize: '11px', padding: '6px 12px', cursor: page === pages - 1 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius)', background: 'transparent', color: page === pages - 1 ? 'var(--iron)' : 'var(--fog)' }}>
            Next
          </button>
        </div>
      )}
    </>
  )
}
