/**
 * SAMPLE data for the Bankroll Challenge UI. The matchups, lines, players and results are INVENTED to exercise every state of
 * the page (open, in play, final, hidden picks, an eliminated player); they are not real games, lines or people. The real feed
 * (board builder, ESPN mapping, API) is milestone M2 of docs/designs/bowl-bankroll-contest-scope.md.
 * Team ids are real ESPN ids so the logos are real.
 */
import type { BoardGame, ContestFeed, ContestRules, GameState, LeaderRow, PlacedBet, PoolBet, TeamRef } from './types'

// PLACEHOLDER limits and teaser tables: identical to contest/rules.py (football). Replace both from the owner's rulebook.
export const SAMPLE_RULES: ContestRules = {
  startBankrollCents: 500_000,
  minStakeCents: 2_500,
  maxStakeBp: 2_500,
  parlayMinLegs: 2,
  parlayMaxLegs: 8,
  teaserMinLegs: 2,
  teaserMaxLegs: 4,
  roundRobinMinPicks: 3,
  roundRobinMaxPicks: 11,
  roundRobinMaxParlays: 500,
  roundRobinMinUnitCents: 500,
  closeBufferMin: 15,
  teaser: { '6.0': { '2': -120, '3': 160, '4': 260 }, '6.5': { '2': -130, '3': 140, '4': 240 }, '7.0': { '2': -140, '3': 120, '4': 200 } },
}

const T = (abbr: string, name: string, espnId: number, rank: number | null = null): TeamRef => ({ abbr, name, espnId, rank })
const OSU = T('OSU', 'Ohio State', 194, 2), ORE = T('ORE', 'Oregon', 2483, 5), TEX = T('TEX', 'Texas', 251, 7), UGA = T('UGA', 'Georgia', 61, 3)
const BAMA = T('ALA', 'Alabama', 333, 9), ND = T('ND', 'Notre Dame', 87, 11), PSU = T('PSU', 'Penn State', 213, 12), MIA = T('MIA', 'Miami', 2390, 8)
const IND = T('IND', 'Indiana', 84, 1), MISS = T('MISS', 'Ole Miss', 145, 10), SMU = T('SMU', 'SMU', 2567, 14), CLEM = T('CLEM', 'Clemson', 228, 16)
const TENN = T('TENN', 'Tennessee', 2633, null), BSU = T('BSU', 'Boise State', 68, 13), ASU = T('ASU', 'Arizona State', 9, null), BYU = T('BYU', 'BYU', 252, null)
const USC = T('USC', 'USC', 30, null), UTAH = T('UTAH', 'Utah', 254, null), OU = T('OU', 'Oklahoma', 201, null), MICH = T('MICH', 'Michigan', 130, null), LSU = T('LSU', 'LSU', 99, null), IOWA = T('IOWA', 'Iowa', 2294, null)

/** home handicap, total, then ML prices (away, home). Spread and total are priced -110 both ways unless a pair is given. */
function g(id: number, round: string, commence: string, away: TeamRef, home: TeamRef, spread: number, total: number, ml: [number, number],
  status: BoardGame['status'] = 'scheduled', juice: [number, number] = [-110, -110], state?: GameState): BoardGame {
  return { id, round, commence, status, away, home, spread: { line: spread, away: juice[0], home: juice[1] }, total: { line: total, over: -110, under: -110 }, ml: { away: ml[0], home: ml[1] }, ...(state ? { state } : {}) }
}

