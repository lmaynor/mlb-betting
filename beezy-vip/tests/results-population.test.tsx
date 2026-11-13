import '@testing-library/jest-dom'
/** Results population filter: the default view is the whole settled ledger with Daily Card rows marked;
 *  ?population=daily narrows the list to the Daily Card plays only. */
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

describe('Results population filter', () => {
  it('shows the whole ledger by default and marks the rows that were Daily Card plays', () => {
    mockParams = new URLSearchParams()
    render(<ResultsClient initialPicks={[mlbBet, dailyBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Gerrit Cole/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Jalen Duren/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Daily Card').length).toBeGreaterThan(0)
  })

  it('?population=daily narrows the ledger to the Daily Card plays only', () => {
    mockParams = new URLSearchParams('population=daily')
    render(<ResultsClient initialPicks={[mlbBet, dailyBet]} initialStats={[]} dailyPicks={[dailyBet]} />)
    expect(screen.queryAllByText(/Jalen Duren/).length).toBeGreaterThan(0)
    expect(screen.queryAllByText(/Gerrit Cole/)).toHaveLength(0)
    expect(screen.getByRole('button', { name: /Daily Picks \(1\)/ })).toBeInTheDocument()
  })
})
