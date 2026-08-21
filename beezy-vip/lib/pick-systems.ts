import { SYSTEM_PILL } from '@/lib/tokens'

export type PickSystem = {
  key: string
  slug: string
  name: string
  shortName: string
  description: string
  learnSlug?: string
}

export const PICK_SYSTEMS: PickSystem[] = [
  {
    key: 'NRFI',
    slug: 'nrfi',
    name: 'No Run First Inning',
    shortName: 'NRFI',
    description: 'Starter form, umpire zone, park, and weather signals for scoreless first innings.',
    learnSlug: 'what-is-nrfi',
  },
  {
    key: '1I',
    slug: '1i',
    name: 'First Inning Moneyline',
    shortName: '1I',
    description: 'Three-way first-inning sides: home, away, or draw before the game settles in.',
  },
  {
    key: 'F3',
    slug: 'f3',
    name: 'First 3 Innings',
    shortName: 'F3',
    description: 'Early-game starter window for openers, short leashes, and lineup-top exposure.',
  },
  {
    key: 'F5',
    slug: 'f5',
    name: 'First 5 Innings',
    shortName: 'F5',
    description: 'Starting pitcher SIERA, recent form, opponent wOBA, and umpire run context.',
    learnSlug: 'f5-betting',
  },
  {
    key: 'F1H',
    slug: 'f1h',
    name: 'First Half',
    shortName: 'F1H',
    description: 'Hybrid innings window for stronger starter splits before bullpen noise dominates.',
  },
  {
    key: 'F7',
    slug: 'f7',
    name: 'First 7 Innings',
    shortName: 'F7',
    description: 'Late starter and bridge-relief pricing before full bullpen exposure.',
  },
  {
    key: 'GAME',
    slug: 'game',
    name: 'Full Game',
    shortName: 'Game',
    description: 'Full-game moneyline model with starter, bullpen, park, weather, and offense context.',
  },
  {
    key: 'HR',
    slug: 'hr',
    name: 'Home Run Props',
    shortName: 'HR',
    description: 'Barrels, exit velocity, launch angle, pitcher HR vulnerability, park, and platoon fit.',
    learnSlug: 'home-run-props',
  },
  {
    key: 'BATTER_TB',
    slug: 'batter-tb',
    name: 'Batter Total Bases',
    shortName: 'Total Bases',
    description: 'Contact quality, matchup, lineup slot, and park context for batter total-base props.',
  },
  {
    key: 'BATTER_HITS',
    slug: 'batter-hits',
    name: 'Batter Hits',
    shortName: 'Hits',
    description: 'BABIP, contact profile, platoon context, and pitcher hits-allowed shape.',
  },
  {
    key: 'SB',
    slug: 'sb',
    name: 'Stolen Base Props',
    shortName: 'SB',
    description: 'On-base skill, base-running speed, lineup slot, opposing pitcher hold rate, and catcher arm strength/pop time for stolen-base props.',
  },
  {
    key: 'BATTER_K',
    slug: 'batter-k',
    name: 'Batter Strikeouts',
    shortName: 'Batter K',
    description: 'Batter whiff and contact tendencies against opposing pitcher strikeout profile.',
  },
  {
    key: 'K',
    slug: 'k',
    name: 'Pitcher Strikeouts',
    shortName: 'K',
    description: 'SwStr%, zone, chase, opponent lineup K rate, umpire tendency, and recent strikeouts.',
    learnSlug: 'mlb-strikeout-props',
  },
  {
    key: 'OUTS',
    slug: 'outs',
    name: 'Pitcher Outs',
    shortName: 'Outs',
    description: 'Pitch efficiency, innings-per-start trend, bullpen availability, and opponent OBP.',
  },
  {
    key: 'PITCHER_ER',
    slug: 'pitcher-er',
    name: 'Pitcher Earned Runs',
    shortName: 'Pitcher ER',
    description: 'Starter quality, opponent run creation, weather, and leash context for earned-runs props.',
  },
]

export const PICK_SYSTEM_BY_SLUG: Record<string, PickSystem> = Object.fromEntries(PICK_SYSTEMS.map(system => [system.slug, system]))
export const PICK_SYSTEM_BY_KEY: Record<string, PickSystem> = Object.fromEntries(PICK_SYSTEMS.map(system => [system.key, system]))

export function getPickSystemBySlug(slug: string) {
  return PICK_SYSTEM_BY_SLUG[slug.toLowerCase()]
}

export function getPickSystemByKey(key: string) {
  return PICK_SYSTEM_BY_KEY[key.toUpperCase()]
}

export function systemPill(system: string) {
  return SYSTEM_PILL[system] ?? SYSTEM_PILL.ALL
}
