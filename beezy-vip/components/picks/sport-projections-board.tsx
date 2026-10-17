'use client'

import { useState } from 'react'
import { PlayerHeadshot, SportLogo } from '@/components/ui/sport-assets'
import { fairAmerican, type BoardRow } from '@/lib/board-rows'
import { gameTimeLabel } from '@/lib/board-games'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { formatOdds } from '@/lib/odds'
import { hasHeadshots } from '@/lib/sport-assets'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { bookDisplay, SURFACE_CARD, TABLE_TD, TABLE_TH } from '@/lib/tokens'

const SURFACE = SURFACE_CARD
const TH = { ...TABLE_TH, textAlign: 'left', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, verticalAlign: 'middle', whiteSpace: 'nowrap' } as const
const PAGE = 80

const pct = (x: number | null | undefined, d = 0) => (x == null ? '--' : `${(x * 100).toFixed(d)}%`)
const signed = (x: number | null | undefined) => (x == null ? '--' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`)
const num = (x: number | null) => (x == null ? '--' : Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 3 ? x.toFixed(1) : x.toFixed(2))
const hitColor = (x: number | null) => (x == null ? 'var(--fog)' : x >= 0.6 ? 'var(--signal)' : x < 0.4 ? 'var(--loss)' : 'var(--silver)')
const RESULT_COLOR = { win: 'var(--signal)', loss: 'var(--loss)', push: 'var(--silver)', void: 'var(--fog)' } as const

/**
 * The one table every sport's picks, projections and system pages render: the model's expected value beside the line, the
 * market's consensus, the player's form and the best price. Rows arrive already shaped (lib/board-rows.ts), already cut by
 * the access policy and already filtered and sorted by the page (the FilterBar above is the only set of controls), so this
 * component only draws them. A column nothing in the table fills is left out rather than printed as a column of dashes.
 */
export function SportProjectionsBoard({ sport, rows, status, statusWarn = false, emptyBody, note }: {
  sport: SportSlug; rows: BoardRow[]; status?: string; statusWarn?: boolean; emptyBody?: string; note?: React.ReactNode
}) {
  const pillar = SPORT_PILLARS[sport]
  const [limit, setLimit] = useState(PAGE)

  if (rows.length === 0) {
    return (
      <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>Nothing to show</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
          {emptyBody ?? `No ${pillar.shortName} rows match. The board fills in once a slate and prices are available.`}
        </div>
      </div>
    )
  }

  const showFair = rows.some(r => r.pFair != null)
  const showForm = rows.some(r => r.l5 != null || r.l10 != null || r.avg10 != null || r.avgSzn != null)
  const showResult = rows.some(r => r.result != null)
  const showModel = rows.some(r => r.proj != null || r.pModel != null)
  const heads = ['SCORE', 'PLAYER', 'GAME', 'MARKET & LINE', ...(showModel ? ['MODEL'] : []), ...(showFair ? ['MARKET FAIR'] : []),
    ...(showForm ? ['FORM'] : []), 'BEST PRICE', 'EDGE / EV', ...(showResult ? ['RESULT'] : [])]

  return (
    <div>
      {status && <div role={statusWarn ? 'status' : undefined} style={{ fontSize: '11.5px', color: statusWarn ? 'var(--warn)' : 'var(--fog)', marginBottom: '8px' }}>{status}</div>}
      <div style={{ ...SURFACE, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '980px', fontSize: '13px' }}>
          <thead><tr>{heads.map(h => <th key={h} className="dell-heading" style={TH}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.slice(0, limit).map(r => {
              const t = r.score > 0 ? TIER_META[scoreTier(r.score)] : null
              const b = r.book ? bookDisplay(r.book) : null
              const fair = fairAmerican(r.pFair)
              const when = r.start ? gameTimeLabel({ start: r.start, startText: null }) : ''
              return (
                <tr key={r.key} className="sys-row">
                  <td style={TD}>
                    {t ? (
                      <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: '6px' }}>
                        <span className="mono" style={{ color: t.color, fontWeight: 700 }}>{r.score}</span>
                        <span className="dell-heading" style={{ fontSize: '10px', color: t.color }}>{t.label}</span>
                      </span>
                    ) : <span style={{ color: 'var(--fog)' }}>--</span>}
                  </td>
                  <td style={TD}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                      {r.headshotId != null && hasHeadshots(r.sport) && <PlayerHeadshot sport={r.sport} id={r.headshotId} name={r.player} size={36} />}
                      <span>
                        <span style={{ color: 'var(--chalk)', fontWeight: 600 }}>{r.player}</span>
                        {r.team && (
                          <span className="mono" style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px', color: 'var(--fog)', marginTop: '1px' }}>
                            <SportLogo sport={r.sport} team={r.team} size={14} />{r.team}
                          </span>
                        )}
                      </span>
                    </span>
                  </td>
                  <td style={TD}>
                    {r.away && r.home ? (
                      <span>
                        <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', color: 'var(--silver)', fontSize: '12px' }}>
                          <SportLogo sport={r.sport} team={r.away} size={16} />{r.away}<span style={{ color: 'var(--fog)' }}>@</span><SportLogo sport={r.sport} team={r.home} size={16} />{r.home}
                        </span>
                        {when && <span className="mono" style={{ display: 'block', fontSize: '11px', color: 'var(--fog)', marginTop: '1px' }}>{when} CT</span>}
                      </span>
                    ) : <span style={{ color: 'var(--fog)' }}>--</span>}
                  </td>
                  <td style={{ ...TD, color: 'var(--silver)' }}>
                    {r.hasLine && r.line != null
                      ? <span><span className="mono" style={{ color: 'var(--chalk)' }}>{r.side === 'under' ? 'Under' : 'Over'} {r.line}</span> {r.marketLabel}</span>
                      : r.hasLine ? <span>{r.marketLabel}</span>
                        : <span>{r.marketLabel} <span style={{ color: 'var(--fog)' }}>(no line yet)</span></span>}
                  </td>
                  {showModel && (
                    <td style={TD}>
                      <span className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{num(r.proj)}</span>
                      {r.pModel != null && <span className="mono" title="The model's probability for the listed side" style={{ marginLeft: '8px', fontSize: '11.5px', color: 'var(--silver)' }}>{pct(r.pModel)}</span>}
                    </td>
                  )}
                  {showFair && (
                    <td className="mono" style={{ ...TD, color: 'var(--silver)' }}>
                      {r.pFair == null ? '--' : <span>{pct(r.pFair)}{fair != null ? ` ${formatOdds(fair)}` : ''}{r.nBooks != null ? <span style={{ color: r.nBooks >= 3 ? 'var(--fog)' : 'var(--warn)' }}> {r.nBooks} bk</span> : null}</span>}
                    </td>
                  )}
                  {showForm && (
                    <td className="mono" style={{ ...TD, fontSize: '12px' }}>
                      {r.l5 != null || r.l10 != null ? (
                        <span title="Share of the last 5 and last 10 games that landed on the listed side of the line">
                          <span style={{ color: hitColor(r.l5) }}>L5 {pct(r.l5)}</span> <span style={{ color: 'var(--fog)' }}>&middot;</span> <span style={{ color: hitColor(r.l10) }}>L10 {pct(r.l10)}</span>
                        </span>
                      ) : r.avg10 != null || r.avgSzn != null ? (
                        <span title="The player's average in this stat over his last 10 games and this season" style={{ color: 'var(--silver)' }}>
                          L10 avg {num(r.avg10)} <span style={{ color: 'var(--fog)' }}>&middot;</span> szn {num(r.avgSzn)}
                        </span>
                      ) : <span style={{ color: 'var(--fog)' }}>--</span>}
                    </td>
                  )}
                  <td style={TD}>
                    {r.odds == null || !b ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                        <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '2px 7px' }}>{b.label}</span>
                        <span className="mono" style={{ color: 'var(--chalk)' }}>{formatOdds(r.odds)}</span>
                      </span>
                    )}
                  </td>
                  <td style={TD}>
                    <span className="mono" title="Model probability minus the de-vigged market" style={{ color: (r.modelEdge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.modelEdge)}</span>
                    {r.marketEv != null && <span className="mono" title="Best price against the market consensus" style={{ marginLeft: '8px', color: r.marketEv > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.marketEv)}</span>}
                    {r.confluence && <span className="dell-heading" title="The projection and the market price both favor this side" style={{ marginLeft: '8px', fontSize: '10px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px' }}>MODEL + MARKET</span>}
                    {r.isPick && <span className="dell-heading" style={{ marginLeft: '8px', fontSize: '10px', color: 'var(--signal)' }}>PICK</span>}
                  </td>
                  {showResult && (
                    <td className="dell-heading" style={{ ...TD, fontSize: '10px', color: r.result ? RESULT_COLOR[r.result] : 'var(--fog)' }}>{r.result ? r.result.toUpperCase() : 'PENDING'}</td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button onClick={() => setLimit(limit + PAGE)} className="btn btn-ghost" style={{ marginTop: '10px' }}>Show {Math.min(PAGE, rows.length - limit)} more</button>
      )}
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '10px', lineHeight: 1.55 }}>
        Model is the expected value for the stat and the probability the model gives the listed side. Market fair is the de-vigged consensus
        probability for that side, its American price and how many books it rests on. Form is the share of recent games that landed on the
        listed side of the line, or the player&rsquo;s own average when there is no line. Edge is the model minus the market; the second
        figure is the best price against the market. Rows with no line are projections only: prices have not posted.
      </p>
      {note}
    </div>
  )
}
