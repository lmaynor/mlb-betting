/**
 * IA-04: the (league, abbrev) team-logo mapping. One resolver (lib/sport-assets anyTeamLogoUrl) serves the
 * Daily Card, Past Cards, Results, the boards and the OG cards, because an abbreviation alone cannot name a
 * logo: ATL is the Braves, the Hawks and the Falcons; MIA is the Marlins, the Heat, the Dolphins and the
 * Hurricanes. These tests pin the collision sets, the known slug divergences, and the fail-closed rule:
 * an unknown (league, abbrev) pair renders NO logo, never another league's and never a default team's.
 */
import * as fs from 'fs'
import * as path from 'path'
import { anyTeamLogoUrl } from '@/lib/sport-assets'
import { ogTeamLogoUrl } from '@/lib/og-brand'
import ncaabLogos from '@/lib/ncaab-logos.json'

const ncaa = (id: number) => `https://a.espncdn.com/combiner/i?img=/i/teamlogos/ncaa/500/${id}.png&w=64&h=64`
const nfl = (slug: string) => `https://a.espncdn.com/combiner/i?img=/i/teamlogos/nfl/500/${slug}.png&w=64&h=64`

describe('collision abbreviations resolve per league', () => {
  it('ATL: Braves / Hawks / Falcons', () => {
    expect(anyTeamLogoUrl('mlb', 'ATL')).toBe('/logos/atl.png')
    expect(anyTeamLogoUrl('nba', 'ATL')).toBe('/logos/nba/atl-dark.svg')
    expect(anyTeamLogoUrl('nfl', 'ATL')).toBe(nfl('atl'))
  })
  it('MIA: Marlins / Heat / Dolphins / Hurricanes', () => {
    expect(anyTeamLogoUrl('mlb', 'MIA')).toBe('/logos/mia.png')
    expect(anyTeamLogoUrl('nba', 'MIA')).toBe('/logos/nba/mia-dark.svg')
    expect(anyTeamLogoUrl('nfl', 'MIA')).toBe(nfl('mia'))
    expect(anyTeamLogoUrl('ncaab', 'MIA')).toBe(ncaa(2390))
  })
  it('KC: Royals / Chiefs, and nothing in the NBA or NHL', () => {
    expect(anyTeamLogoUrl('mlb', 'KC')).toBe('/logos/kc.png')
    expect(anyTeamLogoUrl('nfl', 'KC')).toBe(nfl('kc'))
    expect(anyTeamLogoUrl('nba', 'KC')).toBeNull()
    expect(anyTeamLogoUrl('nhl', 'KC')).toBeNull()
    expect(anyTeamLogoUrl('ncaab', 'KC')).toBeNull()
  })
  it('DET: Tigers / Lions / Detroit Mercy', () => {
    expect(anyTeamLogoUrl('mlb', 'DET')).toBe('/logos/det.png')
    expect(anyTeamLogoUrl('nfl', 'DET')).toBe(nfl('det'))
    expect(anyTeamLogoUrl('ncaab', 'DET')).toBe(ncaa(2174))
  })
  it('GB and IND exist only where the league has them', () => {
    expect(anyTeamLogoUrl('nfl', 'GB')).toBe(nfl('gb'))
    expect(anyTeamLogoUrl('ncaab', 'GB')).toBe(ncaa(2739)) // Green Bay Phoenix, not the Packers
    expect(anyTeamLogoUrl('nfl', 'IND')).toBe(nfl('ind'))
    expect(anyTeamLogoUrl('nba', 'IND')).toBe('/logos/nba/ind-dark.svg')
    expect(anyTeamLogoUrl('ncaab', 'IND')).toBe(ncaa(84)) // Indiana Hoosiers, not the Colts
  })
  it('LA family stays inside its league', () => {
    expect(anyTeamLogoUrl('nfl', 'LA')).toBe(nfl('lar'))
    expect(anyTeamLogoUrl('nfl', 'LAR')).toBe(nfl('lar'))
    expect(anyTeamLogoUrl('nhl', 'LA')).toBe('/logos/nhl/lak-dark.svg') // Kings' canonical code is LAK; LA is its alias
    expect(anyTeamLogoUrl('nhl', 'LAK')).toBe('/logos/nhl/lak-dark.svg')
    expect(anyTeamLogoUrl('nba', 'LAL')).toBe('/logos/nba/lal-dark.svg')
    expect(anyTeamLogoUrl('nba', 'LAC')).toBe('/logos/nba/lac-dark.svg')
    expect(anyTeamLogoUrl('mlb', 'LA')).toBeNull() // MLB codes are LAA / LAD; a bare LA is not guessed
    expect(anyTeamLogoUrl('mlb', 'LAA')).toBe('/logos/laa.png')
    expect(anyTeamLogoUrl('mlb', 'LAD')).toBe('/logos/lad.png')
  })
  it('NY family: NYY/NYM, NYG/NYJ, NYK, NYR/NYI are five different teams', () => {
    expect(anyTeamLogoUrl('mlb', 'NYY')).toBe('/logos/nyy.png')
    expect(anyTeamLogoUrl('mlb', 'NYM')).toBe('/logos/nym.png')
    expect(anyTeamLogoUrl('nfl', 'NYG')).toBe(nfl('nyg'))
    expect(anyTeamLogoUrl('nfl', 'NYJ')).toBe(nfl('nyj'))
    expect(anyTeamLogoUrl('nba', 'NYK')).toBe('/logos/nba/nyk-dark.svg')
    expect(anyTeamLogoUrl('nhl', 'NYR')).toBe('/logos/nhl/nyr-dark.svg')
    expect(anyTeamLogoUrl('nhl', 'NYI')).toBe('/logos/nhl/nyi-dark.svg')
    expect(anyTeamLogoUrl('mlb', 'NYG')).toBeNull()
    expect(anyTeamLogoUrl('nfl', 'NYY')).toBeNull()
  })
})

