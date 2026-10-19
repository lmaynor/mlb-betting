import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current, 'aria-label': label }: { children: React.ReactNode; href: string; 'aria-current'?: 'date' | 'true'; 'aria-label'?: string }) => <a href={href} aria-current={current} aria-label={label}>{children}</a> }))
jest.mock('next/navigation', () => ({
  usePathname: () => '/picks/nfl',
  useSearchParams: () => new URLSearchParams('market=NFL_REC_YDS&game=g2&day=yesterday'),
}))

import { DayToggle } from '../components/picks/day-toggle'
import { GamesStrip } from '../components/picks/games-strip'
import { SystemStrip } from '../components/picks/system-strip'
import { SystemsTable } from '../components/picks/systems-table'
import { systemTodayCounts, type BoardRow } from '../lib/board-rows'
import { gamesFromRows, gamesFromSlate, gameTimeLabel, localTimeLabel, mergeGames, startInstant, withCounts } from '../lib/board-games'
import { anyTeamLogoUrl } from '../lib/sport-assets'
import { liveAccuracy } from '../lib/prop-model-specs'
import { headshotUrlById } from '../lib/sport-assets'
import { playerIdByName } from '../lib/sport-assets.server'
import { codesInMatchup, matchesSearch, searchHaystack, teamSearchTerms } from '../lib/team-directory'
import type { SportProjectionRow } from '../lib/types'

const row = (o: Partial<BoardRow>): BoardRow => ({
  key: 'k', sport: 'nfl', system: 'NFL_REC_YDS', marketLabel: 'Rec Yds', player: 'Pat Receiver', headshotId: null, team: 'HOU', matchup: 'HOU @ JAC', gameKey: 'nfl:2026-10-18:HOU@JAC', away: 'HOU', home: 'JAC', avg10: null, avgSzn: null, result: null, start: '2026-10-18T17:00:00Z',
  side: 'over', line: 64.5, hasLine: true, proj: 70, pModel: 0.6, pFair: 0.5, nBooks: 6, l5: 0.6, l10: 0.6, odds: 110, book: 'draftkings',
  modelEdge: 0.05, marketEv: 0.04, confluence: true, score: 80, isPick: false, ...o,
})

describe('team-aware search', () => {
  it('finds a game by city, nickname, abbreviation or colloquial name, in the right sport', () => {
    const nfl = searchHaystack('nfl', 'C.J. Stroud', 'HOU v JAC')
    for (const q of ['houston', 'texans', 'HOU', 'jacksonville', 'jags', 'jax', 'cj stroud', 'houston texans']) expect(matchesSearch(nfl, q)).toBe(true)
    expect(matchesSearch(nfl, 'chiefs')).toBe(false)
    expect(matchesSearch(nfl, 'houston zzz')).toBe(false)
  })

  it('HOU is the Texans in the NFL and the Astros in MLB, never both', () => {
    expect(teamSearchTerms('nfl', 'HOU')).toContain('texans')
    expect(teamSearchTerms('mlb', 'HOU')).toContain('astros')
    expect(matchesSearch(searchHaystack('mlb', 'Yordan Alvarez', 'HOU @ SEA'), 'texans')).toBe(false)
    expect(matchesSearch(searchHaystack('mlb', 'Yordan Alvarez', 'HOU @ SEA'), 'seattle')).toBe(true)
  })

  it('NHL and NBA come from the generated team table, plus nicknames the table lacks', () => {
    expect(matchesSearch(searchHaystack('nhl', 'Nick Suzuki', 'MTL @ BOS'), 'canadiens')).toBe(true)
    expect(matchesSearch(searchHaystack('nhl', 'Nick Suzuki', 'MTL @ BOS'), 'habs')).toBe(true)
    expect(matchesSearch(searchHaystack('nhl', 'Nick Suzuki', 'MTL @ BOS'), 'boston')).toBe(true)
    expect(matchesSearch(searchHaystack('nba', 'Joel Embiid', 'PHI @ LAL'), 'sixers')).toBe(true)
    expect(matchesSearch(searchHaystack('nba', 'Joel Embiid', 'PHI @ LAL'), 'lakers')).toBe(true)
  })

  it('is accent, case and period insensitive, and an empty query matches everything', () => {
    expect(matchesSearch(searchHaystack('nhl', 'Jesperi Kotkaniemi', 'MTL @ BOS'), 'MONTREAL')).toBe(true)
    expect(matchesSearch(searchHaystack('mlb', 'Paul Goldschmidt', 'STL @ CHC'), 'st louis')).toBe(true)
    expect(matchesSearch('anything', '  ')).toBe(true)
    expect(codesInMatchup('KC@LV and HOU v JAC')).toEqual(['KC', 'LV', 'HOU', 'JAC'])
  })

})

