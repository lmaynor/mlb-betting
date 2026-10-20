'use client'

import { useState } from 'react'
import { PlayerHeadshot, SportLogo } from '@/components/ui/sport-assets'
import { fairAmerican, type BoardRow } from '@/lib/board-rows'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { formatOdds } from '@/lib/odds'
import { hasHeadshots } from '@/lib/sport-assets'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { bookDisplay, SURFACE_CARD, TABLE_TD, TABLE_TH } from '@/lib/tokens'

const SURFACE = SURFACE_CARD
const TH = { ...TABLE_TH, textAlign: 'left', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, verticalAlign: 'middle', whiteSpace: 'nowrap' } as const
const PAGE = 80
const HEAD_TIPS: Record<string, string> = {
  SCORE: 'Beezy Score, 0-100: how strong the play is. Not a win probability.',
  'MARKET FAIR': 'The books\' consensus probability for this side with the vig removed.',
  'MODEL EDGE': 'Model probability minus market fair probability. It says where the model disagrees with the market. It is not a profit estimate.',
  'PRICE EV': 'Return on a 1 unit bet at the best price IF the market fair probability is right. Positive means the best price beats the market\'s own fair price.',
}

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
  const showEv = rows.some(r => r.marketEv != null)
  const showModel = rows.some(r => r.proj != null || r.pModel != null)
  const heads = ['SCORE', 'PLAYER', 'TEAM', 'MARKET & LINE', ...(showModel ? ['MODEL'] : []), ...(showFair ? ['MARKET FAIR'] : []),
    ...(showForm ? ['FORM'] : []), 'BEST PRICE', 'MODEL EDGE', ...(showEv ? ['PRICE EV'] : []), ...(showResult ? ['RESULT'] : [])]

  return (
    <div>
      {status && <div role={statusWarn ? 'status' : undefined} style={{ fontSize: '11.5px', color: statusWarn ? 'var(--warn)' : 'var(--fog)', marginBottom: '8px' }}>{status}</div>}
      <div style={{ ...SURFACE, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '980px', fontSize: '13px' }}>
          <thead><tr>{heads.map(h => <th key={h} className="dell-heading" title={HEAD_TIPS[h]} style={TH}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.slice(0, limit).map(r => {
              const t = r.score > 0 ? TIER_META[scoreTier(r.score)] : null
              const b = r.book ? bookDisplay(r.book) : null
              const fair = fairAmerican(r.pFair)
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
                      </span>
                    </span>
                  </td>
                  <td style={TD}>
                    {r.team ? (
                      <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', color: 'var(--silver)', fontSize: '12px' }}>
                        <SportLogo sport={r.sport} team={r.team} size={20} />{r.team}
                      </span>
                    ) : r.away && r.home ? (
                      <span className="mono" title="The feed does not name this player's team" style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', color: 'var(--silver)', fontSize: '12px' }}>
                        <SportLogo sport={r.sport} team={r.away} size={16} />{r.away}<span style={{ color: 'var(--fog)' }}>@</span><SportLogo sport={r.sport} team={r.home} size={16} />{r.home}
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
                    <span className="mono" title="Model probability minus the de-vigged market probability for this side" style={{ color: (r.modelEdge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.modelEdge)}</span>
                    {r.confluence && <span className="dell-heading" title="The model and the market price both favor this side" style={{ marginLeft: '8px', fontSize: '10px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px' }}>MODEL + MARKET</span>}
                    {r.isPick && <span className="dell-heading" style={{ marginLeft: '8px', fontSize: '10px', color: 'var(--signal)' }}>PICK</span>}
                  </td>
                  {showEv && (
                    <td style={TD}>
                      <span className="mono" title="Best price against the market's fair probability, not the model's" style={{ color: (r.marketEv ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.marketEv)}</span>
                    </td>
                  )}
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
        Model edge is the model&rsquo;s probability minus the market&rsquo;s fair probability: where the model disagrees. Price EV is the best
        price against the market&rsquo;s own fair probability: whether a book is actually off-market. A big edge with a negative EV means only the
        model likes the play. Rows with no line are projections only: prices have not posted.
      </p>
      {note}
    </div>
  )
}
