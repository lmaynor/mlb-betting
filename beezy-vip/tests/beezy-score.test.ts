import { beezyscore, booksBehind, explainScore, scoreParts, scoreTier, TIER_CUTS } from '../lib/beezy-score'
import type { Bet } from '../lib/types'

const bet = (over: Partial<Bet>): Bet => ({
  id: 1, system: 'EV', game_date: '2026-10-14', game_pk: 1, bet_type: 'K_OVER_5.5_betmgm', player: 'P', away_team: 'A', home_team: 'B',
  odds: 110, stake: 100, model_prob: 0.45, market_prob: 0.43, edge: 0.05, kelly_pct: 0.01, kelly_triggered: true, result: null,
  profit: null, paper: true, book: 'betmgm', notes: 'soft-book +EV alert vs consensus (6 books)', created_at: 'x',
  ...over,
} as Bet)

describe('Beezy Score v2', () => {
  it('scores a price with no edge at 0 however many books agree (v1 gave every alert ~30 free points)', () => {
    expect(beezyscore(bet({ edge: 0, notes: 'consensus (13 books)', model_prob: 0.5 }))).toBe(0)
    expect(beezyscore(bet({ edge: -0.03 }))).toBe(0)
  })

  it('rises with edge, with more agreeing books, and with a realistic win probability', () => {
    expect(beezyscore(bet({ edge: 0.08 }))).toBeGreaterThan(beezyscore(bet({ edge: 0.04 })))
    expect(beezyscore(bet({ notes: '(12 books)' }))).toBeGreaterThan(beezyscore(bet({ notes: '(4 books)' })))
    expect(beezyscore(bet({ model_prob: 0.5 }))).toBeGreaterThan(beezyscore(bet({ model_prob: 0.1 })))
  })

  it('penalises a longshot: same edge, a 4% shot scores below a coin flip', () => {
    const longshot = beezyscore(bet({ edge: 0.17, model_prob: 0.04, odds: 2500 }))
    const coinflip = beezyscore(bet({ edge: 0.17, model_prob: 0.5, odds: 100 }))
    expect(longshot).toBeLessThan(coinflip)
    expect(scoreTier(longshot)).not.toBe('watch') // still a big edge, but never a free STRONG
  })

  it('matches the documented worked example: +7.6% edge, 5 books, 45.2% win prob = 52', () => {
    const b = bet({ edge: 0.076, model_prob: 0.452, notes: 'x (5 books)' })
    const p = scoreParts(b)
    expect(Math.round(p.edge)).toBe(30)
    expect(beezyscore(b)).toBe(52)
    expect(explainScore(b)).toBe('Beezy Score 52 = edge 30 + consensus 4 + hit probability 17')
  })

  it('model bets without a books count get neutral consensus points; units of edge do not matter', () => {
    expect(booksBehind(bet({ notes: null }))).toBeNull()
    expect(beezyscore(bet({ system: 'K', notes: null, edge: 0.08 }))).toBe(beezyscore(bet({ system: 'K', notes: null, edge: 8 })))
  })

  it('caps at 100 and tiers use the published cut points', () => {
    expect(beezyscore(bet({ edge: 0.5, model_prob: 0.9, notes: '(20 books)' }))).toBeLessThanOrEqual(100)
    expect(TIER_CUTS).toEqual({ strong: 70, lean: 45 })
  })
})
