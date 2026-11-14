'use client'

import React, { useMemo, useState, useEffect } from 'react'
import { americanToDecimal } from '@/lib/ev-math'
import { formatOdds } from '@/lib/odds'
import { fairAmericanOf, STAT_LABEL, type BoardRow } from '@/lib/odds-screen'
import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import { BookLogo } from '@/components/ui/book-logo'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import {
  ladderGroupFor,
  type LadderCategory,
  type LadderGroup,
  type LadderRung,
  NOISE_FLOOR,
} from '@/lib/ladders'
import { GREEN, HAIR, PAGE, SKY } from './odds-constants'
import { signed, startLabel } from './odds-format'
import type { OddsScreen } from './use-odds-screen'

function formatRungPillLabel(category: LadderCategory, line: number): string {
  if (category === 'count') {
    if (Math.abs(line % 1) === 0.5) {
      return `${Math.ceil(line)}+`
    }
    return `${line}`
  }
  return `${line}`
}

export function LadderCard({ group }: { group: LadderGroup }) {
  const [selectedIdx, setSelectedIdx] = useState<number>(group.defaultRung ?? 0)

  useEffect(() => {
    setSelectedIdx(group.defaultRung ?? 0)
  }, [group.key, group.defaultRung])

  const rungIndex = selectedIdx >= 0 && selectedIdx < group.rungs.length ? selectedIdx : 0
  const rung = group.rungs[rungIndex]
  if (!rung) return null

  // 1. Header row
  const diffMs = Date.parse(group.commence) - Date.now()
  let countdownStr = ''
  if (!isNaN(diffMs) && diffMs > 0) {
    const totalMins = Math.floor(diffMs / 60000)
    const h = Math.floor(totalMins / 60)
    const m = totalMins % 60
    const timeStr = h > 0 ? `${h}h ${m}m` : `${m}m`
    countdownStr = ` · Kickoff in ${timeStr}`
  }

  const badgeText = rung.state === 'verified'
    ? 'VERIFIED DISLOCATION'
    : rung.state === 'market'
    ? 'MARKET LADDER'
    : group.kind === 'milestone'
    ? 'MILESTONE · THIN'
    : 'THIN LADDER'

  const badgeStyle: React.CSSProperties = {
    fontSize: '11px',
    fontWeight: 600,
    padding: '3px 8px',
    borderRadius: '4px',
    ...(rung.state === 'verified'
      ? { color: GREEN, background: 'rgba(87,242,135,0.08)', border: '1px solid rgba(87,242,135,0.25)' }
      : rung.state === 'market'
      ? { color: SKY, background: 'rgba(56,189,248,0.08)', border: '1px solid rgba(56,189,248,0.25)' }
      : { color: '#9ca3af', background: 'rgba(156,163,175,0.08)', border: '1px solid rgba(156,163,175,0.25)' }),
  }

  // 2. Movement subheader
  let movementText: string | null = null
  if (group.modalOpen != null && group.modalNow != null) {
    if (group.modalOpen === group.modalNow) {
      movementText = `Main: ${group.modalNow} · Unchanged`
    } else {
      const delta = group.modalNow - group.modalOpen
      const deltaStr = Number.isInteger(delta)
        ? (delta > 0 ? `+${delta}` : `${delta}`)
        : (delta > 0 ? `+${delta.toFixed(1)}` : `${delta.toFixed(1)}`)
      movementText = `Main: Open ${group.modalOpen} -> Now ${group.modalNow} (${deltaStr})`
    }
  } else if (group.modalNow != null) {
    movementText = `Main: ${group.modalNow}`
  }

  // 3. Player row & Hero Block
  const hero = rung.hero
  const heroSide = hero?.side ?? 'OVER'
  const heroAm = hero?.american
  const heroPriceStr = heroAm != null ? formatOdds(heroAm) : '--'

  // 4. Book bars for the selected rung
  const visibleBars = rung.books
    .filter(b => {
      const p = heroSide === 'OVER' ? b.over : b.under
      const cap = heroSide === 'OVER' ? b.overCap : b.underCap
      return p != null && !cap
    })
    .map(b => {
      const p = (heroSide === 'OVER' ? b.over : b.under)!
      const otherP = heroSide === 'OVER' ? b.under : b.over
      const otherCap = heroSide === 'OVER' ? b.underCap : b.overCap
      const dec = americanToDecimal(p)
      const netPayout = Math.max(0.01, dec - 1)
      return {
        book: b.book,
        heroAm: p,
        otherAm: otherCap ? null : otherP,
        dec,
        netPayout,
        lagMin: b.lagMin,
        staleBand: b.staleBand,
        staleOpener: b.staleOpener,
      }
    })

  visibleBars.sort((a, b) => b.dec - a.dec)
  const bestNetPayout = visibleBars.length > 0 ? visibleBars[0].netPayout : 1

  return (
    <div style={{ ...SURFACE_CARD, padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
      {/* Header row */}
      <div className="flex justify-between items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1.5">
          <span className="mono" style={{ fontSize: '11px', fontWeight: 600, padding: '2px 6px', borderRadius: '4px', background: 'var(--color-background-tertiary-default)', color: 'var(--color-neutral-400)', border: `1px solid ${HAIR}` }}>
            NFL
          </span>
          <span className="mono text-[12px] text-neutral-300">
            {group.away} @ {group.home} · {startLabel(group.commence)}{countdownStr}
          </span>
        </div>
        <div className="mono" style={badgeStyle}>
          {badgeText}
        </div>
      </div>

      {/* Movement subheader */}
      {movementText != null && (
        <div className="mono text-[11px] text-text-secondary -mt-1.5">
          {movementText}
        </div>
      )}

      {/* Player row */}
      <div className="flex justify-between items-center gap-3">
        <div className="flex items-center gap-2.5">
          <span className="shrink-0 w-9 h-9 inline-flex">
            <PlayerHeadshot sport="nfl" id={group.headshotId} name={group.player} size={36} />
          </span>
          <div>
            <div className="text-[15px] font-semibold text-text-primary leading-[1.25]">{group.player}</div>
            <div className="mono text-compact text-neutral-400 flex items-center gap-1.5 mt-[3px]">
              <span>{STAT_LABEL[group.stat] ?? group.stat}</span>
            </div>
          </div>
        </div>
        <div className="text-right">
          <div
            className="mono"
            style={{
              fontSize: '24px',
              fontWeight: 600,
              color: hero?.verified ? GREEN : 'var(--color-text-primary)',
              letterSpacing: '-0.02em',
              lineHeight: 1,
            }}
          >
            {heroPriceStr}
          </div>
          <div className="mono text-micro text-text-secondary mt-[3px]">
            {heroSide} {rung.line}
          </div>
        </div>
      </div>

      {/* Rung pills row */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {group.rungs.map((r, idx) => {
          const isSel = idx === rungIndex
          const label = formatRungPillLabel(group.category, r.line)
          const anyVerified = Boolean(r.over?.verified || r.under?.verified)
          return (
            <button
              key={r.line}
              type="button"
              onClick={() => setSelectedIdx(idx)}
              style={{
                padding: '4px 8px',
                borderRadius: '6px',
                fontSize: '11px',
                fontFamily: 'var(--font-mono), monospace',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                background: isSel ? 'var(--color-background-tertiary-default)' : 'var(--color-background-secondary-default)',
                color: isSel ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
                border: `1px solid ${isSel ? 'var(--color-border-button-hover)' : HAIR}`,
              }}
            >
              <span>{label}</span>
              {anyVerified && (
                <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: GREEN, display: 'inline-block' }} />
              )}
              {r.flatRung && (
                <span title="Flat rung" className="text-[9px] text-warn ml-0.5">Flat</span>
              )}
            </button>
          )
        })}
      </div>

      {/* Strip (not for milestone kind) */}
      {group.kind !== 'milestone' && (
        <>
          {group.category === 'yardage' && (() => {
            const plotted = group.rungs.filter(r => r.pCons != null && r.nRef >= 4)
            const minLine = group.rungs[0]?.line ?? 0
            const maxLine = group.rungs[group.rungs.length - 1]?.line ?? 0
            const lineSpan = maxLine - minLine || 1
            const padX = 14
            const padY = 8
            const svgW = 320
            const svgH = 44
            const usableW = svgW - 2 * padX
            const usableH = svgH - 2 * padY
            const getX = (line: number) => padX + ((line - minLine) / lineSpan) * usableW
            const getY = (p: number) => padY + (1 - p) * usableH
            const pointsStr = plotted.map(p => `${getX(p.line).toFixed(1)},${getY(p.pCons!).toFixed(1)}`).join(' ')

            const selectedHasPlotted = rung.pCons != null && rung.nRef >= 4
            let nearestRung: LadderRung | null = null
            if (!selectedHasPlotted && plotted.length > 0) {
              nearestRung = plotted.reduce((closest, cur) => {
                return Math.abs(cur.line - rung.line) < Math.abs(closest.line - rung.line) ? cur : closest
              }, plotted[0])
            }

            return (
              <div className="flex flex-col gap-1 py-1 px-2 rounded-lg bg-background-secondary-default border border-separator-border">
                <svg width="100%" height={svgH} viewBox={`0 0 ${svgW} ${svgH}`} preserveAspectRatio="none" className="overflow-visible">
                  {plotted.length > 1 && (
                    <polyline fill="none" stroke="#475569" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" points={pointsStr} />
                  )}
                  {plotted.map(p => {
                    const isSel = p.line === rung.line
                    const cx = getX(p.line)
                    const cy = getY(p.pCons!)
                    const r = isSel ? 5 : 3
                    const fill = isSel ? (rung.verified ? GREEN : SKY) : '#64748b'
                    return (
                      <circle key={p.line} cx={cx} cy={cy} r={r} fill={fill} stroke={isSel ? '#0d0f15' : 'none'} strokeWidth={isSel ? 1.5 : 0} />
                    )
                  })}
                </svg>
                <div className="flex justify-between items-center text-[10px] text-text-secondary mono">
                  <span>Consensus P(Over) at priced rungs</span>
                  {nearestRung && <span>Nearest consensus rung: {nearestRung.line}</span>}
                </div>
              </div>
            )
          })()}

          {group.category === 'count' && (
            <div className="flex items-stretch gap-1.5 overflow-x-auto py-1">
              {group.rungs.map((r, idx) => {
                const isSel = idx === rungIndex
                const label = formatRungPillLabel('count', r.line)
                let subText = ''
                if (r.pCons != null && r.nRef >= 4) {
                  subText = `Tail ${Math.round(r.pCons * 100)}%`
                } else {
                  const overPrices = r.books.filter(b => b.over != null && !b.overCap).map(b => b.over!)
                  const nBooks = r.books.filter(b => b.over != null || b.under != null).length
                  if (overPrices.length > 0) {
                    overPrices.sort((a, b) => americanToDecimal(b) - americanToDecimal(a))
                    const best = formatOdds(overPrices[0])
                    const worst = formatOdds(overPrices[overPrices.length - 1])
                    subText = overPrices.length > 1 ? `${best} to ${worst} · ${nBooks} books` : `${best} · ${nBooks} books`
                  } else {
                    subText = `${nBooks} books`
                  }
                }
                return (
                  <button
                    key={r.line}
                    type="button"
                    onClick={() => setSelectedIdx(idx)}
                    className="flex-1 min-w-[70px] p-1.5 rounded-md text-center transition-colors cursor-pointer"
                    style={{
                      background: isSel ? 'var(--color-background-tertiary-default)' : 'var(--color-background-secondary-default)',
                      border: `1px solid ${isSel ? (r.verified ? GREEN : SKY) : HAIR}`,
                    }}
                  >
                    <div className="mono text-[11px] font-semibold text-text-primary">{label}</div>
                    <div className="mono text-[10px] text-text-secondary mt-0.5">{subText}</div>
                  </button>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* Book bars for the selected rung */}
      <div className="flex flex-col gap-2 pt-1.5 border-t border-t-[color:var(--color-border-table)]">
        {visibleBars.map((bar, idx) => {
          const ratio = Math.max(0.12, Math.min(1.0, bar.netPayout / bestNetPayout))
          const isBest = idx === 0
          return (
            <div
              key={`${bar.book}-${idx}`}
              className="flex items-center gap-2.5"
              style={{ opacity: bar.staleBand === 'grey' ? 0.45 : 1 }}
            >
              <div className="w-8 flex justify-center shrink-0">
                <BookLogo book={bar.book} size={20} />
              </div>
              <div className="flex-1 h-2 bg-background-tertiary-default rounded-chip overflow-hidden">
                <div
                  style={{
                    width: `${Math.round(ratio * 100)}%`,
                    height: '100%',
                    background: isBest && rung.verified ? GREEN : isBest ? SKY : '#334155',
                    borderRadius: '4px',
                  }}
                />
              </div>
              <div className="mono text-right shrink-0">
                <span
                  style={{
                    fontSize: '12px',
                    fontWeight: 600,
                    color: isBest && rung.verified ? GREEN : 'var(--color-text-primary)',
                  }}
                >
                  {formatOdds(bar.heroAm)}
                </span>
                {bar.otherAm != null && (
                  <span className="text-[11px] text-text-secondary ml-1.5">
                    · {formatOdds(bar.otherAm)}
                  </span>
                )}
              </div>
              {/* Extras */}
              <div className="flex items-center gap-1.5 shrink-0">
                {bar.staleBand !== 'fresh' && (
                  <span
                    className="mono text-[10px]"
                    style={{
                      color: '#f59e0b',
                      background: 'rgba(245, 158, 11, 0.1)',
                      border: '1px solid rgba(245, 158, 11, 0.25)',
                      padding: '1px 5px',
                      borderRadius: '3px',
                    }}
                  >
                    +{Math.round(bar.lagMin ?? 0)}m older
                  </span>
                )}
                {bar.staleOpener && (
                  <span
                    className="mono text-[10px] text-text-secondary"
                    style={{
                      padding: '1px 5px',
                      borderRadius: '3px',
                      border: `1px solid ${HAIR}`,
                    }}
                  >
                    Unmoved since open
                  </span>
                )}
                {bar.staleBand === 'grey' && (
                  <span className="mono text-[10px] text-text-secondary">
                    Stale quote
                  </span>
                )}
              </div>
            </div>
          )
        })}

        {rung.capsHidden > 0 && (
          <div className="mono text-[11px] text-text-secondary mt-0.5">
            +{rung.capsHidden} extreme tail caps hidden
          </div>
        )}
      </div>

      {/* Footer (state-driven, exactly one block) */}
      <div className="flex justify-between items-center mt-1 text-mini text-text-secondary flex-wrap gap-2 pt-2 border-t border-separator-border">
        {(() => {
          // Milestone kind
          if (group.kind === 'milestone') {
            const nQuoting = rung.books.filter(b => b.over != null && !b.overCap).length
            const bestOverAm = heroAm
            const rawImplied = bestOverAm != null
              ? ((1 / americanToDecimal(bestOverAm)) * 100).toFixed(1)
              : null
            return (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="mono text-micro px-1.5 py-0.5 rounded border border-separator-border text-text-secondary">
                  1-Sided · Overround Unremovable
                </span>
                {nQuoting >= 2 && (
                  <span className="mono text-micro text-text-secondary">
                    Market avg · {nQuoting} books
                  </span>
                )}
                {rawImplied != null && (
                  <span className="mono text-micro text-text-secondary">
                    Implied {rawImplied}%
                  </span>
                )}
                {rung.premiumPct != null && (
                  <span
                    className="mono text-micro font-semibold"
                    style={{
                      color: GREEN,
                      background: 'rgba(87,242,135,0.08)',
                      padding: '2px 6px',
                      borderRadius: '4px',
                      border: '1px solid rgba(87,242,135,0.25)',
                    }}
                  >
                    +{rung.premiumPct.toFixed(1)}% vs Mkt
                  </span>
                )}
              </div>
            )
          }

          // Non-milestone: verified
          if (rung.state === 'verified') {
            const pConsLoo = hero?.pConsLoo
            const fairProb = pConsLoo != null ? (heroSide === 'OVER' ? pConsLoo : 1 - pConsLoo) : null
            const fairAm = fairProb != null ? fairAmericanOf(fairProb) : null
            const fairStr = fairAm != null ? formatOdds(fairAm) : '--'
            return (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="mono font-semibold" style={{ color: GREEN }}>
                  Net EV {signed(hero?.netEvPct ?? 0)}%
                </span>
                <span className="mono text-micro text-text-secondary">
                  Fair {fairStr} · Consensus of {hero?.nRefLoo ?? 0} books (leave-one-out)
                </span>
              </div>
            )
          }

          // Non-milestone: market
          if (rung.state === 'market') {
            const isFloorOrBelow = hero?.netEvPct != null && hero.netEvPct < NOISE_FLOOR[group.category]
            if (rung.nRef >= 4 && isFloorOrBelow) {
              return (
                <span className="mono text-text-secondary text-[11px]">
                  In line with market
                </span>
              )
            }
            if (rung.nRef < 4) {
              return (
                <span className="mono text-micro px-1.5 py-0.5 rounded border border-separator-border text-text-secondary">
                  {rung.nRef}/4 ref books · Prices only
                </span>
              )
            }
            return (
              <span className="mono text-text-secondary text-[11px]">
                In line with market
              </span>
            )
          }

          // Non-milestone: thin
          const rawImplied = heroAm != null ? ((1 / americanToDecimal(heroAm)) * 100).toFixed(1) : null
          return (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="mono text-micro px-1.5 py-0.5 rounded border border-separator-border text-text-secondary">
                {rung.nRef}/4 ref books · Prices only
              </span>
              {rawImplied != null && (
                <span className="mono text-micro text-text-secondary">
                  Implied {rawImplied}%
                </span>
              )}
            </div>
          )
        })()}

        {/* Rung Vig pill: whenever rungVig != null AND (state is verified or a premium badge shows) */}
        {rung.rungVig != null && (rung.state === 'verified' || rung.premiumPct != null) && (
          <span className="mono text-micro px-1.5 py-0.5 rounded border border-separator-border text-text-secondary">
            Rung Vig: {(rung.rungVig <= 1 ? rung.rungVig * 100 : rung.rungVig).toFixed(1)}%
          </span>
        )}
      </div>
    </div>
  )
}

export function LaddersView({ odds }: { odds: OddsScreen }) {
  const {
    ladderSegment,
    setLadderSegment,
    ladderAll,
    setLadderAll,
    ladders,
    ladderTotal,
    limit,
    isMobile,
    setLimit,
  } = odds

  const tabStyle = (on: boolean): React.CSSProperties => ({
    fontFamily: 'var(--font-text), sans-serif',
    fontSize: '11px',
    fontWeight: 600,
    padding: '4px 10px',
    borderRadius: '4px',
    border: 0,
    cursor: 'pointer',
    background: on ? 'var(--color-background-tertiary-default)' : 'transparent',
    color: on ? 'var(--color-text-primary)' : 'var(--color-text-secondary)',
  })

  const emptyTitle = ladderSegment === 'milestones'
    ? 'No milestone payout premiums on the board'
    : 'No verified dislocations on the board'

  return (
    <div className="flex flex-col gap-3">
      {/* Header controls row */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div style={{ display: 'inline-flex', padding: '2px', background: 'var(--color-background-secondary-default)', border: `1px solid ${HAIR}`, borderRadius: '6px' }}>
          <button
            type="button"
            onClick={() => { setLadderSegment('curves'); setLimit(PAGE) }}
            style={tabStyle(ladderSegment === 'curves')}
          >
            Two-Sided Curves
          </button>
          <button
            type="button"
            onClick={() => { setLadderSegment('milestones'); setLimit(PAGE) }}
            style={tabStyle(ladderSegment === 'milestones')}
          >
            One-Sided Milestones
          </button>
          <button
            type="button"
            onClick={() => { setLadderSegment('all'); setLimit(PAGE) }}
            style={tabStyle(ladderSegment === 'all')}
          >
            All
          </button>
        </div>

        <div className="flex items-center gap-3">
          <span className="mono text-mini text-text-secondary">
            Showing {ladders.length} of {ladderTotal} ladders
          </span>
          <button
            type="button"
            aria-pressed={ladderAll}
            onClick={() => setLadderAll(v => !v)}
            className="btn btn-ghost px-2.5 py-1 text-mini font-semibold"
            style={{
              borderColor: ladderAll ? GREEN : HAIR,
              color: ladderAll ? GREEN : 'var(--color-text-primary)',
              background: ladderAll ? 'rgba(87, 242, 135, 0.08)' : undefined,
            }}
          >
            All Ladders (Research)
          </button>
        </div>
      </div>

      {/* Body */}
      {ladderTotal === 0 ? (
        <div className="rounded-xl border border-separator-border bg-background-primary-default px-5 py-9 text-center shadow-card">
          <div className="mb-1.5 text-body-semibold text-text-primary">No NFL ladders on the board</div>
          <div className="mx-auto max-w-[60ch] text-body-2-regular text-text-secondary">
            Ladder markets post closer to kickoff on game days.
          </div>
        </div>
      ) : ladders.length === 0 ? (
        <div className="rounded-xl border border-separator-border bg-background-primary-default px-5 py-9 text-center shadow-card">
          <div className="mb-1.5 text-body-semibold text-text-primary">{emptyTitle}</div>
          <div className="mx-auto max-w-[60ch] text-body-2-regular text-text-secondary">
            Widen to All Ladders (Research) to compare every ladder price.
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : 'repeat(auto-fill, minmax(420px, 1fr))', gap: '14px' }}>
          {ladders.slice(0, limit).map(g => (
            <LadderCard key={g.key} group={g} />
          ))}
        </div>
      )}
    </div>
  )
}

export function LadderSectionForRow({
  rows,
  gameKey,
  playerNorm,
  stat,
}: {
  rows: BoardRow[]
  gameKey: string
  playerNorm: string
  stat: string
}) {
  const g = useMemo(() => ladderGroupFor(rows, gameKey, playerNorm, stat, Date.now()), [rows, gameKey, playerNorm, stat])
  if (!g) return null
  return (
    <section className="mt-3">
      <div className="ui-heading text-micro text-text-secondary mb-2">FULL LADDER</div>
      <LadderCard group={g} />
    </section>
  )
}
