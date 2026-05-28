'use client'

import { useState } from 'react'
import { beezyscore, scoreTier, TIER_COLOR, TIER_LABEL } from '@/lib/beezy-score'
import { B, SYSTEM_PILL, TEAM_ABBREV, pickLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

const PAGE_SIZE = 30
const PROP_SYSTEMS = new Set(['HR', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER'])

function ResultPill({ result }: { result: string | null }) {
  const cfg: Record<string, { label: string; color: string; bg: string }> = {
    win: { label: 'WIN', color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
    loss: { label: 'LOSS', color: '#ef4444', bg: 'rgba(239,68,68,0.08)' },
    push: { label: 'PUSH', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
    void: { label: 'VOID', color: '#71717a', bg: 'transparent' },
  }
  const r = result?.toLowerCase() ?? ''
  const c = cfg[r] ?? { label: 'PENDING', color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' }
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      padding: '2px 7px',
      borderRadius: 'var(--radius-sm)',
      fontSize: '10px',
      fontFamily: 'var(--font-mono)',
      letterSpacing: '0.06em',
      fontWeight: 600,
      color: c.color,
      background: c.bg,
      border: `0.5px solid ${c.color}33`,
    }}>
      {c.label}
    </span>
  )
}

function PnL({ profit, result }: { profit: number | null; result: string | null }) {
  if (profit === null || result === null || result === 'pending') {
    return <span className="mono" style={{ color: '#71717a', fontSize: '12px' }}>--</span>
  }
  const units = (profit / 10).toFixed(1)
  const pos = profit >= 0
  return (
    <span className="mono" style={{ fontSize: '12px', fontWeight: 700, color: pos ? '#10b981' : '#ef4444' }}>
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
      <span className="mono" style={{ fontSize: '16px', fontWeight: 850, color, lineHeight: 1 }}>
        {score}
      </span>
      <span className="mono" style={{
        fontSize: '7px',
        fontWeight: 800,
        letterSpacing: '0.08em',
        padding: '1px 4px',
        borderRadius: 'var(--radius-sm)',
        border: `0.5px solid ${color}44`,
        background: `${color}12`,
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
  return new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
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
  return label.replace(player, '').trim().replace(/^[-\s]+/, '')
}

function TableRow({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label = pickLabel(bet)
  const hasProp = PROP_SYSTEMS.has(bet.system)
  const notes = splitNotes(bet.notes)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '72px 70px 64px 112px minmax(220px, 1fr) 76px 68px 76px 72px 70px',
      alignItems: 'center',
      borderBottom: B,
      minWidth: '980px',
      background: '#0d0d11',
    }}>
      <div className="mono" style={{ padding: '11px 10px', fontSize: '11px', color: '#71717a' }}>
        {fmtDate(bet.game_date)}
      </div>

      <div style={{ padding: '9px 8px' }}>
        <CompactScore bet={bet} />
      </div>

      <div style={{ padding: '10px 6px' }}>
        <span className="mono" style={{
          padding: '2px 7px',
          borderRadius: 'var(--radius-sm)',
          fontSize: '10px',
          letterSpacing: '0.06em',
          fontWeight: 700,
          color: pill.color,
          background: pill.bg,
          border: `0.5px solid ${pill.color}33`,
        }}>
          {bet.system}
        </span>
      </div>

      <div className="mono" style={{ padding: '10px 6px', fontSize: '12px', color: '#a1a1aa' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div style={{ padding: '10px 6px', minWidth: 0 }}>
        {hasProp && bet.player ? (
          <>
            <span style={{ fontSize: '12px', fontWeight: 750, color: '#f5f5f7' }}>{bet.player}</span>
            <span className="mono" style={{ fontSize: '11px', color: '#a1a1aa', marginLeft: '6px' }}>
              {pickDetail(label, bet.player)}
            </span>
          </>
        ) : (
          <span style={{ fontSize: '12px', color: '#f5f5f7', fontWeight: 650 }}>{label}</span>
        )}
        {notes.length > 0 && (
          <div style={{ marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '1px' }}>
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} style={{ display: 'flex', gap: '4px', alignItems: 'flex-start' }}>
                <span style={{ color: pill.color, fontSize: '8px', lineHeight: '14px', flexShrink: 0 }}>{'>'}</span>
                <span className="mono" style={{ fontSize: '9px', color: '#626274', lineHeight: '14px' }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="mono" style={{ padding: '10px 6px', fontSize: '12px', fontWeight: 700, color: '#f5f5f7' }}>
        {fmtOdds(bet.odds)}
      </div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', fontWeight: 700, color: '#10b981' }}>
        {fmtEdge(bet.edge)}
      </div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis' }}>
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

  return (
    <div className="card-hover" style={{
      background: '#0d0d12',
      border: `0.5px solid ${tierColor}33`,
      borderLeft: `3px solid ${tierColor}66`,
      borderRadius: 'var(--radius)',
      boxShadow: score >= 65 ? `0 0 0 1px ${tierColor}25, 0 0 18px ${tierColor}12` : 'var(--shadow-card)',
      padding: '12px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: '10px',
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
          <span className="mono" style={{
            padding: '2px 7px',
            borderRadius: 'var(--radius-sm)',
            fontSize: '10px',
            letterSpacing: '0.06em',
            fontWeight: 700,
            color: pill.color,
            background: pill.bg,
            border: `0.5px solid ${pill.color}33`,
          }}>
            {bet.system}
          </span>
          <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>{fmtDate(bet.game_date)}</span>
        </div>
        <CompactScore bet={bet} align="center" />
      </div>

      <div className="mono" style={{ fontSize: '13px', color: '#a1a1aa' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div>
        {hasProp && bet.player ? (
          <>
            <span style={{ fontSize: '15px', fontWeight: 800, color: '#f5f5f7' }}>{bet.player}</span>
            <span className="mono" style={{ fontSize: '12px', color: '#a1a1aa', marginLeft: '6px' }}>
              {pickDetail(label, bet.player)}
            </span>
          </>
        ) : (
          <span style={{ fontSize: '15px', fontWeight: 750, color: '#f5f5f7' }}>{label}</span>
        )}
        {notes.length > 0 && (
          <div style={{ marginTop: '5px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {notes.slice(0, 2).map((n, i) => (
              <div key={i} style={{ display: 'flex', gap: '4px' }}>
                <span style={{ color: pill.color, fontSize: '8px', flexShrink: 0 }}>{'>'}</span>
                <span className="mono" style={{ fontSize: '9px', color: '#797991' }}>{n}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, minmax(0, 1fr))', gap: '8px', alignItems: 'end' }}>
        {[
          { label: 'ODDS', value: fmtOdds(bet.odds), strong: true },
          { label: 'EDGE', value: fmtEdge(bet.edge), strong: true, color: '#10b981' },
          { label: 'BOOK', value: bet.book ?? '--' },
        ].map(({ label: l, value, strong, color }) => (
          <div key={l}>
            <div className="mono" style={{ fontSize: '9px', color: '#71717a', letterSpacing: '0.08em', marginBottom: '2px' }}>{l}</div>
            <div className="mono" style={{ fontSize: '13px', color: color ?? (strong ? '#f5f5f7' : '#a1a1aa'), fontWeight: strong ? 700 : 500, overflow: 'hidden', textOverflow: 'ellipsis' }}>{value}</div>
          </div>
        ))}
        <div><ResultPill result={bet.result} /></div>
        <div><PnL profit={bet.profit ?? null} result={bet.result} /></div>
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
      <div style={{ padding: '48px 24px', textAlign: 'center', color: '#71717a', fontFamily: 'var(--font-mono)', fontSize: '12px', border: B, borderRadius: 'var(--radius)' }}>
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
      <div className="picks-desktop" style={{ border: B, borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)', background: '#0a0a0c' }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '72px 70px 64px 112px minmax(220px, 1fr) 76px 68px 76px 72px 70px',
          borderBottom: B,
          minWidth: '980px',
          background: '#111114',
        }}>
          {COLS.map(c => c.sortKey ? (
            <button key={c.label} onClick={() => handleSort(c.sortKey!)} style={{
              padding: '8px 10px',
              fontSize: '9px',
              fontFamily: 'var(--font-mono)',
              letterSpacing: '0.1em',
              color: sortKey === c.sortKey ? '#f5f5f7' : '#71717a',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
            }}>
              {c.label}{sortIndicator(c.sortKey)}
            </button>
          ) : (
            <div key={c.label} className="mono" style={{ padding: '8px 10px', fontSize: '9px', letterSpacing: '0.1em', color: '#71717a' }}>{c.label}</div>
          ))}
        </div>
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
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
            style={{ fontSize: '11px', padding: '6px 12px', cursor: page === 0 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius-sm)', background: 'transparent', color: page === 0 ? '#2a2a31' : '#71717a' }}>
            Prev
          </button>
          <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>
            {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <button onClick={() => setPage(p => Math.min(pages - 1, p + 1))} disabled={page === pages - 1}
            className="mono"
            style={{ fontSize: '11px', padding: '6px 12px', cursor: page === pages - 1 ? 'default' : 'pointer', border: B, borderRadius: 'var(--radius-sm)', background: 'transparent', color: page === pages - 1 ? '#2a2a31' : '#71717a' }}>
            Next
          </button>
        </div>
      )}
    </>
  )
}
