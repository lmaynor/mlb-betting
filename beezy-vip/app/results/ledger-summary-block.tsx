import Link from 'next/link'
import type { LedgerSummary } from '@/lib/ledger-summary'
import { formatDateKey } from '@/lib/dates'
import { cx } from '@/utils/cx'

const CARD = 'rounded-xl border border-separator-border bg-background-primary-default shadow-card'
const TH = 'mono whitespace-nowrap border-b border-separator-border bg-background-secondary-default px-3 py-[9px] text-left text-micro text-text-secondary'
const TD = 'border-b border-border-table p-3 align-top'

export function LedgerSummaryBlock({ summary }: { summary: LedgerSummary | null }) {
  if (!summary) return null

  const startDateLong = summary.startDate
    ? formatDateKey(summary.startDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null
  const endDateLong = summary.endDate
    ? formatDateKey(summary.endDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null

  const dateClause = startDateLong && endDateLong
    ? `From ${startDateLong} through ${endDateLong}, `
    : ''

  const gradedLeaguesStr = summary.gradedLeagues.join(', ')

  const winRateStr = summary.winRate != null ? `${summary.winRate.toFixed(1)}%` : '--%'
  const roiStr = summary.roi != null ? `${summary.roi >= 0 ? '+' : ''}${summary.roi.toFixed(2)}%` : '--%'
  const clvClause = summary.avgClv != null
    ? `, and ${summary.avgClv >= 0 ? '+' : ''}${summary.avgClv.toFixed(2)}% average Closing Line Value (CLV)`
    : ''

  const totalDateRange = startDateLong && endDateLong
    ? `${startDateLong} \u2013 ${endDateLong}`
    : '—'

  const clvBenchmarkSource = summary.avgClv != null
    ? `the closing price, averaged over the ${summary.clvCount.toLocaleString()} graded plays with a recorded closing line`
    : 'the closing price'

  return (
    <section aria-labelledby="public-ledger-heading" className="mb-6" data-testid="ledger-summary-block">
      <div className="mb-2">
        <h2 id="public-ledger-heading" className="text-title-3-bold text-text-primary">
          Public Paper-Tracked Ledger
        </h2>
        <p className="mono text-caption-1-regular text-text-secondary">
          As of <time dateTime={summary.startDate ?? undefined}>{summary.asOfDate}</time> &middot; Paper record ({summary.stakingBasis})
        </p>
      </div>

      <p className="mb-3 max-w-[90ch] text-body-2-regular text-text-primary">
        {dateClause}beezy has published and graded {summary.totalGraded.toLocaleString()} paper plays across {gradedLeaguesStr}, recording {summary.wins.toLocaleString()}&ndash;{summary.losses.toLocaleString()}&ndash;{summary.pushes.toLocaleString()} ({winRateStr} win rate), {roiStr} {summary.stakingBasis} Return on Investment (ROI){clvClause}.
      </p>

      <p className="mb-4 max-w-[90ch] text-body-2-regular text-text-secondary">
        How this record is verified: Every flagged play is timestamped and published before game time, then graded automatically against final results&mdash;wins and losses alike, with losing systems included and no picks edited or deleted after the fact.
      </p>

      <div className={cx(CARD, 'mb-4 overflow-x-auto')}>
        <table className="w-full min-w-[760px] border-collapse">
          <thead>
            <tr>
              <th className={TH}>Sport</th>
              <th className={TH}>Date Range</th>
              <th className={TH}>Graded Plays</th>
              <th className={TH}>Record (W&ndash;L&ndash;P)</th>
              <th className={TH}>Win Rate</th>
              <th className={TH}>ROI ({summary.stakingBasis}, paper)</th>
              <th className={TH}>Avg CLV</th>
              <th className={TH}>Status</th>
            </tr>
          </thead>
          <tbody>
            {summary.sports.map(row => (
              <tr key={row.sport} className="transition-colors hover:bg-background-primary-hover">
                <td className={cx('mono font-semibold text-text-primary', TD)}>{row.label || row.sport}</td>
                <td className={cx('mono text-mini text-text-secondary', TD)}>{row.dateRange || '—'}</td>
                <td className={cx('mono text-mini text-text-primary', TD)}>{row.graded.toLocaleString()}</td>
                <td className={cx('mono text-mini text-text-primary', TD)}>
                  {row.wins.toLocaleString()}&ndash;{row.losses.toLocaleString()}&ndash;{row.pushes.toLocaleString()}
                </td>
                <td className={cx('mono text-mini text-text-primary', TD)}>
                  {row.winRate != null ? `${row.winRate.toFixed(1)}%` : '—'}
                </td>
                <td className={cx('mono text-mini font-semibold', TD)} style={{ color: row.roi != null ? (row.roi >= 0 ? 'var(--signal)' : 'var(--loss)') : undefined }}>
                  {row.roi != null ? `${row.roi >= 0 ? '+' : ''}${row.roi.toFixed(2)}%` : '—'}
                </td>
                <td className={cx('mono text-mini', TD)} style={{ color: row.avgClv != null ? (row.avgClv >= 0 ? 'var(--signal)' : 'var(--loss)') : undefined }}>
                  {row.avgClv != null ? `${row.avgClv >= 0 ? '+' : ''}${row.avgClv.toFixed(2)}%` : '—'}
                </td>
                <td className={cx('mono text-mini text-text-secondary', TD)}>{row.status}</td>
              </tr>
            ))}
            <tr className="border-t-2 border-border-table bg-background-secondary-default font-semibold transition-colors hover:bg-background-primary-hover">
              <td className={cx('mono font-semibold text-text-primary', TD)}>All Publicly Settled</td>
              <td className={cx('mono text-mini text-text-secondary', TD)}>{totalDateRange}</td>
              <td className={cx('mono text-mini text-text-primary', TD)}>{summary.totalGraded.toLocaleString()}</td>
              <td className={cx('mono text-mini text-text-primary', TD)}>
                {summary.wins.toLocaleString()}&ndash;{summary.losses.toLocaleString()}&ndash;{summary.pushes.toLocaleString()}
              </td>
              <td className={cx('mono text-mini text-text-primary', TD)}>
                {summary.winRate != null ? `${summary.winRate.toFixed(1)}%` : '—'}
              </td>
              <td className={cx('mono text-mini font-semibold', TD)} style={{ color: summary.roi != null ? (summary.roi >= 0 ? 'var(--signal)' : 'var(--loss)') : undefined }}>
                {summary.roi != null ? `${summary.roi >= 0 ? '+' : ''}${summary.roi.toFixed(2)}%` : '—'}
              </td>
              <td className={cx('mono text-mini', TD)} style={{ color: summary.avgClv != null ? (summary.avgClv >= 0 ? 'var(--signal)' : 'var(--loss)') : undefined }}>
                {summary.avgClv != null ? `${summary.avgClv >= 0 ? '+' : ''}${summary.avgClv.toFixed(2)}%` : '—'}
              </td>
              <td className={cx('mono text-mini text-text-secondary', TD)}>Paper-tracked</td>
            </tr>
          </tbody>
        </table>
      </div>

      <p className="mb-3 max-w-[90ch] text-caption-1-regular text-text-secondary">
        Scope and honesty note: All figures are paper-tracked model results, not real-money sportsbook fills. Individual systems include negative-ROI records; every settled public system is counted above. Leagues still in preview or research (NFL, NBA and NHL) are excluded from public totals until promoted. Closing Line Value compares each play&apos;s published price against {clvBenchmarkSource}; beezy tracks CLV on its own graded plays and provides CLV data exports on the $299/yr annual plan (beezy does not sync personal sportsbook accounts).
      </p>

      <p className="mb-4 text-caption-1-regular text-text-secondary">
        Read full walk-forward methodology, out-of-sample validation, and per-system results on{' '}
        <Link href="/models" className="text-link hover:underline">
          /models &rarr;
        </Link>
      </p>
    </section>
  )
}
