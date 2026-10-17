/**
 * Design tokens shared across all beezy-vip components.
 * Import from here instead of redefining locally.
 *
 * Terminal aesthetic: near-black surfaces, hairline borders, Signal Green
 * brand accent, and a per-system color taxonomy (Discord-style) so each
 * betting system reads as its own identity color across the product.
 */
import type { Bet } from '@/lib/types'

// Hairline border -- replaces the old hard 1px-black Dell frame.
export const B = '1px solid var(--basalt)'
// Subtler inner border for table rows and nested dividers.
export const B_INNER = '1px solid #151822'

// Standard card surface (spec v2): 10px radius, hairline border, soft shadow.
export const CARD_STYLE = {
  background: '#11131a',
  border: '1px solid #1e2230',
  borderRadius: '10px',
  boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
} as const

// -- Confidence tiers (mirror --strong / --lean / --watch in globals.css) --
export type ScoreTier = 'strong' | 'lean' | 'watch'
export const TIER_COLOR: Record<ScoreTier, string> = {
  strong: '#57f287',   // beezy emerald -- high confidence
  lean:   '#facc15',   // amber -- worth a look
  watch:  '#94a3b8',   // slate -- neutral signal
}
export const TIER_LABEL: Record<ScoreTier, string> = {
  strong: 'STRONG',
  lean:   'LEAN',
  watch:  'WATCH',
}

// -- Per-system identity hues (must match --sys-* vars in globals.css) --
export const SYSTEM_COLOR: Record<string, string> = {
  // Game Lines
  NRFI:        '#5fd0a0',   // mint
  F5:          '#4ea6f5',   // azure
  // Innings Windows
  F3:          '#46c0d8',   // cyan
  F1H:         '#6f9cf5',   // cornflower
  F7:          '#8b87f2',   // indigo
  GAME:        '#e3b261',   // gold
  // Pitcher Props
  K:           '#a987f0',   // violet
  OUTS:        '#ef9a52',   // amber
  PITCHER_ER:  '#ef7f6e',   // coral
  // Batter Props
  HR:          '#ee6fae',   // magenta
  BATTER_K:    '#c08cf0',   // lavender
  BATTER_TB:   '#a9d166',   // lime
  BATTER_HITS: '#4fc7bd',   // teal
  SB:          '#d9cf5a',   // yellow -- new 2026-08-20, chosen to sit clear of
                            // the green cluster (lime/mint/--signal) and the
                            // red/orange cluster (coral/amber/gold/--loss)
  // NFL prop ladders (ev/sweep_nfl_props.py stat keys, upper-cased)
  PASS_YDS:    '#f59e0b',
  RUSH_YDS:    '#fb923c',
  REC_YDS:     '#facc15',
  RECEPTIONS:  '#34d399',
  PASS_TDS:    '#f472b6',
  // Multi-sport systems (lib/pick-systems.ts keys). One hue family per sport so a badge reads as its league first:
  // NFL warm (amber / orange / pink / lime), NHL cool (sky / cyan / teal), NBA violet (indigo / purple / fuchsia).
  NFL_PASS_YDS: '#f59e0b', NFL_PASS_TDS: '#f472b6', NFL_PASS_COMP: '#fcd34d', NFL_PASS_ATT: '#d6a24a', NFL_INT: '#fb7185',
  NFL_RUSH_YDS: '#fb923c', NFL_RUSH_ATT: '#e8895a', NFL_REC_YDS: '#facc15', NFL_RECEPTIONS: '#34d399', NFL_ANYTIME_TD: '#a3e635',
  NHL_SOG: '#38bdf8', NHL_GOALS: '#22d3ee', NHL_ASSISTS: '#60a5fa', NHL_POINTS: '#7dd3fc', NHL_PP_POINTS: '#67e8f9',
  NHL_SAVES: '#2dd4bf', NHL_BLOCKS: '#8fb3d9',
  NBA_PTS: '#818cf8', NBA_REB: '#a78bfa', NBA_AST: '#c4b5fd', NBA_3PM: '#e879f9', NBA_PRA: '#6366f1',
  NBA_PR: '#8b5cf6', NBA_PA: '#a855f7', NBA_RA: '#c084fc', NBA_STL: '#f0abfc', NBA_BLK: '#7c83e8',
  // Meta
  KALSHI_EV:   '#00d2a0',   // Kalshi-vs-books mirror alerts (rolled into EV in the DB)
  EV:          '#d97ee0',   // orchid -- new 2026-08-20 (fast_alert_loop +
                            // kalshi_alert pooled +EV tracking, system="EV").
                            // Sits in the one open gap left in the wheel
                            // (between lavender/BATTER_K and magenta/HR) --
                            // deliberately not a per-market hue since EV
                            // isn't its own market, it pools alerts across
                            // every market above.
  ALL:         '#c9c6cf',   // neutral silver
}

