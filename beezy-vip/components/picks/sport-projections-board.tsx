'use client'

import { Fragment, useState } from 'react'
import { useIsMobile } from '@/components/ui/mobile-sheet'
import { PlayerHeadshot, SportLogo } from '@/components/ui/sport-assets'
import { fairAmerican, type BoardRow } from '@/lib/board-rows'
import { formatOdds } from '@/lib/odds'
import { hasHeadshots } from '@/lib/sport-assets'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { bookDisplay, SURFACE_CARD, TABLE_TD, TABLE_TH } from '@/lib/tokens'

const SURFACE = SURFACE_CARD
const TH = { ...TABLE_TH, textAlign: 'left', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, verticalAlign: 'middle', whiteSpace: 'nowrap' } as const
const PAGE = 80
/** columns a phone drops from the row (they move into the tap-to-open detail line): the table keeps SCORE, PLAYER, PICK, MODEL, BEST PRICE and EDGE */
const HIDE_ON_PHONE = new Set(['TEAM', 'MARKET FAIR', 'FORM', 'PRICE EV', 'RESULT'])
const TAP_PX = 44
const HEAD_TIPS: Record<string, string> = {
  SCORE: 'Beezy Score, 0-100: how strong the play is. Not a win probability.',
  'MARKET FAIR': 'The books\' consensus probability for this side with the vig removed.',
  'MODEL EDGE': 'Model probability minus market fair probability. It says where the model disagrees with the market. It is not a profit estimate.',
  'PRICE EV': 'Return on a 1 unit bet at the best price IF the market fair probability is right. Positive means the best price beats the market\'s own fair price.',
}

