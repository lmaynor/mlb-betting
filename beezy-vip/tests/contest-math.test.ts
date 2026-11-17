/** lib/contest: the TypeScript mirror of the Python engine (contest/). Golden numbers are the same ones tests/test_contest_engine.py pins. */
import { contestEnabled } from '@/lib/contest/flag'
import { SAMPLE_RULES } from '@/lib/contest/fixture'
import { americanToDecimal, fmtLineSigned, fmtMoney, fmtPrice, parlayDecimal, parseDollars, payoutCents } from '@/lib/contest/odds'
import {
  combinations, gameClosed, makePick, previewParlay, previewRobin, previewStraight, previewTeaser, robinBreakdown, robinCount, slipIssue, teasedLine, teaserPointOptions,
  type SlipState,
} from '@/lib/contest/slip'
import type { BoardGame, Pick } from '@/lib/contest/types'

const R = SAMPLE_RULES
const game = (over: Partial<BoardGame> = {}): BoardGame => ({
  id: 1, round: 'Bowl', commence: '2026-12-19T17:00:00Z', status: 'scheduled',
  away: { abbr: 'AAA', name: 'Away U', espnId: null }, home: { abbr: 'HHH', name: 'Home U', espnId: null },
  spread: { line: -3.5, away: -110, home: -110 }, total: { line: 47.5, over: -110, under: -110 }, ml: { away: 150, home: -178 }, ...over,
})
const P = (gameId: number, market: Pick['market'] = 'spread', side: Pick['side'] = 'home', line: number | null = -3.5, price = -110): Pick => ({ gameId, market, side, line, price })
const picks = (n: number) => Array.from({ length: n }, (_, i) => P(i + 1))
const S = (kind: SlipState['kind'], p: Pick[], extra: Partial<SlipState> = {}): SlipState => ({ kind, picks: p, teaserPoints: 6, robinSizes: [2], ...extra })

describe('odds math (same golden numbers as the Python engine)', () => {
  it('converts American odds and rejects bad prices', () => {
    expect(americanToDecimal(150)).toBe(2.5)
    expect(americanToDecimal(-100)).toBe(2)
    expect(americanToDecimal(-110)).toBeCloseTo(1.909090909, 8)
    for (const bad of [0, 99, -99, 50]) expect(() => americanToDecimal(bad)).toThrow()
  })
  it('rounds payouts half-up to the cent', () => {
    expect(payoutCents(10_000, americanToDecimal(-110))).toBe(19_091)
    expect(payoutCents(1, 1.5)).toBe(2)
    expect(payoutCents(3, 1.5)).toBe(5)
    expect(payoutCents(10_000, parlayDecimal([-110, -110]))).toBe(36_446)
  })
  it('formats prices, lines, money and typed dollars', () => {
    expect(fmtPrice(150)).toBe('+150'); expect(fmtPrice(-110)).toBe('-110'); expect(fmtPrice(null)).toBe('--')
    expect(fmtLineSigned(-3.5)).toBe('-3.5'); expect(fmtLineSigned(3.5)).toBe('+3.5'); expect(fmtLineSigned(0)).toBe('PK')
    expect(fmtMoney(500_000)).toBe('$5,000.00'); expect(fmtMoney(500_000, { whole: true })).toBe('$5,000'); expect(fmtMoney(-1_250, { sign: true })).toBe('-$12.50')
    expect(fmtMoney(1_250, { sign: true })).toBe('+$12.50')
    expect(parseDollars('25')).toBe(2_500); expect(parseDollars('$25.5')).toBe(2_550); expect(parseDollars('1,000.05')).toBe(100_005)
    for (const bad of ['', '0', 'abc', '-5', '1.234', '1e3']) expect(parseDollars(bad)).toBeNull()
  })
})

describe('picks and previews', () => {
  it('builds each side with the right line and price', () => {
    const g = game()
    expect(makePick(g, 'spread', 'home')).toMatchObject({ line: -3.5, price: -110 })
    expect(makePick(g, 'spread', 'away')).toMatchObject({ line: 3.5 })                    // the away side gets the opposite handicap
    expect(makePick(g, 'total', 'over')).toMatchObject({ line: 47.5 })
    expect(makePick(g, 'ml', 'away')).toMatchObject({ line: null, price: 150 })
    expect(makePick(game({ spread: null }), 'spread', 'home')).toBeNull()
    expect(makePick(g, 'total', 'home')).toBeNull()
  })
  it('moves a teaser line in the bettor\'s favour', () => {
    expect(teasedLine(P(1, 'spread', 'home', -7), 6)).toBe(-1)
    expect(teasedLine(P(1, 'spread', 'away', 2.5), 6)).toBe(8.5)
    expect(teasedLine(P(1, 'total', 'over', 50.5), 6)).toBe(44.5)
    expect(teasedLine(P(1, 'total', 'under', 50.5), 6)).toBe(56.5)
    expect(teasedLine(P(1, 'ml', 'home', null), 6)).toBeNull()
  })
  it('previews straights, parlays and teasers', () => {
    expect(previewStraight(P(1), 10_000)).toEqual({ riskCents: 10_000, payoutCents: 19_091, toWinCents: 9_091 })
    expect(previewParlay(picks(2), 10_000).payoutCents).toBe(36_446)
    expect(previewTeaser(picks(2), 12_000, 6, R)?.payoutCents).toBe(payoutCents(12_000, americanToDecimal(-120)))
    expect(previewTeaser(picks(2), 12_000, 5, R)).toBeNull()                              // 5 points is not a football table
    expect(teaserPointOptions(R)).toEqual([6, 6.5, 7])
  })
  it('counts and prices round robins like itertools.combinations', () => {
    expect(combinations(4, 2)).toEqual([[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]])
    expect(combinations(3, 4)).toEqual([])
    expect(robinBreakdown(4, [3, 2, 2])).toEqual([{ k: 2, count: 6 }, { k: 3, count: 4 }])             // sorted, de-duplicated, one entry per size
    expect(robinBreakdown(11, [2, 5, 11])).toEqual([{ k: 2, count: 55 }, { k: 5, count: 462 }, { k: 11, count: 1 }])
    expect(robinCount(4, [2])).toBe(6); expect(robinCount(5, [2, 3])).toBe(20); expect(robinCount(8, [4])).toBe(70); expect(robinCount(4, [2, 2])).toBe(6)
    const rr = previewRobin(picks(3), [2], 10_000)
    expect(rr.riskCents).toBe(30_000); expect(rr.payoutCents).toBe(3 * 36_446); expect(rr.toWinCents).toBe(3 * 36_446 - 30_000)
  })
})

