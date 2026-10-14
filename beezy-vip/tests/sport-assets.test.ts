/**
 * lib/sport-assets.ts + lib/sport-assets.server.ts: NHL/NBA team + headshot resolution.
 * Every assertion about a file on disk is checked against public/ for real -- a logo path that 404s on a pick card
 * is exactly the failure this guards.
 */
import fs from 'fs'
import path from 'path'
import teams from '../lib/sport-teams.json'
import { fold, headshotUrlById, playerSlug, teamCode, teamLogoUrl, type AssetSport } from '../lib/sport-assets'
import { headshotUrlByName } from '../lib/sport-assets.server'

const PUBLIC = path.join(__dirname, '..', 'public')
const exists = (url: string) => fs.existsSync(path.join(PUBLIC, url))
const ALL = teams as unknown as Record<AssetSport, { abbr: string; name: string; alias: string[] }[]>

describe('team vocabulary', () => {
  it('has the full leagues', () => {
    expect(ALL.nhl).toHaveLength(32)
    expect(ALL.nba).toHaveLength(30)
  })

  it.each([
    ['nhl', 'MTL', 'MTL'], ['nhl', 'Montréal Canadiens', 'MTL'], ['nhl', 'Montreal Canadiens', 'MTL'],
    ['nhl', 'LA', 'LAK'], ['nhl', 'NJ', 'NJD'], ['nhl', 'TB', 'TBL'], ['nhl', 'St. Louis Blues', 'STL'],
    ['nhl', 'Canadiens', 'MTL'],
    ['nba', 'L.A. Clippers', 'LAC'], ['nba', 'LA Clippers', 'LAC'], ['nba', 'GS', 'GSW'],
    ['nba', 'Golden State Warriors', 'GSW'], ['nba', 'NY', 'NYK'], ['nba', 'UTAH', 'UTA'], ['nba', 'Lakers', 'LAL'],
  ] as const)('%s: %s -> %s', (sport, input, expected) => {
    expect(teamCode(sport, input)).toBe(expected)
  })

  it('never guesses on unknown or ambiguous input', () => {
    expect(teamCode('nhl', 'Springfield Isotopes')).toBeNull()
    expect(teamCode('nhl', '')).toBeNull()
    expect(teamCode('nhl', null)).toBeNull()
    expect(teamCode('nba', 'Los Angeles')).toBeNull()        // two teams end/start with it
    expect(teamCode('nhl', 'New York')).toBeNull()           // Rangers AND Islanders
  })

  it('folds accents, periods and hyphens', () => {
    expect(fold('  Montréal  Canadiens ')).toBe('montreal canadiens')
    expect(fold('L.A. Clippers')).toBe('la clippers')
  })
})

describe('team logos', () => {
  it('every team in both leagues has its logo file on disk (both variants)', () => {
    for (const sport of ['nhl', 'nba'] as AssetSport[]) {
      for (const t of ALL[sport]) {
        const dark = teamLogoUrl(sport, t.abbr, { dark: true })
        const light = teamLogoUrl(sport, t.abbr, { dark: false })
        expect(dark && exists(dark)).toBe(true)
        expect(light && exists(light)).toBe(true)
      }
    }
  })

  it('defaults to the dark variant (the UI is dark) for both leagues; light is opt-in', () => {
    expect(teamLogoUrl('nhl', 'EDM')).toBe('/logos/nhl/edm-dark.svg')
    expect(teamLogoUrl('nhl', 'EDM', { dark: false })).toBe('/logos/nhl/edm.svg')
    expect(teamLogoUrl('nba', 'GS')).toBe('/logos/nba/gsw-dark.svg')
    expect(teamLogoUrl('nba', 'GS', { dark: false })).toBe('/logos/nba/gsw.svg')
  })

  it('unknown team -> null (callers render nothing, never a broken image)', () => {
    expect(teamLogoUrl('nhl', 'XYZ')).toBeNull()
  })

  it('does not collide with MLB logos (flat /logos/{abbr}.png) for shared abbreviations', () => {
    expect(teamLogoUrl('nhl', 'TOR')).toBe('/logos/nhl/tor-dark.svg')
    expect(teamLogoUrl('nba', 'MIA')).toBe('/logos/nba/mia-dark.svg')
    expect(exists('/logos/mia.png')).toBe(true)               // the MLB Marlins asset is untouched
  })
})

describe('headshots', () => {
  it('builds official CDN URLs from ids', () => {
    expect(headshotUrlById('nhl', 8478402)).toBe('https://assets.nhle.com/mugs/nhl/latest/8478402.png')
    expect(headshotUrlById('nba', '1966')).toContain('a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/1966.png')
    expect(headshotUrlById('nba', 1966)).toContain('w=200&h=146')
  })

  it.each([null, undefined, 0, -3, 1.5, 'abc', NaN])('rejects invalid id %p', (bad) => {
    expect(headshotUrlById('nhl', bad as never)).toBeNull()
  })

  it('slug matches the Python build script', () => {
    expect(playerSlug('Alex Ovechkin')).toBe('alex_ovechkin')
    expect(playerSlug("Ja'Marr Chase Jr.")).toBe('jamarr_chase_jr')
    expect(playerSlug('Nikola Jokić')).toBe('nikola_jokic')
    expect(playerSlug('Karl-Anthony Towns')).toBe('karl_anthony_towns')
  })

  it('looks players up by name (+ team) in the generated maps', () => {
    expect(headshotUrlByName('nhl', 'Alex Ovechkin')).toBe('https://assets.nhle.com/mugs/nhl/latest/8471214.png')
    expect(headshotUrlByName('nhl', 'Alex Ovechkin', 'Washington Capitals')).toContain('8471214')
    expect(headshotUrlByName('nba', 'LeBron James')).toContain('players/full/1966.png')
    expect(headshotUrlByName('nba', 'Nikola Jokić')).not.toBeNull()          // accent-folded
  })

  it('returns null for unknown players and empty names', () => {
    expect(headshotUrlByName('nhl', 'Nobody McFake')).toBeNull()
    expect(headshotUrlByName('nhl', '')).toBeNull()
    expect(headshotUrlByName('nba', null)).toBeNull()
  })
})
