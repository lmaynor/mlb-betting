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
import { gameDayKey, isDateKey, onDay, slateRows, upcomingDays, viewDay } from '../lib/board-days'
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

describe('the board', () => {
  it('TODAY has only today\'s games and rows; the other days are chips', async () => {
    mockProjections.mockResolvedValue(feed(week()))
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks' })
    expect(b.date).toBe(TODAY)
    expect(b.rows).toHaveLength(3)
    expect(b.games.map(g => g.key)).toEqual([`nba:${TODAY}:BOS@DET`])
    expect(b.days.map(d => d.date)).toEqual([day(1), day(3)])
  })

  it('an upcoming day shows only its own rows and games', async () => {
    mockProjections.mockResolvedValue(feed(week()))
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks', date: day(3) })
    expect(b.date).toBe(day(3))
    expect(b.rows).toHaveLength(4)
    expect(b.games.map(g => g.key)).toEqual([`nba:${day(3)}:BOS@DET`])
  })

  it('opens on the next day with games when today has none', async () => {
    mockProjections.mockResolvedValue(feed(week().filter(r => gameDayKey(r) !== TODAY)))
    const b = await loadSportBoard('nba', viewer, { day: 'today', view: 'picks' })
    expect(b.date).toBe(day(1))
    expect(b.rows).toHaveLength(2)
  })

  it('the yesterday archive is untouched by the day cut', async () => {
    mockProjections.mockResolvedValue({ ...feed([prow(day(-1), 0), prow(day(-1), 1)]), archive: true })
    const b = await loadSportBoard('nba', viewer, { day: 'yesterday', view: 'picks' })
    expect(b.rows).toHaveLength(2)
    expect(b.days).toEqual([])
  })
})

describe('DayToggle with upcoming days', () => {
  it('lists TODAY, YESTERDAY, then one chip per upcoming day, marking the day shown and clearing game and day', () => {
    const upcoming = upcomingDays(week(), TODAY)
    render(<DayToggle day="today" today={TODAY} yesterday={day(-1)} upcoming={upcoming} viewDate={day(3)} />)
    const nav = screen.getByRole('navigation', { name: 'Day' })
    const links = within(nav).getAllByRole('link')
    expect(links).toHaveLength(4)
    expect(links[2]).toHaveAttribute('href', `/picks/nba?market=NBA_PTS&date=${day(1)}`)            // drops game and day, sets date
    expect(links[3]).toHaveAttribute('aria-current', 'date')                                         // the shown day
    expect(links[0]).not.toHaveAttribute('aria-current')
    expect(links[0]).toHaveAttribute('href', '/picks/nba?market=NBA_PTS')                            // TODAY clears date and day
  })

  it('TODAY is the active chip on today, and a board with no later games has just the two chips', () => {
    render(<DayToggle day="today" today={TODAY} yesterday={day(-1)} viewDate={TODAY} />)
    const links = within(screen.getByRole('navigation', { name: 'Day' })).getAllByRole('link')
    expect(links).toHaveLength(2)
    expect(links[0]).toHaveAttribute('aria-current', 'date')
  })
})
