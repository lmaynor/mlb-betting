/** A week of NBA games must not be poured onto TODAY: each row belongs to the day its game is played. */
import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current, title }: { children: React.ReactNode; href: string; 'aria-current'?: 'date'; title?: string }) => <a href={href} aria-current={current} title={title}>{children}</a> }))
jest.mock('next/navigation', () => ({ usePathname: () => '/picks/nba', useSearchParams: () => new URLSearchParams('market=NBA_PTS&game=g1&day=yesterday') }))

const mockProjections = jest.fn()
jest.mock('@/lib/betting-api', () => ({
  apiGetEvFeed: jest.fn(() => Promise.resolve(null)),
  apiGetSportProjections: (...a: unknown[]) => mockProjections(...a),
  apiGetPicks: jest.fn(() => Promise.resolve([])),
  apiGetTodayAllPredictions: jest.fn(() => Promise.resolve([])),
  apiGetTodaySlate: jest.fn(() => Promise.resolve(null)),
}))

import { DayToggle } from '../components/picks/day-toggle'
import { gameDayKey, isDateKey, liveDays, onDay, slateRows, upcomingDays, viewDay } from '../lib/board-days'
import { addDaysToDateKey, siteDateKey } from '../lib/dates'
import { parsePickFilters } from '../lib/picks-filters'
import { loadSportBoard } from '../lib/sport-board-data'
import type { SportProjectionRow } from '../lib/types'

const TODAY = siteDateKey()
const day = (n: number) => dayAfter(n)
function dayAfter(n: number) { return addDaysToDateKey(TODAY, n) }

const prow = (date: string, i: number, extra: Partial<SportProjectionRow> = {}) => ({
  sport: 'nba', stat: 'points', stat_label: 'Points', player: `Player ${date} ${i}`, player_norm: `player ${date} ${i}`, team: 'BOS', opp: 'DET',
  game_key: `nba:${date}:BOS@DET`, commence: `${date}T23:00:00Z`, mins_to_start: 100, has_line: true, line: 20.5, consensus_line: 20.5, side: 'over',
  proj_mean: 22, p_over_model: 0.6, p_model_side: 0.6, p_mkt_side: 0.5, beezy_score: 60, confluence: false, model_edge: 0.05, market_ev: 0.04, n_books: 5,
  l5_hit_rate: 0.6, l10_hit_rate: 0.6, avg_l10: 21, szn_avg: 20, best_over_odds: -110, best_over_book: 'draftkings', best_under_odds: -110, best_under_book: 'fanduel',
  ...extra,
}) as unknown as SportProjectionRow

const week = () => [
  ...[0, 1, 2].map(i => prow(day(0), i)),
  ...[0, 1].map(i => prow(day(1), i)),
  prow(day(3), 0), prow(day(3), 1), prow(day(3), 2), prow(day(3), 3),
]
const feed = (rows: SportProjectionRow[]) => ({
  sport: 'nba', engine: 'x', validated: false, status: 'live', stale: false, as_of: null, age_minutes: 1, lines_age_minutes: 1, lines_stale: false,
  games: [day(0), day(1), day(3)].map(d => ({ game_key: `nba:${d}:BOS@DET`, away: 'BOS', home: 'DET', start: `${d}T23:00:00Z` })),
  gamelog_latest: null, stale_gamelog: null, stats: ['points'], count: rows.length, rows, summary: {},
})
const viewer = { tier: 'pro', sport: null } as never

describe('game days', () => {
  it('a row is on the day in its game key, else the day it starts in the site time zone', () => {
    expect(gameDayKey({ game_key: 'nba:2026-10-17:BOS@LAL', commence: '2026-10-18T03:00:00Z' })).toBe('2026-10-17')
    expect(gameDayKey({ game_key: '', commence: '2026-10-18T03:00:00Z' })).toBe('2026-10-17')     // 10pm Central, still the 17th
    expect(gameDayKey({ game_key: null, commence: null })).toBeNull()
    expect(isDateKey('2026-10-17')).toBe(true)
    expect(isDateKey('17-10-2026')).toBe(false)
  })

  it('splits the week: upcoming days with counts, soonest first, never today', () => {
    expect(upcomingDays(week(), TODAY).map(d => [d.date, d.count])).toEqual([[day(1), 2], [day(3), 4]])
    expect(upcomingDays(week(), TODAY)[0].label).toMatch(/^[A-Z]{3} [A-Z]{3} \d{1,2}$/)
    expect(upcomingDays(week(), TODAY, 1)).toHaveLength(1)
  })

  it('opens on today, an asked-for later day, or the next day with games when today has none', () => {
    expect(viewDay(week(), TODAY)).toBe(TODAY)
    expect(viewDay(week(), TODAY, day(3))).toBe(day(3))
    expect(viewDay(week(), TODAY, day(-2))).toBe(TODAY)                       // a past day is not an upcoming day
    expect(viewDay(week(), TODAY, 'garbage')).toBe(TODAY)
    const noToday = week().filter(r => gameDayKey(r) !== TODAY)
    expect(viewDay(noToday, TODAY)).toBe(day(1))
    expect(viewDay([], TODAY)).toBe(TODAY)
  })

  it('keeps one day of rows; a row with no day at all stays on today', () => {
    expect(onDay(week(), day(3), TODAY)).toHaveLength(4)
    expect(onDay([...week(), { game_key: null, commence: null }], TODAY, TODAY)).toHaveLength(4)
    expect(slateRows(week(), TODAY)).toHaveLength(3)
  })

  it('?date= is a view parameter (valid dates only, and not on the yesterday archive)', () => {
    expect(parsePickFilters({ date: '2026-10-20' }).date).toBe('2026-10-20')
    expect(parsePickFilters({ date: 'x' }).date).toBeUndefined()
    expect(parsePickFilters({ date: '2026-10-20', day: 'yesterday' }).date).toBeUndefined()
  })
})