describe('known slug divergences are handled inside the mapping', () => {
  it('WAS / WSH converge per league', () => {
    expect(anyTeamLogoUrl('mlb', 'WAS')).toBe('/logos/wsh.png')
    expect(anyTeamLogoUrl('mlb', 'WSH')).toBe('/logos/wsh.png')
    expect(anyTeamLogoUrl('nfl', 'WAS')).toBe(nfl('wsh'))
    expect(anyTeamLogoUrl('nfl', 'WSH')).toBe(nfl('wsh'))
    expect(anyTeamLogoUrl('nba', 'WAS')).toBe('/logos/nba/was-dark.svg')
    expect(anyTeamLogoUrl('nhl', 'WSH')).toBe('/logos/nhl/wsh-dark.svg')
  })
  it('JAC / JAX are the Jaguars, and nothing in MLB', () => {
    expect(anyTeamLogoUrl('nfl', 'JAC')).toBe(nfl('jax'))
    expect(anyTeamLogoUrl('nfl', 'JAX')).toBe(nfl('jax'))
    expect(anyTeamLogoUrl('mlb', 'JAC')).toBeNull()
  })
  it('MLB feed variants ATH / CHW resolve to the shipped files', () => {
    expect(anyTeamLogoUrl('mlb', 'ATH')).toBe('/logos/oak.png')
    expect(anyTeamLogoUrl('mlb', 'CHW')).toBe('/logos/cws.png')
    expect(anyTeamLogoUrl('mlb', 'OAK')).toBe('/logos/oak.png')
    expect(anyTeamLogoUrl('mlb', 'CWS')).toBe('/logos/cws.png')
  })
  it('MLB full team names normalise (ledger rows carry names as well as codes)', () => {
    expect(anyTeamLogoUrl('mlb', 'New York Yankees')).toBe('/logos/nyy.png')
    expect(anyTeamLogoUrl('mlb', 'Yankees')).toBe('/logos/nyy.png')
    expect(anyTeamLogoUrl('mlb', 'Kansas City Royals')).toBe('/logos/kc.png')
    expect(anyTeamLogoUrl('mlb', 'Los Angeles Dodgers')).toBe('/logos/lad.png')
  })
})

describe('NCAAB: codes from every board vocabulary resolve to that school', () => {
  it('headline schools', () => {
    expect(anyTeamLogoUrl('ncaab', 'DUKE')).toBe(ncaa(150))
    expect(anyTeamLogoUrl('ncaab', 'UNC')).toBe(ncaa(153))
    expect(anyTeamLogoUrl('ncaab', 'KU')).toBe(ncaa(2305))
    expect(anyTeamLogoUrl('ncaab', 'UK')).toBe(ncaa(96))
    expect(anyTeamLogoUrl('ncaab', 'UCLA')).toBe(ncaa(26))
    expect(anyTeamLogoUrl('ncaab', 'CONN')).toBe(ncaa(41))
    expect(anyTeamLogoUrl('ncaab', 'ARIZ')).toBe(ncaa(12))
  })
  it('BettingPros, Kalshi and BestBook spellings of one school agree', () => {
    expect(anyTeamLogoUrl('ncaab', 'SCAR')).toBe(ncaa(2579)) // the board's anchor vocabulary
    expect(anyTeamLogoUrl('ncaab', 'BUT')).toBe(ncaa(2086))
    expect(anyTeamLogoUrl('ncaab', 'BTLR')).toBe(ncaa(2086)) // BestBook spelling, via the board's own alias table
    expect(anyTeamLogoUrl('ncaab', 'HP')).toBe(ncaa(2272))
    expect(anyTeamLogoUrl('ncaab', 'HPU')).toBe(ncaa(2272))
    expect(anyTeamLogoUrl('ncaab', 'CSF')).toBe(ncaa(2239))
    expect(anyTeamLogoUrl('ncaab', 'CSUF')).toBe(ncaa(2239))
  })
  it('the generated map covers the whole Kalshi census and crosswalk', () => {
    const map = ncaabLogos as Record<string, number>
    expect(Object.keys(map).length).toBeGreaterThanOrEqual(300)
    for (const [code, id] of Object.entries(map)) {
      if (!(Number.isInteger(id) && id > 0)) throw new Error(`bad id for ${code}: ${id}`)
      expect(anyTeamLogoUrl('ncaab', code)).toBe(ncaa(id))
    }
  })
  it('codes outside the vocabulary fail closed instead of guessing a school', () => {
    expect(anyTeamLogoUrl('ncaab', 'UCONN')).toBeNull() // the vocabulary's code is CONN
    expect(anyTeamLogoUrl('ncaab', 'ZZZ')).toBeNull()
    expect(anyTeamLogoUrl('ncaab', 'BUF')).toBeNull() // Buffalo's code in this vocabulary is not BUF
  })
})

