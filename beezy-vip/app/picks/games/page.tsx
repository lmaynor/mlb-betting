export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { redirect } from 'next/navigation'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import { isMlbOffseason } from '@/lib/dates'
import type { SportSlug } from '@/lib/sports'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'MLB game picks: moneyline, run line and game totals, ranked by Beezy Score and settled publicly.',
}

/** Game Picks is the TRACKED, graded game record (MLB). NBA / NHL game lines are unvalidated model estimates with no record, so
 *  they live in the Scanner (/ev/games); old links with ?sport=nba|nhl|all land there. */
export default async function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  if (params.sport === 'nba' || params.sport === 'nhl') redirect(`/ev/games?sport=${params.sport}`)
  if (params.sport === 'all') redirect('/ev/games')
  const mlb: SportSlug = 'mlb'
  return (
    <>
      {isMlbOffseason() && (
        <div role="note" className="max-w-page mx-auto pt-3 px-6 pb-0 text-[12.5px] text-neutral-300">
          MLB is in the offseason, so there are no game picks until it resumes in March. NBA and NHL game lines are in the{' '}
          <Link className="text-link" href="/ev/games">Odds Terminal &rarr; Game Lines</Link>.
        </div>
      )}
      <SportBoardPage sport={mlb} view="picks" kind="game" searchParams={searchParams} />
    </>
  )
}
