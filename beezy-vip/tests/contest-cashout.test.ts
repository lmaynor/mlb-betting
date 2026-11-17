/** lib/contest/cashout: the preview mirror of contest/cashout.py. The golden cents below were produced by the Python engine
 *  (contest.cashout.cashout_offer); the mirror may differ by at most one cent (different inverse-normal implementations). */
import { cashoutGroup, cashoutOffer, devig, legProbability, normCdf, normInv } from '@/lib/contest/cashout'
import { SAMPLE_RULES } from '@/lib/contest/fixture'
import type { BoardGame, PlacedBet } from '@/lib/contest/types'

const R = SAMPLE_RULES
const NOW = Date.parse('2026-11-17T06:00:00Z')
const game = (id: number, over: Partial<BoardGame> = {}): BoardGame => ({
  id, round: 'Bowl', commence: '2026-12-19T17:00:00Z', status: 'scheduled',
  away: { abbr: 'AAA', name: 'Away U', espnId: null }, home: { abbr: 'HHH', name: 'Home U', espnId: null },
  spread: { line: -3.5, away: -110, home: -110 }, total: { line: 47.5, over: -110, under: -110 }, ml: { away: 150, home: -178 }, ...over,
})
const games = (...gs: BoardGame[]) => new Map(gs.map(g => [g.id, g]))
type L = PlacedBet['legs'][number]
const leg = (gameId: number, market: L['market'], side: L['side'], line: number | null, price = -110): L => ({ gameId, market, side, line, price, result: null })
const bet = (kind: PlacedBet['kind'], legs: L[], stake = 10_000, status: PlacedBet['status'] = 'pending'): PlacedBet =>
  ({ id: 'b', kind, groupId: null, stakeCents: stake, toWinCents: 0, status, placedAt: '2026-11-16T00:00:00Z', teaserPoints: 0, legs })
const close = (a: number | undefined, b: number) => expect(Math.abs((a ?? NaN) - b)).toBeLessThanOrEqual(1)

describe('normal helpers', () => {
  it('normCdf and normInv are inverses with standard values', () => {
    expect(normCdf(0)).toBeCloseTo(0.5, 7)
    expect(normCdf(1.959964)).toBeCloseTo(0.975, 6)
    expect(normCdf(-1.959964)).toBeCloseTo(0.025, 6)
    expect(normInv(0.975)).toBeCloseTo(1.959964, 5)
    expect(normInv(0.5)).toBeCloseTo(0, 9)
    for (const p of [0.001, 0.02, 0.3, 0.5, 0.77, 0.99, 0.9999]) expect(normCdf(normInv(p))).toBeCloseTo(p, 6)
  })
  it('devig removes the vig', () => {
    expect(devig(-110, -110)).toBeCloseTo(0.5, 9)
    expect(devig(-150, 130) + devig(130, -150)).toBeCloseTo(1, 9)
  })
})

