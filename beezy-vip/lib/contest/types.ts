/**
 * Bankroll Challenge (paper-money contest) types. Mirrors the Python engine in `contest/` (docs/designs/bowl-bankroll-contest-scope.md):
 * money is INTEGER CENTS, odds are American integers, a spread pick's `line` is the handicap of the side picked.
 */
export type Market = 'spread' | 'total' | 'ml'
export type Side = 'home' | 'away' | 'over' | 'under'
export type BetKind = 'straight' | 'parlay' | 'teaser' | 'round_robin'
export type BetStatus = 'pending' | 'won' | 'lost' | 'push' | 'void'

export interface TeamRef {
  abbr: string
  name: string
  /** ESPN team id: drives the logo (a.espncdn.com/i/teamlogos/ncaa/500-dark/{id}.png). Null when unmapped: the monogram is shown. */
  espnId: number | null
  rank?: number | null
}

export interface BoardGame {
  id: number
  /** Section label on the board, e.g. "CFP First Round" or "Bowl Games". */
  round: string
  commence: string
  status: 'scheduled' | 'live' | 'final'
  away: TeamRef
  home: TeamRef
  /** `line` is the HOME handicap (home -3.5 is -3.5); `away` / `home` are the prices of that side. */
  spread: { line: number; away: number; home: number } | null
  total: { line: number; over: number; under: number } | null
  ml: { away: number; home: number } | null
}

/** One selection on the slip. For a spread, `line` is the picked side's handicap; for a total, the total; null for a moneyline. */
export interface Pick {
  gameId: number
  market: Market
  side: Side
  line: number | null
  price: number
}

export interface ContestRules {
  startBankrollCents: number
  minStakeCents: number
  maxStakeBp: number
  parlayMinLegs: number
  parlayMaxLegs: number
  teaserMinLegs: number
  teaserMaxLegs: number
  roundRobinMinPicks: number
  roundRobinMaxPicks: number
  roundRobinMaxParlays: number
  /** Minimum stake PER PARLAY in a round robin (placeholder; lower than a normal bet so 55 parlays fit the cap). */
  roundRobinMinUnitCents: number
  closeBufferMin: number
  /** {points: {legs: American price}}. PLACEHOLDERS until the real rulebook is supplied (see contest/rules.py). */
  teaser: Record<string, Record<string, number>>
}

export interface PlacedBet {
  id: string
  kind: BetKind
  groupId: string | null
  stakeCents: number
  toWinCents: number
  status: BetStatus
  placedAt: string
  teaserPoints: number
  legs: { gameId: number; market: Market; side: Side; line: number | null; price: number; result: 'win' | 'loss' | 'push' | 'void' | null }[]
}

/** What the player sees right after placing: one slip, however many bets it expanded into (a round robin is several parlays). */
export interface Receipt {
  kind: BetKind
  bets: PlacedBet[]
  riskCents: number
  payoutCents: number
  placedAt: string
}

export interface PoolBet {
  id: string
  owner: string
  kind: BetKind
  status: BetStatus
  /** A hidden leg is `null`: its game has not kicked off yet. */
  legs: ({ gameId: number; market: Market; side: Side; line: number | null; price: number } | null)[]
  /** Null while any leg is hidden. */
  stakeCents: number | null
}

export interface LeaderRow {
  rank: number
  name: string
  bankrollCents: number
  openRiskCents: number
  won: number
  lost: number
  pushed: number
  status: 'active' | 'eliminated'
  mine?: boolean
}

export interface ContestFeed {
  name: string
  sample: boolean
  rules: ContestRules
  nowIso: string
  balanceCents: number
  games: BoardGame[]
  myBets: PlacedBet[]
  pool: PoolBet[]
  leaderboard: LeaderRow[]
}
