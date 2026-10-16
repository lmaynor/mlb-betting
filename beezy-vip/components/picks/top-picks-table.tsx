import Link from 'next/link'
import { SPORT_PILLARS } from '@/lib/sports'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { bookDisplay } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { sportHref } from '@/lib/ia'
import type { PickBasis, TopPick } from '@/lib/top-picks'

const SURFACE = { background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' } as const
const TH = { textAlign: 'left', padding: '9px 10px', fontSize: '10px', color: 'var(--fog)', borderBottom: '1px solid #1e2230', background: '#11131a', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { padding: '10px', borderBottom: '1px solid #151822', verticalAlign: 'middle' } as const

const BASIS_LABEL: Record<PickBasis, string> = { model: 'Model', ev: '+EV alert', projection: 'Projection', confluence: 'Model + market' }

const RESULT: Record<string, { label: string; color: string }> = {
  win: { label: 'WIN', color: 'var(--signal)' },
  loss: { label: 'LOSS', color: 'var(--loss)' },
  push: { label: 'PUSH', color: 'var(--silver)' },
  void: { label: 'VOID', color: 'var(--fog)' },
}

export const fmtEdge = (e: number | null) => (e == null ? '--' : `${e >= 0 ? '+' : ''}${(e * 100).toFixed(1)}%`)

/** One real table for a ranked pick list: today's Top 10 and each day of the history share it. */
export function TopPicksTable({ picks, showResult = false, startRank = 1 }: { picks: TopPick[]; showResult?: boolean; startRank?: number }) {
  return (
    <div style={{ ...SURFACE, overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: showResult ? '900px' : '820px', fontSize: '13px' }}>
        <thead>
          <tr>
            {['# Sport', 'Pick', 'Best price', 'Score', 'Edge', 'Game', 'Source', ...(showResult ? ['Result'] : [])].map(h => (
              <th key={h} className="dell-heading" style={TH}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {picks.map((p, i) => {
            const pillar = SPORT_PILLARS[p.sport]
            const tier = TIER_META[scoreTier(p.score)]
            const b = p.book ? bookDisplay(p.book) : null
            const res = p.result ? RESULT[p.result] : null
            return (
              <tr key={p.key} className="sys-row">
                <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                  <span className="mono" style={{ color: 'var(--fog)', display: 'inline-block', width: '22px' }}>{startRank + i}</span>
                  <Link href={sportHref(p.sport)} className="dell-heading" style={{ fontSize: '10px', color: pillar.accent, textDecoration: 'none' }}>{pillar.shortName}</Link>
                </td>
                <td style={{ ...TD, minWidth: '170px' }}>
                  <div style={{ color: 'var(--chalk)', fontWeight: 600 }}>{p.who}</div>
                  {p.what && <div style={{ color: 'var(--silver)', fontSize: '12px', marginTop: '2px' }}>{p.what}</div>}
                </td>
                <td style={{ ...TD, whiteSpace: 'nowrap' }}>
                  {p.odds == null ? <span style={{ color: 'var(--fog)' }}>--</span> : (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '7px' }}>
                      <span className="mono" style={{ color: 'var(--chalk)' }}>{formatOdds(p.odds)}</span>
                      {b && <span style={{ fontSize: '11px', fontWeight: 700, color: b.color }}>{b.label}</span>}
                    </span>
                  )}
                </td>
                <td style={TD}>
                  <span className="mono" title={`${tier.label}. Beezy Score is a 0-100 strength score, not a win probability.`} style={{ color: tier.color, fontWeight: 700 }}>{p.score}</span>
                </td>
                <td className="mono" style={{ ...TD, color: (p.edge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{fmtEdge(p.edge)}</td>
                <td className="mono" style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{p.game || '--'}</td>
                <td style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--silver)' }}>
                  {BASIS_LABEL[p.basis]}
                  {!p.tracked && (
                    <span className="dell-heading" title="Projections come from an empirical estimator on game logs and are not tracked in the public results yet." style={{ marginLeft: '8px', fontSize: '9px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 5px' }}>UNVALIDATED</span>
                  )}
                </td>
                {showResult && (
                  <td className="dell-heading" style={{ ...TD, fontSize: '10px', color: res?.color ?? 'var(--link)' }}>{res?.label ?? 'PENDING'}</td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
