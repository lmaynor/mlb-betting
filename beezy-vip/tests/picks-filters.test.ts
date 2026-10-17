import { applyPickFilters, pickRows, type BoardRow } from '../lib/board-rows'
import { activeFilterCount, matchesBook, matchesQuery, matchesSignal, matchesTier, parsePickFilters } from '../lib/picks-filters'
import { filterTopPicks, type TopPick } from '../lib/top-picks'

const r = (o: Partial<BoardRow>): BoardRow => ({
  key: 'k', sport: 'nfl', system: 'NFL_REC_YDS', marketLabel: 'Rec Yds', player: 'Pat Receiver', headshotId: null, team: 'KC', matchup: 'KC @ LV', gameKey: 'nfl:2026-10-18:KC@LV', away: 'KC', home: 'LV', avg10: null, avgSzn: null, result: null, start: '2026-10-18T17:00:00Z',
  side: 'over', line: 64.5, hasLine: true, proj: 70, pModel: 0.6, pFair: 0.5, nBooks: 6, l5: 0.6, l10: 0.6, odds: 110, book: 'draftkings',
  modelEdge: 0.05, marketEv: 0.04, confluence: true, score: 80, isPick: false, ...o,
})

describe('parsePickFilters', () => {
  it('reads every param, ignores junk, and defaults sensibly', () => {
    expect(parsePickFilters({})).toMatchObject({ day: 'today', game: undefined, market: undefined, signal: undefined, tier: undefined, book: undefined, books: [], q: '', sort: 'score', dir: 'desc' })
    const f = parsePickFilters({ market: 'NFL_REC_YDS', tier: 'strong', book: 'Hard Rock', books: 'DraftKings, fanduel,,', q: ' Mahomes ', sort: 'prob', dir: 'asc', sport: 'NFL', day: 'yesterday', game: 'nfl:2026-10-18:KC@LV', signal: 'Strong', result: 'WIN' })
    expect(f).toMatchObject({ market: 'NFL_REC_YDS', tier: 'STRONG', book: 'hardrock', books: ['draftkings', 'fanduel'], sort: 'prob', dir: 'asc', sport: 'nfl', day: 'yesterday', game: 'nfl:2026-10-18:KC@LV', signal: 'strong', result: 'win' })
    expect(parsePickFilters({ tier: 'bogus', sort: 'bogus', dir: 'sideways', market: 'ALL', book: 'ALL', sport: 'ALL', day: 'last-week', signal: 'nonsense', result: 'maybe' })).toMatchObject({ tier: undefined, sort: 'score', dir: 'desc', market: undefined, book: undefined, sport: undefined, day: 'today', signal: undefined, result: undefined })
    // TIME is no longer a sort: an old link that asks for it falls back to the score
    expect(parsePickFilters({ sort: 'date' }).sort).toBe('score')
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

  it('applyPickFilters cuts by game, market, signal, tier, book, result and search, then sorts', () => {
    const rows = [
      r({ key: 'a', player: 'Early', score: 50, odds: -120, pModel: 0.7, gameKey: 'g1' }),
      r({ key: 'b', player: 'Late', score: 90, odds: 200, pModel: 0.55, book: 'fanduel', gameKey: 'g2', confluence: false, modelEdge: -0.02, marketEv: -0.01 }),
      r({ key: 'c', player: 'Other', score: 75, system: 'NFL_PASS_YDS', hasLine: false, gameKey: 'g2', result: 'win' }),
    ]
    const f = (o: Record<string, string>) => applyPickFilters(rows, parsePickFilters(o)).map(x => x.key)
    expect(f({})).toEqual(['b', 'c', 'a'])
    expect(f({ game: 'g2' })).toEqual(['b', 'c'])
    expect(f({ market: 'NFL_REC_YDS' })).toEqual(['b', 'a'])
    expect(f({ signal: 'priced' })).toEqual(['b', 'a'])
    expect(f({ signal: 'confluence' })).toEqual(['c', 'a'])           // b has no agreement
    expect(f({ signal: 'ev' })).toEqual(['c', 'a'])                   // b's edge is negative
    expect(f({ signal: 'strong' })).toEqual(['b', 'c'])
    expect(f({ signal: 'lean' })).toEqual(['b', 'c', 'a'])
    expect(f({ tier: 'strong' })).toEqual(['b', 'c'])
    expect(f({ book: 'fanduel' })).toEqual(['b'])
    expect(f({ result: 'win' })).toEqual(['c'])
    expect(f({ result: 'pending' })).toEqual(['b', 'a'])
    expect(f({ q: 'early' })).toEqual(['a'])
    expect(f({ sort: 'odds' })).toEqual(['b', 'c', 'a'])
    expect(f({ sort: 'prob' })).toEqual(['a', 'c', 'b'])
    expect(f({ sort: 'prob', dir: 'asc' })).toEqual(['b', 'c', 'a'])
  })

  it('signal facts: priced, model + market, +EV and the score cuts', () => {
    const base = { score: 50, hasLine: true, confluence: false, edge: 0.01 }
    expect(matchesSignal(base, undefined)).toBe(true)
    expect(matchesSignal({ ...base, hasLine: false }, 'priced')).toBe(false)
    expect(matchesSignal({ ...base, edge: -0.01 }, 'ev')).toBe(false)
    expect(matchesSignal({ ...base, score: 69 }, 'strong')).toBe(false)
    expect(matchesSignal({ ...base, score: 70 }, 'strong')).toBe(true)
    expect(matchesSignal({ ...base, score: 44 }, 'lean')).toBe(false)
  })

  it('a team search matches the player\'s own team, not the opponent', () => {
    const rows = [
      r({ key: 'k', player: 'A Kings Player', team: 'LAK', matchup: 'BOS @ LAK', away: 'BOS', home: 'LAK', sport: 'nhl', system: 'NHL_SOG' }),
      r({ key: 'b', player: 'A Bruins Player', team: 'BOS', matchup: 'BOS @ LAK', away: 'BOS', home: 'LAK', sport: 'nhl', system: 'NHL_SOG' }),
    ]
    const f = (q: string) => applyPickFilters(rows, parsePickFilters({ q })).map(x => x.key)
    expect(f('kings')).toEqual(['k'])
    expect(f('los angeles')).toEqual(['k'])
    expect(f('boston')).toEqual(['b'])
    expect(f('bruins')).toEqual(['b'])
    expect(f('LAK')).toEqual(['k'])
    // a row whose team is unknown (MLB bets carry only the game) falls back to both teams of the game
    const mlb = [r({ key: 'm', sport: 'mlb', system: 'K', team: null, matchup: 'TB @ CLE', away: 'TB', home: 'CLE', player: 'Nick Martinez' })]
    expect(applyPickFilters(mlb, parsePickFilters({ q: 'guardians' })).map(x => x.key)).toEqual(['m'])
    expect(applyPickFilters(mlb, parsePickFilters({ q: 'rays' })).map(x => x.key)).toEqual(['m'])
  })

  it('counts active filters but not the day or the sort', () => {
    expect(activeFilterCount(parsePickFilters({ day: 'yesterday', sort: 'edge' }))).toBe(0)
    expect(activeFilterCount(parsePickFilters({ q: 'x', market: 'K', signal: 'ev', game: 'g' }))).toBe(4)
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