describe('cash-out value matches the Python engine', () => {
  it('straight on a flat board: 9,068 of a 19,091 payout', () => {
    const o = cashoutOffer(bet('straight', [leg(1, 'spread', 'home', -3.5)]), games(game(1)), R, NOW)
    expect(o.ok && o.payoutCents).toBe(19_091)
    close(o.ok ? o.cents : undefined, 9_068)
  })
  it('a line that moved 3 points your way cashes out above the $100 stake; 3 points against, well below', () => {
    const held = bet('straight', [leg(1, 'spread', 'home', -3.5)])
    const up = cashoutOffer(held, games(game(1, { spread: { line: -6.5, away: -110, home: -110 } })), R, NOW)
    const down = cashoutOffer(held, games(game(1, { spread: { line: -0.5, away: -110, home: -110 } })), R, NOW)
    close(up.ok ? up.cents : undefined, 10_352)
    expect(up.ok && up.cents).toBeGreaterThan(10_000)
    close(down.ok ? down.cents : undefined, 7_150)
  })
  it('moneyline and total', () => {
    const ml = cashoutOffer(bet('straight', [leg(1, 'ml', 'home', null, -150)]), games(game(1, { ml: { away: 170, home: -200 } })), R, NOW)
    expect(ml.ok && ml.payoutCents).toBe(16_667)
    close(ml.ok ? ml.cents : undefined, 10_178)
    const tot = cashoutOffer(bet('straight', [leg(2, 'total', 'over', 47.5)]), games(game(2, { total: { line: 49.5, over: -110, under: -110 } })), R, NOW)
    close(tot.ok ? tot.cents : undefined, 9_999)
  })
  it('parlay: flat two legs 8,655; the sample parlay bought before the lines moved 11,399', () => {
    const flat = cashoutOffer(bet('parlay', [leg(1, 'spread', 'home', -3.5), leg(2, 'spread', 'home', -3.5)]), games(game(1), game(2)), R, NOW)
    close(flat.ok ? flat.cents : undefined, 8_655)
    const moved = cashoutOffer(bet('parlay', [leg(1, 'spread', 'home', -3.5), leg(3, 'total', 'under', 50.5)]),
      games(game(1, { spread: { line: -6.5, away: -110, home: -110 } }), game(3)), R, NOW)
    close(moved.ok ? moved.cents : undefined, 11_399)
    expect(moved.ok && moved.winProb).toBeCloseTo(0.329247, 4)
  })
  it('never pays above the payout', () => {
    const o = cashoutOffer(bet('straight', [leg(1, 'spread', 'home', -3.5)]), games(game(1, { spread: { line: -60.5, away: -110, home: -110 } })), { ...R, cashoutMarginBp: 0 }, NOW)
    expect(o.ok && o.cents <= o.payoutCents).toBe(true)
  })
  it('orientation: an over held below the board total is worth more than 50%, an under held there is worth less', () => {
    const g = game(1, { total: { line: 49.5, over: -110, under: -110 } })
    expect(legProbability(leg(1, 'total', 'over', 47.5), g, R)).toBeGreaterThan(0.5)
    expect(legProbability(leg(1, 'total', 'under', 47.5), g, R)).toBeLessThan(0.5)
    expect(legProbability(leg(1, 'spread', 'away', 9.5), game(1, { spread: { line: -6.5, away: -110, home: -110 } }), R)).toBeGreaterThan(0.5)   // away +9.5 against a board of +6.5
  })
})

describe('refusals', () => {
  const held = bet('straight', [leg(1, 'spread', 'home', -3.5)])
  it('is not offered on teasers, settled bets, a disabled contest, closed games or a missing price', () => {
    expect(cashoutOffer(bet('teaser', [leg(1, 'spread', 'home', -3.5), leg(2, 'spread', 'home', -3.5)]), games(game(1), game(2)), R, NOW)).toEqual({ ok: false, reason: 'teaser' })
    expect(cashoutOffer({ ...held, status: 'won' }, games(game(1)), R, NOW)).toEqual({ ok: false, reason: 'settled' })
    expect(cashoutOffer(held, games(game(1)), { ...R, cashoutEnabled: false }, NOW)).toEqual({ ok: false, reason: 'disabled' })
    expect(cashoutOffer(held, games(game(1)), R, Date.parse('2026-12-19T16:46:00Z'))).toEqual({ ok: false, reason: 'closed' })      // inside the 15-minute close
    expect(cashoutOffer(held, games(game(1, { status: 'live' })), R, NOW)).toEqual({ ok: false, reason: 'closed' })
    expect(cashoutOffer(held, games(), R, NOW)).toEqual({ ok: false, reason: 'closed' })
    expect(cashoutOffer(held, games(game(1, { spread: null })), R, NOW)).toEqual({ ok: false, reason: 'no_quote' })
  })
})

describe('a round robin group', () => {
  it('sums every open parlay, and is refused if any one is', () => {
    const a = { ...bet('parlay', [leg(1, 'spread', 'home', -3.5), leg(2, 'spread', 'home', -3.5)], 2_500), id: 'a' }
    const b = { ...bet('parlay', [leg(1, 'spread', 'home', -3.5), leg(3, 'spread', 'home', -3.5)], 2_500), id: 'b' }
    const g = games(game(1), game(2), game(3))
    const ok = cashoutGroup([a, b], g, R, NOW)
    expect(ok.ok && ok.offers.map(o => o.id)).toEqual(['a', 'b'])
    expect(ok.ok && ok.stakeCents).toBe(5_000)
    expect(ok.ok && ok.cents).toBe(2 * 2_164)                                                  // 2,500 at -110/-110 pays 9,112; x 0.25 x 0.95 rounds down to 2,164
    expect(cashoutGroup([a, b], games(game(1), game(2), game(3, { status: 'live' })), R, NOW)).toEqual({ ok: false, reason: 'closed' })
    expect(cashoutGroup([{ ...a, status: 'lost' }], g, R, NOW)).toEqual({ ok: false, reason: 'settled' })
  })
})