// Build a tinted pill { bg, color, border } from a system hue. Surfaces and
// borders are derived from the hue so the whole taxonomy stays consistent.
type Pill = { bg: string; color: string; border: string }
function pill(hue: string): Pill {
  return {
    bg:     `color-mix(in oklab, ${hue} 16%, #090a0f)`,
    color:  hue,
    border: `1px solid color-mix(in oklab, ${hue} 40%, #090a0f)`,
  }
}

export const SYSTEM_PILL: Record<string, Pill> = Object.fromEntries(
  Object.entries(SYSTEM_COLOR).map(([sys, hue]) => [sys, pill(hue)]),
) as Record<string, Pill>

// Fallback for unknown or newly-pipelined systems.
SYSTEM_PILL.ALL = {
  bg:     'color-mix(in oklab, #c9c6cf 10%, #090a0f)',
  color:  '#c9c6cf',
  border: '1px solid #2e3446',
}

// Canonical SHORT display label per system -- the ONE place pill wording
// lives. Raw registry keys (BATTER_TB, PITCHER_ER, 1IOU) are internal ids,
// not UI copy; every pill/chip should render systemLabel(sys) instead.
export const SYSTEM_LABEL: Record<string, string> = {
  NRFI: 'NRFI', YRFI: 'YRFI', '1IOU': 'NRFI', '1I': '1st Inn',
  F5: 'F5', F3: 'F3', F7: 'F7', F1H: '1st Half', GAME: 'Game',
  HR: 'HR', K: 'K', OUTS: 'Outs',
  BATTER_TB: 'Total Bases', BATTER_HITS: 'Hits', BATTER_K: 'Batter K', SB: 'Stolen Bases',
  PITCHER_ER: 'Earned Runs', EV: 'EV', KALSHI_EV: 'Kalshi EV',
  PASS_YDS: 'Pass Yds', RUSH_YDS: 'Rush Yds', REC_YDS: 'Rec Yds',
  RECEPTIONS: 'Receptions', PASS_TDS: 'Pass TDs',
  NFL_PASS_YDS: 'Pass Yds', NFL_PASS_TDS: 'Pass TDs', NFL_PASS_COMP: 'Completions', NFL_PASS_ATT: 'Pass Attempts',
  NFL_INT: 'Interceptions', NFL_RUSH_YDS: 'Rush Yds', NFL_RUSH_ATT: 'Carries', NFL_REC_YDS: 'Rec Yds',
  NFL_RECEPTIONS: 'Receptions', NFL_ANYTIME_TD: 'Anytime TD',
  NHL_SOG: 'Shots on Goal', NHL_GOALS: 'Goals', NHL_ASSISTS: 'Assists', NHL_POINTS: 'Points', NHL_PP_POINTS: 'PP Points',
  NHL_SAVES: 'Saves', NHL_BLOCKS: 'Blocked Shots',
  NBA_PTS: 'Points', NBA_REB: 'Rebounds', NBA_AST: 'Assists', NBA_3PM: '3-Pointers', NBA_PRA: 'Pts+Reb+Ast',
  NBA_PR: 'Pts+Reb', NBA_PA: 'Pts+Ast', NBA_RA: 'Reb+Ast', NBA_STL: 'Steals', NBA_BLK: 'Blocks',
}

