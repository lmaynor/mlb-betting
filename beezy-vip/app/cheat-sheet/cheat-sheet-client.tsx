'use client'

import { useState } from 'react'
import type { Bet } from '@/lib/types'
import { beezyscore, scoreTier, TIER_COLOR } from '@/lib/beezy-score'

// ── Constants ─────────────────────────────────────────────────────────────────

const SYSTEM_COLOR: Record<string, string> = {
  NRFI: '#10b981', HR: '#f59e0b', F5: '#3b82f6',
  K: '#a78bfa', OUTS: '#fb923c', BATTER_HITS: '#e879f9',
}
const SYSTEM_LABEL: Record<string, string> = {
  NRFI: 'NRFI', HR: 'HR', F5: 'F5', K: 'K', OUTS: 'OUTS', BATTER_HITS: 'HITS',
}

const GAME_SYSTEMS     = new Set(['NRFI', 'F5'])
const PITCHER_SYSTEMS  = new Set(['K', 'OUTS'])
const PLAYER_SYSTEMS   = new Set(['HR', 'BATTER_HITS'])

type FilterKey = 'all' | 'game' | 'pitcher' | 'player'

const FILTERS: { key: FilterKey; label: string; sub: string }[] = [
  { key: 'all',     label: 'All Picks',     sub: 'Every system today' },
  { key: 'game',    label: 'Game Picks',    sub: 'NRFI · F5 · Totals' },
  { key: 'pitcher', label: 'Pitcher Props', sub: 'Strikeouts · Outs' },
  { key: 'player',  label: 'Player Props',  sub: 'HR · Hits · Bases' },
]

function filterBets(bets: EnrichedBet[], key: FilterKey): EnrichedBet[] {
  if (key === 'game')    return bets.filter(b => GAME_SYSTEMS.has(b.system))
  if (key === 'pitcher') return bets.filter(b => PITCHER_SYSTEMS.has(b.system))
  if (key === 'player')  return bets.filter(b => PLAYER_SYSTEMS.has(b.system))
  return bets
}

function fmtOdds(o: number) { return o > 0 ? `+${o}` : String(o) }

function fmtEdge(e: number | null) {
  if (e == null) return null
  const pct = Math.abs(e) < 2 ? e * 100 : e   // handle both fraction and % form
  return `+${pct.toFixed(1)}%`
}

// ── Enriched bet type (URLs resolved server-side) ─────────────────────────────

export interface EnrichedBet extends Bet {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
}

// ── Pick card ─────────────────────────────────────────────────────────────────

