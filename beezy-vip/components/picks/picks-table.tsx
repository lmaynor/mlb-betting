'use client'

import { B, SYSTEM_PILL, TEAM_ABBREV, pickLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

// ── Helpers ────────────────────────────────────────────────────────────────

function ResultPill({ result }: { result: string | null }) {
  const cfg: Record<string, { label: string; color: string; bg: string }> = {
    win:  { label: 'WIN',  color: '#10b981', bg: 'rgba(16,185,129,0.08)'  },
    loss: { label: 'LOSS', color: '#ef4444', bg: 'rgba(239,68,68,0.08)'   },
    push: { label: 'PUSH', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)'  },
    void: { label: 'VOID', color: '#71717a', bg: 'transparent'            },
  }
  const r = result?.toLowerCase() ?? ''
  const c = cfg[r] ?? { label: 'PENDING', color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' }
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center',
      padding: '2px 7px', borderRadius: '2px',
      fontSize: '10px', fontFamily: 'var(--font-mono)',
      letterSpacing: '0.06em', fontWeight: 600,
      color: c.color, background: c.bg,
      border: `0.5px solid ${c.color}33`,
    }}>{c.label}</span>
  )
}

function PnL({ profit, result }: { profit: number | null; result: string | null }) {
  if (profit === null || result === null || result === 'pending') {
    return <span style={{ color: '#71717a', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>—</span>
  }
  const units = (profit / 10).toFixed(1)
  const pos = profit >= 0
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 600, color: pos ? '#10b981' : '#ef4444' }}>
      {pos ? '+' : ''}{units}u
    </span>
  )
}

function fmtOdds(o: number) { return o > 0 ? `+${o}` : `${o}` }

