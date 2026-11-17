/** lib/contest/blackjack mirrors contest/blackjack.py (same decks, same answers) and the lifeline cap in contest/lifeline.py. */
import { canDouble, cardLabel, creditedAfterCap, deal, doubleDown, handValue, hit, isBlackjack, newDeck, payout, stand, viewOf } from '@/lib/contest/blackjack'

describe('hand values', () => {
  it('counts aces as 11 until that busts', () => {
    expect(handValue(['AS', 'KS'])).toEqual({ total: 21, soft: true })
    expect(handValue(['AS', 'AH'])).toEqual({ total: 12, soft: true })
    expect(handValue(['AS', '6H', 'TD'])).toEqual({ total: 17, soft: false })
    expect(handValue(['TS', '6H', '5D'])).toEqual({ total: 21, soft: false })
    expect(handValue(['9S', '9H', '9D']).total).toBe(27)
    expect(isBlackjack(['AS', 'KD'])).toBe(true)
    expect(isBlackjack(['TS', '6H', '5D'])).toBe(false)
  })
  it('a deck has 52 unique cards and shuffles from the rng it is given', () => {
    const d = newDeck(() => 0.3)
    expect(d).toHaveLength(52)
    expect(new Set(d).size).toBe(52)
    expect(newDeck()).toHaveLength(52)
  })
})

describe('rules', () => {
  it('blackjack pays 3 to 2; a dealer blackjack ends the hand; both is a push', () => {
    const a = deal(1_000, ['AS', '9H', 'KS', '8D'])
    expect([a.done, a.outcome, payout(a)]).toEqual([true, 'blackjack', 2_500])
    const b = deal(1_000, ['9S', 'AH', '8S', 'KD'])
    expect([b.done, b.outcome, payout(b)]).toEqual([true, 'lose', 0])
    const c = deal(1_000, ['AS', 'AH', 'KS', 'KD'])
    expect([c.outcome, payout(c)]).toEqual(['push', 1_000])
  })
  it('the dealer draws to 17 and stands on soft 17', () => {
    const a = stand(deal(1_000, ['TS', '6H', '8D', 'TD', '5C']))
    expect([a.outcome, handValue(a.dealer).total]).toEqual(['lose', 21])
    const b = stand(deal(1_000, ['TS', 'AH', '8D', '6C', '9S']))
    expect([b.dealer, b.outcome, payout(b)]).toEqual([['AH', '6C'], 'win', 2_000])
    expect(stand(deal(1_000, ['TS', '6H', '8D', 'TD', 'TC'])).outcome).toBe('win')          // dealer busts
    const c = stand(deal(1_000, ['TS', '8H', '8D', 'TD']))
    expect([c.outcome, payout(c)]).toEqual(['push', 1_000])
  })
  it('hit can bust; reaching 21 stands automatically; a finished hand refuses more', () => {
    const a = hit(deal(1_000, ['TS', '9H', '6D', '8D', 'TC']))
    expect([a.done, a.outcome, payout(a)]).toEqual([true, 'bust', 0])
    const b = hit(deal(1_000, ['5S', 'TH', '6D', '7D', 'TC']))
    expect([b.done, b.outcome]).toEqual([true, 'win'])
    expect(() => hit(b)).toThrow('HAND_OVER')
  })
  it('double down: one card, double stake, only on the first two cards', () => {
    const a = doubleDown(deal(1_000, ['5S', 'TH', '6D', '7D', 'TC']))
    expect([a.doubled, a.outcome, payout(a)]).toEqual([true, 'win', 4_000])
    expect(doubleDown(deal(1_000, ['5S', 'TH', '6D', '7D', '2C'])).outcome).toBe('lose')
    expect(doubleDown(deal(1_000, ['TS', 'TH', '6D', '7D', 'TC'])).outcome).toBe('bust')
    const started = hit(deal(1_000, ['2S', 'TH', '3D', '7D', '2C', '2D']))
    expect(canDouble(started)).toBe(false)
    expect(() => doubleDown(started)).toThrow('CANNOT_DOUBLE')
    expect(() => payout(deal(1_000, ['TS', '6H', '8D', 'TD', '5C']))).toThrow('HAND_OPEN')
  })
  it('the view hides the hole card and never carries the deck', () => {
    const h = deal(1_000, ['TS', '6H', '8D', 'TD', '5C'])
    const v = viewOf(h)
    expect(v.dealer).toEqual(['6H', '??'])
    expect(v.dealerTotal).toBe(6)
    expect('deck' in v).toBe(false)
    stand(h)
    expect(viewOf(h).dealer).toEqual(['6H', 'TD', '5C'])
    expect(cardLabel('TD')).toBe('10 of diamonds')
    expect(cardLabel('??')).toBe('Face-down card')
  })
})

describe('the lifeline cap (same numbers as the Python tests)', () => {
  it('a win can never lift the bankroll above the threshold', () => {
    const win = stand(deal(5_000, ['TS', '7H', 'TD', 'TD']))                                  // $50 stake, 20 beats 17: payout $100
    expect(creditedAfterCap(win, 9_000, 10_000)).toEqual({ payout: 10_000, credited: 6_000, capped: true })     // bankroll $90 -> exactly $100
    const small = stand(deal(2_000, ['TS', '7H', 'TD', 'TD']))
    expect(creditedAfterCap(small, 6_000, 10_000)).toEqual({ payout: 4_000, credited: 4_000, capped: false })
    const nat = deal(4_000, ['AS', '9H', 'KS', '8D'])                                         // natural: payout $100 on a $40 stake
    expect(creditedAfterCap(nat, 5_000, 10_000)).toEqual({ payout: 10_000, credited: 9_000, capped: true })
    const dbl = doubleDown(deal(2_000, ['5S', 'TH', '6D', '7D', 'TC']))
    expect(creditedAfterCap(dbl, 6_000, 10_000)).toEqual({ payout: 8_000, credited: 8_000, capped: false })    // exactly $100
    const loss = stand(deal(2_000, ['TS', 'TH', '8D', '9D']))
    expect(creditedAfterCap(loss, 6_000, 10_000)).toEqual({ payout: 0, credited: 0, capped: false })
  })
})
