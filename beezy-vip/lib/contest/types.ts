/**
 * Bankroll Challenge (paper-money contest) types. Mirrors the Python engine in `contest/` (docs/designs/bowl-bankroll-contest-scope.md):
 * money is INTEGER CENTS, odds are American integers, a spread pick's `line` is the handicap of the side picked.
 */
export type Market = 'spread' | 'total' | 'ml'
export type Side = 'home' | 'away' | 'over' | 'under'
export type BetKind = 'straight' | 'parlay' | 'teaser' | 'round_robin' | 'sgp'
export type BetStatus = 'pending' | 'won' | 'lost' | 'push' | 'void' | 'cashed_out'

export interface TeamRef {
  abbr: string
  name: string
  /** ESPN team id: drives the logo (a.espncdn.com/i/teamlogos/ncaa/500-dark/{id}.png). Null when unmapped: the monogram is shown. */
  espnId: number | null
  rank?: number | null
}

/**
 * Live or final game state, refreshed from the ESPN scoreboard (its `status`, `competitors[].score` and, for football, `situation`).
 * Absent before kickoff. `downDistance` and `possession` are football-only and UNCONFIRMED against a live payload (no football game
 * was live when this was written); basketball and every final carry just the score and the status line.
 */
export interface GameState {
  homeScore: number
  awayScore: number
  /** ESPN's own status line: "8:42 - 3rd Quarter", "Halftime", "Final", "Final/OT". */
  detail: string
  /** Who has the ball (football), or null/absent. */
  possession?: 'home' | 'away' | null
  /** Football only, e.g. "2nd & 7 at LSU 35". */
  downDistance?: string | null
  redZone?: boolean
}

export interface BoardGame {
  id: number
  /** Section label on the board, e.g. "CFP First Round" or "Bowl Games". */
  round: string
  commence: string
  /** `void`: postponed or cancelled. Every open leg on it is refunded or dropped by the server. */
  status: 'scheduled' | 'live' | 'final' | 'void'
  away: TeamRef
  home: TeamRef
  /** `line` is the HOME handicap (home -3.5 is -3.5); `away` / `home` are the prices of that side. */
  spread: { line: number; away: number; home: number } | null
  total: { line: number; over: number; under: number } | null
  ml: { away: number; home: number } | null
  state?: GameState
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
  /** Cash out: value = payout x P(win now) x (1 - margin). Margin and sigmas are PLACEHOLDERS (see contest/rules.py). */
  /** The hidden blackjack lifeline: opens under the threshold, winnings never lift a bankroll above it (contest/lifeline.py). Placeholders. */
  blackjackEnabled: boolean
  blackjackThresholdCents: number
  blackjackMinChipCents: number
  cashoutEnabled: boolean
  /** Alt lines: any half-point number within `altMaxPoints` of the main line, priced by the contest from the main price (lib/contest/margin.ts). UNVALIDATED. */
  altLinesEnabled: boolean
  /** Same-game parlays: a spread or moneyline with the total, priced with a correlation (lib/contest/sgp.ts). UNVALIDATED. */
  sgpEnabled: boolean
  sgpMarginBp: number
  sgpRhoBp: number
  sgpRhoMlBp: number
  altMarginBp: number
  altMaxPoints: number
  /** Football spread: how many times likelier a final margin of exactly k is than a normal curve says. */
  keyWeights: Record<string, number>
  cashoutMarginBp: number
  cashoutSigma: { spread: number; total: number }
  /** {points: {legs: American price}}. PLACEHOLDERS until the real rulebook is supplied (see contest/rules.py). */
  teaser: Record<string, Record<string, number>>
}

export interface PlacedBet {
  id: string
  kind: BetKind
  groupId: string | null
  stakeCents: number
  toWinCents: number
  /** What a cashed-out bet paid. */
  payoutCents?: number
  status: BetStatus
  placedAt: string
  teaserPoints: number
  /** A same-game parlay's American ticket price. */
  ticketPrice?: number | null
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

/** The signed-in player's place in this contest. Before joining, `suggestedName` seeds the join form (from the account). */
export interface MeState {
  joined: boolean
  displayName: string
  renamesLeft: number
  suggestedName: string
}

export interface ContestFeed {
  name: string
  sample: boolean
  rules: ContestRules
  nowIso: string
  me: MeState
  balanceCents: number
  /** The player's bankroll after each event that changed it, oldest first (contest/store.py bankroll_history). */
  history?: { ts: string; cents: number }[]
  games: BoardGame[]
  myBets: PlacedBet[]
  pool: PoolBet[]
  leaderboard: LeaderRow[]
}
