/**
 * The contract for adding a league (docs/ADDING_A_NEW_SPORT.md). It loops over every slug in SPORT_SLUGS, so a sport added
 * there fails CI until its pillar, systems, badge tokens and links are all wired -- no page needs to change.
 */
import fs from 'fs'
import path from 'path'
import { sportHref } from '../lib/ia'
import { getSportSystemBySlug, getSystemByStat, getSystemsBySport, PICK_SYSTEMS } from '../lib/pick-systems'
import { LIVE_SPORTS, SPORT_PILLARS, SPORT_SLUGS } from '../lib/sports'
import { LEAGUES } from '../lib/tiers'
import { SYSTEM_COLOR, SYSTEM_LABEL, SYSTEM_LABEL_SHORT } from '../lib/tokens'

describe.each([...SPORT_SLUGS])('sport onboarding contract: %s', sport => {
  it('has a live pillar with an accent and a board source', () => {
    const p = SPORT_PILLARS[sport]
    expect(p).toBeDefined()
    expect(p.slug).toBe(sport)
    expect(p.status).toBe('live')
    expect(['tracked', 'projection']).toContain(p.board)
    expect(p.accent).toMatch(/^#[0-9a-f]{6}$/i)
    expect(p.shortName).toBeTruthy()
  })

  it('lists at least five systems', () => {
    expect(getSystemsBySport(sport).length).toBeGreaterThanOrEqual(5)
  })

  it('every system has a badge color, a label, a description, and resolves by its slug', () => {
    for (const sys of getSystemsBySport(sport)) {
      expect(SYSTEM_COLOR[sys.key]).toMatch(/^#[0-9a-f]{6}$/i)
      expect(SYSTEM_LABEL[sys.key]).toBeTruthy()
      expect(SYSTEM_LABEL_SHORT[sys.key]).toBeTruthy()
      expect(sys.description.trim().length).toBeGreaterThan(0)
      expect(sys.family.trim().length).toBeGreaterThan(0)
      expect(sys.features.length).toBeGreaterThan(0)
      expect(sys.sport).toBe(sport)
      expect(getSportSystemBySlug(sport, sys.slug)).toBe(sys)
    }
  })

  it('sportHref is /picks/<sport> and the single-sport pass can be bought for it', () => {
    expect(sportHref(sport)).toBe(`/picks/${sport}`)
    expect(LEAGUES).toContain(sport)
    expect(LIVE_SPORTS.map(p => p.slug)).toContain(sport)
  })

  it('every pillar market points at a registered system of this sport, and no listed system is missing from the pillar', () => {
    const p = SPORT_PILLARS[sport]
    for (const m of p.markets) {
      expect(m.system).toBeTruthy()
      expect(getSystemsBySport(sport).map(s => s.key)).toContain(m.system)
    }
    expect(p.markets.map(m => m.system).sort()).toEqual(getSystemsBySport(sport).map(s => s.key).sort())
  })
})

describe('registry integrity', () => {
  it('keys are unique, slugs are unique within a sport, and the specified counts hold', () => {
    expect(new Set(PICK_SYSTEMS.map(s => s.key)).size).toBe(PICK_SYSTEMS.length)
    for (const sport of SPORT_SLUGS) {
      const slugs = PICK_SYSTEMS.filter(s => s.sport === sport).map(s => s.slug)
      expect(new Set(slugs).size).toBe(slugs.length)
    }
    expect(Object.fromEntries(SPORT_SLUGS.map(s => [s, getSystemsBySport(s).length]))).toEqual({ mlb: 10, nfl: 10, nhl: 7, nba: 10 })
  })

  it('slugs that repeat across sports resolve to the right sport', () => {
    expect(getSportSystemBySlug('nhl', 'blocks')!.key).toBe('NHL_BLOCKS')
    expect(getSportSystemBySlug('nba', 'blocks')!.key).toBe('NBA_BLK')
    expect(getSportSystemBySlug('nfl', 'blocks')).toBeUndefined()
  })

  it('shelved MLB systems keep their URL but are not listed', () => {
    for (const slug of ['f3', 'f7', '1i', 'f1h', 'batter-k']) {
      expect(getSportSystemBySlug('mlb', slug)).toBeDefined()
      expect(getSystemsBySport('mlb').map(s => s.slug)).not.toContain(slug)
    }
  })

  it('the specified MLB / NFL / NHL / NBA slugs are all registered', () => {
    const slugs = (s: (typeof SPORT_SLUGS)[number]) => getSystemsBySport(s).map(x => x.slug)
    expect(slugs('mlb').sort()).toEqual(['batter-hits', 'batter-tb', 'f5', 'game', 'hr', 'k', 'nrfi', 'outs', 'pitcher-er', 'sb'].sort())
    expect(slugs('nfl').sort()).toEqual(['anytime-td', 'interceptions', 'pass-att', 'pass-comp', 'pass-tds', 'pass-yds', 'receptions', 'rec-yds', 'rush-att', 'rush-yds'].sort())
    expect(slugs('nhl').sort()).toEqual(['assists', 'blocks', 'goals', 'pp-points', 'points', 'saves', 'sog'].sort())
    expect(slugs('nba').sort()).toEqual(['ast', 'blocks', 'pa', 'pr', 'pra', 'ra', 'reb', 'steals', 'threes', 'pts'].sort())
  })
})

/**
 * The site's projection systems and the Python engine's PROJECT_STATS (ev/daily_projections.py) are two lists of the same
 * thing. This reads the Python source so they cannot drift: a stat added on one side only fails here.
 */
describe('registry matches the projection engine', () => {
  const src = fs.readFileSync(path.resolve(__dirname, '../../ev/daily_projections.py'), 'utf8')
  const block = src.slice(src.indexOf('PROJECT_STATS = {'), src.indexOf('STAT_LABEL = {'))
  const engine = (sport: string) => {
    const start = block.indexOf(`"${sport}": {`)
    const end = block.indexOf('}', start)
    return [...block.slice(start, end).matchAll(/"([a-z_0-9]+)":\s*"(?:normal|negbin|anytime)"/g)].map(m => m[1]).sort()
  }

  it.each(['nfl', 'nhl', 'nba'] as const)('%s: every projectable registered system has an engine stat, and every engine stat a system', sport => {
    const registered = getSystemsBySport(sport).filter(s => !s.gap).map(s => s.statKey!).sort()
    expect(engine(sport)).toEqual(registered)
    for (const stat of engine(sport)) expect(getSystemByStat(sport, stat)).toBeDefined()
  })

  it('the only registered system without an engine stat is the documented NHL power-play-points gap', () => {
    const gaps = PICK_SYSTEMS.filter(s => s.gap).map(s => s.key)
    expect(gaps).toEqual(['NHL_PP_POINTS'])
  })
})