const pct = (x: number | null | undefined, d = 0) => (x == null ? '--' : `${(x * 100).toFixed(d)}%`)
const signed = (x: number | null | undefined) => (x == null ? '--' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`)
const num = (x: number | null) => (x == null ? '--' : Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 3 ? x.toFixed(1) : x.toFixed(2))
const hitColor = (x: number | null) => (x == null ? 'var(--color-text-secondary)' : x >= 0.6 ? 'var(--signal)' : x < 0.4 ? 'var(--loss)' : 'var(--color-neutral-300)')
const RESULT_COLOR = { win: 'var(--signal)', loss: 'var(--loss)', push: 'var(--color-neutral-300)', void: 'var(--color-text-secondary)' } as const

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
  // phone only: a row opens a detail line with the columns the phone table drops. Desktop shows every column, so it never renders one.
  const isMobile = useIsMobile(768)
  const [open, setOpen] = useState<string | null>(null)

  if (rows.length === 0) {
    return (
      <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center' }}>
        <div className="text-text-primary font-semibold mb-1.5">Nothing to show</div>
        <div className="text-text-secondary text-cozy max-w-[62ch] mx-auto leading-[1.55]">
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
      {status && <div className="text-[11.5px] mb-2" role={statusWarn ? 'status' : undefined} style={{ color: statusWarn ? 'var(--warn)' : 'var(--color-text-secondary)' }}>{status}</div>}
      <div className="pb-wrap" style={{ ...SURFACE, overflowX: 'auto' }}>
        <table className="pb-table w-full border-collapse text-cozy">
          <thead><tr>{heads.map(h => <th key={h} className={`ui-heading${HIDE_ON_PHONE.has(h) ? ' pb-hide-m' : ''}`} title={HEAD_TIPS[h]} style={TH}>{h}</th>)}</tr></thead>
          <tbody>
            {rows.slice(0, limit).map(r => {
              const b = r.book ? bookDisplay(r.book) : null
              const fair = fairAmerican(r.pFair)
              const ev = r.marketEv ?? 0
              const isOfficial = ev >= 0.03 && ev <= 0.10 && r.score >= 68 && (r.sport === 'mlb' ? r.isPick !== false : (!!r.confluence && (r.nBooks == null || r.nBooks >= 4) && (r.modelEdge == null || r.modelEdge >= 0.015)))
              const isOutlier = ev >= 0.025 && ev <= 0.15 && (r.nBooks == null || r.nBooks >= 3) && !isOfficial
              const isOpen = isMobile && open === r.key
              return (
                <Fragment key={r.key}>
                <tr className="sys-row" onClick={isMobile ? () => setOpen(isOpen ? null : r.key) : undefined} style={isMobile ? { cursor: 'pointer' } : undefined}>
                  <td className="pb-score" style={TD}>
                    {r.score > 0 ? (
                      <div className="inline-flex items-center gap-1.5">
                        {isOfficial ? (
                          <span className="ui-heading pb-hide-m text-micro text-signal border border-[color:#57F287] rounded-chip bg-[rgba(87,242,135,0.12)]" style={{ padding: '1px 5px' }}>OFFICIAL PLAY</span>
                        ) : isOutlier ? (
                          <span className="ui-heading pb-hide-m text-micro text-link border border-[color:#38bdf8] rounded-chip bg-[rgba(56,189,248,0.12)]" style={{ padding: '1px 5px' }}>+EV OUTLIER</span>
                        ) : null}
                        <span className="mono font-bold" style={{ color: isOfficial ? '#57F287' : isOutlier ? '#38bdf8' : 'var(--color-neutral-300)' }}>{r.score}</span>
                      </div>
                    ) : <span className="text-text-secondary">--</span>}
                  </td>
                  <td className="pb-player" style={TD}>
                    <span className="inline-flex items-center gap-2" style={{ maxWidth: '100%' }}>
                      {hasHeadshots(r.sport) && <span className="pb-hide-m inline-flex"><PlayerHeadshot sport={r.sport} id={r.headshotId} name={r.player} size={36} /></span>}
                      <span className="min-w-0">
                        {isMobile ? (
                          <button type="button" className="pb-name text-text-primary bg-transparent bg-none border-0 text-left cursor-pointer block" aria-expanded={isOpen} title={r.player}
                            style={{ fontWeight: 600, padding: 0, font: 'inherit', minHeight: `${TAP_PX}px`, maxWidth: '100%' }}>
                            <span className="pb-name-text">{r.player}</span>
                          </button>
                        ) : (
                          <span className="pb-name text-text-primary font-semibold" title={r.player}>{r.player}</span>
                        )}
                      </span>
                    </span>
                  </td>
                  <td className="pb-hide-m" style={TD}>
                    {r.team ? (
                      <span className="mono inline-flex items-center gap-1.5 text-neutral-300 text-compact">
                        <SportLogo sport={r.sport} team={r.team} size={20} />{r.team}
                      </span>
                    ) : r.away && r.home ? (
                      <span className="mono inline-flex items-center gap-[5px] text-neutral-300 text-compact" title="The feed does not name this player's team">
                        <SportLogo sport={r.sport} team={r.away} size={16} />{r.away}<span className="text-text-secondary">@</span><SportLogo sport={r.sport} team={r.home} size={16} />{r.home}
                      </span>
                    ) : <span className="text-text-secondary">--</span>}
                  </td>
                  <td className="pb-pick" style={{ ...TD, color: 'var(--color-neutral-300)' }}>
                    {r.hasLine && r.line != null
                      ? <span><span className="mono pb-line text-text-primary">{r.side === 'under' ? 'Under' : 'Over'} {r.line}</span>{' '}<span className="pb-mkt">{r.marketLabel}</span></span>
                      : r.hasLine ? <span className="pb-mkt">{r.marketLabel}</span>
                        : <span><span className="pb-mkt">{r.marketLabel}</span>{' '}<span className="pb-noline text-text-secondary">(no line yet)</span></span>}
                  </td>
                  {showModel && (
                    <td className="pb-model" style={TD}>
                      <span className="mono text-text-primary font-bold">{num(r.proj)}</span>
                      {r.pModel != null && <span className="mono pb-hide-m ml-2 text-[11.5px] text-neutral-300" title="The model's probability for the listed side">{pct(r.pModel)}</span>}
                    </td>
                  )}
                  {showFair && (
                    <td className="mono pb-hide-m" style={{ ...TD, color: 'var(--color-neutral-300)' }}>
                      {r.pFair == null ? '--' : <span>{pct(r.pFair)}{fair != null ? ` ${formatOdds(fair)}` : ''}{r.nBooks != null ? <span style={{ color: r.nBooks >= 3 ? 'var(--color-text-secondary)' : 'var(--warn)' }}> {r.nBooks} bk</span> : null}</span>}
                    </td>
                  )}
                  {showForm && (
                    <td className="mono pb-hide-m" style={{ ...TD, fontSize: '12px' }}>
                      {r.l5 != null || r.l10 != null ? (
                        <span title="Share of the last 5 and last 10 games that landed on the listed side of the line">
                          <span style={{ color: hitColor(r.l5) }}>L5 {pct(r.l5)}</span> <span className="text-text-secondary">&middot;</span> <span style={{ color: hitColor(r.l10) }}>L10 {pct(r.l10)}</span>
                        </span>
                      ) : r.avg10 != null || r.avgSzn != null ? (
                        <span title="The player's average in this stat over his last 10 games and this season" className="text-neutral-300">
                          L10 avg {num(r.avg10)} <span className="text-text-secondary">&middot;</span> szn {num(r.avgSzn)}
                        </span>
                      ) : <span className="text-text-secondary">--</span>}
                    </td>
                  )}
                  <td className="pb-price" style={TD}>
                    {r.odds == null || !b ? <span className="text-text-secondary">--</span> : (
                      <span className="inline-flex items-center gap-[7px]">
                        <span className="pb-book text-mini font-bold" data-short={b.short} title={b.label} style={{ color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '2px 7px' }}>{b.label}</span>
                        <span className="mono text-text-primary">{formatOdds(r.odds)}</span>
                      </span>
                    )}
                  </td>
                  <td className="pb-edge" style={TD}>
                    <span className="mono" title="Model probability minus the de-vigged market probability for this side" style={{ color: (r.modelEdge ?? 0) > 0 ? 'var(--signal)' : 'var(--color-text-secondary)' }}>{signed(r.modelEdge)}</span>
                    {r.confluence && <span className="ui-heading pb-hide-m ml-2 text-micro text-signal border border-[color:rgba(87,242,135,0.35)] rounded-chip" title="The model and the market price both favor this side" style={{ padding: '1px 5px' }}>MODEL + MARKET</span>}
                    {r.isPick && <span className="ui-heading pb-hide-m ml-2 text-signal">PICK</span>}
                  </td>
                  {showEv && (
                    <td className="pb-hide-m" style={TD}>
                      <span className="mono" title="Best price against the market's fair probability, not the model's" style={{ color: (r.marketEv ?? 0) > 0 ? 'var(--signal)' : 'var(--color-text-secondary)' }}>{signed(r.marketEv)}</span>
                    </td>
                  )}
                  {showResult && (
                    <td className="ui-heading pb-hide-m" style={{ ...TD, fontSize: '10px', color: r.result ? RESULT_COLOR[r.result] : 'var(--color-text-secondary)' }}>{r.result ? r.result.toUpperCase() : 'PENDING'}</td>
                  )}
                </tr>
                {isOpen && <DetailRow r={r} span={heads.length} showResult={showResult} />}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button onClick={() => setLimit(limit + PAGE)} className="btn btn-ghost tap-m mt-[10px]">Show {Math.min(PAGE, rows.length - limit)} more</button>
      )}
      <p className="text-[11.5px] text-text-secondary mt-2.5 leading-[1.55]">
        Model edge is the model&rsquo;s probability minus the market&rsquo;s fair probability: where the model disagrees. Price EV is the best
        price against the market&rsquo;s own fair probability: whether a book is actually off-market. A big edge with a negative EV means only the
        model likes the play. Rows with no line are projections only: prices have not posted.
      </p>
      {note}
    </div>
  )
}

/** The phone's tap-open line: every column the phone table drops, plus the engine that priced the row and the unvalidated reminder. */
function DetailRow({ r, span, showResult }: { r: BoardRow; span: number; showResult: boolean }) {
  const fair = fairAmerican(r.pFair)
  const item = (label: string, value: React.ReactNode) => (
    <div className="flex gap-1.5 min-w-0">
      <dt className="ui-heading" style={{ fontSize: '10px', color: 'var(--color-text-secondary)', flex: '0 0 auto' }}>{label}</dt>
      <dd className="mono" style={{ margin: 0, color: 'var(--color-neutral-300)', fontSize: '12px', minWidth: 0 }}>{value}</dd>
    </div>
  )
  return (
    <tr className="pb-detail">
      <td colSpan={span} style={{ ...TABLE_TD, background: '#0e1016', padding: '10px 12px' }}>
        <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '6px 14px' }}>
          {item('GAME', r.matchup || '--')}
          {r.team && item('TEAM', r.team)}
          {r.pModel != null && item('MODEL', `${pct(r.pModel)} ${r.side === 'under' ? 'under' : 'over'}`)}
          {r.pFair != null && item('MARKET FAIR', `${pct(r.pFair)}${fair != null ? ` ${formatOdds(fair)}` : ''}${r.nBooks != null ? ` \u00b7 ${r.nBooks} bk` : ''}`)}
          {(r.l5 != null || r.l10 != null) && item('FORM', `L5 ${pct(r.l5)} \u00b7 L10 ${pct(r.l10)}`)}
          {r.l5 == null && r.l10 == null && (r.avg10 != null || r.avgSzn != null) && item('AVG', `L10 ${num(r.avg10)} \u00b7 szn ${num(r.avgSzn)}`)}
          {r.marketEv != null && item('PRICE EV', signed(r.marketEv))}
          {r.confluence && item('SIGNAL', 'Model + market agree')}
          {showResult && r.result && item('RESULT', r.result.toUpperCase())}
          {r.engine && item('ENGINE', r.engine)}
        </dl>
        {r.sport !== 'mlb' && <div className="mt-2 text-mini text-warn">Unvalidated model output. Paper only, not in the public results.</div>}
      </td>
    </tr>
  )
}
