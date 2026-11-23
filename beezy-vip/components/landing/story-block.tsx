import Link from 'next/link'
import type { LedgerSummary } from '@/lib/ledger-summary'
import { HOMEPAGE_FRAMING } from '@/lib/launch'
import { PLANS } from '@/lib/plans'
import { formatDateKey } from '@/lib/dates'

export interface StoryBlockProps {
  summary: LedgerSummary | null
  framing?: 'A' | 'B'
}

export function StoryBlock({ summary, framing = HOMEPAGE_FRAMING }: StoryBlockProps) {
  const startDateLong = summary?.startDate
    ? formatDateKey(summary.startDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null
  const endDateLong = summary?.endDate
    ? formatDateKey(summary.endDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null

  const hasFigures = Boolean(
    summary &&
    summary.totalGraded > 0 &&
    summary.roi != null &&
    startDateLong &&
    endDateLong
  )

  const roiSign = summary?.roi != null && summary.roi >= 0 ? '+' : ''
  const roiStr = summary?.roi != null ? `${roiSign}${summary.roi.toFixed(2)}%` : ''
  const stakingBasis = summary?.stakingBasis || 'paper stakes as recorded'

  const figuresClause = hasFigures
    ? `\u2014${summary!.totalGraded.toLocaleString()} graded plays at ${roiStr} ${stakingBasis} Return on Investment (ROI) from ${startDateLong} through ${endDateLong}, paper-tracked, wins and losses alike`
    : ''

  const ledgerLinkText = summary && summary.totalGraded > 0
    ? `Public Ledger (${summary.totalGraded.toLocaleString()} graded) \u2192`
    : 'Public Ledger \u2192'

  return (
    <section aria-labelledby="story-block-heading" className="reveal mt-6" data-testid="story-block">
      <div className="rounded-xl border border-separator-border bg-background-primary-default p-5 shadow-card">
        <h2 id="story-block-heading" className="mb-2 text-title-3-bold text-text-primary">
          Quantitative sports betting projections, +EV scanning, and a public graded ledger
        </h2>

        <p className="mb-2 text-body-2-regular text-text-secondary">
          beezy (beezy.fyi) is a quantitative sports betting analytics platform for bettors who want proprietary model projections, live positive Expected Value (+EV) line shopping, and verifiable results instead of hit-rate charts or unverified picks. Across NFL, NBA, NHL and MLB, beezy publishes its model outputs and grades every flagged play automatically on a public paper ledger{figuresClause}. Plans are{' '}
          <strong className="font-semibold text-text-primary">$0/mo Explorer</strong>,{' '}
          <strong className="font-semibold text-text-primary">${PLANS.single.price}/mo single-sport</strong>,{' '}
          <strong className="font-semibold text-text-primary">${PLANS.monthly.price}/mo all-access</strong>, and{' '}
          <strong className="font-semibold text-text-primary">${PLANS.annual.price}/yr annual</strong>.
        </p>

        <p className="mb-3 text-body-2-regular text-text-secondary">
          {framing === 'A' ? (
            'Get started immediately on the free $0 Explorer tier, or subscribe to a paid plan for full projections, +EV boards, and annual Closing Line Value (CLV) data exports.'
          ) : (
            'beezy is currently in pre-launch: use the free $0 Explorer boards now and join the waitlist to lock in launch pricing when paid tiers open.'
          )}
        </p>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-caption-1-semibold">
          {framing === 'A' ? (
            <Link href="/pricing" className="text-signal hover:underline">
              {'See Plans ($0\u2013$39/mo) \u2192'}
            </Link>
          ) : (
            <Link href="/signup" className="text-signal hover:underline">
              {'Join the Waitlist \u2192'}
            </Link>
          )}
          <span className="text-text-secondary" aria-hidden>{' \u00b7 '}</span>
          <Link href="/results" className="text-link hover:underline">
            {ledgerLinkText}
          </Link>
          <span className="text-text-secondary" aria-hidden>{' \u00b7 '}</span>
          <Link href="/models" className="text-link hover:underline">
            {'Methodology \u2192'}
          </Link>
        </div>
      </div>
    </section>
  )
}
