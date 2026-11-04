import { readFileSync } from 'fs'
import { join } from 'path'
import { LIVE_SPORTS, sportsInSeason, sportsWithRows } from '../lib/sports'

describe('sports offered right now', () => {
  it('MLB drops out for the winter and is back from opening day; the other live sports never do', () => {
    const slugs = (iso: string) => sportsInSeason(new Date(iso)).map(p => p.slug)
    expect(slugs('2026-12-15T18:00:00Z')).not.toContain('mlb')
    expect(slugs('2026-12-15T18:00:00Z')).toEqual(expect.arrayContaining(['nfl', 'nhl', 'nba']))
    expect(slugs('2027-07-01T18:00:00Z')).toContain('mlb')
    expect(slugs('2027-03-19T18:00:00Z')).not.toContain('mlb')
    expect(slugs('2027-03-20T18:00:00Z')).toContain('mlb')
  })
  it('a sport with no rows is not offered, whatever the calendar says', () => {
    expect(sportsWithRows({ nfl: { rows: [1] }, nhl: { rows: [1, 2] }, nba: { rows: [1] }, mlb: { rows: [] } })).toEqual(['nfl', 'nhl', 'nba'])
  })
  it('a sport whose board did not load is not offered, but a total outage does not hide every pill', () => {
    expect(sportsWithRows({ nfl: { rows: [1] }, mlb: null })).toEqual(['nfl'])
    expect(sportsWithRows({})).toEqual(LIVE_SPORTS.map(p => p.slug))
  })
})

describe('the global sport ribbon is gone', () => {
  it('the nav renders no sport ribbon; sports are chosen inside the page that needs them', () => {
    const nav = readFileSync(join(__dirname, '../components/layout/nav.tsx'), 'utf8')
    expect(nav).not.toMatch(/sport-ribbon|SportLink|ALL SPORTS/)
  })
})
