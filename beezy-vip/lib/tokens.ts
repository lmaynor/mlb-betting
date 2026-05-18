/**
 * Design tokens shared across all beezy-vip components.
 * Import from here instead of redefining locally.
 */
import type { Bet } from '@/lib/types'

// Border constant
export const B = '0.5px solid #1f1f24'

// System solid colors (charts, text, filter chips)
export const SYSTEM_COLOR: Record<string, string> = {
  NRFI: '#10b981',
  HR:   '#f59e0b',
  F5:   '#3b82f6',
  K:    '#a78bfa',
  OUTS: '#fb923c',
  ALL:  '#f5f5f7',
}

// System pill styles (bg + color + border) for badges
export const SYSTEM_PILL: Record<string, { bg: string; color: string; border: string }> = {
  NRFI: { bg: '#052016', color: '#10b981', border: '0.5px solid #0f6e56' },
  HR:   { bg: '#1c1207', color: '#f59e0b', border: '0.5px solid #854f0b' },
  F5:   { bg: '#040e1c', color: '#3b82f6', border: '0.5px solid #185fa5' },
  K:    { bg: '#0e0718', color: '#a78bfa', border: '0.5px solid #534ab7' },
  OUTS: { bg: '#1a0d05', color: '#fb923c', border: '0.5px solid #9a3412' },
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

// Human-readable pick label from a Bet row
export function pickLabel(bet: Bet): string {
  const bt     = bet.bet_type ?? ''
  const sys    = bet.system
  const away   = bet.away_team ?? ''
  const home   = bet.home_team ?? ''
  const player = bet.player ?? ''
  const team   = away.length <= 3 ? away : home.length <= 3 ? home : away

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
  if (sys === 'HR')   return `${player} (${team}) to Hit a Home Run`
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
  return bt
}
