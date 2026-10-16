'use client'

import { useMemo, useState } from 'react'
import type { SportProjectionRow, SportProjections } from '@/lib/types'
import { propStatLabel, SPORT_PILLARS, type SportSlug } from '@/lib/sports'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { bookDisplay } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { PlayerHeadshot } from '@/components/ui/sport-assets'

const SURFACE = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' } as const
const TH = { textAlign: 'left', padding: '9px 10px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { padding: '9px 10px', borderBottom: '1px solid #151822', verticalAlign: 'middle' } as const
const PAGE = 80

export const pct = (x: number | null | undefined, d = 0) => (x == null ? '--' : `${(x * 100).toFixed(d)}%`)
export const signedPct = (x: number | null | undefined) => (x == null ? '--' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`)
const num = (x: number | null | undefined, d = 1) => (x == null ? '--' : x.toFixed(d))

/** The price the model's side would be struck at (best over / best under across books), or the best over when there is no side. */
export function sidePrice(r: SportProjectionRow): { odds: number | null; book: string | null; side: 'over' | 'under' } {
  const side = r.side ?? 'over'
  return side === 'over'
    ? { odds: r.best_over_odds, book: r.best_over_book, side }
    : { odds: r.best_under_odds, book: r.best_under_book, side }
}

/** Plain-language freshness line: what the user is looking at and how old it is. */
export function boardStatusText(d: SportProjections): string {
  if (d.status === 'unavailable') return 'Projections are not available right now.'
  const parts: string[] = []
  if (d.status === 'live') parts.push(d.lines_age_minutes != null ? `Lines updated ${Math.round(d.lines_age_minutes)} min ago` : 'Lines live')
  else if (d.status === 'awaiting_lines') parts.push('Lines have not posted yet: projections only')
  else parts.push('No games scheduled in the next 36 hours')
  if (d.stale_gamelog) parts.push(`game logs end ${d.gamelog_latest ?? 'last season'} (off-season carry-over)`)
  return parts.join(' \u00b7 ')
}

function ConfluenceBadge() {
  return <span className="dell-heading" title="The projection and the market price both favor this side" style={{ fontSize: '9px', color: '#57F287', border: '1px solid rgba(87,242,135,0.35)', borderRadius: '4px', padding: '1px 5px', marginLeft: '8px', whiteSpace: 'nowrap' }}>MODEL + MARKET</span>
}

function ScoreCell({ r }: { r: SportProjectionRow }) {
  if (!r.has_line || r.beezy_score <= 0) return <span style={{ color: 'var(--fog)' }}>--</span>
  const t = TIER_META[scoreTier(r.beezy_score)]
  return <span className="mono" title={`${t.label}; edge basis: ${r.score_basis}`} style={{ color: t.color, fontWeight: 700 }}>{r.beezy_score}</span>
}

export function ProjectionsBoard({ sport, data }: { sport: Exclude<SportSlug, 'mlb'>; data: SportProjections | null }) {
  const [stat, setStat] = useState('ALL')
  const [limit, setLimit] = useState(PAGE)
  const pillar = SPORT_PILLARS[sport]
  const rows = data?.rows ?? []
  const stats = useMemo(() => {
    const seen = new Set(rows.map(r => r.stat))
    return (data?.stats ?? []).filter(s => seen.has(s))
  }, [rows, data])
  const shown = useMemo(() => rows.filter(r => stat === 'ALL' || r.stat === stat), [rows, stat])

  if (!data || data.status === 'unavailable' || rows.length === 0) {
    return (
      <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>{pillar.shortName} projections</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
          {!data || data.status === 'unavailable'
            ? 'The projection board could not be loaded. It refreshes automatically; try again in a few minutes.'
            : `No ${pillar.shortName} games are scheduled in the next 36 hours. The board fills in once a slate and prices are available.`}
        </div>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 14px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <div role="group" aria-label="Filter by market" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {['ALL', ...stats].map(k => (
            <button key={k} aria-pressed={stat === k} onClick={() => { setStat(k); setLimit(PAGE) }}
              style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
                color: stat === k ? '#090a0f' : 'var(--silver)', background: stat === k ? pillar.accent : 'var(--surface-elevated)',
                border: `1px solid ${stat === k ? pillar.accent : '#1e2230'}`, transition: 'background 120ms ease, border-color 120ms ease' }}>
              {k === 'ALL' ? 'All' : (rows.find(r => r.stat === k)?.stat_label ?? propStatLabel(k))}
            </button>
          ))}
        </div>
        <div style={{ fontSize: '11.5px', color: 'var(--fog)', display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono">{shown.length} rows</span>
          <span>{boardStatusText(data)}</span>
          <span className="dell-heading" title="Projections come from an empirical estimator on game logs and have not been validated against prices." style={{ fontSize: '9px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 6px' }}>UNVALIDATED</span>
        </div>
      </div>

      <div style={{ ...SURFACE, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '980px', fontSize: '13px' }}>
          <thead>
            <tr>
              {['Player', 'Matchup', 'Market', 'Line', 'Proj', 'Model P', 'L5', 'L10', 'Best price', 'Model edge', 'Market EV', 'Score'].map(h => (
                <th key={h} className="dell-heading" style={TH}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.slice(0, limit).map(r => {
              const best = sidePrice(r)
              const b = best.book ? bookDisplay(best.book) : null
              return (
                <tr key={`${r.game_key}-${r.player_norm}-${r.stat}`}>
                  <td style={{ ...TD, color: 'var(--chalk)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                      {sport !== 'nfl' && r.headshot_id != null && <PlayerHeadshot sport={sport} id={r.headshot_id} name={r.player} size={28} />}
                      <span>{r.player}</span>
                      {r.confluence && <ConfluenceBadge />}
                    </span>
                  </td>
                  <td className="mono" style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{r.team && r.opp ? `${r.team} v ${r.opp}` : (r.game_key.split(':')[2] ?? '').replace('@', ' @ ')}</td>
                  <td style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{r.stat_label}</td>
                  <td className="mono" style={{ ...TD, whiteSpace: 'nowrap' }}>{r.has_line && r.line != null ? `${r.side === 'under' ? 'U' : r.side === 'over' ? 'O' : ''} ${r.line}` : <span style={{ color: 'var(--fog)' }}>--</span>}</td>
                  <td className="mono" title={`${r.n_games} games; ${r.usage_label.toLowerCase()} adjusted; season avg ${num(r.szn_avg)}`} style={{ ...TD, color: 'var(--chalk)' }}>{num(r.proj_mean)}</td>
                  <td className="mono" style={TD}>{r.has_line ? pct(r.p_model_side, 0) : '--'}</td>
                  <td className="mono" title="Share of the last 5 games that cleared the line (over)" style={{ ...TD, color: 'var(--silver)' }}>{r.has_line ? pct(r.l5_hit_rate) : '--'}</td>
                  <td className="mono" title="Share of the last 10 games that cleared the line (over)" style={{ ...TD, color: 'var(--silver)' }}>{r.has_line ? pct(r.l10_hit_rate) : '--'}</td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    {best.odds == null || !b ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                        <span className="mono" style={{ color: 'var(--chalk)' }}>{formatOdds(best.odds)}</span>
                        <span style={{ fontSize: '11px', fontWeight: 700, color: b.color }}>{b.label}</span>
                      </span>
                    )}
                  </td>
                  <td className="mono" style={{ ...TD, color: (r.model_edge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signedPct(r.model_edge)}</td>
                  <td className="mono" style={{ ...TD, color: (r.market_ev ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signedPct(r.market_ev)}</td>
                  <td style={TD}><ScoreCell r={r} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {shown.length > limit && (
        <button onClick={() => setLimit(limit + PAGE)} className="btn btn-ghost" style={{ marginTop: '10px' }}>Show {Math.min(PAGE, shown.length - limit)} more</button>
      )}
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '10px', lineHeight: 1.5 }}>
        Model P is the projection&apos;s probability for the listed side; edge is the model minus the de-vigged market, and market EV is the best price against the market consensus.
        L5 and L10 count games that cleared the line. Rows without a line are rotation projections: prices have not posted.
      </p>
    </div>
  )
}
