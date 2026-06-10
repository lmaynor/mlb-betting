/**
 * Design tokens shared across all beezy-vip components.
 * Import from here instead of redefining locally.
 */
import type { Bet } from '@/lib/types'

// Border constant -- Dell 1996: hard 1px black for outer frames
export const B = '1px solid #000'
// Softer inner border for table rows and subdividers
export const B_INNER = '1px solid #1f1f24'

// Confidence tier colors (mirror --strong/--lean/--watch in globals.css)
export type ScoreTier = 'strong' | 'lean' | 'watch'
export const TIER_COLOR: Record<ScoreTier, string> = {
  strong: '#b3bd95',   // sage -- WIN color, high confidence
  lean:   '#fcc20f',   // Dell yellow -- worth a look
  watch:  '#a5b8c0',   // steel -- neutral signal
}
export const TIER_LABEL: Record<ScoreTier, string> = {
  strong: 'STRONG PLAY',
  lean:   'LEAN PLAY',
  watch:  'WATCH',
}

// System solid colors -- mapped to Dell 1996 catalog tints
export const SYSTEM_COLOR: Record<string, string> = {
  // Game Lines
  NRFI:        '#b3bd95',   // sage
  F5:          '#9ab6c8',   // sky
  // Innings Windows
  F3:          '#a5b8c0',   // steel -- shorter window
  F1H:         '#9ab6c8',   // sky
  F7:          '#8c9ae0',   // periwinkle -- deeper into game
  GAME:        '#8e8a25',   // olive -- full game
  // Pitcher Props
  K:           '#8c9ae0',   // periwinkle
  OUTS:        '#e6915d',   // peach
  PITCHER_ER:  '#d77a7a',   // salmon
  // Batter Props
  HR:          '#d77a7a',   // salmon
  BATTER_K:    '#8c9ae0',   // periwinkle
  BATTER_TB:   '#c0d4a7',   // lime
  BATTER_HITS: '#a5b8c0',   // steel
  // Meta
  ALL:         '#f5f5f7',
}

// System pill styles -- mapped to Dell 1996 catalog tints, hard 1px borders
export const SYSTEM_PILL: Record<string, { bg: string; color: string; border: string }> = {
  // Game Lines
  NRFI:        { bg: '#1a2218', color: '#b3bd95', border: '1px solid #8e9e78' },  // sage
  F5:          { bg: '#131e24', color: '#9ab6c8', border: '1px solid #6a8fa0' },  // sky
  // Innings Windows
  F3:          { bg: '#131a1e', color: '#a5b8c0', border: '1px solid #7a9aa5' },  // steel
  F1H:         { bg: '#131e24', color: '#9ab6c8', border: '1px solid #6a8fa0' },  // sky
  F7:          { bg: '#0f1024', color: '#8c9ae0', border: '1px solid #5c6bbc' },  // periwinkle
  GAME:        { bg: '#1c1c0a', color: '#8e8a25', border: '1px solid #6a6615' },  // olive
  // Pitcher Props
  K:           { bg: '#0f1024', color: '#8c9ae0', border: '1px solid #5c6bbc' },  // periwinkle
  OUTS:        { bg: '#2a1a0f', color: '#e6915d', border: '1px solid #c06830' },  // peach
  PITCHER_ER:  { bg: '#2a1818', color: '#d77a7a', border: '1px solid #b05050' },  // salmon
  // Batter Props
  HR:          { bg: '#2a1818', color: '#d77a7a', border: '1px solid #b05050' },  // salmon
  BATTER_K:    { bg: '#0f1024', color: '#8c9ae0', border: '1px solid #5c6bbc' },  // periwinkle
  BATTER_TB:   { bg: '#141e0f', color: '#c0d4a7', border: '1px solid #8aaa6c' },  // lime
  BATTER_HITS: { bg: '#131a1e', color: '#a5b8c0', border: '1px solid #7a9aa5' },  // steel
  // Fallback for unknown or newly-pipelined systems.
  ALL:          { bg: '#1f1f24', color: '#a1a1aa', border: '1px solid #2a2a31' },
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
