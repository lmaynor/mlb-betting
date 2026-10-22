import { limitOddsBoard } from '@/lib/access-feeds'
import type { BoardRow, OddsBoard } from '@/lib/odds-screen'

const row = (i: number, nBooks: number): BoardRow => ({
  key: `k${i}`, game_key: 'g', commence: '2026-10-22T01:00:00+00:00', away: 'A', home: 'B', player: `P${i}`, player_norm: `p${i}`, stat: 'points', line: 20.5,
  books: Array.from({ length: nBooks }, (_, j) => ({ book: `b${j}`, over: -110, under: -110 })),
})
const board = (): OddsBoard => ({
  sport: 'nba', status: 'live', stale: false, as_of: null, age_minutes: 3, count: 8, books: [], offshore_books: [], stats: [], games: [],
  lines_source: [], lines_age_minutes: 3, rows: [2, 9, 4, 7, 3, 8, 5, 6].map((n, i) => row(i, n)),
})
const ON = { ENFORCE_TIERS: '1' }

describe('limitOddsBoard', () => {
  it('shows everything while gating is off', () => {
    expect(limitOddsBoard(board(), { tier: 'public', sport: null }, {}).locked).toBe(0)
  })
  it('gives a free viewer the 5 best-covered lines and counts the rest as locked', () => {
    const out = limitOddsBoard(board(), { tier: 'public', sport: null }, ON)
    expect(out.board.rows.map(r => r.books.length).sort((a, b) => b - a)).toEqual([9, 8, 7, 6, 5])
    expect(out.locked).toBe(3)
    expect(out.board.count).toBe(5)
  })
  it('a single-sport pass is NOT enough: the odds screen is Pro only like the terminal', () => {
    expect(limitOddsBoard(board(), { tier: 'single_sport', sport: 'nba' }, ON).locked).toBe(3)
  })
  it('all-access sees the whole board', () => {
    const full = limitOddsBoard(board(), { tier: 'pro', sport: null }, ON)
    expect(full.locked).toBe(0)
    expect(full.board.rows).toHaveLength(8)
  })
})
