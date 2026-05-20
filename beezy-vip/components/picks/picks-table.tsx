'use client'

import { useState } from 'react'
import { B, SYSTEM_PILL, TEAM_ABBREV, pickLabel } from '@/lib/tokens'
import type { Bet } from '@/lib/types'

// ── Helpers ────────────────────────────────────────────────────────────────

function ResultPill({ result }: { result: string | null }) {
  const cfg: Record<string, { label: string; color: string; bg: string }> = {
    win:  { label: 'WIN',  color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
    loss: { label: 'LOSS', color: '#ef4444', bg: 'rgba(239,68,68,0.08)'  },
    push: { label: 'PUSH', color: '#f59e0b', bg: 'rgba(245,158,11,0.08)' },
    void: { label: 'VOID', color: '#71717a', bg: 'transparent'           },
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
  if (profit === null || result === null || result === 'pending') return <span style={{ color: '#71717a', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>—</span>
  const units = (profit / 10).toFixed(1)
  const pos = profit >= 0
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: '12px', fontWeight: 600,
      color: pos ? '#10b981' : '#ef4444',
    }}>{pos ? '+' : ''}{units}u</span>
  )
}

function fmtOdds(o: number) {
  return o > 0 ? `+${o}` : `${o}`
}

function fmtDate(d: string) {
  const dt = new Date(d + 'T12:00:00')
  return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function edgeBin(e: number | null) {
  if (e === null) return '—'
  const pct = e * 100
  if (pct >= 10) return '10%+'
  if (pct >= 5)  return '5–10%'
  return '0–5%'
}

// ── Desktop table row ──────────────────────────────────────────────────────

function TableRow({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label = pickLabel(bet)

  // Extract player/pick name — green highlight
  // pickLabel returns things like "Ronald Acuña Jr. — HR Yes" or "Over 7.5 Ks"
  // For player props (HR, K, OUTS) the player name is in bet.player
  const hasPlayer = ['HR', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER'].includes(bet.system)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '80px 65px 130px 1fr 80px 55px 75px 70px 70px',
      alignItems: 'center',
      borderBottom: B,
      minWidth: '860px',
    }}>
      <div className="mono" style={{ padding: '10px 12px', fontSize: '11px', color: '#71717a' }}>{fmtDate(bet.game_date)}</div>

      <div style={{ padding: '10px 8px' }}>
        <span style={{
          padding: '2px 7px', borderRadius: '2px', fontSize: '10px',
          fontFamily: 'var(--font-mono)', letterSpacing: '0.06em', fontWeight: 600,
          color: pill.color, background: pill.bg, border: `0.5px solid ${pill.color}33`,
        }}>{bet.system}</span>
      </div>

      <div style={{ padding: '10px 8px', fontSize: '12px', color: '#a1a1aa', fontFamily: 'var(--font-mono)' }}>
        {awayAbbr} @ {homeAbbr}
      </div>

      <div style={{ padding: '10px 8px' }}>
        {hasPlayer && bet.player ? (
          <span style={{ fontSize: '12px', fontWeight: 600, color: '#10b981' }}>{bet.player}</span>
        ) : (
          <span style={{ fontSize: '12px', color: '#f5f5f7' }}>{label}</span>
        )}
        {hasPlayer && (
          <span style={{ fontSize: '11px', color: '#71717a', marginLeft: '6px', fontFamily: 'var(--font-mono)' }}>
            {label.replace(bet.player ?? '', '').trim().replace(/^[—–-]\s*/, '')}
          </span>
        )}
      </div>

      <div className="mono" style={{ padding: '10px 8px', fontSize: '12px', color: '#f5f5f7' }}>{fmtOdds(bet.odds)}</div>
      <div className="mono" style={{ padding: '10px 8px', fontSize: '11px', color: '#a1a1aa' }}>{(bet.edge !== null && bet.edge !== undefined ? (bet.edge * 100).toFixed(1) + "%" : "—")}</div>
      <div className="mono" style={{ padding: '10px 8px', fontSize: '11px', color: '#a1a1aa' }}>{bet.book ?? '—'}</div>

      <div style={{ padding: '10px 8px' }}><ResultPill result={bet.result} /></div>
      <div style={{ padding: '10px 8px' }}><PnL profit={bet.profit ?? null} result={bet.result} /></div>
    </div>
  )
}

// ── Mobile card ────────────────────────────────────────────────────────────

function BetCard({ bet }: { bet: Bet }) {
  const pill = SYSTEM_PILL[bet.system as keyof typeof SYSTEM_PILL] ?? SYSTEM_PILL.ALL
  const awayAbbr = TEAM_ABBREV[bet.away_team ?? ''] ?? bet.away_team ?? '?'
  const homeAbbr = TEAM_ABBREV[bet.home_team ?? ''] ?? bet.home_team ?? '?'
  const label = pickLabel(bet)
  const hasPlayer = ['HR', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'PITCHER_ER'].includes(bet.system)

  return (
    <div style={{
      background: '#111114',
      border: B,
      borderRadius: '3px',
      padding: '12px 14px',
      display: 'flex',
      flexDirection: 'column',
      gap: '10px',
    }}>
      {/* Card header: date + system pill + result */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            padding: '2px 7px', borderRadius: '2px', fontSize: '10px',
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

      {/* Pick — player name in green */}
      <div>
        {hasPlayer && bet.player ? (
          <div>
            <span style={{ fontSize: '14px', fontWeight: 700, color: '#10b981' }}>{bet.player}</span>
            <span style={{ fontSize: '12px', color: '#a1a1aa', marginLeft: '6px', fontFamily: 'var(--font-mono)' }}>
              {label.replace(bet.player ?? '', '').trim().replace(/^[—–-]\s*/, '')}
            </span>
          </div>
        ) : (
          <span style={{ fontSize: '14px', fontWeight: 600, color: '#f5f5f7' }}>{label}</span>
        )}
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
        <div>
          <div style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', marginBottom: '2px' }}>ODDS</div>
          <div className="mono" style={{ fontSize: '13px', color: '#f5f5f7', fontWeight: 600 }}>{fmtOdds(bet.odds)}</div>
        </div>
        <div>
          <div style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', marginBottom: '2px' }}>EDGE</div>
          <div className="mono" style={{ fontSize: '13px', color: '#a1a1aa' }}>{(bet.edge !== null && bet.edge !== undefined ? (bet.edge * 100).toFixed(1) + "%" : "—")}</div>
        </div>
        <div>
          <div style={{ fontSize: '9px', fontFamily: 'var(--font-mono)', color: '#71717a', letterSpacing: '0.08em', marginBottom: '2px' }}>BOOK</div>
          <div className="mono" style={{ fontSize: '13px', color: '#a1a1aa' }}>{bet.book ?? '—'}</div>
        </div>
      </div>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────

interface PicksTableProps {
  bets: Bet[]
}

export function PicksTable({ bets }: PicksTableProps) {
  if (bets.length === 0) {
    return (
      <div style={{ padding: '48px 24px', textAlign: 'center', color: '#71717a', fontFamily: 'var(--font-mono)', fontSize: '12px' }}>
        No picks match the selected filters.
      </div>
    )
  }

  return (
    <>
      {/* ── Desktop table (hidden on mobile) ── */}
      <div className="picks-desktop">
        {/* Header */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '80px 65px 130px 1fr 80px 55px 75px 70px 70px',
          borderBottom: B,
          minWidth: '860px',
        }}>
          {['DATE','SYSTEM','GAME','PICK','ODDS','EDGE','BOOK','RESULT','P&L'].map(h => (
            <div key={h} style={{
              padding: '8px 12px',
              fontSize: '9px', fontFamily: 'var(--font-mono)',
              letterSpacing: '0.1em', color: '#71717a',
            }}>{h}</div>
          ))}
        </div>
        {/* Scrollable body */}
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
          {bets.map(bet => <TableRow key={bet.id} bet={bet} />)}
        </div>
      </div>

      {/* ── Mobile cards (hidden on desktop) ── */}
      <div className="picks-mobile">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', padding: '12px' }}>
          {bets.map(bet => <BetCard key={bet.id} bet={bet} />)}
        </div>
      </div>
    </>
  )
}
