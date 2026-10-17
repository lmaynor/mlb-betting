'use client'

import { useMemo, useState } from 'react'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { BOARD_SORT_LABEL, fairAmerican, filterBoard, type BoardRow, type BoardSort } from '@/lib/board-rows'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { formatOdds } from '@/lib/odds'
import { getSystemsBySport } from '@/lib/pick-systems'
import { SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { bookDisplay, SYSTEM_LABEL_SHORT } from '@/lib/tokens'

const SURFACE = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px' } as const
const TH = { textAlign: 'left', padding: '9px 10px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { padding: '9px 10px', borderBottom: '1px solid #151822', verticalAlign: 'middle' } as const
const PAGE = 80

const pct = (x: number | null | undefined, d = 0) => (x == null ? '--' : `${(x * 100).toFixed(d)}%`)
const signed = (x: number | null | undefined) => (x == null ? '--' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`)
const proj = (x: number | null) => (x == null ? '--' : x >= 3 ? x.toFixed(1) : x.toFixed(2))
const startLabel = (iso: string | null) => {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/Chicago' })
}
const hitColor = (x: number | null) => (x == null ? 'var(--fog)' : x >= 0.6 ? 'var(--signal)' : x < 0.4 ? 'var(--loss)' : 'var(--silver)')

const SIDES = ['ALL', 'OVER', 'UNDER'] as const
const SIGNALS: { id: 'ALL' | 'CONFLUENCE' | 'EV'; label: string }[] = [
  { id: 'ALL', label: 'ALL' }, { id: 'CONFLUENCE', label: 'MODEL + MARKET' }, { id: 'EV', label: '+EV ONLY' },
]

function Seg<T extends string>({ label, value, options, onChange, accent }: {
  label: string; value: T; options: readonly { id: T; label: string }[]; onChange: (v: T) => void; accent: string
}) {
  return (
    <div role="group" aria-label={label} style={{ display: 'inline-flex', gap: '4px', alignItems: 'center' }}>
      <span style={{ fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.06em' }}>{label.toUpperCase()}</span>
      {options.map(o => (
        <button key={o.id} aria-pressed={value === o.id} onClick={() => onChange(o.id)}
          style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11px', fontWeight: 600, padding: '4px 8px', borderRadius: '6px', cursor: 'pointer',
            color: value === o.id ? '#090a0f' : 'var(--silver)', background: value === o.id ? accent : 'var(--surface-elevated)',
            border: `1px solid ${value === o.id ? accent : '#1e2230'}` }}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * The projections board for every sport: the model's expected value beside the line, the market's consensus, the recent
 * hit rate and the best price. Rows arrive already shaped (lib/board-rows.ts) and already cut by the access policy.
 * `status` is an optional one-line freshness note from the feed; `children` is rendered under the table (notices).
 */
export function SportProjectionsBoard({ sport, rows, status, emptyBody, note, controls = true, marketChips = true }: {
  sport: SportSlug; rows: BoardRow[]; status?: string; emptyBody?: string; note?: React.ReactNode
  /** false on a system page, where every row is already one market */
  marketChips?: boolean
  /** false on the picks pages: the FilterBar above has already filtered and sorted `rows`, so the board only renders them */
  controls?: boolean
}) {
  const pillar = SPORT_PILLARS[sport]
  const accent = pillar.accent
  const systems = useMemo(() => getSystemsBySport(sport), [sport])
  const [market, setMarket] = useState('ALL')
  const [side, setSide] = useState<'ALL' | 'OVER' | 'UNDER'>('ALL')
  const [signal, setSignal] = useState<'ALL' | 'CONFLUENCE' | 'EV'>('ALL')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<BoardSort>('score')
  const [limit, setLimit] = useState(PAGE)
  const shown = useMemo(() => (controls ? filterBoard(rows, { market, side, signal, q, sort }) : rows), [controls, rows, market, side, signal, q, sort])
  const reset = () => setLimit(PAGE)

  if (rows.length === 0) {
    return (
      <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{pillar.shortName} projections</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
          {emptyBody ?? `No ${pillar.shortName} projections yet. The board fills in once a slate and prices are available.`}
        </div>
      </div>
    )
  }

  return (
    <div>
      {controls && <div style={{ display: 'flex', alignItems: 'center', gap: '8px 14px', flexWrap: 'wrap', marginBottom: '10px' }}>
        {marketChips && <div role="group" aria-label="Filter by market" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {[{ key: 'ALL', label: 'ALL' }, ...systems.map(s => ({ key: s.key, label: SYSTEM_LABEL_SHORT[s.key] ?? s.shortName }))].map(c => (
            <button key={c.key} aria-pressed={market === c.key} onClick={() => { setMarket(c.key); reset() }}
              style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
                color: market === c.key ? '#090a0f' : 'var(--silver)', background: market === c.key ? accent : 'var(--surface-elevated)',
                border: `1px solid ${market === c.key ? accent : '#1e2230'}` }}>
              {c.label}
            </button>
          ))}
        </div>}
        <Seg label="Side" value={side} options={SIDES.map(s => ({ id: s, label: s }))} onChange={v => { setSide(v); reset() }} accent={accent} />
        <Seg label="Signal" value={signal} options={SIGNALS} onChange={v => { setSignal(v); reset() }} accent={accent} />
        <input type="search" aria-label="Search player or team" placeholder="Player or team" value={q} onChange={e => { setQ(e.target.value); reset() }}
          style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '12px', padding: '5px 9px', borderRadius: '6px', border: '1px solid #1e2230', background: '#090a0f', color: 'var(--chalk)', width: '150px' }} />
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '10px', color: 'var(--fog)', letterSpacing: '0.06em' }}>
          SORT
          <select aria-label="Sort" value={sort} onChange={e => setSort(e.target.value as BoardSort)}
            style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', padding: '4px 6px', borderRadius: '6px', border: '1px solid #1e2230', background: '#090a0f', color: 'var(--chalk)' }}>
            {(Object.keys(BOARD_SORT_LABEL) as BoardSort[]).map(k => <option key={k} value={k}>{BOARD_SORT_LABEL[k]}</option>)}
          </select>
        </label>
        <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{shown.length} rows</span>
        {sport !== 'mlb' && (
          <span className="dell-heading" title="Projections come from an empirical estimator on game logs and have not been validated against prices." style={{ fontSize: '9px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 6px' }}>UNVALIDATED</span>
        )}
      </div>}
      {status && <div style={{ fontSize: '11.5px', color: 'var(--fog)', marginBottom: '8px' }}>{status}</div>}

      <div style={{ ...SURFACE, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '1040px', fontSize: '13px' }}>
          <thead>
            <tr>{['SCORE', 'PLAYER & MATCHUP', 'MARKET & LINE', 'MODEL PROJ', 'CONSENSUS FAIR', 'L5 / L10 HIT %', 'BEST BOOK & PRICE', 'EDGE / EV'].map(h => (
              <th key={h} className="dell-heading" style={TH}>{h}</th>))}</tr>
          </thead>
          <tbody>
            {shown.slice(0, limit).map(r => {
              const t = r.score > 0 ? TIER_META[scoreTier(r.score)] : null
              const b = r.book ? bookDisplay(r.book) : null
              const fair = fairAmerican(r.pFair)
              const when = startLabel(r.start)
              return (
                <tr key={r.key} className="sys-row">
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    {t ? (
                      <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: '6px' }}>
                        <span className="mono" style={{ color: t.color, fontWeight: 700 }}>{r.score}</span>
                        <span className="dell-heading" style={{ fontSize: '9px', color: t.color }}>{t.label}</span>
                      </span>
                    ) : <span style={{ color: 'var(--fog)' }}>--</span>}
                  </td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                      {r.headshotId != null && (r.sport === 'nhl' || r.sport === 'nba') && <PlayerHeadshot sport={r.sport} id={r.headshotId} name={r.player} size={28} />}
                      <span>
                        <span style={{ color: 'var(--chalk)', fontWeight: 600 }}>{r.player}</span>
                        <span className="mono" style={{ display: 'block', fontSize: '11px', color: 'var(--fog)' }}>{r.matchup || '--'}{when ? ` \u00b7 ${when} CT` : ''}</span>
                      </span>
                    </span>
                  </td>
                  <td style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--silver)' }}>
                    {r.hasLine && r.line != null
                      ? <span><span className="mono" style={{ color: 'var(--chalk)' }}>{r.side === 'under' ? 'Under' : 'Over'} {r.line}</span> {r.marketLabel}</span>
                      : <span>{r.marketLabel} <span style={{ color: 'var(--fog)' }}>(no line yet)</span></span>}
                  </td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    <span className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{proj(r.proj)}</span>
                    <span className="mono" title="The model's probability for the listed side" style={{ marginLeft: '8px', fontSize: '11.5px', color: 'var(--silver)' }}>{pct(r.pModel)}</span>
                  </td>
                  <td className="mono" style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--silver)' }}>
                    {r.pFair == null ? '--' : <span>{pct(r.pFair)}{fair != null ? ` ${formatOdds(fair)}` : ''}{r.nBooks != null ? <span style={{ color: r.nBooks >= 3 ? 'var(--fog)' : 'var(--warn)' }}> {r.nBooks} bk</span> : null}</span>}
                  </td>
                  <td className="mono" title="Share of the last 5 and last 10 games that landed on the listed side of the line" style={{ ...TD, whiteSpace: 'nowrap', fontSize: '12px' }}>
                    {r.l5 == null && r.l10 == null ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                      <span><span style={{ color: hitColor(r.l5) }}>L5: {pct(r.l5)}</span> <span style={{ color: 'var(--fog)' }}>&middot;</span> <span style={{ color: hitColor(r.l10) }}>L10: {pct(r.l10)}</span></span>
                    )}
                  </td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    {r.odds == null || !b ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                        <span style={{ fontSize: '11px', fontWeight: 700, color: b.color, background: b.bg, border: `1px solid ${b.color}55`, borderRadius: '6px', padding: '2px 7px' }}>{b.label}</span>
                        <span className="mono" style={{ color: 'var(--chalk)' }}>{formatOdds(r.odds)}</span>
                      </span>
                    )}
                  </td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    <span className="mono" title="Model probability minus the de-vigged market" style={{ color: (r.modelEdge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.modelEdge)}</span>
                    {r.marketEv != null && <span className="mono" title="Best price against the market consensus" style={{ marginLeft: '8px', color: r.marketEv > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.marketEv)}</span>}
                    {r.confluence && <span className="dell-heading" title="The projection and the market price both favor this side" style={{ marginLeft: '8px', fontSize: '9px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px' }}>MODEL + MARKET</span>}
                    {r.isPick && <span className="dell-heading" style={{ marginLeft: '8px', fontSize: '9.5px', color: 'var(--signal)' }}>PICK</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {shown.length === 0 && <div style={{ ...SURFACE, padding: '18px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px', marginTop: '10px' }}>No rows match these filters.</div>}
      {shown.length > limit && (
        <button onClick={() => setLimit(limit + PAGE)} className="btn btn-ghost" style={{ marginTop: '10px' }}>Show {Math.min(PAGE, shown.length - limit)} more</button>
      )}
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '10px', lineHeight: 1.55 }}>
        Model proj is the expected value for the stat and the probability the model gives the listed side. Consensus fair is the de-vigged
        market probability for that side, its American price and how many books it rests on. L5 / L10 count the recent games that landed
        on the listed side of the line. Edge is the model minus the market; the second figure is the best price against the market.
        Rows without a line are rotation projections: prices have not posted.
      </p>
      {note}
    </div>
  )
}