describe('unknown (league, abbrev) pairs fail closed', () => {
  it('cross-league codes get no logo', () => {
    expect(anyTeamLogoUrl('nfl', 'DUKE')).toBeNull()
    expect(anyTeamLogoUrl('nfl', 'NYY')).toBeNull()
    expect(anyTeamLogoUrl('mlb', 'BUF')).toBeNull()
    expect(anyTeamLogoUrl('mlb', 'DET')).toBe('/logos/det.png') // same code, right league: the Tigers
    expect(anyTeamLogoUrl('nhl', 'MIA')).toBeNull()
    expect(anyTeamLogoUrl('nba', 'GB')).toBeNull()
  })
  it('an unrecognised league never falls through to another league\'s table', () => {
    expect(anyTeamLogoUrl('curling' as never, 'KC')).toBeNull()
    expect(anyTeamLogoUrl('ncaaf' as never, 'MIA')).toBeNull()
    expect(anyTeamLogoUrl('' as never, 'MIA')).toBeNull()
  })
  it('soccer has no crest source yet and stays logo-less by design', () => {
    expect(anyTeamLogoUrl('soccer', 'ARS')).toBeNull()
    expect(anyTeamLogoUrl('soccer', 'Arsenal')).toBeNull()
  })
  it('empty inputs', () => {
    expect(anyTeamLogoUrl('mlb', null)).toBeNull()
    expect(anyTeamLogoUrl('nfl', undefined)).toBeNull()
    expect(anyTeamLogoUrl('ncaab', '')).toBeNull()
    expect(anyTeamLogoUrl('nba', '  ')).toBeNull()
  })
})

describe('every local logo the mapping can return actually ships', () => {
  it('no 404s across the pro vocabularies', () => {
    const pub = path.join(__dirname, '..', 'public')
    const inputs: [Parameters<typeof anyTeamLogoUrl>[0], string][] = []
    for (const c of 'ARI ATL BAL BOS CHC CIN CLE COL CWS DET HOU KC LAA LAD MIA MIL MIN NYM NYY OAK PHI PIT SD SEA SF STL TB TEX TOR WSH ATH CHW WAS'.split(' '))
      inputs.push(['mlb', c])
    for (const c of 'ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split(' '))
      inputs.push(['nba', c])
    for (const c of 'ANA BOS BUF CGY CAR CHI COL CBJ DAL DET EDM FLA LA MIN MTL NJ NSH NYI NYR OTT PHI PIT SEA SJ STL TB TOR UTA VAN VGK WPG WSH'.split(' '))
      inputs.push(['nhl', c])
    for (const [sport, code] of inputs) {
      const url = anyTeamLogoUrl(sport, code)
      if (!url) throw new Error(`${sport}/${code} resolved to no logo`)
      if (url.startsWith('/') && !fs.existsSync(path.join(pub, url))) throw new Error(`${sport}/${code} -> ${url} is not shipped`)
    }
  })
})

describe('OG card helper', () => {
  it('absolutises local logos and passes CDN logos through', () => {
    expect(ogTeamLogoUrl('mlb', 'NYY')).toBe('https://beezy.fyi/logos/nyy.png')
    expect(ogTeamLogoUrl('mlb', 'ATH')).toBe('https://beezy.fyi/logos/oak.png')
    expect(ogTeamLogoUrl('nfl', 'KC')).toBe(nfl('kc'))
    expect(ogTeamLogoUrl('ncaab', 'DUKE')).toBe(ncaa(150))
  })
  it('unknown pairs get NO logo -- the old Yankees fallback is gone', () => {
    expect(ogTeamLogoUrl('mlb', 'BUF')).toBeNull()
    expect(ogTeamLogoUrl('mlb', 'DUKE')).toBeNull()
    expect(ogTeamLogoUrl('mlb', null)).toBeNull()
    expect(ogTeamLogoUrl('mlb', 'XXX')).toBeNull()
  })
})
