import { applyPickFilters, filterBoard, pickRows, type BoardRow } from '../lib/board-rows'
import { matchesBook, matchesQuery, matchesTier, parsePickFilters } from '../lib/picks-filters'
import { filterTopPicks, type TopPick } from '../lib/top-picks'

const r = (o: Partial<BoardRow>): BoardRow => ({
  key: 'k', sport: 'nfl', system: 'NFL_REC_YDS', marketLabel: 'Rec Yds', player: 'Pat Receiver', headshotId: null, matchup: 'KC v LV', start: '2026-10-18T17:00:00Z',
  side: 'over', line: 64.5, hasLine: true, proj: 70, pModel: 0.6, pFair: 0.5, nBooks: 6, l5: 0.6, l10: 0.6, odds: 110, book: 'draftkings',
  modelEdge: 0.05, marketEv: 0.04, confluence: true, score: 80, isPick: false, ...o,
})

describe('parsePickFilters', () => {
  it('reads every param, ignores junk, and defaults sensibly', () => {
    expect(parsePickFilters({})).toMatchObject({ market: undefined, tier: undefined, book: undefined, books: [], q: '', sort: 'score', dir: 'desc' })
    const f = parsePickFilters({ market: 'NFL_REC_YDS', tier: 'strong', book: 'Hard Rock', books: 'DraftKings, fanduel,,', q: ' Mahomes ', sort: 'prob', dir: 'asc', sport: 'NFL' })
    expect(f).toMatchObject({ market: 'NFL_REC_YDS', tier: 'STRONG', book: 'hardrock', books: ['draftkings', 'fanduel'], sort: 'prob', dir: 'asc', sport: 'nfl' })
    expect(parsePickFilters({ tier: 'bogus', sort: 'bogus', dir: 'sideways', market: 'ALL', book: 'ALL', sport: 'ALL' })).toMatchObject({ tier: undefined, sort: 'score', dir: 'desc', market: undefined, book: undefined, sport: undefined })
  })
})

describe('tier, book and text matching', () => {
  it('tiers use the published cuts: STRONG 70+, LEAN 45-69, WATCH under 45', () => {
    expect([70, 69, 45, 44].map(s => ['STRONG', 'LEAN', 'WATCH'].filter(t => matchesTier(s, t as 'STRONG')))).toEqual([['STRONG'], ['LEAN'], ['LEAN'], ['WATCH']])
    expect(matchesTier(10, undefined)).toBe(true)
  })
  it('book filters: one book, my books, and a row with no book is judged by the filter', () => {
    expect(matchesBook('DraftKings', { book: 'draftkings', books: [] })).toBe(true)
    expect(matchesBook('fanduel', { book: 'draftkings', books: [] })).toBe(false)
    expect(matchesBook('fanduel', { book: undefined, books: ['draftkings', 'fanduel'] })).toBe(true)
    expect(matchesBook('caesars', { book: undefined, books: ['draftkings'] })).toBe(false)
    expect(matchesBook(null, { book: undefined, books: [] })).toBe(true)
    expect(matchesBook(null, { book: 'draftkings', books: [] })).toBe(false)
  })
  it('text search is case-insensitive and empty matches everything', () => {
    expect(matchesQuery('Pat Mahomes KC v LV', 'mahomes')).toBe(true)
    expect(matchesQuery('Pat Mahomes', ' zzz ')).toBe(false)
    expect(matchesQuery('anything', '')).toBe(true)
  })
})