// Tighter variant for narrow table cells / chips.
export const SYSTEM_LABEL_SHORT: Record<string, string> = {
  ...SYSTEM_LABEL,
  BATTER_TB: 'TB', BATTER_HITS: 'HITS', BATTER_K: 'B.K', SB: 'SB',
  PITCHER_ER: 'ER', '1I': '1I', F1H: '1H', GAME: 'GAME',
  // compact chip / table-pill wording for the multi-sport systems
  NFL_PASS_YDS: 'PASS YDS', NFL_PASS_TDS: 'PASS TDS', NFL_PASS_COMP: 'COMP', NFL_PASS_ATT: 'ATT', NFL_INT: 'INT',
  NFL_RUSH_YDS: 'RUSH YDS', NFL_RUSH_ATT: 'CARRIES', NFL_REC_YDS: 'REC YDS', NFL_RECEPTIONS: 'REC', NFL_ANYTIME_TD: 'ANYTIME TD',
  NHL_SOG: 'SOG', NHL_GOALS: 'GOALS', NHL_ASSISTS: 'ASSISTS', NHL_POINTS: 'POINTS', NHL_PP_POINTS: 'PP PTS',
  NHL_SAVES: 'SAVES', NHL_BLOCKS: 'BLOCKS',
  NBA_PTS: 'PTS', NBA_REB: 'REB', NBA_AST: 'AST', NBA_3PM: '3PM', NBA_PRA: 'PRA', NBA_PR: 'PR', NBA_PA: 'PA', NBA_RA: 'RA',
  NBA_STL: 'STL', NBA_BLK: 'BLK',
}

export function systemLabel(sys: string | null | undefined, short = false): string {
  if (!sys) return '?'
  const map = short ? SYSTEM_LABEL_SHORT : SYSTEM_LABEL
  return map[sys] ?? sys
}

// Team full name -> 3-letter abbreviation
export const TEAM_ABBREV: Record<string, string> = {
  'Angels': 'LAA', 'Astros': 'HOU', 'Athletics': 'OAK', 'Blue Jays': 'TOR',
  'Braves': 'ATL', 'Brewers': 'MIL', 'Cardinals': 'STL', 'Cubs': 'CHC',
  'Diamondbacks': 'ARI', 'Dodgers': 'LAD', 'Giants': 'SF', 'Guardians': 'CLE',
  'Mariners': 'SEA', 'Marlins': 'MIA', 'Mets': 'NYM', 'Nationals': 'WSH',
  'Orioles': 'BAL', 'Padres': 'SD', 'Phillies': 'PHI', 'Pirates': 'PIT',
  'Rangers': 'TEX', 'Rays': 'TB', 'Red Sox': 'BOS', 'Reds': 'CIN',
  'Rockies': 'COL', 'Royals': 'KC', 'Tigers': 'DET', 'Twins': 'MIN',
  'White Sox': 'CWS', 'Yankees': 'NYY',
}

// Known team logo slugs (public/logos/{slug}.png). Derived from TEAM_ABBREV
// so we only ever render a logo we actually ship -- server components can't
// use onError, so gating on this set avoids broken <img>s.
const TEAM_SLUGS = new Set(Object.values(TEAM_ABBREV).map(a => a.toLowerCase()))

const TEAM_ABBREV_SET = new Set(Object.values(TEAM_ABBREV))

/**
 * Normalize any team string -- full name ("Yankees", "New York Yankees"),
 * city+nickname, or an existing abbrev ("NYY") -- to its canonical 3-letter
 * code. Falls back to the trimmed input uppercased if nothing matches, so the
 * Team filter never shows a mix of names and abbrevs. Safe in server components.
 */