/** The API: no `date` = the live feed, `date` = that day's archive. */
const serve = (live: SportProjectionRow[], archive: SportProjectionRow[] = []) =>
  mockProjections.mockImplementation((_s: string, o: { date?: string } = {}) => Promise.resolve(o.date ? { ...feed(archive), archive: true } : feed(live)))

describe('the board', () => {
  it('liveDays is the strip: only dates with events, today first when it has any', () => {
    expect(liveDays(week(), TODAY).map(d => [d.date, d.count])).toEqual([[TODAY, 3], [day(1), 2], [day(3), 4]])
    expect(liveDays(week().filter(r => gameDayKey(r) !== TODAY), TODAY).map(d => d.date)).toEqual([day(1), day(3)])
  })

  it('TODAY has only today\'s games and rows; the other game days are chips', async () => {
    serve(week())
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks' })
    expect(b.date).toBe(TODAY)
    expect(b.rows).toHaveLength(3)
    expect(b.games.map(g => g.key)).toEqual([`nba:${TODAY}:BOS@DET`])
    expect(b.days.map(d => d.date)).toEqual([TODAY, day(1), day(3)])
    expect(b.yesterday).toBeNull()                                              // no archive rows, so no chip to nowhere
  })

  it('an asked-for day shows only its own rows and games', async () => {
    serve(week())
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks', date: day(3) })
    expect(b.date).toBe(day(3))
    expect(b.rows).toHaveLength(4)
    expect(b.games.map(g => g.key)).toEqual([`nba:${day(3)}:BOS@DET`])
  })

  it('TODAY asked for by date with no games today is an empty today, not the next game day', async () => {
    serve(week().filter(r => gameDayKey(r) !== TODAY))
    const b = await loadSportBoard('nfl', viewer, { day: 'today', view: 'picks', date: TODAY })
    expect(b.date).toBe(TODAY)
    expect(b.rows).toHaveLength(0)
  })

  it('football: no games today means no TODAY chip, and the board opens on the first game day', async () => {
    serve(week().filter(r => gameDayKey(r) !== TODAY))
    const b = await loadSportBoard('nfl', viewer, { day: 'today', view: 'picks' })
    expect(b.date).toBe(day(1))
    expect(b.rows).toHaveLength(2)
    expect(b.days.map(d => d.date)).toEqual([day(1), day(3)])                    // skips the empty days
  })

  it('basketball: YESTERDAY (archive) and TODAY, each chip only when it has events', async () => {
    serve(week().filter(r => gameDayKey(r) === TODAY), [prow(day(-1), 0), prow(day(-1), 1)])
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks' })
    expect(b.yesterday).toMatchObject({ date: day(-1), count: 2 })
    expect(b.days.map(d => d.date)).toEqual([TODAY])
    expect(b.date).toBe(TODAY)
  })

  it('the yesterday archive is untouched by the day cut, and the strip is still there to step forward', async () => {
    serve(week(), [prow(day(-1), 0), prow(day(-1), 1)])
    const b = await loadSportBoard('nba', viewer, { day: 'yesterday', view: 'picks' })
    expect(b.rows).toHaveLength(2)
    expect(b.date).toBe(day(-1))
    expect(b.days.map(d => d.date)).toEqual([TODAY, day(1), day(3)])
  })
})

describe('DayToggle', () => {
  const chips = (props: Partial<React.ComponentProps<typeof DayToggle>> = {}) => {
    render(<DayToggle today={TODAY} yesterday={{ date: day(-1), label: 'SAT OCT 17', count: 2 }} days={liveDays(week(), TODAY)} selected={TODAY} {...props} />)
    return within(screen.getByRole('navigation', { name: 'Day' })).getAllByRole('link')
  }

  it('one chip per date with events: YESTERDAY, TODAY, then each later game day, the shown date marked', () => {
    const links = chips({ selected: day(3) })
    expect(links).toHaveLength(4)
    expect(links[0]).toHaveTextContent('YESTERDAY')
    expect(links[1]).toHaveTextContent('TODAY')
    expect(links[0]).toHaveAttribute('href', '/picks/nba?market=NBA_PTS&day=yesterday')           // drops game, sets day
    expect(links[1]).toHaveAttribute('href', `/picks/nba?market=NBA_PTS&date=${TODAY}`)           // names its own date
    expect(links[2]).toHaveAttribute('href', `/picks/nba?market=NBA_PTS&date=${day(1)}`)
    expect(links[3]).toHaveAttribute('aria-current', 'date')
    expect(links[1]).not.toHaveAttribute('aria-current')
  })

  it('no archive and no games today: just the days with events, and the first one is the shown date', () => {
    const links = chips({ yesterday: null, days: liveDays(week().filter(r => gameDayKey(r) !== TODAY), TODAY), selected: day(1) })
    expect(links).toHaveLength(2)
    expect(links[0]).toHaveAttribute('aria-current', 'date')
    expect(links[0]).not.toHaveTextContent('TODAY')
  })

  it('MLB keeps clean URLs (its board is always the day itself)', () => {
    const links = chips({ pinDates: false })
    expect(links[1]).toHaveAttribute('href', '/picks/nba?market=NBA_PTS')
  })
})