function PickCard({ bet, rank, showRationale }: {
  bet: EnrichedBet
  rank: number
  showRationale: boolean
}) {
  const color   = SYSTEM_COLOR[bet.system] ?? '#71717a'
  const isProp  = PLAYER_SYSTEMS.has(bet.system) || PITCHER_SYSTEMS.has(bet.system)
  const isGame  = GAME_SYSTEMS.has(bet.system)
  const edge    = fmtEdge(bet.edge)
  const bullets = bet.notes ? bet.notes.split(' · ').filter(Boolean) : []

  const tier      = scoreTier(beezyscore(bet))
  const tierColor = TIER_COLOR[tier]

  const tierGlow: Record<string, string> = {
    strong: `0 0 0 1px ${tierColor}28, 0 0 16px ${tierColor}12`,
    lean:   `0 0 0 1px ${tierColor}20, 0 0 12px ${tierColor}0a`,
    watch:  'var(--shadow-card)',
  }

  const title = isProp
    ? (bet.player ?? bet.bet_type ?? '—')
    : `${bet.away_team ?? '?'} @ ${bet.home_team ?? '?'}`

  const sub = [bet.bet_type, fmtOdds(bet.odds)].filter(Boolean).join(' · ')

  return (
    <div style={{
      background: '#0c0c12',
      borderBottom: '1px solid #16161e',
      borderLeft: `3px solid ${tierColor}55`,
      display: 'flex',
      alignItems: 'stretch',
      minHeight: '100px',
      overflow: 'hidden',
      position: 'relative',
      boxShadow: tierGlow[tier],
    }}>
      {/* Left panel — gradient bg + portrait or logos */}
      <div style={{
        width: '86px', minWidth: '86px',
        position: 'relative',
        overflow: 'hidden',
        background: `linear-gradient(135deg, ${color}28 0%, #09090f 70%)`,
        borderRight: `1px solid ${color}22`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        {/* Rank badge — top-left corner */}
        <span style={{
          position: 'absolute', top: '5px', left: '6px',
          fontFamily: 'monospace', fontSize: '8px', color: `${color}88`, fontWeight: 700,
        }}>
          #{rank}
        </span>

        {isProp && bet.headshotUrl ? (
          // Contained portrait — shows full head/shoulders, transparent bg shows gradient
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={bet.headshotUrl}
            alt={bet.player ?? ''}
            width={72}
            height={88}
            style={{
              objectFit: 'contain',
              objectPosition: 'bottom center',
              width: '72px',
              height: '88px',
              position: 'absolute',
              bottom: 0,
              left: '50%',
              transform: 'translateX(-50%)',
            }}
          />
        ) : isGame && (bet.awayLogoUrl || bet.homeLogoUrl) ? (
          // Two team logos stacked
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', zIndex: 1 }}>
            {bet.awayLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={bet.awayLogoUrl} alt={bet.away_team ?? ''} width={32} height={32} style={{ objectFit: 'contain' }} />
            )}
            <span style={{ fontFamily: 'monospace', fontSize: '7px', color: `${color}66` }}>@</span>
            {bet.homeLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={bet.homeLogoUrl} alt={bet.home_team ?? ''} width={32} height={32} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          // Fallback: system label
          <span style={{ fontFamily: 'monospace', fontSize: '13px', color, fontWeight: 800, zIndex: 1 }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
        )}

        {/* Subtle bottom fade */}
        <div style={{
          position: 'absolute', bottom: 0, left: 0, right: 0, height: '20px',
          background: 'linear-gradient(to top, #0c0c12cc, transparent)',
          zIndex: 1,
        }} />
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: '10px 12px', display: 'flex', flexDirection: 'column', justifyContent: 'center', minWidth: 0 }}>
        {/* System badge */}
        <div style={{ marginBottom: '4px' }}>
          <span style={{
            fontFamily: 'monospace', fontSize: '8px', fontWeight: 700,
            letterSpacing: '0.1em', padding: '2px 6px',
            background: `${color}20`, color, border: `1px solid ${color}44`,
            borderRadius: 'var(--radius-sm)',
          }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
        </div>

        {/* Player / matchup name */}
        <div style={{
          fontSize: '15px', fontWeight: 800, color: '#f5f5f7',
          letterSpacing: '-0.01em', lineHeight: 1.1,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          marginBottom: '3px',
        }}>
          {title}
        </div>

        {/* Bet type + odds */}
        <div style={{ fontFamily: 'monospace', fontSize: '10px', color: '#44445a', marginBottom: (showRationale && bullets.length) ? '6px' : 0 }}>
          {sub}
        </div>

        {/* Rationale bullets */}
        {showRationale && bullets.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {bullets.slice(0, 3).map((b, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px' }}>
                <span style={{ color, fontSize: '8px', lineHeight: '14px', flexShrink: 0 }}>▸</span>
                <span style={{ fontFamily: 'monospace', fontSize: '9px', color: '#6a6a8a', lineHeight: '14px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Edge panel */}
      {edge && (
        <div style={{
          minWidth: '56px', display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          borderLeft: `1px solid ${color}18`, padding: '8px 6px',
          background: `${color}08`,
        }}>
          <span style={{ fontFamily: 'monospace', fontSize: '7px', color: `${color}55`, letterSpacing: '0.1em', marginBottom: '3px' }}>EDGE</span>
          <span style={{ fontFamily: 'monospace', fontSize: '13px', fontWeight: 800, color }}>{edge}</span>
        </div>
      )}
    </div>
  )
}

// ── Main client component ─────────────────────────────────────────────────────

const DEFAULT_LIMIT = 5

export function CheatSheetClient({
  picks,
  today,
}: {
  picks: EnrichedBet[]
  today: string
}) {
  const [filter, setFilter]         = useState<FilterKey>('all')
  const [showRationale, setRationale] = useState(true)
  const [expanded, setExpanded]     = useState(false)

  const filtered = filterBets(picks, filter)
  const displayed = expanded ? filtered : filtered.slice(0, DEFAULT_LIMIT)
  const hiddenCount = filtered.length - DEFAULT_LIMIT

  const activeFilter = FILTERS.find(f => f.key === filter)!

  return (
    <div style={{
      minHeight: '100vh',
      background: '#06060a',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'flex-start',
      padding: '20px 12px 40px',
      fontFamily: 'Inter, -apple-system, sans-serif',
    }}>
      {/* Card */}
      <div style={{
        width: '100%',
        maxWidth: '390px',
        border: '1px solid #22222e',
        background: '#08080d',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-card)',
        overflow: 'hidden',
      }}>

        {/* Header */}
        <div style={{
          background: 'linear-gradient(135deg, #0d1a12 0%, #09090f 60%)',
          padding: '18px 16px 12px',
          borderBottom: '1px solid #1a1a22',
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '6px' }}>
            <div>
              <div style={{ fontSize: '22px', fontWeight: 800, letterSpacing: '-0.02em', color: '#f5f5f7', lineHeight: 1 }}>
                BEEZY<span style={{ color: '#10b981' }}>.VIP</span>
              </div>
              <div style={{ fontFamily: 'monospace', fontSize: '9px', color: '#52525b', letterSpacing: '0.1em', marginTop: '4px' }}>
                {today}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
              <div style={{
                fontFamily: 'monospace', fontSize: '8px', fontWeight: 600,
                letterSpacing: '0.12em', color: '#10b981', padding: '4px 8px',
                border: '1px solid #0f6e5644', background: '#052016',
              }}>
                CHEAT SHEET
              </div>
              {/* Rationale toggle */}
              <button
                onClick={() => setRationale(r => !r)}
                style={{
                  fontFamily: 'monospace', fontSize: '8px', letterSpacing: '0.08em',
                  color: showRationale ? '#10b981' : '#3a3a48',
                  background: 'none', border: `1px solid ${showRationale ? '#0f6e5644' : '#1a1a22'}`,
                  padding: '3px 7px', cursor: 'pointer',
                }}
              >
                {showRationale ? '▸ ANALYSIS ON' : '▸ ANALYSIS OFF'}
              </button>
            </div>
          </div>

          {/* Filter dropdown */}
          <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <select
              value={filter}
              onChange={e => { setFilter(e.target.value as FilterKey); setExpanded(false) }}
              style={{
                fontFamily: 'monospace', fontSize: '10px', letterSpacing: '0.06em',
                color: '#f0f0f5', background: '#0e0e14',
                border: '1px solid #2a2a36', padding: '5px 10px 5px 8px',
                cursor: 'pointer', outline: 'none', flex: 1,
              }}
            >
              {FILTERS.map(f => (
                <option key={f.key} value={f.key}>{f.label}</option>
              ))}
            </select>
            <div style={{ fontFamily: 'monospace', fontSize: '9px', color: '#3a3a48', whiteSpace: 'nowrap' }}>
              {activeFilter.sub}
            </div>
          </div>
        </div>

        {/* Column labels */}
        <div style={{
          display: 'flex', borderBottom: '1px solid #1a1a22',
          background: '#0a0a0f', padding: '5px 0',
        }}>
          <div style={{ width: '26px', minWidth: '26px' }} />
          <div style={{ width: '60px', minWidth: '60px' }} />
          <div style={{ flex: 1, paddingLeft: '11px', fontFamily: 'monospace', fontSize: '8px', color: '#2e2e3a', letterSpacing: '0.1em' }}>
            PICK {showRationale && filtered.length > 0 ? '· ANALYSIS' : ''}
          </div>
          <div style={{ minWidth: '52px', textAlign: 'center', fontFamily: 'monospace', fontSize: '8px', color: '#2e2e3a', letterSpacing: '0.1em' }}>
            EDGE
          </div>
        </div>

        {/* Picks */}
        {filtered.length === 0 ? (
          <div style={{ padding: '40px 24px', textAlign: 'center' }}>
            <div style={{ fontFamily: 'monospace', fontSize: '11px', color: '#2e2e3a', letterSpacing: '0.08em' }}>
              NO {filter.toUpperCase()} PICKS TODAY
            </div>
            <div style={{ fontSize: '12px', color: '#1e1e26', marginTop: '6px' }}>
              Check back after 9 AM ET
            </div>
          </div>
        ) : (
          <div>
            {displayed.map((bet, i) => (
              <PickCard key={bet.id} bet={bet} rank={i + 1} showRationale={showRationale} />
            ))}

            {/* Expand / collapse */}
            {!expanded && hiddenCount > 0 && (
              <button
                onClick={() => setExpanded(true)}
                style={{
                  width: '100%', padding: '10px',
                  fontFamily: 'monospace', fontSize: '10px', letterSpacing: '0.08em',
                  color: '#52525b', background: '#0a0a0f',
                  border: 'none', borderTop: '1px solid #1a1a22',
                  cursor: 'pointer',
                }}
              >
                + {hiddenCount} MORE PICKS
              </button>
            )}
            {expanded && filtered.length > DEFAULT_LIMIT && (
              <button
                onClick={() => setExpanded(false)}
                style={{
                  width: '100%', padding: '10px',
                  fontFamily: 'monospace', fontSize: '10px', letterSpacing: '0.08em',
                  color: '#3a3a48', background: '#0a0a0f',
                  border: 'none', borderTop: '1px solid #1a1a22',
                  cursor: 'pointer',
                }}
              >
                ↑ SHOW TOP 5
              </button>
            )}
          </div>
        )}

        {/* Footer */}
        <div style={{
          borderTop: '1px solid #1a1a22', padding: '11px 16px',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          background: '#06060a',
        }}>
          <span style={{ fontFamily: 'monospace', fontSize: '8px', color: '#1e1e26', letterSpacing: '0.08em' }}>
            BEEZY.VIP · ML PICKS
          </span>
          <span style={{ fontFamily: 'monospace', fontSize: '8px', color: '#1e1e26', letterSpacing: '0.06em' }}>
            discord.gg/HfMYCmbmE
          </span>
        </div>
      </div>

      {/* Outside card */}
      <a href="/picks" style={{
        marginTop: '18px', fontFamily: 'monospace', fontSize: '10px',
        color: '#2e2e3a', textDecoration: 'none', letterSpacing: '0.06em',
      }}>
        ← FULL PICKS TABLE
      </a>
    </div>
  )
}
