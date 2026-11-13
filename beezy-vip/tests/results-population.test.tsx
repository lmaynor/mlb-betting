import '@testing-library/jest-dom'
/** Results population selector (IA S5): the canonical record opens on the Official Daily Card;
 *  ?population=all widens to every tracked paper bet, ?population=ev narrows to the +EV alert pool. */
import { render, screen } from '@testing-library/react'
import { ResultsClient } from '@/app/results/results-client'
import type { Bet } from '@/lib/types'

let mockParams = new URLSearchParams()
jest.mock('next/navigation', () => ({
  useSearchParams: () => mockParams,
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  usePathname: () => '/results',
}))

const bet = (o: Partial<Bet>): Bet => ({
  id: 1, system: 'K', game_date: '2026-10-16', game_pk: 100, bet_type: 'K_OVER_5.5', player: 'Gerrit Cole',
  away_team: 'NYY', home_team: 'BOS', odds: 110, stake: 25, model_prob: 0.58, market_prob: 0.5, edge: 0.08,
  kelly_pct: 0.02, kelly_triggered: true, result: 'win', profit: 27.5, paper: true, book: 'draftkings', notes: null,
  created_at: '2026-10-16T16:00:00Z', ...o,
})

const mlbBet = bet({})
const dailyBet = bet({
  id: 2, system: 'PROPS', sport: 'nba', game_date: '2026-11-03', game_pk: 0, bet_type: 'rebounds_over_10.5',
  player: 'Jalen Duren', away_team: 'CHI', home_team: 'DET', odds: 105, stake: 10, result: 'loss', profit: -10,
  book: 'fanduel', notes: JSON.stringify({ tier: 'official' }),
})
const evBet = bet({
  id: 3, system: 'EV', sport: 'nhl', game_date: '2026-11-05', game_pk: 0, bet_type: 'saves_over_26.5',
  player: 'Igor Shesterkin', away_team: 'NYR', home_team: 'BOS', odds: 115, stake: 100, result: 'win', profit: 115,
  book: 'betmgm', notes: null,
})

describe('Results population selector', () => {
  it('opens on the Official Daily Card by default', () => {
    mockParams = new URLSearchParams()
    render(<ResultsClient initialPicks={[mlbBet, dailyBet, evBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Jalen Duren/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Gerrit Cole/)).toHaveLength(0)
    expect(screen.queryAllByText(/Igor Shesterkin/)).toHaveLength(0)
    expect(screen.getByRole('button', { name: /Official Daily Card \(1\)/ })).toBeInTheDocument()
  })

  it('?population=all shows the whole ledger and marks the rows that were Daily Card plays', () => {
    mockParams = new URLSearchParams('population=all')
    render(<ResultsClient initialPicks={[mlbBet, dailyBet, evBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Gerrit Cole/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Jalen Duren/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Igor Shesterkin/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Daily Card').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: /All Tracked Paper \(3\)/ })).toBeInTheDocument()
  })

  it('?population=ev narrows the ledger to the +EV alert pool only', () => {
    mockParams = new URLSearchParams('population=ev')
    render(<ResultsClient initialPicks={[mlbBet, dailyBet, evBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Igor Shesterkin/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Gerrit Cole/)).toHaveLength(0)
    expect(screen.queryAllByText(/Jalen Duren/)).toHaveLength(0)
    expect(screen.getByRole('button', { name: /\+EV Alerts \(1\)/ })).toBeInTheDocument()
  })

  it('?population=daily still narrows to the Daily Card plays (PR #254 links stay valid)', () => {
    mockParams = new URLSearchParams('population=daily')
    render(<ResultsClient initialPicks={[mlbBet, dailyBet, evBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Jalen Duren/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Gerrit Cole/)).toHaveLength(0)
  })
})
