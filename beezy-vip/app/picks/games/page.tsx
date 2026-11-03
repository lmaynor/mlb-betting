export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { SportBoardPage } from '@/components/picks/sport-board-page'
import { limitGameOdds } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameOddsBoard, apiGetGameProjections } from '@/lib/betting-api'
import { isMlbOffseason } from '@/lib/dates'
import { restrictProjToOdds } from '@/lib/game-board'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import { GAME_ODDS_SPORTS, type GameOddsFeed } from '@/lib/game-odds'
import { GamePicksTable } from './game-picks-table'

export const metadata: Metadata = {
  title: 'Game Picks',
  description: 'Game picks: the side where our model is furthest above the market on moneyline, spread and total, with every line and book one tap away. Model estimates are unvalidated.',
  // Hidden from the navigation, the sitemap and search: the page still answers so an old link does not 404.
  robots: { index: false, follow: false },
}

/** Game Picks. While MLB is in season its graded game card leads (tracked and settled publicly). Below it, and on its own in the winter,
 *  are the sports with a game model (NBA, NHL): the side where our model is furthest above the market fair price, one row per game.
 *  Those rows have no record yet and say so. */
export default async function GamePicksPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const viewer = await getViewer()
  const offseason = isMlbOffseason()

  const sports = GAME_ODDS_SPORTS
  const oddsRaw = await Promise.all(sports.map(s => apiGetGameOddsBoard(s).catch(() => null)))
  const odds: Record<string, GameOddsFeed | null> = {}
  sports.forEach((s, i) => {
    const f = oddsRaw[i]
    if (!f || !Array.isArray(f.rows)) { odds[s] = null; return }
    const c = limitGameOdds(f, viewer)
    odds[s] = { ...c.feed, locked: c.locked }
  })
  const projRaw = await Promise.all(sports.map(s => (odds[s] ? apiGetGameProjections(s).catch(() => null) : Promise.resolve(null))))
  const proj: Record<string, GameProjectionsFeed | null> = {}
  sports.forEach((s, i) => { proj[s] = restrictProjToOdds(projRaw[i], odds[s]) })

  return (
    <>
      {!offseason && <SportBoardPage sport="mlb" view="picks" kind="game" searchParams={searchParams} />}
      <div className="max-w-page mx-auto pt-4 px-6 pb-6 gutter-m" data-testid="game-picks-section">
        <div className="flex items-baseline justify-between gap-y-2 gap-x-4 flex-wrap mb-3">
          {offseason
            ? <h1 className="text-[20px] font-bold text-text-primary m-0">Game Picks</h1>
            : <h2 className="text-[20px] font-bold text-text-primary m-0">NBA and NHL model picks</h2>}
          <Link className="text-link text-compact" href="/ev/games">Every game and line &rarr;</Link>
        </div>
        <GamePicksTable feeds={{ odds, proj }} />
      </div>
    </>
  )
}
