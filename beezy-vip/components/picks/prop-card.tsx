'use client'

import React from 'react'
import type { BoardRow } from '@/lib/board-rows'
import { fairAmerican, isOfficialRow, isOutlierRow } from '@/lib/board-rows'
import { formatOdds } from '@/lib/odds'
import { bookDisplay, SURFACE_CARD } from '@/lib/tokens'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { startLabel } from '@/app/ev/odds-format'
import { PlayerHeadshot, SportLogo } from '@/components/ui/sport-assets'
import { BookLogo } from '@/components/ui/book-logo'
import { cockpitHref } from '@/lib/prop-cards'
import { DistributionStrip, fmtStrip } from './distribution-strip'

export function PropCard({ r }: { r: BoardRow }) {
  const isOfficial = isOfficialRow(r)
  const isOutlier = isOutlierRow(r)
  const meta = r.score > 0 ? TIER_META[scoreTier(r.score)] : null

  // Odds and book display
  const bDisp = r.book ? bookDisplay(r.book) : null
  const fairVal = fairAmerican(r.pBlend)
  const fairStr = fairVal != null ? formatOdds(fairVal) : '--'

  // Time & matchup
  const timeStr = r.start ? startLabel(r.start) : ''
  const hasTeams = Boolean(r.away && r.home)

  // Blend & model pct
  const blendPct = r.pBlend != null ? `${(r.pBlend * 100).toFixed(1)}%` : '--'
  const modelPct = r.pModel != null ? `${(r.pModel * 100).toFixed(1)}%` : null

  // EV badge
  const evVal = r.marketEv
  const signedEv = evVal != null ? `${evVal > 0 ? '+' : ''}${(evVal * 100).toFixed(1)}% EV` : null

  return (
    <div style={{ ...SURFACE_CARD, padding: '14px 16px', display: 'flex', flexDirection: 'column' }}>
      {/* Header row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: 11,
          color: 'var(--color-text-secondary)',
          minHeight: 22,
        }}
      >
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
          {timeStr && <span>{timeStr}</span>}
          {timeStr && (hasTeams || r.matchup) && <span>·</span>}
          {hasTeams ? (
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
              <SportLogo sport={r.sport} team={r.away} size={14} />
              <span>{r.away}</span>
              <span style={{ opacity: 0.6 }}>@</span>
              <SportLogo sport={r.sport} team={r.home} size={14} />
              <span>{r.home}</span>
            </span>
          ) : r.matchup ? (
            <span>{r.matchup}</span>
          ) : null}
        </div>

        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {isOfficial ? (
            <span
              className="ui-heading text-micro text-signal border border-[color:#57F287] rounded-chip bg-[rgba(87,242,135,0.12)]"
              style={{ padding: '1px 5px' }}
            >
              Official play
            </span>
          ) : isOutlier ? (
            <span
              className="ui-heading text-micro text-link border border-[color:#38bdf8] rounded-chip bg-[rgba(56,189,248,0.12)]"
              style={{ padding: '1px 5px' }}
            >
              +EV OUTLIER
            </span>
          ) : null}
          {r.score > 0 && meta && (
            <span
              style={{
                color: meta.color,
                background: meta.bg,
                border: `1px solid ${meta.border}`,
                borderRadius: 999,
                padding: '1px 7px',
                fontFamily: 'monospace',
                fontSize: 11,
              }}
            >
              Score {r.score}
            </span>
          )}
        </div>
      </div>

      {/* Player row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          margin: '8px 0',
          gap: 12,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <PlayerHeadshot sport={r.sport} id={r.headshotId} name={r.player} size={40} />
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 15,
                fontWeight: 700,
                color: '#f3f4f6',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
              title={r.player}
            >
              {r.player}
            </div>
            <div
              style={{
                fontSize: 12,
                color: 'var(--color-text-secondary)',
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                marginTop: 2,
              }}
            >
              <SportLogo sport={r.sport} team={r.team ?? r.away ?? undefined} size={14} />
              <span>{r.team ?? r.matchup}</span>
            </div>
          </div>
        </div>

        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          {r.odds != null && r.book != null && bDisp ? (
            <div>
              <div
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  background: '#171a24',
                  border: `1px solid ${bDisp.color}55`,
                  borderRadius: 8,
                  padding: '4px 9px',
                }}
              >
                <BookLogo book={r.book} size={18} />
                <span style={{ fontSize: 18, fontWeight: 700, fontFamily: 'monospace', color: '#f3f4f6' }}>
                  {formatOdds(r.odds)}
                </span>
              </div>
              <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-secondary)', marginTop: 3 }}>
                Fair {fairStr}{r.nBooks != null ? ` · ${r.nBooks} books` : ''}
              </div>
            </div>
          ) : (
            <div style={{ fontSize: 14, color: 'var(--color-text-secondary)' }}>--</div>
          )}
        </div>
      </div>

      {/* Market band */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginTop: 2,
          gap: 8,
        }}
      >
        <div>
          <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: '0.02em', color: '#f3f4f6' }}>
            {r.hasLine && r.line != null
              ? `${r.marketLabel} ${r.side === 'under' ? 'Under' : 'Over'} ${r.line}`
              : r.marketLabel}
          </div>
          <div style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--color-text-secondary)', marginTop: 2 }}>
            Blend {blendPct}{modelPct != null ? ` · Model ${modelPct}` : ''}
          </div>
        </div>

        {signedEv != null && (
          <div
            title="Best price against the market's fair probability, not the model's"
            style={
              evVal != null && evVal > 0
                ? {
                    color: '#57F287',
                    background: 'rgba(87, 242, 135, 0.15)',
                    border: '1px solid rgba(87, 242, 135, 0.4)',
                    borderRadius: 6,
                    padding: '2px 8px',
                    fontFamily: 'monospace',
                    fontWeight: 600,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                  }
                : {
                    color: 'var(--color-text-secondary)',
                    background: '#171a24',
                    border: '1px solid #2e3446',
                    borderRadius: 6,
                    padding: '2px 8px',
                    fontFamily: 'monospace',
                    fontWeight: 600,
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                  }
            }
          >
            {signedEv}
          </div>
        )}
      </div>

      {/* Distribution Strip */}
      <div style={{ marginTop: 10 }}>
        <DistributionStrip
          mean={r.proj}
          p10={r.p10}
          p90={r.p90}
          line={r.hasLine ? r.line : null}
          side={r.side}
        />
      </div>

      {/* Footer row */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginTop: 10,
          fontSize: 11,
          fontFamily: 'monospace',
          color: 'var(--color-text-secondary)',
        }}
      >
        <div>
          Proj: {r.proj != null ? fmtStrip(r.proj) : '--'}{r.engine ? ` · ${r.engine}` : ''}
        </div>
        <a
          href={cockpitHref(r)}
          style={{
            fontSize: 11,
            color: 'var(--color-text-secondary)',
            border: '1px solid #2e3446',
            borderRadius: 6,
            padding: '5px 9px',
            textDecoration: 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          {r.nBooks != null ? `Compare ${r.nBooks} books` : 'Compare books'} ↗
        </a>
      </div>
    </div>
  )
}