describe('games strip and day toggle', () => {
  const g = (o: Partial<BoardRow>) => row({ sport: 'nhl', ...o })
  const rows = [
    g({ key: '1', gameKey: 'nhl:2026-10-17:BOS@LAK', away: 'BOS', home: 'LAK', start: '2026-10-18T01:00:00Z' }),
    g({ key: '2', gameKey: 'nhl:2026-10-17:BOS@LAK', away: 'BOS', home: 'LAK', start: '2026-10-18T01:00:00Z' }),
    g({ key: '3', gameKey: 'nhl:2026-10-17:MTL@SEA', away: 'MTL', home: 'SEA', start: '2026-10-18T02:00:00Z' }),
    g({ key: '4', gameKey: '', away: null, home: null }),
  ]

  it('builds the games from the rows, soonest first, with counts; a row with no game belongs to none', () => {
    expect(gamesFromRows(rows).map(x => [x.key, x.count])).toEqual([['nhl:2026-10-17:BOS@LAK', 2], ['nhl:2026-10-17:MTL@SEA', 1]])
  })

  it('merges the feed game list: a scheduled game with no rows stays listed with a count of 0', () => {
    const feed = [
      { game_key: 'nhl:2026-10-17:BOS@LAK', away: 'BOS', home: 'LAK', start: '2026-10-18T01:00:00Z' },
      { game_key: 'nhl:2026-10-18:NYR@DAL', away: 'NYR', home: 'DAL', start: '2026-10-19T00:00:00Z' },
    ]
    const merged = mergeGames('nhl', feed, rows)
    expect(merged.map(x => [x.key, x.count])).toEqual([['nhl:2026-10-17:BOS@LAK', 2], ['nhl:2026-10-17:MTL@SEA', 1], ['nhl:2026-10-18:NYR@DAL', 0]])
    expect(mergeGames('nhl', undefined, rows)).toHaveLength(2)         // an older payload: derive from the rows
    expect(withCounts(merged, rows.slice(0, 1)).map(x => x.count)).toEqual([1, 0, 0])
  })

  it('MLB games come from the slate, keyed like the MLB rows', () => {
    const slate = { games: [{ game_pk: 849816, away_team: 'TB', home_team: 'CLE', start_time: '8:00 PM ET', away_pitcher: null, home_pitcher: null, picks: [] }], run_date: '2026-10-17', as_of: '', total_picks: 0, total_games: 1 }
    const mlb = [row({ sport: 'mlb', key: 'm', gameKey: 'mlb:849816', away: 'TB', home: 'CLE' })]
    expect(gamesFromSlate(slate, mlb)).toEqual([{ key: 'mlb:849816', sport: 'mlb', away: 'TB', home: 'CLE', start: null, startText: '8:00 PM ET', count: 1 }])
  })

  it('labels a game with its time today and the weekday for a later day', () => {
    expect(gameTimeLabel({ start: '2026-10-18T01:00:00Z', startText: null }, '2026-10-17')).toBe('8:00 PM')
    expect(gameTimeLabel({ start: '2026-10-18T17:00:00Z', startText: null }, '2026-10-17')).toBe('SUN 12:00 PM')
    expect(gameTimeLabel({ start: null, startText: '8:00 PM ET' }, '2026-10-17')).toBe('8:00 PM ET')
  })

  it('GamesStrip lists ALL GAMES and a card per game, toggles ?game= and keeps the other params', () => {
    render(<GamesStrip games={gamesFromRows(rows)} selected="g2" today="2026-10-17" total={3} />)
    const nav = screen.getByRole('navigation', { name: 'Games' })
    const hrefs = within(nav).getAllByRole('link').map(l => l.getAttribute('href'))
    expect(hrefs[0]).toBe('/picks/nfl?market=NFL_REC_YDS&day=yesterday')                      // ALL GAMES drops game
    expect(hrefs).toContain('/picks/nfl?market=NFL_REC_YDS&game=nhl%3A2026-10-17%3ABOS%40LAK&day=yesterday')
    // compact cards: both logos and a time; the team codes and the count live in the accessible name, not as text
    expect(within(nav).getByRole('link', { name: /^All games: 2 games, 3 rows/ })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: /^BOS at LAK, .*2 rows$/ })).toBeInTheDocument()
    expect(within(nav).queryByText('BOS')).toBeNull()
    expect(within(nav).queryByText(/rows/)).toBeNull()
    expect(within(nav).getAllByText(/\d:\d\d (AM|PM)/)).toHaveLength(2)
  })

  it('shows start times on the viewer\'s own clock, with the weekday only when it is another local day', () => {
    const nhl = { start: '2026-10-18T01:00:00Z', startText: null }
    const now = new Date('2026-10-17T20:00:00Z')
    expect(localTimeLabel(nhl, '2026-10-17', { now, timeZone: 'America/Chicago' })).toBe('8:00 PM')
    expect(localTimeLabel(nhl, '2026-10-17', { now, timeZone: 'America/Los_Angeles' })).toBe('6:00 PM')
    expect(localTimeLabel(nhl, '2026-10-17', { now: new Date('2026-10-17T12:00:00Z'), timeZone: 'Asia/Tokyo' })).toBe('SUN 10:00 AM')
    // MLB's slate has only '8:00 PM ET' (no date): it is resolved on the site date, then shown in the viewer's zone
    const mlb = { start: null, startText: '8:00 PM ET' }
    expect(localTimeLabel(mlb, '2026-10-17', { now, timeZone: 'America/New_York' })).toBe('8:00 PM')
    expect(localTimeLabel(mlb, '2026-10-17', { now, timeZone: 'America/Los_Angeles' })).toBe('5:00 PM')
    expect(startInstant(mlb, '2026-12-05')?.toISOString()).toBe('2026-12-06T01:00:00.000Z')   // EST in winter
    expect(localTimeLabel({ start: null, startText: 'TBD' }, '2026-10-17')).toBe('TBD')
  })

  it('GamesStrip renders nothing for a board with no games', () => {
    const { container } = render(<GamesStrip games={[]} today="2026-10-17" total={0} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('DayToggle is YESTERDAY | TODAY, marks the shown day, clears the game and keeps a clean URL when dates are not pinned', () => {
    render(<DayToggle today="2026-10-17" yesterday={{ date: '2026-10-16', label: 'FRI OCT 16' }} days={[{ date: '2026-10-17', label: 'SAT OCT 17' }]} selected="2026-10-16" pinDates={false} />)
    const nav = screen.getByRole('navigation', { name: 'Day' })
    const today = within(nav).getByText('TODAY', { exact: false }).closest('a')!
    const yest = within(nav).getByText('YESTERDAY', { exact: false }).closest('a')!
    expect(today.getAttribute('href')).toBe('/picks/nfl?market=NFL_REC_YDS')
    expect(yest.getAttribute('href')).toBe('/picks/nfl?market=NFL_REC_YDS&day=yesterday')
    expect(yest).toHaveAttribute('aria-current', 'date')
    expect(today).not.toHaveAttribute('aria-current')
    expect(within(nav).getByText('SAT OCT 17')).toBeInTheDocument()
  })

  it('every sport has a team logo path: local files for MLB / NHL / NBA, ESPN for the NFL, nothing invented for an unknown team', () => {
    expect(anyTeamLogoUrl('mlb', 'NYY')).toBe('/logos/nyy.png')
    expect(anyTeamLogoUrl('mlb', 'WSH')).toBe('/logos/wsh.png')
    expect(anyTeamLogoUrl('nhl', 'BOS')).toBe('/logos/nhl/bos-dark.svg')
    expect(anyTeamLogoUrl('nba', 'LAL')).toBe('/logos/nba/lal-dark.svg')
    expect(anyTeamLogoUrl('nfl', 'JAC')).toContain('/teamlogos/nfl/500/jax.png')
    expect(anyTeamLogoUrl('nfl', 'WAS')).toContain('/wsh.png')
    expect(anyTeamLogoUrl('nfl', 'LAR')).toContain('/lar.png')
    expect(anyTeamLogoUrl('nfl', 'XXX')).toBeNull()
    expect(anyTeamLogoUrl('nhl', '')).toBeNull()
  })

  it('SystemStrip is ALL SYSTEMS plus one tab per system, linking to each system page', () => {
    render(<SystemStrip sport="nfl" active="NFL_REC_YDS" />)
    const nav = screen.getByRole('navigation', { name: 'NFL systems' })
    const links = within(nav).getAllByRole('link')
    expect(links[0].getAttribute('href')).toBe('/picks/nfl/systems')
    expect(links[0]).not.toHaveAttribute('aria-current')
    expect(links).toHaveLength(11)
    const rec = within(nav).getByText('Rec Yds').closest('a')!
    expect(rec).toHaveAttribute('aria-current', 'page')
    expect(rec.getAttribute('href')).toBe('/picks/nfl/rec-yds')
  })
})

describe('NFL headshots', () => {
  it('builds the nfl.com URL from a token and rejects anything that is not a token', () => {
    expect(headshotUrlById('nfl', 'tt4zrtxlhifaljhj0rn7')).toBe('https://static.www.nfl.com/image/upload/f_png,q_auto,w_200/league/tt4zrtxlhifaljhj0rn7')
    expect(headshotUrlById('nfl', 12345)).toBeNull()
    expect(headshotUrlById('nfl', '../../etc/passwd')).toBeNull()
    expect(headshotUrlById('nfl', 'https://evil.example/x.png')).toBeNull()
    expect(headshotUrlById('nfl', null)).toBeNull()
  })

  it('resolves a player by name and team, and by name alone only when the name is unique', () => {
    expect(playerIdByName('nfl', 'C.J. Stroud', 'HOU')).toBe('tt4zrtxlhifaljhj0rn7')
    expect(playerIdByName('nfl', 'C.J. Stroud')).toBe('tt4zrtxlhifaljhj0rn7')
    expect(playerIdByName('nfl', 'Amon-Ra St. Brown', 'DET')).toEqual(expect.any(String))
    expect(playerIdByName('nfl', 'Nobody Atall', 'HOU')).toBeNull()
  })

  it('the other sports still resolve ids as before', () => {
    expect(headshotUrlById('mlb', 592450)).toContain('/people/592450/')
    expect(headshotUrlById('nhl', 8478402)).toBe('https://assets.nhle.com/mugs/nhl/latest/8478402.png')
  })
})

describe('systems statistics', () => {
  it('live accuracy exists for the projected stats of each projection sport, with the engine beating the L5 baseline', () => {
    for (const [sport, stat] of [['nfl', 'rec_yds'], ['nfl', 'anytime_td'], ['nhl', 'sog'], ['nhl', 'saves'], ['nba', 'points'], ['nba', 'pr']] as const) {
      const a = liveAccuracy(sport, stat)
      expect(a).not.toBeNull()
      expect(a!.n).toBeGreaterThan(1000)
      expect(a!.mae).toBeLessThan(a!.baselineMae)
      expect(a!.dMae).toBeLessThan(0)
    }
    expect(liveAccuracy('nhl', 'pp_points')).toBeNull()                 // a data gap: no number is invented
    expect(liveAccuracy('mlb', 'K')).toBeNull()
    expect(liveAccuracy('nfl', undefined)).toBeNull()
  })

  it('counts rows, priced rows and model + market rows per system from the uncut feed', () => {
    const p = (o: Partial<SportProjectionRow>) => ({ sport: 'nfl', stat: 'rec_yds', has_line: true, confluence: false, ...o }) as SportProjectionRow
    const counts = systemTodayCounts([p({}), p({ has_line: false }), p({ confluence: true }), p({ stat: 'receptions' })])
    expect(counts.NFL_REC_YDS).toEqual({ rows: 3, priced: 2, confluence: 1 })
    expect(counts.NFL_RECEPTIONS).toEqual({ rows: 1, priced: 1, confluence: 0 })
  })

  it('the projection footer shows today counts and the live MAE (engine / L5) instead of dashes', () => {
    render(<SystemsTable sport="nfl" today={{ NFL_REC_YDS: { rows: 12, priced: 5, confluence: 1 } }} />)
    const rec = within(screen.getByText('Receiving Yards').closest('tr')!)
    expect(rec.getByText('12')).toBeInTheDocument()
    expect(rec.getByText('25.9 / 27.1')).toBeInTheDocument()
    expect(screen.getByText('LIVE MAE (ENGINE / L5)')).toBeInTheDocument()
    // a system with no rows today and no replay still reads as a dash, not a made-up number
    const none = within(screen.getByText('Interceptions Thrown').closest('tr')!)
    expect(none.getAllByText('--').length).toBeGreaterThan(0)
  })
})