export function teamAbbrev(team: string | null | undefined): string {
  if (!team) return ''
  const t = team.trim()
  if (!t) return ''
  // exact nickname match ("Yankees" -> "NYY")
  if (TEAM_ABBREV[t]) return TEAM_ABBREV[t]
  const up = t.toUpperCase()
  // already an abbrev
  if (TEAM_ABBREV_SET.has(up)) return up
  // "City Nickname" -- match by trailing nickname (longest key first so
  // "Red Sox"/"White Sox"/"Blue Jays" beat a shorter accidental hit)
  for (const name of Object.keys(TEAM_ABBREV).sort((a, b) => b.length - a.length)) {
    if (t.endsWith(name)) return TEAM_ABBREV[name]
  }
  return up
}

/**
 * Resolve a team (full name or 3-letter abbrev) to its logo path, or null if
 * we don't ship a matching logo. Safe to call in server components.
 */
export function teamLogoSrc(team: string | null | undefined): string | null {
  if (!team) return null
  const abbrev = teamAbbrev(team).toLowerCase()
  return abbrev && TEAM_SLUGS.has(abbrev) ? `/logos/${abbrev}.png` : null
}

// Innings window human labels (used by pickLabel below)
const INNINGS_LABEL: Record<string, string> = {
  F3:   'First 3 Innings',
  F1H:  'First Half',
  F7:   'First 7 Innings',
  GAME: 'Full Game',
}

// Extract "N+" from a threshold sub-market bet_type (e.g. "K_2PLUS_2.0" ->
// "2+"), or null if this bet_type isn't one. Added 2026-08-19 for K/OUTS/
// BATTER_TB/BATTER_HITS's one-sided 2+/3+ sub-markets -- checked before each
// system's existing OVER/UNDER string-replace logic below, which would
// otherwise silently mislabel these (side defaults to 'Under', and since
// neither '..._OVER_' nor '..._UNDER_' is a substring, .replace() is a
// no-op and the whole raw bet_type leaks into the line field --
// e.g. "Under K_3PLUS_3.0 Strikeouts").
function plusLabel(bt: string): string | null {
  const m = bt.match(/_(\d+)PLUS_/)
  return m ? `${m[1]}+` : null
}

// Strip an EV row's trailing "_{book}" suffix using its own `book` column
// (fast_alert_loop._ev_bet_type always appends the REAL book key, so this
// matches settle_bets._strip_ev_book_suffix exactly instead of guessing
// against a hardcoded book list).
function stripEvBookSuffix(bt: string, book: string | null | undefined): string {
  const tag = book ? `_${book.toLowerCase()}` : ''
  return tag && bt.toLowerCase().endsWith(tag) ? bt.slice(0, bt.length - tag.length) : bt
}

// Classify a (book-suffix-stripped) EV bet_type back to the underlying
// system that would grade it -- mirrors settle_bets._settle_ev's own
// prefix classification exactly (that function is the source of truth:
// it's what actually settles these rows), so a market this doesn't
// recognize wouldn't settle server-side either. Returns null for anything
// _settle_ev would fall through to its "unrecognised bet_type" warning for.
function resolveEvSystem(bt: string): string | null {
  const up = bt.toUpperCase()
  if (up.startsWith('HR'))            return 'HR'
  if (up.startsWith('OUTS_'))         return 'OUTS'
  if (up.startsWith('K_'))            return 'K'
  if (up.startsWith('PITCHER_ER_'))   return 'PITCHER_ER'
  if (up.startsWith('BATTER_TB_'))    return 'BATTER_TB'
  if (up.startsWith('BATTER_HITS_'))  return 'BATTER_HITS'
  if (up.startsWith('BATTER_K_'))     return 'BATTER_K'
  if (up.startsWith('SB_'))           return 'SB'
  if (up.startsWith('NRFI') || up.startsWith('YRFI')) return 'NRFI'
  if (up.startsWith('GAME_'))         return 'GAME'
  if (up === 'HOME' || up === 'AWAY') return 'F5'
  return null
}

