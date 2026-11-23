import Link from 'next/link'
import type { LedgerSummary } from '@/lib/ledger-summary'
import { formatDateKey } from '@/lib/dates'

export interface GamesHeaderProps {
  summary: LedgerSummary | null
  date?: Date | string
  headingLevel?: 'h1' | 'h2'
  activeLeagues?: string
  linesAgeMinutes?: number | null
  linesUpdated?: string | null
}

/**
 * Format lines age in minutes into the site's terse style:
 * - for n < 60: '{n}m ago'
 * - for n >= 60: '{h}h {m}m ago' or '{h}h ago' (when remainder is 0)
 */
export function formatLinesAge(minutes: number): string {
  if (!isFinite(minutes) || isNaN(minutes)) return ''
  const n = Math.max(0, Math.round(minutes))
  if (n < 60) {
    return `${n}m ago`
  }
  const h = Math.floor(n / 60)
  const m = n % 60
  return m > 0 ? `${h}h ${m}m ago` : `${h}h ago`
}

function getDates(input?: Date | string) {
  let d: Date
  if (input instanceof Date) {
    d = input
  } else if (typeof input === 'string') {
    d = new Date(input.includes('T') ? input : `${input}T12:00:00Z`)
  } else {
    d = new Date()
  }
  const todayLong = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', month: 'long', day: 'numeric', year: 'numeric' }).format(d)
  const todayShort = new Intl.DateTimeFormat('en-US', { timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric' }).format(d)
  return { todayLong, todayShort }
}

/**
 * AEO-02 Item 5: Server-rendered header block for /picks/games.
 * Renders Neon Item 5 verbatim copy with slots filled from LedgerSummary and site metadata.
 */
export function GamesHeader({
  summary,
  date,
  headingLevel = 'h2',
  activeLeagues = 'NBA and NHL',
  linesAgeMinutes = null,
  linesUpdated = null,
}: GamesHeaderProps) {
  const { todayLong, todayShort } = getDates(date)
  const HeadingTag = headingLevel === 'h1' ? 'h1' : 'h2'

  let linesUpdatedStr: string | null = null
  if (typeof linesAgeMinutes === 'number' && isFinite(linesAgeMinutes)) {
    linesUpdatedStr = formatLinesAge(linesAgeMinutes)
  } else if (linesUpdated) {
    linesUpdatedStr = linesUpdated
  }

  const linesUpdatedClause = linesUpdatedStr ? ` \u00b7 Lines updated: ${linesUpdatedStr}` : ''

  const startDateLong = summary?.startDate
    ? formatDateKey(summary.startDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null
  const endDateLong = summary?.endDate
    ? formatDateKey(summary.endDate.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
    : null

  const dateRangePart = startDateLong && endDateLong
    ? `, ${startDateLong} \u2013 ${endDateLong}`
    : ''

  const gradedLeaguesStr = summary?.gradedLeagues?.join(', ') ?? 'MLB'

  const roiStr = summary?.roi != null
    ? `${summary.roi >= 0 ? '+' : ''}${summary.roi}%`
    : '--%'

  const clvPart = summary?.avgClv != null
    ? ` and ${summary.avgClv >= 0 ? '+' : ''}${summary.avgClv}% average CLV`
    : ''

  return (
    <section aria-labelledby="games-header-heading" className="mb-4 rounded-xl border border-separator-border bg-background-primary-default p-4 shadow-card" data-testid="games-header">
      <HeadingTag id="games-header-heading" className="text-[16px] font-bold text-text-primary m-0 mb-1">
        Daily Model Game Projections &mdash; {todayLong}
      </HeadingTag>

      <p className="mono text-caption-2 text-text-secondary m-0 mb-2.5">
        Date: {todayShort} &middot; Daily card publishes: 11:00 AM CT{linesUpdatedClause}
      </p>

      <p className="mb-2 max-w-[90ch] text-body-2-regular text-text-primary">
        This board compares beezy&apos;s quantitative game probabilities against current sportsbook prices across today&apos;s active slates ({activeLeagues}), listing matchups where the model price sits above the market. Rows tagged &quot;Unvalidated&quot; come from leagues or models running live for research and preview that are not yet settled in beezy&apos;s public results ledger&mdash;see Methodology (<Link href="/models" className="text-link hover:underline">/models</Link>) for each sport&apos;s current validation status.
      </p>

      {summary && (
        <p className="mb-2 max-w-[90ch] text-body-2-regular text-text-primary" data-testid="games-header-ledger-record">
          Public ledger record ({gradedLeaguesStr} settled systems{dateRangePart}):{' '}
          {summary.totalGraded.toLocaleString()} paper-graded plays at {roiStr} {summary.stakingBasis} ROI{clvPart}, wins and losses included (View full ledger on <Link href="/results" className="text-link hover:underline">/results &rarr;</Link>).
        </p>
      )}

      <p className="m-0 max-w-[90ch] text-caption-1-regular text-text-secondary">
        Reading the table: Model Side is the side beezy prices above the market; Model Prob is beezy&apos;s estimated win probability; Gap is the difference between model probability and implied market probability; Best Price is the strongest available sportsbook odds scanned.
      </p>
    </section>
  )
}
