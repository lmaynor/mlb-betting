'use client'

import { useMemo, useState } from 'react'
import { PROJECTION_MARKETS, type MlbProjectionRow } from '@/lib/mlb-projections'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { bookDisplay } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'

const ACCENT = '#57F287'
const SURFACE = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' } as const
const TH = { textAlign: 'left', padding: '9px 10px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { padding: '9px 10px', borderBottom: '1px solid #151822', verticalAlign: 'middle' } as const
const PAGE = 80

const proj = (x: number) => (x >= 3 ? x.toFixed(1) : x.toFixed(2))
const pct = (x: number | null) => (x == null ? '--' : `${(x * 100).toFixed(0)}%`)
const signed = (x: number | null) => (x == null ? '--' : `${x > 0 ? '+' : ''}${(x * 100).toFixed(1)}%`)

/** The MLB projection board: the model's expected value beside the line, the side it prefers and the best price. */
export function MlbProjectionsBoard({ rows }: { rows: MlbProjectionRow[] }) {
  const [market, setMarket] = useState('ALL')
  const [limit, setLimit] = useState(PAGE)
  const systems = useMemo(() => Object.keys(PROJECTION_MARKETS).filter(k => rows.some(r => r.system === k)), [rows])
  const shown = useMemo(() => rows.filter(r => market === 'ALL' || r.system === market), [rows, market])

  if (rows.length === 0) {
    return (
      <div style={{ ...SURFACE, padding: '28px 20px', textAlign: 'center' }}>
        <div style={{ color: 'var(--chalk)', fontWeight: 600, marginBottom: '6px' }}>No projections yet</div>
        <div style={{ color: 'var(--fog)', fontSize: '13px', maxWidth: '62ch', margin: '0 auto', lineHeight: 1.55 }}>
          Today&rsquo;s projections publish with the ~11:00 AM CT model run, once lineups and prices are in.
        </div>
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px 14px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <div role="group" aria-label="Filter by market" style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
          {['ALL', ...systems].map(k => (
            <button key={k} aria-pressed={market === k} onClick={() => { setMarket(k); setLimit(PAGE) }}
              style={{ fontFamily: 'var(--font-text), sans-serif', fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', cursor: 'pointer',
                color: market === k ? '#090a0f' : 'var(--silver)', background: market === k ? ACCENT : 'var(--surface-elevated)',
                border: `1px solid ${market === k ? ACCENT : '#1e2230'}`, transition: 'background 120ms ease, border-color 120ms ease' }}>
              {k === 'ALL' ? 'All' : PROJECTION_MARKETS[k].label}
            </button>
          ))}
        </div>
        <span className="mono" style={{ fontSize: '11.5px', color: 'var(--fog)' }}>{shown.length} rows</span>
      </div>

      <div style={{ ...SURFACE, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: '900px', fontSize: '13px' }}>
          <thead>
            <tr>{['Player', 'Matchup', 'Market', 'Line', 'Proj', 'Model P', 'Best price', 'Edge', 'Score', ''].map((h, i) => <th key={i} className="dell-heading" style={TH}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {shown.slice(0, limit).map(r => {
              const t = TIER_META[scoreTier(r.score)]
              const b = r.book ? bookDisplay(r.book) : null
              return (
                <tr key={r.key} className="sys-row">
                  <td style={{ ...TD, color: 'var(--chalk)', fontWeight: 600, whiteSpace: 'nowrap' }}>{r.player}</td>
                  <td className="mono" style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{r.matchup || '--'}</td>
                  <td style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{r.marketLabel}</td>
                  <td className="mono" style={{ ...TD, whiteSpace: 'nowrap' }}>{r.side === 'UNDER' ? 'U' : 'O'} {r.line}</td>
                  <td className="mono" title={`The model's expected ${PROJECTION_MARKETS[r.system].unit} for this game`} style={{ ...TD, color: 'var(--chalk)', fontWeight: 700 }}>{proj(r.proj)}</td>
                  <td className="mono" title="Probability of the listed side" style={TD}>{pct(r.pModel)}</td>
                  <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                    {r.odds == null ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                        <span className="mono" style={{ color: 'var(--chalk)' }}>{formatOdds(r.odds)}</span>
                        {b && <span style={{ fontSize: '11px', fontWeight: 700, color: b.color }}>{b.label}</span>}
                      </span>
                    )}
                  </td>
                  <td className="mono" style={{ ...TD, color: (r.edge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{signed(r.edge)}</td>
                  <td style={TD}>{r.score > 0 ? <span className="mono" title={t.label} style={{ color: t.color, fontWeight: 700 }}>{r.score}</span> : <span style={{ color: 'var(--fog)' }}>--</span>}</td>
                  <td className="dell-heading" style={{ ...TD, fontSize: '9.5px', color: r.isPick ? 'var(--signal)' : 'var(--fog)' }}>{r.isPick ? 'PICK' : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {shown.length > limit && (
        <button onClick={() => setLimit(limit + PAGE)} className="btn btn-ghost" style={{ marginTop: '10px' }}>Show {Math.min(PAGE, shown.length - limit)} more</button>
      )}
      <p style={{ fontSize: '11.5px', color: 'var(--fog)', marginTop: '10px', lineHeight: 1.55 }}>
        Proj is the model&rsquo;s expected value for the stat; Model P, price and edge are for the side it prefers at that line. PICK marks the plays that cleared the
        edge and Kelly gates and are tracked at a stake; the rest are logged predictions. HR and the game markets are probability models with no expected count, so
        they are on the player and game pick pages instead.
      </p>
    </div>
  )
}
