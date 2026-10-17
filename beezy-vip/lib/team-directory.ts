/**
 * Team vocabulary for SEARCH on the picks pages: every sport's teams by abbreviation, city and nickname, so a user can type
 * "houston", "texans", "vegas" or "habs" and not only "HOU". Abbreviations repeat across sports (HOU is the Astros and the
 * Texans), so every lookup is keyed by sport.
 *
 * NFL and MLB are listed here (the codes the feeds use: NFL follows ev/sports/nfl.py CODES, so JAC and LAR, with JAX / LA
 * / WSH accepted as aliases). NHL and NBA come from lib/sport-teams.json, which is generated from the Python team tables
 * (tests/test_sport_assets.py keeps it in step), so those two sports never drift from the data.
 */
import teams from './sport-teams.json'
import type { SportSlug } from '@/lib/sports'

interface Team { abbr: string; name: string; alias?: string[] }

const NFL: Team[] = [
  { abbr: 'ARI', name: 'Arizona Cardinals', alias: ['cards'] }, { abbr: 'ATL', name: 'Atlanta Falcons' },
  { abbr: 'BAL', name: 'Baltimore Ravens' }, { abbr: 'BUF', name: 'Buffalo Bills' },
  { abbr: 'CAR', name: 'Carolina Panthers' }, { abbr: 'CHI', name: 'Chicago Bears' },
  { abbr: 'CIN', name: 'Cincinnati Bengals' }, { abbr: 'CLE', name: 'Cleveland Browns' },
  { abbr: 'DAL', name: 'Dallas Cowboys' }, { abbr: 'DEN', name: 'Denver Broncos' },
  { abbr: 'DET', name: 'Detroit Lions' }, { abbr: 'GB', name: 'Green Bay Packers', alias: ['pack'] },
  { abbr: 'HOU', name: 'Houston Texans' }, { abbr: 'IND', name: 'Indianapolis Colts' },
  { abbr: 'JAC', name: 'Jacksonville Jaguars', alias: ['JAX', 'jags'] }, { abbr: 'KC', name: 'Kansas City Chiefs' },
  { abbr: 'LAC', name: 'Los Angeles Chargers', alias: ['LA Chargers', 'bolts'] },
  { abbr: 'LAR', name: 'Los Angeles Rams', alias: ['LA', 'LA Rams'] }, { abbr: 'LV', name: 'Las Vegas Raiders', alias: ['vegas'] },
  { abbr: 'MIA', name: 'Miami Dolphins', alias: ['fins'] }, { abbr: 'MIN', name: 'Minnesota Vikings', alias: ['vikes'] },
  { abbr: 'NE', name: 'New England Patriots', alias: ['pats'] }, { abbr: 'NO', name: 'New Orleans Saints', alias: ['nola'] },
  { abbr: 'NYG', name: 'New York Giants', alias: ['NY Giants', 'big blue'] }, { abbr: 'NYJ', name: 'New York Jets', alias: ['NY Jets'] },
  { abbr: 'PHI', name: 'Philadelphia Eagles', alias: ['philly'] }, { abbr: 'PIT', name: 'Pittsburgh Steelers' },
  { abbr: 'SEA', name: 'Seattle Seahawks', alias: ['hawks'] }, { abbr: 'SF', name: 'San Francisco 49ers', alias: ['niners', 'sf 49ers'] },
  { abbr: 'TB', name: 'Tampa Bay Buccaneers', alias: ['bucs'] }, { abbr: 'TEN', name: 'Tennessee Titans' },
  { abbr: 'WAS', name: 'Washington Commanders', alias: ['WSH', 'DC'] },
]

const MLB: Team[] = [
  { abbr: 'ARI', name: 'Arizona Diamondbacks', alias: ['dbacks', 'd-backs'] }, { abbr: 'ATL', name: 'Atlanta Braves' },
  { abbr: 'BAL', name: 'Baltimore Orioles', alias: ['os'] }, { abbr: 'BOS', name: 'Boston Red Sox', alias: ['sox'] },
  { abbr: 'CHC', name: 'Chicago Cubs' }, { abbr: 'CWS', name: 'Chicago White Sox', alias: ['CHW', 'sox'] },
  { abbr: 'CIN', name: 'Cincinnati Reds' }, { abbr: 'CLE', name: 'Cleveland Guardians' },
  { abbr: 'COL', name: 'Colorado Rockies' }, { abbr: 'DET', name: 'Detroit Tigers' },
  { abbr: 'HOU', name: 'Houston Astros', alias: ['stros'] }, { abbr: 'KC', name: 'Kansas City Royals' },
  { abbr: 'LAA', name: 'Los Angeles Angels', alias: ['LA Angels', 'anaheim'] },
  { abbr: 'LAD', name: 'Los Angeles Dodgers', alias: ['LA Dodgers'] }, { abbr: 'MIA', name: 'Miami Marlins' },
  { abbr: 'MIL', name: 'Milwaukee Brewers', alias: ['brew crew'] }, { abbr: 'MIN', name: 'Minnesota Twins' },
  { abbr: 'NYM', name: 'New York Mets', alias: ['NY Mets'] }, { abbr: 'NYY', name: 'New York Yankees', alias: ['NY Yankees', 'yanks'] },
  { abbr: 'OAK', name: 'Oakland Athletics', alias: ['ATH', 'as', 'sacramento'] }, { abbr: 'PHI', name: 'Philadelphia Phillies', alias: ['philly'] },
  { abbr: 'PIT', name: 'Pittsburgh Pirates', alias: ['bucs'] }, { abbr: 'SD', name: 'San Diego Padres', alias: ['friars'] },
  { abbr: 'SF', name: 'San Francisco Giants' }, { abbr: 'SEA', name: 'Seattle Mariners', alias: ['ms'] },
  { abbr: 'STL', name: 'St. Louis Cardinals', alias: ['saint louis', 'cards'] }, { abbr: 'TB', name: 'Tampa Bay Rays' },
  { abbr: 'TEX', name: 'Texas Rangers' }, { abbr: 'TOR', name: 'Toronto Blue Jays', alias: ['jays'] },
  { abbr: 'WSH', name: 'Washington Nationals', alias: ['WAS', 'nats', 'DC'] },
]

