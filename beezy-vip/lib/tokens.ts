/**
 * Design tokens shared across all beezy-vip components.
 * Import from here instead of redefining locally.
 */
import type { Bet } from '@/lib/types'

// Border constant
export const B = '0.5px solid #1f1f24'

// System solid colors (charts, text, filter chips)
export const SYSTEM_COLOR: Record<string, string> = {
  // Game Lines
  NRFI:        '#10b981',
  F5:          '#3b82f6',
  // Innings Windows
  F3:          '#06b6d4',   // cyan -- shorter window, lighter feel
  F1H:         '#0ea5e9',   // sky blue -- between F3 and F5
  F7:          '#6366f1',   // indigo -- deeper into game
  GAME:        '#475569',   // slate -- full game, neutral
  // Pitcher Props
  K:           '#a78bfa',
  OUTS:        '#fb923c',
  PITCHER_ER:  '#f43f5e',   // rose
  // Batter Props
  HR:          '#f59e0b',
  BATTER_K:    '#e879f9',   // fuchsia
  BATTER_TB:   '#84cc16',   // lime
  BATTER_HITS: '#14b8a6',   // teal
  // Meta
  ALL:         '#f5f5f7',
}

// System pill styles (bg + color + border) for badges
export const SYSTEM_PILL: Record<string, { bg: string; color: string; border: string }> = {
  // Game Lines
  NRFI:        { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  F5:          { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  // Innings Windows
  F3:          { bg: '#041418', color: '#06b6d4', border: '0.5px solid #0e7490' },
  F1H:         { bg: '#040e18', color: '#0ea5e9', border: '0.5px solid #0369a1' },
  F7:          { bg: '#07071a', color: '#6366f1', border: '0.5px solid #3730a3' },
  GAME:        { bg: '#0d0f12', color: '#94a3b8', border: '0.5px solid #334155' },
  // Pitcher Props
  K:           { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS:        { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
  PITCHER_ER:  { bg: '#1a0508', color: '#f43f5e', border: '0.5px solid #9f1239' },
  // Batter Props
  HR:          { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  BATTER_K:    { bg: '#1a0518', color: '#e879f9', border: '0.5px solid #86198f' },
  BATTER_TB:   { bg: '#071505', color: '#84cc16', border: '0.5px solid #3f6212' },
  BATTER_HITS: { bg: '#041412', color: '#14b8a6', border: '0.5px solid #0f766e' },
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
