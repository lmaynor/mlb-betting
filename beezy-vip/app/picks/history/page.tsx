export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { apiGetPicks } from '@/lib/betting-api'
import { addDaysToDateKey, siteDateKey } from '@/lib/dates'
import { recordOf, topFromBets, TOP_N } from '@/lib/top-picks'
import type { Bet } from '@/lib/types'
import { HistoryTable, type HistoryDay } from './history-table'

export const metadata: Metadata = {
  title: 'Picks History',
  description: 'Every past day\'s top 10 MLB player picks with the result of each, graded at a flat 1 unit.',
}

const RANGES = [7, 14, 30] as const

export default async function PicksHistoryPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams
  const asked = Number(sp.days)
  const days = (RANGES as readonly number[]).includes(asked) ? asked : 14
  const today = siteDateKey()

  // Newest first, today excluded (it lives on /picks). Days with no card (off days) are dropped.
  const dates = Array.from({ length: days }, (_, i) => addDaysToDateKey(today, -(i + 1)))
  const cards = await Promise.all(dates.map(d => apiGetPicks({ date: d, limit: 200 }).catch(() => [] as Bet[])))
  const history: HistoryDay[] = dates
    .map((date, i) => {
      const picks = topFromBets(cards[i], TOP_N)
      return { date, picks, record: recordOf(picks) }
    })
    .filter(d => d.picks.length > 0)

  const total = recordOf(history.flatMap(d => d.picks))
  const roi = total.graded > 0 ? (total.units / total.graded) * 100 : null

  return (
    <div className="max-w-[1200px] mx-auto p-[24px]">
      <div className="flex items-baseline justify-between gap-y-[8px] gap-x-[16px] flex-wrap mb-[12px]">
        <h1 className="text-[20px] font-bold text-text-primary m-0">Picks history</h1>
        <div role="group" aria-label="Range" className="flex gap-[6px]">
          {RANGES.map(r => (
            <Link key={r} href={r === 14 ? '/picks/history' : `/picks/history?days=${r}`} aria-current={r === days ? 'true' : undefined}
              className="mono"
              style={{ fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', textDecoration: 'none',
                color: r === days ? 'var(--color-background-secondary-default)' : 'var(--color-neutral-300)', background: r === days ? 'var(--signal)' : 'var(--surface-elevated)',
                border: `1px solid ${r === days ? 'var(--signal)' : 'var(--color-separator-border)'}` }}>
              {r}D
            </Link>
          ))}
        </div>
      </div>

      <div className="mono bg-[var(--color-background-primary-default)] border border-[color:var(--color-separator-border)] rounded-[8px] py-[9px] px-[14px] mb-[14px] text-[12.5px] text-[var(--color-neutral-300)] flex gap-y-[6px] gap-x-[16px] flex-wrap">
        <span>{history.length} days</span>
        <span>{total.graded} graded</span>
        <span className="text-text-primary">{total.wins}-{total.losses}{total.pushes ? `-${total.pushes}` : ''}</span>
        <span style={{ color: total.units >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{total.units >= 0 ? '+' : ''}{total.units.toFixed(1)}u flat</span>
        <span style={{ color: roi == null ? 'var(--color-text-secondary)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`}</span>
        {total.pending > 0 && <span className="text-text-secondary">{total.pending} pending</span>}
      </div>

      {history.length === 0 ? (
        <div className="bg-[var(--color-background-primary-default)] border border-[color:var(--color-separator-border)] rounded-[8px] py-[36px] px-[20px] text-center text-text-secondary text-[13px]">
          No settled cards in this range yet.
        </div>
      ) : (
        <HistoryTable days={history} />
      )}

      <p style={{ fontSize: '11.5px', color: 'var(--color-text-secondary)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>
        Each day shows that day&rsquo;s ten highest-scoring player picks from the tracked MLB card and +EV alerts, one row per prop at the best book, ranked the same way as
        today&rsquo;s <Link href="/picks" style={{ color: 'var(--link)' }}>Top 10</Link>. Results are graded at a flat 1 unit at the flagged price; pushes and voids
        are excluded. This is not the staked ledger: see <Link href="/results" style={{ color: 'var(--link)' }}>Results</Link> for that.
        NFL, NHL and NBA picks are not tracked yet, so they have no history.
      </p>
    </div>
  )
}
