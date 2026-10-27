import Link from 'next/link'
import { SPORT_PILLARS } from '@/lib/sports'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { bookDisplay, SURFACE_CARD, TABLE_TD, TABLE_TH } from '@/lib/tokens'
import { formatOdds } from '@/lib/odds'
import { sportHref } from '@/lib/ia'
import { isOfficialCardPlay, isEvOutlier, type PickBasis, type TopPick } from '@/lib/top-picks'

const SURFACE = SURFACE_CARD
const TH = { ...TABLE_TH, textAlign: 'left', position: 'sticky', top: 0, whiteSpace: 'nowrap' } as const
const TD = { ...TABLE_TD, padding: '10px', verticalAlign: 'middle' } as const

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
            const b = p.book ? bookDisplay(p.book) : null
            const res = p.result ? RESULT[p.result] : null
            const isOfficial = isOfficialCardPlay(p)
            const isOutlier = isEvOutlier(p)
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
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
                    {isOfficial ? (
                      <span className="dell-heading" style={{ fontSize: '10px', color: '#57F287', border: '1px solid #57F287', borderRadius: '4px', padding: '1px 5px', background: 'rgba(87,242,135,0.12)' }}>OFFICIAL PLAY</span>
                    ) : isOutlier ? (
                      <span className="dell-heading" style={{ fontSize: '10px', color: '#38bdf8', border: '1px solid #38bdf8', borderRadius: '4px', padding: '1px 5px', background: 'rgba(56,189,248,0.12)' }}>+EV OUTLIER</span>
                    ) : null}
                    <span className="mono" title={`Beezy Score ${p.score}. Not a win probability.`} style={{ color: isOfficial ? '#57F287' : isOutlier ? '#38bdf8' : 'var(--silver)', fontWeight: 700 }}>{p.score}</span>
                  </div>
                </td>
                <td className="mono" style={{ ...TD, color: (p.edge ?? 0) > 0 ? 'var(--signal)' : 'var(--fog)' }}>{fmtEdge(p.edge)}</td>
                <td className="mono" style={{ ...TD, color: 'var(--silver)', whiteSpace: 'nowrap' }}>{p.game || '--'}</td>
                <td style={{ ...TD, whiteSpace: 'nowrap', color: 'var(--silver)' }}>
                  {BASIS_LABEL[p.basis]}
                  {!p.tracked && (
                    <span className="dell-heading" title="Projections come from an empirical estimator on game logs and are not tracked in the public results yet." style={{ marginLeft: '8px', fontSize: '10px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 5px' }}>SHADOW &middot; CALIBRATING</span>
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
