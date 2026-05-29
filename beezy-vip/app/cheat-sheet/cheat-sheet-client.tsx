'use client'

import { useMemo, useState } from 'react'
import type { Bet } from '@/lib/types'
import { beezyscore, scoreTier, TIER_COLOR } from '@/lib/beezy-score'
import { pickLabel, SYSTEM_COLOR } from '@/lib/tokens'
import { ScoreBadge } from '@/components/ui/primitives'

const SYSTEM_LABEL: Record<string, string> = {
  NRFI: 'NRFI',
  HR: 'HR',
  F5: 'F5',
  K: 'K',
  OUTS: 'OUTS',
  BATTER_HITS: 'HITS',
}

const GAME_SYSTEMS = new Set(['NRFI', 'F5'])
const PITCHER_SYSTEMS = new Set(['K', 'OUTS'])
const PLAYER_SYSTEMS = new Set(['HR', 'BATTER_HITS'])

type FilterKey = 'all' | 'game' | 'pitcher' | 'player'

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'game', label: 'Game' },
  { key: 'pitcher', label: 'Pitchers' },
  { key: 'player', label: 'Players' },
]

const DEFAULT_LIMIT = 5

export interface EnrichedBet extends Bet {
  headshotUrl: string | null
  awayLogoUrl: string | null
  homeLogoUrl: string | null
}

function filterBets(bets: EnrichedBet[], key: FilterKey): EnrichedBet[] {
  if (key === 'game') return bets.filter(b => GAME_SYSTEMS.has(b.system))
  if (key === 'pitcher') return bets.filter(b => PITCHER_SYSTEMS.has(b.system))
  if (key === 'player') return bets.filter(b => PLAYER_SYSTEMS.has(b.system))
  return bets
}

function fmtOdds(o: number) {
  return o > 0 ? `+${o}` : String(o)
}

