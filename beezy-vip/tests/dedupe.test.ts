import { collapseBooks, propKey } from '../lib/dedupe'
import type { Bet } from '../lib/types'

const bet = (over: Partial<Bet>): Bet => ({
  id: 1, system: 'EV', game_date: '2026-10-13', game_pk: 849813, bet_type: 'K_UNDER_4.5_betrivers', player: 'Parker Messick',
  away_team: 'CLE', home_team: 'TB', odds: 106, stake: 100, model_prob: 0.5, market_prob: 0.48, edge: 0.06, kelly_pct: 0.01,
  kelly_triggered: true, result: 'win', profit: 106, paper: true, book: 'betrivers', notes: null, created_at: '2026-10-13T20:00:00',
  ...over,
} as Bet)

describe('collapseBooks', () => {
  it('keys a pooled EV row by its underlying prop, not the book suffix', () => {
    expect(propKey(bet({}))).toBe(propKey(bet({ id: 2, book: 'sugarhouse', bet_type: 'K_UNDER_4.5_sugarhouse' })))
    expect(propKey(bet({}))).not.toBe(propKey(bet({ bet_type: 'K_OVER_4.5_betrivers' })))
  })

  it('keeps the best-edge row per prop and counts the books that flagged it', () => {
    const rows = collapseBooks([
      bet({ id: 1, edge: 0.04 }),
      bet({ id: 2, edge: 0.06, book: 'sugarhouse', bet_type: 'K_UNDER_4.5_sugarhouse' }),
      bet({ id: 3, edge: 0.03, book: 'underdog', bet_type: 'K_UNDER_4.5_underdog' }),
      bet({ id: 4, bet_type: 'K_OVER_4.5_betrivers' }),
    ])
    expect(rows).toHaveLength(2)
    expect(rows[0].id).toBe(2)
    expect(rows[0].bookCount).toBe(3)
    expect(rows[1].bookCount).toBe(1)
  })
})