function fmtDate(d: string) {
  return new Date(d + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function fmtEdge(e: number | null | undefined) {
  if (e === null || e === undefined) return '—'
  return (e * 100).toFixed(1) + '%'
}

const PROP_SYSTEMS = new Set(['HR', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER'])

// ── Desktop table row ──────────────────────────────────────────────────────

function TableRow({ bet }: { bet: Bet }) {
  const pill     = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label    = pickLabel(bet)
  const hasProp  = PROP_SYSTEMS.has(bet.system)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '72px 60px 120px 1fr 76px 60px 72px 68px 66px',
      alignItems: 'center',
      borderBottom: B,
      minWidth: '820px',
    }}>
      <div className="mono" style={{ padding: '10px 10px', fontSize: '11px', color: '#71717a' }}>{fmtDate(bet.game_date)}</div>

      <div style={{ padding: '10px 6px' }}>
        <span style={{
          padding: '2px 7px', borderRadius: '2px', fontSize: '10px',
          fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', fontWeight: 600,
          color: pill.color, background: pill.bg, border: `0.5px solid ${pill.color}33`,
        }}>{bet.system}</span>
      </div>

      <div style={{ padding: '10px 6px', fontSize: '12px', color: '#a1a1aa', fontFamily: 'var(--font-mono)' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div style={{ padding: '10px 6px' }}>
        {hasProp && bet.player ? (
          <>
            <span style={{ fontSize: '12px', fontWeight: 600, color: '#10b981' }}>{bet.player}</span>
            <span style={{ fontSize: '11px', color: '#71717a', marginLeft: '6px', fontFamily: 'var(--font-mono)' }}>
              {label.replace(bet.player, '').trim().replace(/^[—–\-]\s*/, '')}
            </span>
          </>
        ) : (
          <span style={{ fontSize: '12px', color: '#f5f5f7' }}>{label}</span>
        )}
        {bet.notes && (
          <div className="mono" style={{ fontSize: '10px', color: '#52525b', marginTop: '2px' }}>{bet.notes}</div>
        )}
      </div>

      <div className="mono" style={{ padding: '10px 6px', fontSize: '12px', color: '#f5f5f7' }}>{fmtOdds(bet.odds)}</div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', color: '#a1a1aa' }}>{fmtEdge(bet.edge)}</div>
      <div className="mono" style={{ padding: '10px 6px', fontSize: '11px', color: '#a1a1aa', overflow: 'hidden', textOverflow: 'ellipsis' }}>{bet.book ?? '—'}</div>
      <div style={{ padding: '10px 6px' }}><ResultPill result={bet.result} /></div>
      <div style={{ padding: '10px 6px' }}><PnL profit={bet.profit ?? null} result={bet.result} /></div>
    </div>
  )
}

// ── Mobile card ────────────────────────────────────────────────────────────

function BetCard({ bet }: { bet: Bet }) {
  const pill     = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label    = pickLabel(bet)
  const hasProp  = PROP_SYSTEMS.has(bet.system)

  return (
    <div className="card-hover" style={{ background: '#111114', border: B, borderRadius: 'var(--radius)', boxShadow: 'var(--shadow-card)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
      {/* Header: system + date | result + P&L */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            padding: '2px 7px', borderRadius: 'var(--radius-sm)', fontSize: '10px',
            fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', fontWeight: 600,
            color: pill.color, background: pill.bg, border: `0.5px solid ${pill.color}33`,
          }}>{bet.system}</span>
          <span className="mono" style={{ fontSize: '11px', color: '#71717a' }}>{fmtDate(bet.game_date)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <ResultPill result={bet.result} />
          <PnL profit={bet.profit ?? null} result={bet.result} />
        </div>
      </div>

      {/* Matchup */}
      <div style={{ fontSize: '13px', color: '#a1a1aa', fontFamily: 'var(--font-mono)' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      {/* Pick */}
      <div>
        {hasProp && bet.player ? (
          <>
            <span style={{ fontSize: '14px', fontWeight: 700, color: '#10b981' }}>{bet.player}</span>
            <span style={{ fontSize: '12px', color: '#a1a1aa', marginLeft: '6px', fontFamily: 'var(--font-mono)' }}>
              {label.replace(bet.player, '').trim().replace(/^[—–\-]\s*/, '')}
            </span>
          </>
        ) : (
          <span style={{ fontSize: '14px', fontWeight: 600, color: '#f5f5f7' }}>{label}</span>
        )}
        {bet.notes && (
          <div className="mono" style={{ fontSize: '10px', color: '#52525b', marginTop: '3px' }}>{bet.notes}</div>
        )}
      </div>

      {/* Stats: Odds / Edge / Book */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
        {[
          { label: 'ODDS', value: fmtOdds(bet.odds) },
          { label: 'EDGE', value: fmtEdge(bet.edge) },
          { label: 'BOOK', value: bet.book ?? '—' },
        ].map(({ label: l, value }) => (
          <div key={l}>
            <div style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', marginBottom: '2px' }}>{l}</div>
            <div className="mono" style={{ fontSize: '13px', color: l === 'ODDS' ? '#f5f5f7' : '#a1a1aa', fontWeight: l === 'ODDS' ? 600 : 400 }}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Sorting ────────────────────────────────────────────────────────────────

function sortBets(bets: Bet[], sort: string, dir: string): Bet[] {
  const mul = dir === 'asc' ? 1 : -1
  return [...bets].sort((a, b) => {
    if (sort === 'edge') return mul * ((a.edge ?? -1) - (b.edge ?? -1))
    if (sort === 'odds') return mul * (a.odds - b.odds)
    // default: date, then by id as tiebreaker
    const d = a.game_date.localeCompare(b.game_date)
    return mul * (d !== 0 ? d : a.id - b.id)
  })
}

// ── Main component ─────────────────────────────────────────────────────────

interface PicksTableProps {
  bets:  Bet[]
  sort?: 'date' | 'edge' | 'odds'
  dir?:  'asc' | 'desc'
}

export function PicksTable({ bets, sort = 'date', dir = 'desc' }: PicksTableProps) {
  const sorted = sortBets(bets, sort, dir)

  if (sorted.length === 0) {
    return (
      <div style={{ padding: '48px 24px', textAlign: 'center', color: '#71717a', fontFamily: 'var(--font-mono)', fontSize: '12px', border: B }}>
        No picks match the selected filters.
      </div>
    )
  }

  const COL_HEADERS = ['DATE','SYSTEM','GAME','PICK','ODDS','EDGE','BOOK','RESULT','P&L']

  return (
    <>
      {/* Desktop table */}
      <div className="picks-desktop">
        <div style={{
          display: 'grid',
          gridTemplateColumns: '72px 60px 120px 1fr 76px 60px 72px 68px 66px',
          borderBottom: B, minWidth: '820px',
        }}>
          {COL_HEADERS.map(h => (
            <div key={h} style={{ padding: '8px 10px', fontSize: '9px', fontFamily: 'var(--font-mono)', letterSpacing: '0.1em', color: '#71717a' }}>{h}</div>
          ))}
        </div>
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {sorted.map(bet => <TableRow key={bet.id} bet={bet} />)}
        </div>
      </div>

      {/* Mobile cards */}
      <div className="picks-mobile">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px' }}>
          {sorted.map(bet => <BetCard key={bet.id} bet={bet} />)}
        </div>
      </div>
    </>
  )
}
