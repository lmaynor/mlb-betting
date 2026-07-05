/**
 * Design tokens shared across all beezy-vip components.
 * Import from here instead of redefining locally.
 *
 * Terminal aesthetic: near-black surfaces, hairline borders, Signal Green
 * brand accent, and a per-system color taxonomy (Discord-style) so each
 * betting system reads as its own identity color across the product.
 */
import type { Bet } from '@/lib/types'

// Hairline border — replaces the old hard 1px-black Dell frame.
export const B = '1px solid var(--basalt)'
// Subtler inner border for table rows and nested dividers.
export const B_INNER = '1px solid #201f22'

// ── Confidence tiers (mirror --strong / --lean / --watch in globals.css) ──
export type ScoreTier = 'strong' | 'lean' | 'watch'
export const TIER_COLOR: Record<ScoreTier, string> = {
  strong: '#71d083',   // signal green — high confidence
  lean:   '#e3b261',   // amber — worth a look
  watch:  '#b5b2bc',   // silver — neutral signal
}
export const TIER_LABEL: Record<ScoreTier, string> = {
  strong: 'STRONG PLAY',
  lean:   'LEAN PLAY',
  watch:  'WATCH',
}

// ── Per-system identity hues (must match --sys-* vars in globals.css) ──
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
  // Meta
  ALL:         '#c9c6cf',   // neutral silver
}

// Build a tinted pill { bg, color, border } from a system hue. Surfaces and
// borders are derived from the hue so the whole taxonomy stays consistent.
type Pill = { bg: string; color: string; border: string }
function pill(hue: string): Pill {
  return {
    bg:     `color-mix(in oklab, ${hue} 16%, #04040b)`,
    color:  hue,
    border: `1px solid color-mix(in oklab, ${hue} 40%, #04040b)`,
  }
}

export const SYSTEM_PILL: Record<string, Pill> = Object.fromEntries(
  Object.entries(SYSTEM_COLOR).map(([sys, hue]) => [sys, pill(hue)]),
) as Record<string, Pill>

// Fallback for unknown or newly-pipelined systems.
SYSTEM_PILL.ALL = {
  bg:     'color-mix(in oklab, #c9c6cf 10%, #04040b)',
  color:  '#c9c6cf',
  border: '1px solid #323035',
}

// Canonical SHORT display label per system -- the ONE place pill wording
// lives. Raw registry keys (BATTER_TB, PITCHER_ER, 1IOU) are internal ids,
// not UI copy; every pill/chip should render systemLabel(sys) instead.
export const SYSTEM_LABEL: Record<string, string> = {
  NRFI: 'NRFI', YRFI: 'YRFI', '1IOU': 'NRFI', '1I': '1st Inn',
  F5: 'F5', F3: 'F3', F7: 'F7', F1H: '1st Half', GAME: 'Game',
  HR: 'HR', K: 'K', OUTS: 'Outs',
  BATTER_TB: 'Total Bases', BATTER_HITS: 'Hits', BATTER_K: 'Batter K',
  PITCHER_ER: 'Earned Runs',
}

// Tighter variant for narrow table cells / chips.
export const SYSTEM_LABEL_SHORT: Record<string, string> = {
  ...SYSTEM_LABEL,
  BATTER_TB: 'TB', BATTER_HITS: 'HITS', BATTER_K: 'B.K',
  PITCHER_ER: 'ER', '1I': '1I', F1H: '1H', GAME: 'GAME',
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
// so we only ever render a logo we actually ship — server components can't
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

// Human-readable pick label from a Bet row
export function pickLabel(bet: Bet): string {
  const bt     = bet.bet_type ?? ''
  const sys    = bet.system
  const away   = bet.away_team ?? ''
  const home   = bet.home_team ?? ''
  const player = bet.player ?? ''
  // Prefer 3-letter abbrevs; fall back to whatever is shorter
  const team   = away.length <= 3 ? away : home.length <= 3 ? home : away

  // -- Game Lines -------------------------------------------------------
  if (sys === 'NRFI') {
    if (bt === 'NRFI')    return 'No Run 1st Inning'
    if (bt === 'YRFI')    return 'Run in 1st Inning'
    if (bt === '1I_HOME') return `${home} 1st Inning Moneyline`
    if (bt === '1I_AWAY') return `${away} 1st Inning Moneyline`
    if (bt === '1I_DRAW') return 'Draw 1st Inning Moneyline'
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
    const side = bt.startsWith('K_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('K_OVER_', '').replace('K_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Strikeouts`
  }
  if (sys === 'OUTS') {
    const side = bt.startsWith('OUTS_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('OUTS_OVER_', '').replace('OUTS_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Outs Recorded`
  }
  if (sys === 'PITCHER_ER') {
    // bet_type: PITCHER_ER_OVER_2.5 / PITCHER_ER_UNDER_2.5
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('PITCHER_ER_OVER_', '').replace('PITCHER_ER_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Earned Runs`
  }

  // -- Batter Props -----------------------------------------------------
  if (sys === 'HR') return `${player} (${team}) to Hit a Home Run`
  if (sys === 'BATTER_K') {
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_K_OVER_', '').replace('BATTER_K_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Strikeouts`
  }
  if (sys === 'BATTER_TB') {
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_TB_OVER_', '').replace('BATTER_TB_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Total Bases`
  }
  if (sys === 'BATTER_HITS') {
    const side = bt.includes('_OVER_') ? 'Over' : 'Under'
    const line = bt.replace('BATTER_HITS_OVER_', '').replace('BATTER_HITS_UNDER_', '')
    return `${player} (${team}) ${side} ${line} Hits`
  }

  return bt
}
