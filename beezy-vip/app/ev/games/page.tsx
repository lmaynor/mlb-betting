export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { limitGameOdds } from '@/lib/access-feeds'
import { getViewer } from '@/lib/auth'
import { apiGetGameOddsBoard, apiGetGameProjections } from '@/lib/betting-api'
import { restrictProjToOdds } from '@/lib/game-board'
import type { GameProjectionsFeed } from '@/lib/game-lines'
import { GAME_ODDS_SPORTS, type GameOddsFeed } from '@/lib/game-odds'
import { BestBoard } from './best-board'
import { GamePricesClient } from './game-prices-client'
import { analysisGame, GamesNav, parseGameSport, parseGamesView } from './games-nav'

export const metadata: Metadata = {
  title: 'Game Lines',
  description: 'Every game\'s moneyline, spread and total: our projection, every alternate line and every book\'s price side by side, with best price, fair value and arbitrage. Unvalidated.',
}

/** The Scanner's Game Lines tab. BOARD (default): one row per game with the best price at every market, the edges worth a look and how each game has moved; double-click a row for its card (markets, movement, and the full analysis) (our projection
 *  against the market on every alternate line, and every book side by side). TABLE is the dense all-rows price screen. Both are
 *  UNVALIDATED and have no tracked record, so they live here beside the market data and not under Picks. MLB's tracked game picks
 *  stay at /picks/games. */
export default async function GameLinesPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const params = await searchParams
  const view = parseGamesView(params.view)
  const sport = parseGameSport(params.sport)
  const viewer = await getViewer()
  const sports = view === 'table' ? GAME_ODDS_SPORTS.filter(s => sport === 'all' || sport === s) : GAME_ODDS_SPORTS

  const oddsRaw = await Promise.all(sports.map(s => apiGetGameOddsBoard(s).catch(() => null)))
  // The price screen is the Odds Terminal's Pro rule (limitGameOdds): free viewers get a small preview, cut here, server-side.
  const odds: Record<string, GameOddsFeed | null> = {}
  sports.forEach((s, i) => {
    const f = oddsRaw[i]
    if (!f || !Array.isArray(f.rows)) { odds[s] = null; return }
    const c = limitGameOdds(f, viewer)
    odds[s] = { ...c.feed, locked: c.locked }
  })

  let body: React.ReactNode
  if (view === 'table') {
    body = <GamePricesClient key={sport} feeds={odds} sport={sport} />
  } else {
    const projRaw = await Promise.all(sports.map(s => (odds[s] ? apiGetGameProjections(s).catch(() => null) : Promise.resolve(null))))
    // The model never rides along for a game the viewer's price preview withheld.
    const proj: Record<string, GameProjectionsFeed | null> = {}
    sports.forEach((s, i) => { proj[s] = restrictProjToOdds(projRaw[i], odds[s]) })
    const open = analysisGame(params.view, params.game)
    body = <BestBoard feeds={{ odds, proj }} initialSport={params.sport === 'nba' || params.sport === 'nhl' ? params.sport : undefined} initialOpen={open ? { key: open, pane: 'analysis' } : undefined} />
  }

  return (
    <div className="max-w-[1400px] mx-auto pt-4 px-6 pb-6 gutter-m">
      <div className="flex items-baseline justify-between gap-y-2 gap-x-4 flex-wrap mb-3">
        <h1 className="text-[20px] font-semibold text-text-primary m-0 sr-only md:not-sr-only">Game Lines</h1>
        <GamesNav view={view} sport={sport} />
      </div>
      {body}
    </div>
  )
}
