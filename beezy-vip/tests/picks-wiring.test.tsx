import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current, 'aria-label': label }: { children: React.ReactNode; href: string; 'aria-current'?: 'date' | 'true'; 'aria-label'?: string }) => <a href={href} aria-current={current} aria-label={label}>{children}</a> }))
jest.mock('next/navigation', () => ({
  usePathname: () => '/picks/nfl',
  useSearchParams: () => new URLSearchParams('market=NFL_REC_YDS'),
}))

import { DayScroller } from '../components/picks/day-scroller'
import { SystemsFooter } from '../components/picks/systems-footer'
import { applyPickFilters, filterBoard, systemTodayCounts, type BoardRow } from '../lib/board-rows'
import { boardDays, chooseDay, inDay, sliceByDay } from '../lib/board-days'
import { parsePickFilters } from '../lib/picks-filters'
import { liveAccuracy } from '../lib/prop-model-specs'
import { headshotUrlById } from '../lib/sport-assets'
import { playerIdByName } from '../lib/sport-assets.server'
import { codesInMatchup, matchesSearch, searchHaystack, teamSearchTerms } from '../lib/team-directory'
import type { SportProjectionRow } from '../lib/types'

const row = (o: Partial<BoardRow>): BoardRow => ({
  key: 'k', sport: 'nfl', system: 'NFL_REC_YDS', marketLabel: 'Rec Yds', player: 'Pat Receiver', headshotId: null, matchup: 'HOU v JAC', start: '2026-10-18T17:00:00Z',
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

  it('reaches the picks filters: a city name now cuts the board, the abbreviation still does', () => {
    const rows = [row({ key: 'a', player: 'A One', matchup: 'HOU v JAC' }), row({ key: 'b', player: 'B Two', matchup: 'KC v LV' })]
    const f = (q: string) => applyPickFilters(rows, parsePickFilters({ q })).map(r => r.key)
    expect(f('kansas city')).toEqual(['b'])
    expect(f('jaguars')).toEqual(['a'])
    expect(f('LV')).toEqual(['b'])
    const base = { market: 'ALL', side: 'ALL', signal: 'ALL', sort: 'score' } as const
    expect(filterBoard(rows, { ...base, q: 'vegas' }).map(r => r.key)).toEqual(['b'])
  })
})

describe('game days', () => {
  // 17:00Z is noon Central, the same calendar day; 03:00Z is the previous evening in Central
  const starts = ['2026-10-18T17:00:00Z', '2026-10-18T18:30:00Z', '2026-10-19T03:00:00Z', '2026-10-24T17:00:00Z', null]

  it('lists the days that have games, soonest first, on the site clock, with counts; a missing start is no day', () => {
    expect(boardDays(starts)).toEqual([{ key: '2026-10-18', count: 3 }, { key: '2026-10-24', count: 1 }])
  })

  it('chooses all, a day that has rows, or the first day (also for a stale or malformed date)', () => {
    const days = boardDays(starts)
    expect(chooseDay(undefined, days)).toBe('2026-10-18')
    expect(chooseDay('2026-10-24', days)).toBe('2026-10-24')
    expect(chooseDay('2026-10-20', days)).toBe('2026-10-18')
    expect(chooseDay('yesterday', days)).toBe('2026-10-18')
    expect(chooseDay('all', days)).toBe('all')
    expect(chooseDay('2026-10-18', [])).toBe('all')
  })

  it('cuts rows to the selected day; all keeps rows with no start', () => {
    expect(inDay('2026-10-24T17:00:00Z', '2026-10-24')).toBe(true)
    expect(inDay('2026-10-24T17:00:00Z', '2026-10-18')).toBe(false)
    expect(inDay(null, 'all')).toBe(true)
    expect(inDay(null, '2026-10-18')).toBe(false)
    const rows = starts.map((s, i) => ({ id: i, s }))
    const day = sliceByDay(rows, r => r.s, '2026-10-24')
    expect(day.rows.map(r => r.id)).toEqual([3])
    expect(day.days).toHaveLength(2)                                    // the strip keeps every day whatever is selected
    expect(sliceByDay(rows, r => r.s, 'all').rows).toHaveLength(5)
  })

  it('DayScroller renders ALL DAYS plus a chip per day, marks the selected one, keeps other params and drops date for the first day', () => {
    const days = boardDays(starts)
    render(<DayScroller days={days} selected="2026-10-24" today="2026-10-17" />)
    const nav = screen.getByRole('navigation', { name: 'Game day' })
    const links = within(nav).getAllByRole('link')
    const hrefs = links.map(l => l.getAttribute('href'))
    expect(hrefs).toContain('/picks/nfl?market=NFL_REC_YDS&date=all')
    expect(hrefs).toContain('/picks/nfl?market=NFL_REC_YDS')             // the first day is the default: no date param
    expect(hrefs).toContain('/picks/nfl?market=NFL_REC_YDS&date=2026-10-24')
    expect(within(nav).getByText('ALL DAYS').closest('a')).not.toHaveAttribute('aria-current', 'date')
    expect(within(nav).getByText('SAT, OCT 24').closest('a')).toHaveAttribute('aria-current', 'date')
    expect(within(nav).getByText(/TOMORROW/)).toBeInTheDocument()
    // from the last day there is no next day, so Next is not a link
    expect(within(nav).queryByRole('link', { name: 'Next day' })).toBeNull()
    expect(within(nav).getByRole('link', { name: 'Previous day' })).toBeInTheDocument()
  })

  it('DayScroller renders nothing when the board has no games', () => {
    const { container } = render(<DayScroller days={[]} selected="all" today="2026-10-17" />)
    expect(container).toBeEmptyDOMElement()
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
    render(<SystemsFooter sport="nfl" today={{ NFL_REC_YDS: { rows: 12, priced: 5, confluence: 1 } }} />)
    const rec = within(screen.getByText('Receiving Yards').closest('tr')!)
    expect(rec.getByText('12')).toBeInTheDocument()
    expect(rec.getByText('25.9 / 27.1')).toBeInTheDocument()
    expect(screen.getByText('LIVE MAE (ENGINE / L5)')).toBeInTheDocument()
    // a system with no rows today and no replay still reads as a dash, not a made-up number
    const none = within(screen.getByText('Interceptions Thrown').closest('tr')!)
    expect(none.getAllByText('--').length).toBeGreaterThan(0)
  })
})