function fmtEdge(e: number | null) {
  if (e == null) return null
  const pct = Math.abs(e) < 2 ? e * 100 : e
  return `+${pct.toFixed(1)}%`
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

function PickCard({ bet, rank, expanded, onToggle }: {
  bet: EnrichedBet
  rank: number
  expanded: boolean
  onToggle: () => void
}) {
  const color = SYSTEM_COLOR[bet.system] ?? '#71717a'
  const isProp = PLAYER_SYSTEMS.has(bet.system) || PITCHER_SYSTEMS.has(bet.system)
  const isGame = GAME_SYSTEMS.has(bet.system)
  const edge = fmtEdge(bet.edge)
  const bullets = splitNotes(bet.notes)
  const tier = scoreTier(beezyscore(bet))
  const tierColor = TIER_COLOR[tier]
  const title = isProp
    ? (bet.player ?? bet.bet_type ?? 'No player listed')
    : `${bet.away_team ?? '?'} @ ${bet.home_team ?? '?'}`
  const sub = [bet.bet_type, fmtOdds(bet.odds), bet.book].filter(Boolean).join(' / ')

  const tierGlow: Record<string, string> = {
    strong: `0 0 0 1px ${tierColor}30, 0 0 18px ${tierColor}14`,
    lean: `0 0 0 1px ${tierColor}24, 0 0 14px ${tierColor}0f`,
    watch: 'var(--shadow-card)',
  }

  return (
    <div
      onClick={onToggle}
      className="card-hover"
      style={{
        background: '#0d0d12',
        borderBottom: '1px solid #181820',
        borderLeft: `3px solid ${tierColor}66`,
        display: 'flex',
        alignItems: 'stretch',
        minHeight: '104px',
        overflow: 'hidden',
        position: 'relative',
        boxShadow: tierGlow[tier],
        cursor: 'pointer',
      }}
    >
      <div style={{
        width: '86px',
        minWidth: '86px',
        position: 'relative',
        overflow: 'hidden',
        background: `linear-gradient(145deg, ${color}26 0%, #08080d 74%)`,
        borderRight: `1px solid ${color}22`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}>
        <span className="mono" style={{
          position: 'absolute',
          top: '6px',
          left: '7px',
          fontSize: '8px',
          color: `${color}99`,
          fontWeight: 800,
        }}>
          #{rank}
        </span>

        {isProp && bet.headshotUrl ? (
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
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', zIndex: 1 }}>
            {bet.awayLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={bet.awayLogoUrl} alt={bet.away_team ?? ''} width={32} height={32} style={{ objectFit: 'contain' }} />
            )}
            <span className="mono" style={{ fontSize: '7px', color: `${color}66` }}>@</span>
            {bet.homeLogoUrl && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={bet.homeLogoUrl} alt={bet.home_team ?? ''} width={32} height={32} style={{ objectFit: 'contain' }} />
            )}
          </div>
        ) : (
          <span className="mono" style={{ fontSize: '13px', color, fontWeight: 800, zIndex: 1 }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
        )}
      </div>

      <div style={{
        flex: 1,
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        minWidth: 0,
      }}>
        <div style={{ marginBottom: '5px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <span className="mono" style={{
            fontSize: '8px',
            fontWeight: 800,
            letterSpacing: '0.1em',
            padding: '2px 6px',
            background: `${color}20`,
            color,
            border: `1px solid ${color}44`,
            borderRadius: 'var(--radius-sm)',
          }}>
            {SYSTEM_LABEL[bet.system] ?? bet.system}
          </span>
          {edge && (
            <span className="mono" style={{ fontSize: '10px', fontWeight: 800, color: tierColor, letterSpacing: '0.04em' }}>
              {edge} EDGE
            </span>
          )}
        </div>

        <div style={{
          fontSize: '15px',
          fontWeight: 800,
          color: '#f5f5f7',
          lineHeight: 1.12,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          marginBottom: '3px',
        }}>
          {title}
        </div>

        <div className="mono" style={{
          fontSize: '10px',
          color: '#626274',
          marginBottom: (expanded && bullets.length) ? '6px' : 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          {sub}
        </div>

        {expanded && bullets.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {bullets.slice(0, 3).map((b, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: '5px' }}>
                <span style={{ color, fontSize: '9px', lineHeight: '14px', flexShrink: 0 }}>{'>'}</span>
                <span className="mono" style={{ fontSize: '9px', color: '#797991', lineHeight: '14px' }}>{b}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div style={{
        minWidth: '68px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        borderLeft: `1px solid ${color}18`,
        padding: '8px 6px',
        background: `${tierColor}08`,
      }}>
        <ScoreBadge bet={bet} />
      </div>
    </div>
  )
}

export function CheatSheetClient({
  picks,
  today,
}: {
  picks: EnrichedBet[]
  today: string
}) {
  const [filter, setFilter] = useState<FilterKey>('all')
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set())
  const [showMore, setShowMore] = useState(false)

  const sorted = useMemo(() => [...picks].sort((a, b) => beezyscore(b) - beezyscore(a)), [picks])
  const filtered = filterBets(sorted, filter)
  const displayed = showMore ? filtered : filtered.slice(0, DEFAULT_LIMIT)
  const hiddenCount = filtered.length - DEFAULT_LIMIT
  const allExpanded = filtered.length > 0 && filtered.every(b => expandedIds.has(b.id))
  const strongCount = filtered.filter(b => scoreTier(beezyscore(b)) === 'strong').length
  const leanCount = filtered.filter(b => scoreTier(beezyscore(b)) === 'lean').length
  const topEdgePct = filtered.length > 0
    ? Math.max(...filtered.map(b => b.edge != null ? (Math.abs(b.edge) < 2 ? b.edge * 100 : b.edge) : 0))
    : 0

  function toggleCard(id: number) {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    setExpandedIds(allExpanded ? new Set() : new Set(filtered.map(b => b.id)))
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#07070b',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'flex-start',
      padding: '18px 12px 40px',
      fontFamily: 'Inter, -apple-system, sans-serif',
    }}>
      <div style={{
        width: '100%',
        maxWidth: '390px',
        border: '1px solid #23232d',
        background: '#08080d',
        borderRadius: 'var(--radius-lg)',
        boxShadow: 'var(--shadow-card)',
        overflow: 'hidden',
      }}>
        <div style={{
          background: 'linear-gradient(135deg, #0f2017 0%, #09090f 64%)',
          padding: '16px 16px 12px',
          borderBottom: '1px solid #1a1a22',
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '12px' }}>
            <div>
              <div style={{ fontSize: '22px', fontWeight: 850, color: '#f5f5f7', lineHeight: 1 }}>
                BEEZY<span style={{ color: '#10b981' }}>.FYI</span>
              </div>
              <div style={{ fontSize: '15px', fontWeight: 800, color: '#f5f5f7', lineHeight: 1.15, marginTop: '8px' }}>
                MLB Daily Card
              </div>
              <div className="mono" style={{ fontSize: '9px', color: '#737383', letterSpacing: '0.1em', marginTop: '5px' }}>
                {today}
              </div>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
              <div className="mono" style={{
                fontSize: '8px',
                fontWeight: 800,
                letterSpacing: '0.12em',
                color: '#10b981',
                padding: '4px 8px',
                border: '1px solid #0f6e5644',
                background: '#052016',
                borderRadius: 'var(--radius-sm)',
              }}>
                DAILY CARD
              </div>
              <div
                className="mono"
                style={{
                  fontSize: '8px',
                  fontWeight: 800,
                  letterSpacing: '0.08em',
                  color: '#f5f5f7',
                  background: '#111114',
                  border: '1px solid #2a2a36',
                  borderRadius: 'var(--radius-sm)',
                  padding: '5px 9px',
                }}
              >
                beezy.fyi
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '10px' }}>
            {filtered.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}>
                <span className="mono" style={{ fontSize: '9px', color: '#737383', letterSpacing: '0.08em' }}>
                  TOP PLAY
                </span>
                <span style={{ fontSize: '13px', color: '#d4d4d8', fontWeight: 700, lineHeight: 1.25, maxWidth: '245px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {pickLabel(filtered[0])}
                </span>
              </div>
            ) : (
              <p style={{ fontSize: '12px', color: '#a1a1aa', lineHeight: 1.45, maxWidth: '245px' }}>
                No card posted yet.
              </p>
            )}
            <button
              onClick={toggleAll}
              className="mono"
              style={{
                fontSize: '8px',
                letterSpacing: '0.08em',
                color: allExpanded ? '#10b981' : '#737383',
                background: 'transparent',
                border: `1px solid ${allExpanded ? '#0f6e5644' : '#2a2a36'}`,
                borderRadius: 'var(--radius-sm)',
                padding: '4px 7px',
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {allExpanded ? 'COLLAPSE' : 'EXPAND'}
            </button>
          </div>

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px' }}>
            {FILTERS.map(f => (
              <button
                key={f.key}
                onClick={() => { setFilter(f.key); setShowMore(false); setExpandedIds(new Set()) }}
                className="mono"
                style={{
                  padding: '5px 10px',
                  fontSize: '9px',
                  cursor: 'pointer',
                  border: `0.5px solid ${filter === f.key ? '#10b981' : '#2a2a36'}`,
                  borderRadius: 'var(--radius-sm)',
                  background: filter === f.key ? '#10b98118' : 'transparent',
                  color: filter === f.key ? '#10b981' : '#737383',
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {filtered.length > 0 && (
          <div style={{
            padding: '7px 16px',
            background: '#0a0a0f',
            borderBottom: '1px solid #1a1a22',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '10px',
            alignItems: 'center',
          }}>
            <span className="mono" style={{ fontSize: '9px', color: '#737383', letterSpacing: '0.08em' }}>
              {filtered.length} PICKS
            </span>
            {strongCount > 0 && (
              <span className="mono" style={{ fontSize: '9px', color: '#22c55e', letterSpacing: '0.06em', fontWeight: 800 }}>
                {strongCount} STRONG
              </span>
            )}
            {leanCount > 0 && (
              <span className="mono" style={{ fontSize: '9px', color: '#facc15', letterSpacing: '0.06em', fontWeight: 700 }}>
                {leanCount} LEAN
              </span>
            )}
            {topEdgePct > 0 && (
              <span className="mono" style={{ fontSize: '9px', color: '#10b981', letterSpacing: '0.06em' }}>
                BEST EDGE +{topEdgePct.toFixed(1)}%
              </span>
            )}
          </div>
        )}

        <div style={{
          display: 'flex',
          borderBottom: '1px solid #1a1a22',
          background: '#0a0a0f',
          padding: '5px 0',
        }}>
          <div style={{ width: '86px', minWidth: '86px' }} />
          <div className="mono" style={{ flex: 1, paddingLeft: '12px', fontSize: '8px', color: '#3f3f4e', letterSpacing: '0.1em' }}>
            PICK / TAP FOR WHY
          </div>
          <div className="mono" style={{ minWidth: '68px', textAlign: 'center', fontSize: '8px', color: '#3f3f4e', letterSpacing: '0.1em' }}>
            SCORE
          </div>
        </div>

        {filtered.length === 0 ? (
          <div style={{ padding: '28px 20px', textAlign: 'center' }}>
            <div className="mono" style={{ fontSize: '10px', color: '#737383', letterSpacing: '0.08em', marginBottom: '6px' }}>
              NO {filter.toUpperCase()} PICKS TODAY
            </div>
            <p style={{ fontSize: '12px', color: '#52525b', lineHeight: 1.5 }}>
              The next card appears after the model run.
            </p>
          </div>
        ) : (
          <>
            {displayed.map((bet, i) => (
              <PickCard
                key={bet.id}
                bet={bet}
                rank={i + 1}
                expanded={expandedIds.has(bet.id)}
                onToggle={() => toggleCard(bet.id)}
              />
            ))}

            {!showMore && hiddenCount > 0 && (
              <button
                onClick={() => setShowMore(true)}
                className="mono"
                style={{
                  width: '100%',
                  padding: '10px',
                  fontSize: '10px',
                  letterSpacing: '0.08em',
                  color: '#737383',
                  background: '#0a0a0f',
                  border: 'none',
                  borderTop: '1px solid #1a1a22',
                  cursor: 'pointer',
                }}
              >
                + {hiddenCount} MORE PICKS
              </button>
            )}
            {showMore && filtered.length > DEFAULT_LIMIT && (
              <button
                onClick={() => setShowMore(false)}
                className="mono"
                style={{
                  width: '100%',
                  padding: '10px',
                  fontSize: '10px',
                  letterSpacing: '0.08em',
                  color: '#737383',
                  background: '#0a0a0f',
                  border: 'none',
                  borderTop: '1px solid #1a1a22',
                  cursor: 'pointer',
                }}
              >
                SHOW TOP 5
              </button>
            )}
          </>
        )}

        <div style={{
          borderTop: '1px solid #1a1a22',
          padding: '11px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: '#06060a',
        }}>
          <span className="mono" style={{ fontSize: '8px', color: '#3f3f4e', letterSpacing: '0.08em' }}>
            BEEZY.FYI / MLB DAILY CARD
          </span>
          <span className="mono" style={{ fontSize: '8px', color: '#3f3f4e', letterSpacing: '0.06em' }}>
            beezy.fyi
          </span>
        </div>
      </div>

      <a
        href="/picks"
        className="mono"
        style={{
          marginTop: '16px',
          fontSize: '10px',
          color: '#52525b',
          textDecoration: 'none',
          letterSpacing: '0.06em',
        }}
      >
        FULL PICKS TABLE
      </a>
    </div>
  )
}
