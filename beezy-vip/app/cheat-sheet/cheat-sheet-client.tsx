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
  const color = SYSTEM_COLOR[bet.system] ?? '#888890'
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

  return (
    <div
      onClick={onToggle}
      className="card-hover"
      style={{
        background: '#111114',
        borderBottom: '1px solid #000',
        borderLeft: `3px solid ${tierColor}`,
        display: 'flex',
        alignItems: 'stretch',
        minHeight: '104px',
        overflow: 'hidden',
        position: 'relative',
        cursor: 'pointer',
      }}
    >
      <div style={{
        width: '86px',
        minWidth: '86px',
        position: 'relative',
        overflow: 'hidden',
        background: `${color}18`,
        borderRight: `1px solid ${color}44`,
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
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
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
          <span className="dell-heading" style={{
            fontSize: '8px',
            letterSpacing: '0.1em',
            padding: '2px 6px',
            background: `${color}20`,
            color,
            border: `1px solid ${color}`,
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
          color: '#888890',
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
                <span className="mono" style={{ fontSize: '9px', color: '#888890', lineHeight: '14px' }}>{b}</span>
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

function YesterdayRow({ bet }: { bet: EnrichedBet }) {
  const isWin = bet.result === 'win'
  const label = pickLabel(bet)
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '6px 0',
      borderBottom: '1px solid #1f1f24',
    }}>
      <span className="dell-heading" style={{
        fontSize: '8px',
        letterSpacing: '0.06em',
        padding: '2px 5px',
        background: isWin ? '#b3bd9520' : '#d77a7a20',
        color: isWin ? '#b3bd95' : '#d77a7a',
        border: `1px solid ${isWin ? '#b3bd95' : '#d77a7a'}`,
        flexShrink: 0,
      }}>
        {isWin ? 'W' : 'L'}
      </span>
      <span className="mono" style={{ fontSize: '9px', color: '#888890', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {label}
      </span>
    </div>
  )
}

export function CheatSheetClient({
  picks,
  today,
  yesterdayPicks = [],
}: {
  picks: EnrichedBet[]
  today: string
  yesterdayPicks?: EnrichedBet[]
}) {
  const [filter, setFilter] = useState<FilterKey>('all')
  const [expandedIds, setExpandedIds] = useState<Set<number>>(() => new Set())
  const [showMore, setShowMore] = useState(false)
  const [copied, setCopied] = useState(false)

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

  const ydayWins = yesterdayPicks.filter(b => b.result === 'win').length
  const ydayLosses = yesterdayPicks.filter(b => b.result === 'loss').length

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

  async function handleShare() {
    const url = 'https://beezy.fyi/cheat-sheet'
    if (typeof navigator !== 'undefined' && navigator.share) {
      try {
        await navigator.share({ title: 'Beezy.FYI MLB Daily Card', url })
      } catch {
        // user cancelled — no-op
      }
    } else {
      try {
        await navigator.clipboard.writeText(url)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
      } catch {
        // clipboard not available
      }
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: '#0a0a0c',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'flex-start',
      padding: '18px 12px 40px',
    }}>
      <div className="cheat-sheet-layout">
        {/* ── Card ─────────────────────────────────────────────────── */}
        <div style={{
          width: '100%',
          maxWidth: '390px',
          border: '1px solid #1f1f24',
          background: '#0a0a0c',
          borderRadius: 0,
          overflow: 'hidden',
          flexShrink: 0,
        }}>
          {/* Header */}
          <div style={{
            background: '#0f1a14',
            padding: '16px 16px 12px',
            borderBottom: '1px solid #1f1f24',
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '10px', gap: '12px' }}>
              <div>
                <div className="dell-display" style={{ fontSize: '22px', color: '#f5f5f7', lineHeight: 1 }}>
                  BEEZY<span style={{ color: '#fcc20f' }}>.FYI</span>
                </div>
                <div style={{ fontSize: '15px', fontWeight: 800, color: '#f5f5f7', lineHeight: 1.15, marginTop: '8px' }}>
                  MLB Daily Card
                </div>
                <div className="mono" style={{ fontSize: '9px', color: '#888890', letterSpacing: '0.1em', marginTop: '5px' }}>
                  {today}
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '6px' }}>
                <div className="dell-heading" style={{
                  fontSize: '8px',
                  letterSpacing: '0.12em',
                  color: '#fcc20f',
                  padding: '4px 8px',
                  border: '1px solid #000',
                  background: '#000',
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
                    border: '1px solid #1f1f24',
                    borderRadius: 0,
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
                  <span className="mono" style={{ fontSize: '9px', color: '#888890', letterSpacing: '0.08em' }}>
                    TOP PLAY
                  </span>
                  <span style={{ fontSize: '13px', color: '#f5f5f7', fontWeight: 700, lineHeight: 1.25, maxWidth: '245px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {pickLabel(filtered[0])}
                  </span>
                </div>
              ) : (
                <p style={{ fontSize: '12px', color: '#888890', lineHeight: 1.45, maxWidth: '245px' }}>
                  No card posted yet.
                </p>
              )}
              <button
                onClick={toggleAll}
                className="mono"
                style={{
                  fontSize: '8px',
                  letterSpacing: '0.08em',
                  color: allExpanded ? '#a5b8c0' : '#888890',
                  background: 'transparent',
                  border: `1px solid ${allExpanded ? '#a5b8c0' : '#1f1f24'}`,
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
                    border: `1px solid ${filter === f.key ? '#a5b8c0' : '#1f1f24'}`,
                    background: filter === f.key ? '#131a1e' : 'transparent',
                    color: filter === f.key ? '#a5b8c0' : '#888890',
                    letterSpacing: '0.06em',
                    textTransform: 'uppercase',
                  }}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </div>

          {/* Stats bar */}
          {filtered.length > 0 && (
            <div style={{
              padding: '7px 16px',
              background: '#0a0a0c',
              borderBottom: '1px solid #1f1f24',
              display: 'flex',
              flexWrap: 'wrap',
              gap: '10px',
              alignItems: 'center',
            }}>
              <span className="mono" style={{ fontSize: '9px', color: '#888890', letterSpacing: '0.08em' }}>
                {filtered.length} PICKS
              </span>
              {strongCount > 0 && (
                <span className="dell-heading" style={{ fontSize: '9px', color: '#b3bd95', letterSpacing: '0.06em' }}>
                  {strongCount} STRONG
                </span>
              )}
              {leanCount > 0 && (
                <span className="dell-heading" style={{ fontSize: '9px', color: '#fcc20f', letterSpacing: '0.06em' }}>
                  {leanCount} LEAN
                </span>
              )}
              {topEdgePct > 0 && (
                <span className="dell-heading" style={{ fontSize: '9px', color: '#c0d4a7', letterSpacing: '0.06em' }}>
                  BEST EDGE +{topEdgePct.toFixed(1)}%
                </span>
              )}
            </div>
          )}

          {/* Column header */}
          <div style={{
            display: 'flex',
            borderBottom: '1px solid #1f1f24',
            background: '#0a0a0c',
            padding: '5px 0',
          }}>
            <div style={{ width: '86px', minWidth: '86px' }} />
            <div className="mono" style={{ flex: 1, paddingLeft: '12px', fontSize: '8px', color: '#888890', letterSpacing: '0.1em' }}>
              PICK / TAP FOR WHY
            </div>
            <div className="mono" style={{ minWidth: '68px', textAlign: 'center', fontSize: '8px', color: '#888890', letterSpacing: '0.1em' }}>
              SCORE
            </div>
          </div>

          {/* Picks or empty state */}
          {filtered.length === 0 ? (
            <div style={{ padding: '20px 16px' }}>
              <div className="mono" style={{ fontSize: '10px', color: '#888890', letterSpacing: '0.08em', marginBottom: '14px', textAlign: 'center' }}>
                NO {filter.toUpperCase()} PICKS YET — NEXT CARD ~11AM ET
              </div>
              {yesterdayPicks.length > 0 && (
                <>
                  <div className="mono" style={{ fontSize: '8px', color: '#52525b', letterSpacing: '0.1em', marginBottom: '6px', textAlign: 'center' }}>
                    YESTERDAY&apos;S RESULTS
                  </div>
                  {yesterdayPicks.slice(0, 3).map(bet => (
                    <YesterdayRow key={bet.id} bet={bet} />
                  ))}
                  {(ydayWins > 0 || ydayLosses > 0) && (
                    <div className="mono" style={{ fontSize: '9px', color: '#888890', textAlign: 'center', marginTop: '10px', letterSpacing: '0.06em' }}>
                      {ydayWins}–{ydayLosses} yesterday
                    </div>
                  )}
                </>
              )}
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
                    color: '#888890',
                    background: '#0a0a0c',
                    border: 'none',
                    borderTop: '1px solid #1f1f24',
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
                    color: '#888890',
                    background: '#0a0a0c',
                    border: 'none',
                    borderTop: '1px solid #1f1f24',
                    cursor: 'pointer',
                  }}
                >
                  SHOW TOP 5
                </button>
              )}
            </>
          )}

          {/* Footer */}
          <div style={{
            borderTop: '1px solid #1f1f24',
            padding: '11px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            background: '#0a0a0c',
          }}>
            <button
              onClick={handleShare}
              className="dell-heading"
              style={{
                fontSize: '9px',
                letterSpacing: '0.08em',
                padding: '6px 12px',
                background: '#fcc20f',
                color: '#000',
                border: '1px solid #000',
                borderRadius: 0,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {copied ? 'COPIED!' : 'SHARE CARD'}
            </button>
            <div style={{ flex: 1 }} />
            <span className="mono" style={{ fontSize: '8px', color: '#52525b', letterSpacing: '0.08em' }}>
              BEEZY.FYI
            </span>
          </div>
        </div>

        {/* ── Desktop sidebar ──────────────────────────────────────── */}
        <aside className="cheat-sheet-sidebar">
          <div style={{
            border: '1px solid #1f1f24',
            background: '#111114',
            padding: '20px',
            marginBottom: '16px',
          }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: '#fcc20f', marginBottom: '12px' }}>
              WHAT IS THIS?
            </div>
            <p className="times" style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.65, marginBottom: '12px' }}>
              The Daily Card surfaces today&apos;s top MLB bets ranked by Beezy Score — a composite of edge, historical system accuracy, and model confidence.
            </p>
            <p className="times" style={{ fontSize: '13px', color: '#a1a1aa', lineHeight: 1.65 }}>
              Cards are published each morning after the model run (~11am ET). Tap any pick to see why the model likes it.
            </p>
          </div>

          <div style={{
            border: '1px solid #1f1f24',
            background: '#111114',
            padding: '20px',
            marginBottom: '16px',
          }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: '#888890', marginBottom: '12px' }}>
              SCORE LEGEND
            </div>
            {[
              { tier: 'strong', color: '#b3bd95', label: 'STRONG', desc: 'High edge + high confidence' },
              { tier: 'lean',   color: '#fcc20f', label: 'LEAN',   desc: 'Positive edge, moderate confidence' },
              { tier: 'pass',   color: '#888890', label: 'PASS',   desc: 'Listed for reference, not recommended' },
            ].map(row => (
              <div key={row.tier} style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                <span className="dell-heading" style={{
                  fontSize: '8px',
                  letterSpacing: '0.08em',
                  padding: '2px 6px',
                  background: `${row.color}20`,
                  color: row.color,
                  border: `1px solid ${row.color}`,
                  flexShrink: 0,
                }}>
                  {row.label}
                </span>
                <span className="mono" style={{ fontSize: '10px', color: '#888890' }}>{row.desc}</span>
              </div>
            ))}
          </div>

          <div style={{
            border: '1px solid #1f1f24',
            background: '#111114',
            padding: '20px',
          }}>
            <div className="dell-heading" style={{ fontSize: '10px', letterSpacing: '0.12em', color: '#888890', marginBottom: '12px' }}>
              SYSTEMS
            </div>
            {[
              { key: 'NRFI', color: '#9ab6c8', desc: 'No Run First Inning — game pick' },
              { key: 'F5',   color: '#e6915d', desc: 'First 5 innings ML — game pick' },
              { key: 'HR',   color: '#d77a7a', desc: 'Home Run — batter prop' },
              { key: 'K',    color: '#c0d4a7', desc: 'Strikeouts Over — pitcher prop' },
              { key: 'OUTS', color: '#a5b8c0', desc: 'Outs Recorded — pitcher prop' },
              { key: 'HITS', color: '#b3bd95', desc: 'Batter Hits Over — batter prop' },
            ].map(row => (
              <div key={row.key} style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '7px' }}>
                <span className="mono" style={{ fontSize: '9px', color: row.color, width: '36px', flexShrink: 0 }}>{row.key}</span>
                <span className="mono" style={{ fontSize: '10px', color: '#888890' }}>{row.desc}</span>
              </div>
            ))}
          </div>
        </aside>
      </div>

      <a
        href="/picks"
        className="mono cheat-sheet-picks-link"
        style={{
          marginTop: '16px',
          fontSize: '10px',
          color: '#888890',
          textDecoration: 'none',
          letterSpacing: '0.06em',
        }}
      >
        FULL PICKS TABLE →
      </a>
    </div>
  )
}
