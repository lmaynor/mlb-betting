'use client'

import { Fragment, useMemo, useState } from 'react'
import Link from 'next/link'
import type { Bet } from '@/lib/types'
import { beezyscore } from '@/lib/beezy-score'
import { bookDisplay, pickLabel } from '@/lib/tokens'
import { cx } from '@/utils/cx'
import { formatOdds } from '@/lib/odds'
import { SystemBadge } from '@/components/ui/primitives'
import { collapseBooks } from '@/lib/dedupe'
import { readableNotes } from '@/lib/bet-notes'
import { PlayerHeadshot } from '@/components/ui/sport-assets'
import type { HeadshotSport } from '@/lib/sport-assets'

const TH = 'sticky top-0 border-b border-separator-border bg-background-secondary-default px-3 py-2 text-left text-micro font-semibold text-text-secondary'
const TD = 'border-b border-border-table px-3 py-2.5 align-middle'

export function CommandCenterClient({ bets, headshots }: { bets: Bet[]; headshots: Record<number, { sport: HeadshotSport; id: number | string | null }> }) {
  const [open, setOpen] = useState<number | null>(null)
  const top = useMemo(() => collapseBooks([...bets].sort((a, b) => beezyscore(b) - beezyscore(a))).slice(0, 6), [bets])

  return (
    <section className="mt-7" aria-label="Today's top plays">
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 className="text-headline-bold text-text-primary">Today&apos;s top plays</h2>
        <span className="flex gap-3.5">
          <Link href="/ev" className="text-caption-1-semibold text-link hover:underline">Odds Screen &rarr;</Link>
          <Link href="/picks" className="text-caption-1-semibold text-link hover:underline">All picks &rarr;</Link>
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border border-separator-border bg-background-primary-default shadow-card">
        {top.length === 0 ? (
          <div className="p-5 text-center text-body-2-regular text-text-secondary">
            No open plays right now. The model card publishes after the ~11:00 AM CT run and the +EV scanner re-checks lines every 15 minutes.
          </div>
        ) : (
          <table className="w-full border-collapse">
            <thead>
              <tr>
                <th className={TH}>Play</th>
                <th className={TH}>Book &amp; price</th>
                <th className={cx(TH, 'text-right')}>Edge</th>
              </tr>
            </thead>
            <tbody>
              {top.map(b => {
                const book = bookDisplay(b.book)
                const bullets = (readableNotes(b.notes) ?? '').split(' . ').map(s => s.trim()).filter(Boolean)
                const isOpen = open === b.id
                const edge = Math.abs(b.edge ?? 0) < 2 ? (b.edge ?? 0) * 100 : (b.edge ?? 0)
                return (
                  <Fragment key={b.id}>
                    <tr onClick={bullets.length ? () => setOpen(isOpen ? null : b.id) : undefined} className={cx(bullets.length ? 'cursor-pointer hover:bg-background-primary-hover' : 'cursor-default')} aria-expanded={bullets.length ? isOpen : undefined}>
                      <td className={TD}>
                        <div className="flex items-center gap-2">
                          <SystemBadge system={b.system} />
                          {headshots[b.id] && <PlayerHeadshot sport={headshots[b.id].sport} id={headshots[b.id].id} name={b.player ?? undefined} size={32} />}
                          <span className="text-body-2-bold text-text-primary">{pickLabel(b)}</span>
                        </div>
                      </td>
                      <td className={cx('mono whitespace-nowrap text-caption-1-regular text-text-primary', TD)}>
                        {b.book ? book.short : '--'}{b.bookCount > 1 ? ` +${b.bookCount - 1}` : ''} {formatOdds(b.odds)}
                      </td>
                      <td className={cx('mono text-right text-body-2-bold text-signal', TD)}>+{edge.toFixed(1)}%</td>
                    </tr>
                    {isOpen && (
                      <tr><td colSpan={3} className={cx(TD, 'bg-background-secondary-default')}>
                        <ul className="m-0 grid list-none gap-1 p-0">
                          {bullets.map((t, i) => <li key={i} className="mono text-mini text-text-secondary">&middot; {t}</li>)}
                        </ul>
                      </td></tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