describe('slip validation mirrors the engine', () => {
  const issue = (s: SlipState, stake = 10_000, bal = 500_000) => slipIssue(s, stake, bal, R)
  it('structure', () => {
    expect(issue(S('parlay', []))).toBe('Add a pick')
    expect(issue(S('parlay', picks(1)))).toMatch(/2\+ legs/)
    expect(issue(S('parlay', picks(9)))).toMatch(/at most 8/)
    expect(issue(S('parlay', [P(1), P(1, 'total', 'over', 40.5)]))).toMatch(/One leg per game/)
    expect(issue(S('parlay', picks(2)))).toBeNull()
    expect(issue(S('straight', [P(1), P(1, 'total', 'over', 40.5)]))).toBeNull()         // straights may repeat a game
    expect(issue(S('teaser', [P(1), P(2, 'ml', 'home', null, -150)]))).toMatch(/spreads and totals only/)
    expect(issue(S('teaser', picks(2), { teaserPoints: 5 }))).toMatch(/not offered/)
    expect(issue(S('teaser', picks(5)))).toMatch(/at most 4/)
    expect(issue(S('round_robin', picks(2)))).toMatch(/3\+ picks/)
    expect(issue(S('round_robin', picks(3), { robinSizes: [] }))).toMatch(/Choose a size/)
    expect(issue(S('round_robin', picks(3), { robinSizes: [4] }))).toMatch(/sizes/)
    expect(issue(S('round_robin', picks(11), { robinSizes: [2, 3, 4, 5, 6, 7, 8, 9, 10] }), 500, 5_000_000)).toMatch(/2035 parlays is over the limit of 500/)
    expect(issue(S('round_robin', picks(12), { robinSizes: [2] }), 500)).toMatch(/at most 11 picks/)
    expect(issue(S('round_robin', picks(11), { robinSizes: [2] }), 500)).toBeNull()                   // 55 parlays x $5 = $275
    expect(issue(S('round_robin', picks(11), { robinSizes: [11] }), 2_500)).toBeNull()                // by 11s is one 11-leg parlay, past the 8-leg parlay cap on purpose
    expect(issue(S('round_robin', picks(4), { robinSizes: [5] }))).toMatch(/sizes/)
    expect(issue(S('round_robin', picks(4), { robinSizes: [2, 3] }), 5_000)).toBeNull()
  })
  it('stakes', () => {
    expect(issue(S('parlay', picks(2)), 2_499)).toMatch(/Minimum/)
    expect(issue(S('parlay', picks(2)), 600_000)).toMatch(/Not enough/)
    expect(issue(S('parlay', picks(2)), 125_001)).toMatch(/25%/)
    expect(issue(S('parlay', picks(2)), 125_000)).toBeNull()
    expect(issue(S('parlay', picks(2)), 2_500, 3_000)).toBeNull()                         // the minimum stake is always allowed
    expect(issue(S('round_robin', picks(4)), 50_000)).toBe('6 parlays x $500.00 = $3,000.00 is over 25% of your bankroll ($1,250.00)')   // the cap is on the TOTAL
    expect(issue(S('round_robin', picks(4)), 499)).toBe('Minimum stake is $5.00 per parlay')           // lower than a normal bet's $25 minimum
    expect(issue(S('round_robin', picks(4)), 500)).toBeNull()
    expect(issue(S('round_robin', picks(11), { robinSizes: [2] }), 5_000)).toMatch(/55 parlays x \$50.00 = \$2,750.00 is over 25%/)
    expect(issue(S('straight', [P(1), P(2)]), 70_000)).toMatch(/25%/)                      // 2 x 700 = 1,400
  })
  it('betting closes 15 minutes before kickoff and never on a started game', () => {
    const t = Date.parse('2026-12-19T17:00:00Z')
    expect(gameClosed(game(), t - 16 * 60_000, R).closed).toBe(false)
    expect(gameClosed(game(), t - 15 * 60_000, R).closed).toBe(true)
    expect(gameClosed(game({ status: 'live' }), t - 3_600_000, R)).toEqual({ closed: true, reason: 'In play' })
    expect(gameClosed(game({ status: 'final' }), t - 3_600_000, R).reason).toBe('Final')
  })
})

describe('kill switch', () => {
  it('is off unless explicitly on; 0, false, off, no and empty all mean off', () => {
    for (const v of [undefined, '', '0', 'false', 'FALSE', 'off', 'Off', 'no', ' no ']) expect(contestEnabled({ CONTEST_ENABLED: v })).toBe(false)
    for (const v of ['1', 'true', 'on', 'yes']) expect(contestEnabled({ CONTEST_ENABLED: v })).toBe(true)
  })
})
