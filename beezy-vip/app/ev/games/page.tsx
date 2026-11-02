export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import Link from 'next/link'
import { limitGameLines, limitGameOdds } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameOddsBoard, apiGetGameProjections } from '@/lib/betting-api'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import type { GameOddsFeed } from '@/lib/game-odds'
import { GameLinesClient } from './game-lines-client'
import { GamePricesClient } from './game-prices-client'
import { GamesNav, parseGameSport, parseGamesView } from './games-nav'

export const metadata: Metadata = {
  title: 'Game Lines',
  description: 'NBA and NHL moneyline, spread / puck line and total: every book\'s price side by side with best price, de-vigged fair value and arbitrage, plus the model estimate. Unvalidated.',
}

/** The Scanner's Game Lines tab. PRICES (default) is the price screen: every book's game-line prices with best price, fair
 *  value from the reference books you pick, EV and arbitrage. MODEL is "what is the model saying against the market?", the
 *  game-line counterpart of the Odds Terminal's player props. Both are UNVALIDATED and have no tracked record, so they live
 *  here beside the market data and not under Picks (the tracked, graded record). MLB's tracked game picks stay at /picks/games. */
export default async function GameLinesPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const sport = parseGameSport(params.sport)
  const view = parseGamesView(params.view)
  const viewer = await getViewer()
  const wants = (s: 'nba' | 'nhl') => sport === 'all' || sport === s

  let body: React.ReactNode
  if (view === 'model') {
    const [nba, nhl] = await Promise.all([
      wants('nba') ? apiGetGameProjections('nba').catch(() => null) : Promise.resolve(null),
      wants('nhl') ? apiGetGameProjections('nhl').catch(() => null) : Promise.resolve(null),
    ])
    // Same tier rule as the Odds Terminal (limitGameLines): cut before the payload leaves the server.
    const cut = (feed: GameProjectionsFeed | null): GameProjectionsFeed | null => {
      if (!feed) return null
      const c = limitGameLines(feed, viewer)
      return { ...c.feed, locked: c.locked }
    }
    body = <GameLinesClient feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={sport} />
  } else {
    const [nba, nhl] = await Promise.all([
      wants('nba') ? apiGetGameOddsBoard('nba').catch(() => null) : Promise.resolve(null),
      wants('nhl') ? apiGetGameOddsBoard('nhl').catch(() => null) : Promise.resolve(null),
    ])
    // The price screen is the Odds Terminal's Pro rule (limitGameOdds): free viewers get a small preview, cut here, server-side.
    const cut = (feed: GameOddsFeed | null): GameOddsFeed | null => {
      if (!feed || !Array.isArray(feed.rows)) return null
      const c = limitGameOdds(feed, viewer)
      return { ...c.feed, locked: c.locked }
    }
    body = <GamePricesClient key={sport} feeds={{ nba: cut(nba), nhl: cut(nhl) }} sport={sport} />
  }

  return (
    <div className="max-w-[1400px] mx-auto pt-4 px-6 pb-6">
      <div className="flex items-baseline justify-between gap-y-2 gap-x-4 flex-wrap mb-3">
        <h1 className="text-[20px] font-bold text-text-primary m-0">Game Lines</h1>
        <GamesNav view={view} sport={sport} />
      </div>
      {body}
      <p className="text-compact text-text-secondary mt-4">
        Looking for MLB game picks with a graded record? They are under <Link className="text-link" href="/picks/games">Picks &rarr; Game Picks</Link>.
      </p>
    </div>
  )
}