type JsonTeam = { abbr: string; name: string; alias: string[] }
const JSON_TEAMS = teams as unknown as Record<'nhl' | 'nba', JsonTeam[]>

// Colloquial names the official tables do not carry. Matching is substring on folded text, so a nickname that is a word
// of the full name ("Knights") already works; these are the ones that are not.
const NICKNAMES: Record<'nhl' | 'nba', Record<string, string[]>> = {
  nhl: { MTL: ['habs'], PIT: ['pens'], WSH: ['caps'], TBL: ['bolts'], TB: ['bolts'], NSH: ['preds'], NYI: ['isles'], VGK: ['vegas'], NJD: ['jersey', 'NJ'], NJ: ['jersey'], LAK: ['kings', 'LA'], SJS: ['SJ'], CBJ: ['jackets'], UTA: ['utah'] },
  nba: { PHI: ['sixers', 'philly'], MIN: ['wolves', 'twolves'], CLE: ['cavs'], DAL: ['mavs'], POR: ['blazers'], GSW: ['dubs', 'GS'], NOP: ['pels', 'NO'], NYK: ['knicks', 'NY'], SAS: ['SA'], BKN: ['brooklyn'] },
}

/** accent-fold, lowercase, drop periods and apostrophes, hyphens to spaces, collapse spaces. */
export function foldText(s: string | null | undefined): string {
  return (s ?? '')
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[.'\u2019]/g, '').replace(/-/g, ' ').replace(/\s+/g, ' ').trim()
}

type SearchSport = SportSlug

function table(sport: SearchSport): { abbr: string; terms: string[] }[] {
  const mk = (t: Team) => ({ abbr: t.abbr, terms: [t.abbr, t.name, ...(t.alias ?? [])] })
  if (sport === 'nfl') return NFL.map(mk)
  if (sport === 'mlb') return MLB.map(mk)
  return JSON_TEAMS[sport].map(t => ({ abbr: t.abbr, terms: [t.abbr, t.name, ...t.alias, ...(NICKNAMES[sport][t.abbr] ?? [])] }))
}

const INDEX: Partial<Record<SearchSport, Map<string, string>>> = {}
function termsByAbbr(sport: SearchSport): Map<string, string> {
  let m = INDEX[sport]
  if (!m) {
    m = new Map()
    for (const t of table(sport)) {
      const text = [...new Set(t.terms.map(foldText))].join(' | ')
      m.set(foldText(t.abbr), text)
      // an alias that is itself a code (JAX, WSH, LA) must reach the same team
      for (const a of t.terms) if (/^[A-Za-z]{2,4}$/.test(a) && a === a.toUpperCase()) m.set(foldText(a), text)
    }
    INDEX[sport] = m
  }
  return m
}

/** Searchable text for one team code in one sport: abbreviation, city, nickname and aliases ('' when the code is unknown). */
export function teamSearchTerms(sport: SearchSport, abbr: string | null | undefined): string {
  return termsByAbbr(sport).get(foldText(abbr)) ?? ''
}

/** The team codes that appear in a matchup string ('HOU v JAC', 'BOS @ LAL', 'KC@LV'). */
export function codesInMatchup(matchup: string | null | undefined): string[] {
  return (matchup ?? '').match(/\b[A-Z]{2,4}\b/g) ?? []
}

/**
 * The text a pick row is searched against: the player (or pick) text, the matchup as written, and every team named in the
 * matchup expanded to its city, nickname and aliases. So "houston" finds a Texans game, "texans" too, and "HOU" still works.
 */
export function searchHaystack(sport: SearchSport, ...parts: (string | null | undefined)[]): string {
  const text = parts.filter(Boolean).join(' ')
  const teamText = codesInMatchup(text).map(c => teamSearchTerms(sport, c)).filter(Boolean).join(' | ')
  return foldText(`${text} | ${teamText}`)
}

/** Every whitespace-separated word of the query must appear somewhere in the haystack (accent / case / period insensitive). */
export function matchesSearch(haystack: string, q: string): boolean {
  const words = foldText(q).split(' ').filter(Boolean)
  if (words.length === 0) return true
  const h = foldText(haystack)
  return words.every(w => h.includes(w))
}