// Recover the underlying system + native bet_type from a pooled +EV alert
// row (system === "EV", added 2026-08-20 -- see CONTEXT.md s5 "EV bet
// tracking"). Exported so components that branch on system for layout
// (e.g. picks-table's prop-vs-game-line row shape) can resolve the real
// market too, not just this file's own pickLabel().
export function resolveEvUnderlying(bet: Bet): { system: string; bet_type: string } {
  const bt = stripEvBookSuffix(bet.bet_type ?? '', bet.book)
  return { system: resolveEvSystem(bt) ?? 'EV', bet_type: bt }
}

// The actual per-system label logic, factored out of pickLabel so an EV
// row can be formatted through the exact same rules as a native bet on
// its resolved underlying market (see resolveEvUnderlying above) instead
// of duplicating every branch below a second time.
function formatPick(sys: string, bt: string, player: string, team: string, away: string, home: string): string {
  // -- Game Lines -------------------------------------------------------
  if (sys === 'NRFI' || sys === '1I' || sys === '1IOU') {
    if (bt === 'NRFI')    return 'No Run 1st Inning'
    if (bt === 'YRFI')    return 'Run in 1st Inning'
    if (bt === '1I_HOME') return `${home} 1st Inning Moneyline`
    if (bt === '1I_AWAY') return `${away} 1st Inning Moneyline`
    if (bt === '1I_DRAW') return 'Draw after 1st Inning'
    return bt
  }
  if (sys === 'F5') {
    if (bt === 'HOME') return `${home} First 5 Innings Moneyline`
    if (bt === 'AWAY') return `${away} First 5 Innings Moneyline`
    return bt
  }

  // -- Innings Windows --------------------------------------------------
  // bet_type format: F3_HOME, F1H_AWAY, F7_HOME, GAME_AWAY
  if (sys === 'F3' || sys === 'F1H' || sys === 'F7' || sys === 'GAME') {
    const label = INNINGS_LABEL[sys] ?? sys
    // Parse side from bet_type by splitting on last underscore
    const parts = bt.split('_')
    const side  = parts[parts.length - 1]
    if (side === 'HOME') return `${home} ${label} Moneyline`
    if (side === 'AWAY') return `${away} ${label} Moneyline`
    return `${label} ${bt}`
  }

  // -- Pitcher Props ----------------------------------------------------
  if (sys === 'K') {
    const plus = plusLabel(bt)
    if (plus) return `${player} ${plus} Strikeouts`
    const side = bt.startsWith('K_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('K_OVER_', '').replace('K_UNDER_', '')
    return `${player} ${side} ${line} Strikeouts`
  }
  if (sys === 'OUTS') {
    const plus = plusLabel(bt)
    if (plus) return `${player} ${plus} Outs Recorded`
    const side = bt.startsWith('OUTS_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('OUTS_OVER_', '').replace('OUTS_UNDER_', '')
    return `${player} ${side} ${line} Outs Recorded`
  }
  if (sys === 'PITCHER_ER') {
    // bet_type: PITCHER_ER_OVER_2.5 / PITCHER_ER_UNDER_2.5
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('PITCHER_ER_OVER_', '').replace('PITCHER_ER_UNDER_', '')
    return `${player} ${side} ${line} Earned Runs`
  }

  // -- Batter Props -----------------------------------------------------
  if (sys === 'HR') {
    // "HR_NO" (EV rows only): the Under 0.5 / No-HR side. Token match, not
    // startsWith('HR_NO') -- a 1+ HR row on book "novig" is "HR_novig".
    if (bt.split('_')[1]?.toUpperCase() === 'NO') return `${player} to Not Hit a Home Run`
    return `${player} to Hit a Home Run`
  }
  if (sys === 'BATTER_K') {
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_K_OVER_', '').replace('BATTER_K_UNDER_', '')
    return `${player} ${side} ${line} Strikeouts`
  }
  if (sys === 'BATTER_TB') {
    const plus = plusLabel(bt)
    if (plus) return `${player} ${plus} Total Bases`
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_TB_OVER_', '').replace('BATTER_TB_UNDER_', '')
    return `${player} ${side} ${line} Total Bases`
  }
  if (sys === 'BATTER_HITS') {
    const plus = plusLabel(bt)
    if (plus) return `${player} ${plus} Hits`
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_HITS_OVER_', '').replace('BATTER_HITS_UNDER_', '')
    return `${player} ${side} ${line} Hits`
  }
  if (sys === 'SB') {
    const plus = plusLabel(bt)
    if (plus) return `${player} ${plus} Stolen Bases`
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('SB_OVER_', '').replace('SB_UNDER_', '')
    return `${player} ${side} ${line} Stolen Bases`
  }

  return bt
}

// Human-readable pick label from a Bet row
export function pickLabel(bet: Bet): string {
  const away   = bet.away_team ?? ''
  const home   = bet.home_team ?? ''
  const player = bet.player ?? ''
  // Prefer 3-letter abbrevs; fall back to whatever is shorter
  const team   = away.length <= 3 ? away : home.length <= 3 ? home : away

  // EV rows pool alerts across every market above under one tracking
  // system -- format through the market they actually resolve to (see
  // resolveEvUnderlying) so the label reads like a native pick instead of
  // leaking the raw "{bet_type}_{book}" string (e.g. "K_OVER_7.5_draftkings").
  if (bet.system === 'EV') {
    const { system, bet_type } = resolveEvUnderlying(bet)
    return formatPick(system, bet_type, player, team, away, home)
  }

  return formatPick(bet.system, bet.bet_type ?? '', player, team, away, home)
}

// -- Sportsbook / exchange identity -------------------------------------------
// Keys are canonical lowercase book ids as stored in bets.book / odds_history.
export interface BookMeta {
  label: string
  short: string
  color: string
  bg: string
  isExchange?: boolean
}

function bookMeta(label: string, short: string, color: string, isExchange = false): BookMeta {
  return {
    label, short, color,
    bg: `color-mix(in oklab, ${color} 14%, #090a0f)`,
    ...(isExchange ? { isExchange: true } : {}),
  }
}

export const BOOK_META: Record<string, BookMeta> = {
  draftkings: bookMeta('DraftKings',  'DK',   '#53d337'),
  fanduel:    bookMeta('FanDuel',     'FD',   '#1493ff'),
  betmgm:     bookMeta('BetMGM',      'MGM',  '#c4a35a'),
  caesars:    bookMeta('Caesars',     'CZR',  '#d4af37'),
  bet365:     bookMeta('bet365',      '365',  '#1ba87e'),
  fanatics:   bookMeta('Fanatics',    'FAN',  '#e6007e'),
  hardrock:   bookMeta('Hard Rock',   'HRB',  '#f5a623'),
  betrivers:  bookMeta('BetRivers',   'BR',   '#3d7bd9'),
  espnbet:    bookMeta('ESPN BET',    'ESPN', '#ff5a4f'),
  thescore:   bookMeta('theScore',    'SCR',  '#ff5a4f'),
  pinnacle:   bookMeta('Pinnacle',    'PIN',  '#f97316'),
  kalshi:     bookMeta('Kalshi',      'KAL',  '#00d2a0', true),
  novig:      bookMeta('Novig',       'NOV',  '#38bdf8', true),
  prophetx:   bookMeta('ProphetX',    'PRX',  '#8b5cf6', true),
  partycasino: bookMeta('Party Casino', 'PTY', '#c4a35a'),
  sugarhouse: bookMeta('SugarHouse',  'SUG',  '#3d7bd9'),
  underdog:   bookMeta('Underdog',    'UND',  '#facc15'),
  parx:       bookMeta('Parx',        'PAR',  '#53d337'),
  pointsbet:  bookMeta('PointsBet',   'PTB',  '#ff5a4f'),
}

export function bookDisplay(book: string | null | undefined): BookMeta {
  const key = (book ?? '').toLowerCase()
  return BOOK_META[key] ?? bookMeta(book ? book[0].toUpperCase() + book.slice(1) : '--', (book ?? '--').slice(0, 3).toUpperCase(), '#9ca3af')
}
