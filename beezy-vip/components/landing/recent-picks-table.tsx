import Link from 'next/link'
import { apiGetRecentSettled } from '@/lib/betting-api'
import { isWinterSport } from '@/lib/bet-notes'
import { SYSTEM_PILL, pickLabel, systemLabel } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { formatOdds } from '@/lib/odds'
import { collapseBooks } from '@/lib/dedupe'
import { BookLogo } from '@/components/ui/book-logo'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import { hasHeadshots, type HeadshotSport } from '@/lib/sport-assets'
import { playerIdByName } from '@/lib/sport-assets.server'
import { SectionHeader } from './section-header'
import type { Bet } from '@/lib/types'

const TH = 'sticky top-0 border-b border-separator-border bg-background-secondary-default px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-text-secondary'
const TD = 'border-b border-border-table px-3 py-2.5 align-middle'

function units(profit: number | null, stake: number | null) {
  if (profit == null) return '--'
  const unitSize = stake && stake > 0 ? stake : 10
  return `${profit >= 0 ? '+' : ''}${(profit / unitSize).toFixed(2)}u`
}

function pickText(bet: Bet) {
  const label = pickLabel(bet)
  if (!bet.player) return label
  return label.replace(bet.player, '').trim().replace(/^[-\s]+/, '') || label
}

const RESULT: Record<string, { label: string; color: string }> = {
  win: { label: 'WIN', color: '#22c55e' }, loss: { label: 'LOSS', color: '#ef4444' },
  push: { label: 'PUSH', color: 'var(--color-neutral-400)' }, void: { label: 'VOID', color: 'var(--color-text-secondary)' },
}

export async function RecentPicksTable() {
  let rows: Bet[] = []
  try {
    rows = collapseBooks(await apiGetRecentSettled(60)).slice(0, 6)
  } catch { rows = [] }

  return (
    <section className="pt-8">
      <SectionHeader title="Recent graded plays" href="/results" link="View full ledger &rarr;" />
      <div className="overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        {rows.length === 0 ? (
          <div className="px-4 py-5 text-center text-body-2-regular text-text-secondary">
            No settled bets yet. Check back after today&apos;s games.
          </div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={TH}>Play</th>
                <th className={TH}>Book &amp; price</th>
                <th className={cx(TH, 'text-right')}>Result</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(b => {
                const pill = SYSTEM_PILL[b.system] ?? SYSTEM_PILL.ALL
                const res = RESULT[b.result ?? ''] ?? { label: 'OPEN', color: 'var(--color-text-secondary)' }
                const sp: HeadshotSport = b.sport && hasHeadshots(b.sport.toLowerCase()) ? (b.sport.toLowerCase() as HeadshotSport) : 'mlb'
                return (
                  <tr key={b.id}>
                    <td className={cx(TD, 'min-w-0')}>
                      <div className="flex items-center gap-2">
                        <span className="dell-heading shrink-0 rounded-md px-1.5 py-0.5 font-semibold" style={{ background: pill.bg, color: pill.color, border: pill.border }}>{systemLabel(b.system, true)}</span>
                        <span className="inline-flex size-[30px] shrink-0">
                          {b.player && <PlayerHeadshot sport={sp} id={playerIdByName(sp, b.player)} name={b.player} size={30} />}
                        </span>
                        <div className="min-w-0">
                          <div className="text-body-2-bold text-text-primary">{b.player ?? pickText(b)}</div>
                          {b.player && <div className="text-caption-1-regular text-text-secondary">{pickText(b)}{isWinterSport(b.sport) && <span className="dell-heading ml-[8px] text-[#f59e0b]" title="Paper-tracked model output with no tested edge">{b.sport!.toUpperCase()} UNVALIDATED</span>}</div>}
                        </div>
                      </div>
                    </td>
                    <td className={cx('mono whitespace-nowrap text-caption-1-regular text-text-primary', TD)}>
                      <span className="inline-flex items-center gap-2">
                        {b.book ? <BookLogo book={b.book} size={22} /> : null}
                        {formatOdds(b.odds)}
                      </span>
                    </td>
                    <td className={cx(TD, 'whitespace-nowrap text-right')}>
                      <span className="mono mr-2 text-[10px] font-bold" style={{ color: res.color }}>{res.label}</span>
                      <span className="mono text-body-2-bold" style={{ color: (b.profit ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>{units(b.profit, b.stake)}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="mt-2"><Link href="/results" className="text-caption-1-regular text-[var(--link)]">All results by sport &rarr;</Link></div>
    </section>
  )
}
