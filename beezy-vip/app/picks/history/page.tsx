export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { addDaysToDateKey, siteDateKey } from '@/lib/dates'
import { recordOf } from '@/lib/top-picks'
import { loadDailyPlaysHistory } from '@/lib/top-picks-data'
import { HistoryTable } from './history-table'

export const metadata: Metadata = {
  title: 'Graded History',
  description: 'Every past day\'s Daily Card: each official play with its result, graded at a flat 1 unit.',
}

const RANGES = [7, 14, 30] as const

export default async function PicksHistoryPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams
  const asked = Number(sp.days)
  const days = (RANGES as readonly number[]).includes(asked) ? asked : 14
  const today = siteDateKey()

  // Newest first, today excluded (it lives on the Daily Card). Days with no plays (off days) are dropped.
  const dates = Array.from({ length: days }, (_, i) => addDaysToDateKey(today, -(i + 1)))
  const history = await loadDailyPlaysHistory(dates)

  const total = recordOf(history.flatMap(d => d.picks))
  const roi = total.graded > 0 ? (total.units / total.graded) * 100 : null

  return (
    <div className="max-w-page mx-auto p-6">
      <div className="flex items-baseline justify-between gap-y-2 gap-x-4 flex-wrap mb-3">
        <h1 className="text-[20px] font-bold text-text-primary m-0">Graded history</h1>
        <div role="group" aria-label="Range" className="flex gap-1.5">
          {RANGES.map(r => (
            <Link key={r} href={r === 14 ? '/picks/history' : `/picks/history?days=${r}`} aria-current={r === days ? 'true' : undefined}
              className="mono text-[12px] font-semibold py-[5px] px-[10px] no-underline"
              style={{
                borderRadius: '6px',
                color: r === days ? 'var(--color-background-secondary-default)' : 'var(--color-neutral-300)',
                background: r === days ? 'var(--signal)' : 'var(--surface-elevated)',
                border: `1px solid ${r === days ? 'var(--signal)' : 'var(--color-separator-border)'}`,
              }}>
              {r}D
            </Link>
          ))}
        </div>
      </div>

      <p className="text-body-2-regular text-text-secondary max-w-[90ch] mb-3.5">The Daily Card&apos;s record: every official play from each past day, graded at a flat 1 unit. Top Signals are not graded here; the staked ledger is <Link className="text-link" href="/results">Results</Link>.</p>

      <div className="mono bg-background-primary-default border border-separator-border rounded-control py-[9px] px-3.5 mb-3.5 text-[13px] text-neutral-300 flex gap-y-1.5 gap-x-4 flex-wrap">
        <span>{history.length} days</span>
        <span>{total.graded} graded</span>
        <span className="text-text-primary">{total.wins}-{total.losses}{total.pushes ? `-${total.pushes}` : ''}</span>
        <span style={{ color: total.units >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{total.units >= 0 ? '+' : ''}{total.units.toFixed(1)}u flat</span>
        <span style={{ color: roi == null ? 'var(--color-text-secondary)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`}</span>
        {total.pending > 0 && <span className="text-text-secondary">{total.pending} pending</span>}
      </div>

      {history.length === 0 ? (
        <div className="bg-background-primary-default border border-separator-border rounded-control py-9 px-5 text-center text-text-secondary text-cozy">
          No daily plays in this range yet. The tracker started logging NFL, NHL and NBA plays on 2 Nov 2026; MLB resumes in March.
        </div>
      ) : (
        <HistoryTable days={history} />
      )}

      <p className="text-[12px] text-text-secondary leading-[1.55] max-w-[90ch]" style={{ margin: '14px 2px 0' }}>
        Each day is that day&rsquo;s <Link className="text-link" href="/cheat-sheet">Daily Card</Link>: every official play, no count cap, one row per prop at the best
        book. A play is official when it clears the model-plus-market gate described there; it is logged at the flagged price the first time it qualifies and never
        re-priced afterwards, so this record cannot be edited after the fact. Results are graded at a flat 1 unit; pushes and voids are excluded. NFL, NHL and NBA plays
        are paper and unvalidated: their models have no tested edge against prices, and this record is how that gets settled. See{' '}
        <Link className="text-link" href="/results">Results</Link> for the full staked ledger, or <Link className="text-link" href="/results?population=daily">just the Daily Card plays</Link> inside it.
      </p>
    </div>
  )
}