const GAMES: BoardGame[] = [
  g(1, 'CFP First Round', '2026-12-19T17:00:00Z', ORE, OSU, -6.5, 54.5, [195, -240]),
  g(2, 'CFP First Round', '2026-12-19T21:00:00Z', TEX, UGA, -3.5, 51.5, [150, -178], 'scheduled', [-108, -112]),
  g(3, 'CFP First Round', '2026-12-20T01:00:00Z', ND, IND, -4.0, 47.5, [165, -198]),
  g(4, 'CFP First Round', '2026-12-20T18:00:00Z', MISS, PSU, -2.5, 49.5, [124, -148]),
  g(5, 'Bowl Games', '2026-12-26T21:30:00Z', SMU, CLEM, -2.5, 55.5, [118, -138], 'scheduled', [-112, -108]),
  g(6, 'Bowl Games', '2026-12-27T17:00:00Z', BSU, ASU, 3.0, 58.5, [-142, 120]),
  g(7, 'Bowl Games', '2026-12-28T01:00:00Z', BYU, MIA, -7.0, 52.5, [245, -305]),
  g(8, 'Bowl Games', '2026-12-29T21:00:00Z', TENN, BAMA, -3.0, 50.5, [135, -160]),
  g(9, 'Bowl Games', '2026-12-30T17:00:00Z', OU, MICH, 1.5, 41.5, [-108, -112]),
  g(10, 'Bowl Games', '2026-12-31T01:00:00Z', LSU, IOWA, 2.5, 38.5, [-135, 114]),
  g(13, 'Bowl Games', '2027-01-01T18:00:00Z', USC, UTAH, -1.0, 56.5, [-118, 100]),
  // States the board has to render: one in play and one final (both closed to betting).
  g(11, 'Earlier', '2026-11-16T18:00:00Z', IOWA, MICH, -3.5, 44.5, [140, -165], 'final', [-110, -110], { homeScore: 24, awayScore: 17, detail: 'Final' }),
  g(12, 'Earlier', '2026-11-17T01:00:00Z', LSU, TEX, -1.5, 52.5, [105, -125], 'live', [-110, -110],
    { homeScore: 20, awayScore: 17, detail: '8:42 - 3rd Quarter', possession: 'home', downDistance: '2nd & 7 at LSU 35', redZone: false }),
]

const LEG = (gameId: number, market: 'spread' | 'total' | 'ml', side: 'home' | 'away' | 'over' | 'under', line: number | null, price: number,
  result: PlacedBet['legs'][number]['result'] = null) => ({ gameId, market, side, line, price, result })

const MY_BETS: PlacedBet[] = [
  { id: 'b1', kind: 'straight', groupId: null, stakeCents: 20_000, toWinCents: 18_182, status: 'won', placedAt: '2026-11-15T15:00:00Z', teaserPoints: 0,
    legs: [LEG(11, 'spread', 'away', 3.5, -110, 'win')] },
  { id: 'b2', kind: 'parlay', groupId: null, stakeCents: 10_000, toWinCents: 26_446, status: 'pending', placedAt: '2026-11-16T16:00:00Z', teaserPoints: 0,
    legs: [LEG(1, 'spread', 'home', -6.5, -110), LEG(3, 'total', 'under', 47.5, -110)] },
]

const POOL: PoolBet[] = [
  { id: 'p1', owner: 'Marcus', kind: 'parlay', status: 'pending', stakeCents: null, legs: [null, null, null] },
  { id: 'p2', owner: 'Dani', kind: 'straight', status: 'pending', stakeCents: 25_000, legs: [{ gameId: 12, market: 'total', side: 'over', line: 52.5, price: -110 }] },
  { id: 'p3', owner: 'Priya', kind: 'teaser', status: 'pending', stakeCents: null, legs: [{ gameId: 12, market: 'spread', side: 'home', line: -1.5, price: -110 }, null] },
  { id: 'p4', owner: 'Tom', kind: 'round_robin', status: 'pending', stakeCents: null, legs: [null, null] },
]

const LEADERS: Omit<LeaderRow, 'rank'>[] = [
  { name: 'Marcus', bankrollCents: 812_500, openRiskCents: 40_000, won: 9, lost: 4, pushed: 1, status: 'active' },
  { name: 'Priya', bankrollCents: 733_000, openRiskCents: 55_000, won: 7, lost: 5, pushed: 0, status: 'active' },
  { name: 'You', bankrollCents: 518_182, openRiskCents: 10_000, won: 1, lost: 0, pushed: 0, status: 'active', mine: true },
  { name: 'Dani', bankrollCents: 466_000, openRiskCents: 25_000, won: 5, lost: 6, pushed: 2, status: 'active' },
  { name: 'Tom', bankrollCents: 301_500, openRiskCents: 60_000, won: 3, lost: 8, pushed: 0, status: 'active' },
  { name: 'Wes', bankrollCents: 1_250, openRiskCents: 0, won: 2, lost: 11, pushed: 0, status: 'eliminated' },
]

export function sampleFeed(nowIso: string): ContestFeed {
  return {
    name: 'Bowl Bankroll',
    sample: true,
    rules: SAMPLE_RULES,
    nowIso,
    balanceCents: 508_182,
    games: GAMES,
    myBets: MY_BETS,
    pool: POOL,
    leaderboard: [...LEADERS].sort((a, b) => b.bankrollCents - a.bankrollCents).map((r, i) => ({ ...r, rank: i + 1 })),
  }
}
