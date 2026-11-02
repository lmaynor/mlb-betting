import Link from 'next/link'
import { SPORT_PILLARS } from '@/lib/sports'
import { scoreTier, TIER_META } from '@/lib/beezy-score'
import { bookDisplay } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { formatOdds } from '@/lib/odds'
import { sportHref } from '@/lib/ia'
import { isOfficialCardPlay, isEvOutlier, type PickBasis, type TopPick } from '@/lib/top-picks'

const TH = 'ui-heading sticky top-0 whitespace-nowrap border-b border-separator-border bg-background-secondary-default px-2.5 py-2.5 text-left text-text-secondary'
const TD = 'px-2.5 py-2.5 align-middle'

const BASIS_LABEL: Record<PickBasis, string> = { model: 'Model', ev: '+EV alert', projection: 'Projection', confluence: 'Model + market' }

const RESULT: Record<string, { label: string; color: string }> = {
  win: { label: 'WIN', color: 'var(--signal)' },
  loss: { label: 'LOSS', color: 'var(--loss)' },
  push: { label: 'PUSH', color: 'var(--color-neutral-300)' },
  void: { label: 'VOID', color: 'var(--color-text-secondary)' },
}

export const fmtEdge = (e: number | null) => (e == null ? '--' : `${e >= 0 ? '+' : ''}${(e * 100).toFixed(1)}%`)

/** One real table for a ranked pick list: today's Top 10 and each day of the history share it. */
export function TopPicksTable({ picks, showResult = false, startRank = 1 }: { picks: TopPick[]; showResult?: boolean; startRank?: number }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-separator-border bg-background-primary-default shadow-card">
      <table className={cx('w-full border-collapse text-body-2-regular', showResult ? 'min-w-[900px]' : 'min-w-[820px]')}>
        <thead>
          <tr>
            {['# Sport', 'Pick', 'Best price', 'Score', 'Edge', 'Game', 'Source', ...(showResult ? ['Result'] : [])].map(h => (
              <th key={h} className={TH}>{h}</th>
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
              <tr key={p.key} className="sys-row border-b border-border-table">
                <td className={cx(TD, 'whitespace-nowrap')}>
                  <span className="mono inline-block w-[22px] text-text-secondary">{startRank + i}</span>
                  <Link href={sportHref(p.sport)} className="ui-heading no-underline" style={{ color: pillar.accent }}>{pillar.shortName}</Link>
                </td>
                <td className={cx(TD, 'min-w-[170px]')}>
                  <div className="font-semibold text-text-primary">{p.who}</div>
                  {p.what && <div className="mt-0.5 text-caption-1-regular text-text-secondary">{p.what}</div>}
                </td>
                <td className={cx(TD, 'whitespace-nowrap')}>
                  {p.odds == null ? <span className="text-text-secondary">--</span> : (
                    <span className="inline-flex items-center gap-[7px]">
                      <span className="mono text-text-primary">{formatOdds(p.odds)}</span>
                      {b && <span className="text-caption-2-bold" style={{ color: b.color }}>{b.label}</span>}
                    </span>
                  )}
                </td>
                <td className={TD}>
                  <div className="inline-flex items-center gap-1.5">
                    {isOfficial ? (
                      <span className="ui-heading" style={{ fontSize: '10px', color: '#57F287', border: '1px solid #57F287', borderRadius: '4px', padding: '1px 5px', background: 'rgba(87,242,135,0.12)' }}>OFFICIAL PLAY</span>
                    ) : isOutlier ? (
                      <span className="ui-heading" style={{ fontSize: '10px', color: '#38bdf8', border: '1px solid #38bdf8', borderRadius: '4px', padding: '1px 5px', background: 'rgba(56,189,248,0.12)' }}>+EV OUTLIER</span>
                    ) : null}
                    <span className="mono" title={`Beezy Score ${p.score}. Not a win probability.`} style={{ color: isOfficial ? '#57F287' : isOutlier ? '#38bdf8' : 'var(--color-neutral-300)', fontWeight: 700 }}>{p.score}</span>
                  </div>
                </td>
                <td className={cx('mono', TD)} style={{ color: (p.edge ?? 0) > 0 ? 'var(--signal)' : 'var(--color-text-secondary)' }}>{fmtEdge(p.edge)}</td>
                <td className={cx('mono whitespace-nowrap text-text-secondary', TD)}>{p.game || '--'}</td>
                <td className={cx(TD, 'whitespace-nowrap text-text-secondary')}>
                  {BASIS_LABEL[p.basis]}
                  {p.tracked && p.unvalidated && (
                    <span className="ui-heading" title="The model behind this pick has no tested edge against prices. It is tracked on paper at 1 unit and graded after the game." style={{ marginLeft: '8px', fontSize: '10px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 5px' }}>UNVALIDATED &middot; PAPER</span>
                  )}
                  {!p.tracked && (
                    <span className="ui-heading" title="Projections come from an empirical estimator on game logs and are not tracked in the public results yet." style={{ marginLeft: '8px', fontSize: '10px', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.35)', borderRadius: '4px', padding: '1px 5px' }}>SHADOW &middot; CALIBRATING</span>
                  )}
                </td>
                {showResult && (
                  <td className={cx('ui-heading', TD)} style={{ color: res?.color ?? 'var(--link)' }}>{res?.label ?? 'PENDING'}</td>
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