describe('board rows', () => {
  it('pickRows keeps only rows with a line and a positive score', () => {
    expect(pickRows([r({}), r({ hasLine: false }), r({ score: 0 })])).toHaveLength(1)
  })

  it('applyPickFilters cuts by market, tier, book and search, then sorts (TIME is soonest-first by default)', () => {
    const rows = [
      r({ key: 'a', player: 'Early', score: 50, start: '2026-10-18T13:00:00Z', odds: -120, pModel: 0.7 }),
      r({ key: 'b', player: 'Late', score: 90, start: '2026-10-18T20:00:00Z', odds: 200, pModel: 0.55, book: 'fanduel' }),
      r({ key: 'c', player: 'Other', score: 75, system: 'NFL_PASS_YDS', start: null }),
    ]
    const f = (o: Record<string, string>) => applyPickFilters(rows, parsePickFilters(o)).map(x => x.key)
    expect(f({})).toEqual(['b', 'c', 'a'])
    expect(f({ market: 'NFL_REC_YDS' })).toEqual(['b', 'a'])
    expect(f({ tier: 'strong' })).toEqual(['b', 'c'])
    expect(f({ book: 'fanduel' })).toEqual(['b'])
    expect(f({ q: 'early' })).toEqual(['a'])
    expect(f({ sort: 'date' })).toEqual(['a', 'b', 'c'])           // soonest first; a game with no time last
    expect(f({ sort: 'date', dir: 'asc' })).toEqual(['c', 'b', 'a'])
    expect(f({ sort: 'odds' })).toEqual(['b', 'c', 'a'])
    expect(f({ sort: 'prob' })).toEqual(['a', 'c', 'b'])
  })

  it('filterBoard (the client board) filters side, signal, search and sorts by the chosen key', () => {
    const rows = [r({ key: 'a', side: 'under', confluence: false, marketEv: -0.02, modelEdge: -0.01, l10: 0.9, score: 10 }), r({ key: 'b', score: 80, l10: 0.2 })]
    const base = { market: 'ALL', side: 'ALL' as const, signal: 'ALL' as const, q: '', sort: 'score' as const }
    expect(filterBoard(rows, base).map(x => x.key)).toEqual(['b', 'a'])
    expect(filterBoard(rows, { ...base, side: 'UNDER' }).map(x => x.key)).toEqual(['a'])
    expect(filterBoard(rows, { ...base, signal: 'CONFLUENCE' }).map(x => x.key)).toEqual(['b'])
    expect(filterBoard(rows, { ...base, signal: 'EV' }).map(x => x.key)).toEqual(['b'])
    expect(filterBoard(rows, { ...base, sort: 'l10' }).map(x => x.key)).toEqual(['a', 'b'])
  })
})

describe('cross-sport Top 10 filters', () => {
  const tp = (o: Partial<TopPick>): TopPick => ({ key: 'x', sport: 'mlb', who: 'Aaron Judge', what: 'Over 1.5 Total Bases', game: 'NYY @ BOS', odds: 120, book: 'fanduel', edge: 0.05, score: 70, basis: 'model', tracked: true, result: null, ...o })
  const picks = [tp({ key: '1', score: 90 }), tp({ key: '2', sport: 'nfl', who: 'Pat Mahomes', score: 80, edge: 0.09, book: 'draftkings', tracked: false }), tp({ key: '3', sport: 'nfl', who: 'Travis Kelce', score: 40, odds: -150 })]
  it('keeps the ten best of what the filters leave, not the sport rows inside the overall ten', () => {
    expect(filterTopPicks(picks, parsePickFilters({ sport: 'nfl' })).map(p => p.key)).toEqual(['2', '3'])
    expect(filterTopPicks(picks, parsePickFilters({ tier: 'watch' })).map(p => p.key)).toEqual(['3'])
    expect(filterTopPicks(picks, parsePickFilters({ q: 'judge' })).map(p => p.key)).toEqual(['1'])
    expect(filterTopPicks(picks, parsePickFilters({ book: 'draftkings' })).map(p => p.key)).toEqual(['2'])
    expect(filterTopPicks(picks, parsePickFilters({ sort: 'edge' })).map(p => p.key)).toEqual(['2', '1', '3'])
    expect(filterTopPicks(picks, parsePickFilters({}), 2).map(p => p.key)).toEqual(['1', '2'])
  })
})
