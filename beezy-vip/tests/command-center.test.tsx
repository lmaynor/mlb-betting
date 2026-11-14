import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { CommandCenterClient } from '@/components/landing/command-center-client'
import type { Bet } from '@/lib/types'

const bet = (over: Partial<Bet>): Bet => ({
  id: 1, sport: 'nba', system: 'EV', game_date: '2026-11-14', game_pk: 0,
  bet_type: 'rebounds OVER 4.5', player: 'Ayo Dosunmu', away_team: 'CHI', home_team: 'MIN',
  odds: 132, stake: 10, model_prob: 0.5, market_prob: 0.4, edge: 0.101, kelly_pct: 0.02,
  kelly_triggered: true, result: null, profit: null, paper: true, book: 'prophetx',
  notes: null, created_at: '2026-11-14T00:00:00Z', ...over,
} as Bet)

describe('CommandCenterClient top plays', () => {
  it('renders the headshot when the resolved entry names the same player', () => {
    const { container } = render(<CommandCenterClient bets={[bet({})]} headshots={{ 1: { sport: 'nba', id: 4397002, name: 'Ayo Dosunmu' } }} />)
    expect(container.querySelector('img[src*="headshots/nba/players/full/4397002"]')).not.toBeNull()
  })

  it('fails closed to the silhouette when the headshot entry names a different player (id collision)', () => {
    const { container } = render(<CommandCenterClient bets={[bet({})]} headshots={{ 1: { sport: 'nhl', id: 8474563, name: 'Drew Doughty' } }} />)
    expect(container.querySelector('img[src*="nhle.com"]')).toBeNull()
    expect(screen.getByLabelText('No photo')).toBeInTheDocument()
  })

  it('renders the canonical book logo beside the book code', () => {
    const { container } = render(<CommandCenterClient bets={[bet({})]} headshots={{}} />)
    expect(container.querySelector('img[src="/logos/books/prophetx.png"]')).not.toBeNull()
    expect(screen.getByText(/PRX/)).toBeInTheDocument()
  })
})
