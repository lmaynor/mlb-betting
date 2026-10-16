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
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginBottom: '12px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: 0 }}>Picks history</h1>
        <div role="group" aria-label="Range" style={{ display: 'flex', gap: '6px' }}>
          {RANGES.map(r => (
            <Link key={r} href={r === 14 ? '/picks/history' : `/picks/history?days=${r}`} aria-current={r === days ? 'true' : undefined}
              className="mono"
              style={{ fontSize: '11.5px', fontWeight: 600, padding: '5px 10px', borderRadius: '6px', textDecoration: 'none',
                color: r === days ? '#090a0f' : 'var(--silver)', background: r === days ? 'var(--signal)' : 'var(--surface-elevated)',
                border: `1px solid ${r === days ? 'var(--signal)' : '#1e2230'}` }}>
              {r}D
            </Link>
          ))}
        </div>
      </div>

      <div className="mono" style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '9px 14px', marginBottom: '14px', fontSize: '12.5px', color: 'var(--silver)', display: 'flex', gap: '6px 16px', flexWrap: 'wrap' }}>
        <span>{history.length} days</span>
        <span>{total.graded} graded</span>
        <span style={{ color: 'var(--chalk)' }}>{total.wins}-{total.losses}{total.pushes ? `-${total.pushes}` : ''}</span>
        <span style={{ color: total.units >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{total.units >= 0 ? '+' : ''}{total.units.toFixed(1)}u flat</span>
        <span style={{ color: roi == null ? 'var(--fog)' : roi >= 0 ? 'var(--signal)' : 'var(--loss)' }}>{roi == null ? '--' : `${roi >= 0 ? '+' : ''}${roi.toFixed(1)}% ROI`}</span>
        {total.pending > 0 && <span style={{ color: 'var(--fog)' }}>{total.pending} pending</span>}
      </div>

      {history.length === 0 ? (
        <div style={{ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', padding: '36px 20px', textAlign: 'center', color: 'var(--fog)', fontSize: '13px' }}>
          No settled cards in this range yet.
        </div>
      ) : (
        <HistoryTable days={history} />
      )}

      <p style={{ fontSize: '11.5px', color: 'var(--fog)', margin: '14px 2px 0', lineHeight: 1.55, maxWidth: '90ch' }}>
        Each day shows that day&rsquo;s ten highest-scoring player picks from the tracked MLB card and +EV alerts, one row per prop at the best book, ranked the same way as
        today&rsquo;s <Link href="/picks" style={{ color: 'var(--link)' }}>Top 10</Link>. Results are graded at a flat 1 unit at the flagged price; pushes and voids
        are excluded. This is not the staked ledger: see <Link href="/results" style={{ color: 'var(--link)' }}>Results</Link> for that.
        NFL, NHL and NBA picks are not tracked yet, so they have no history.
      </p>
    </div>
  )
}
