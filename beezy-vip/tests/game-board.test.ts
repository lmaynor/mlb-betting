import { buildGameBoard, buildGameModel, leanOf, modelSideProb, normalCdf, restrictProjToOdds, sortBoard } from '../lib/game-board'
import type { GameProjectionsFeed } from '../lib/game-lines'
import type { GameOddsFeed, GameOddsRow } from '../lib/game-odds'

const q = (book: string, a: number | null, b: number | null) => ({ book, a, b })
const GK = 'nhl:2026-11-02:DET@FLA'
const mk = (market: GameOddsRow['market'], line: number | null, books: GameOddsRow['books'], gk = GK): GameOddsRow => ({
  key: `${gk}|${market}|${line ?? ''}`, sport: 'nhl', game_key: gk, away: 'DET', home: 'FLA', commence: '2026-11-03T00:10:00Z', market, line, books,
})
const SIX = (a: number, b: number) => ['draftkings', 'fanduel', 'betmgm', 'caesars', 'novig', 'kalshi'].map(k => q(k, a, b))
const oddsFeed = (rows: GameOddsRow[]): GameOddsFeed => ({
  sport: 'nhl', status: 'live', as_of: '2026-11-02T20:00:00Z', age_minutes: 4, stale: false, validated: false, lines_source: 'parlayapi', count: rows.length,
  books: [], offshore_books: [], games: [], rows,
})
const projFeed = (priced: GameProjectionsFeed['priced_games'], games: GameProjectionsFeed['games'] = []): GameProjectionsFeed => ({
  sport: 'nhl', status: 'live', engine: 'dixon_coles_eng_matrix_v1', as_of: null, age_minutes: 4, stale: false, count: 0, rows: [], priced_games: priced, games, markets: [],
})
const base = { refs: [] as string[], method: 'proportional' as const, bettable: null }

// FLA -1.5 is quoted by six books, the -3.5 and +2.5 alt rungs by two each
const ROWS = [
  mk('spread', -3.5, [q('draftkings', -300, 240), q('fanduel', -280, 235)]),
  mk('spread', -1.5, SIX(-150, 130)),
  mk('spread', 2.5, [q('draftkings', 350, -480), q('fanduel', 340, -450)]),
  mk('total', 6.5, SIX(-110, -110)),
  mk('ml', null, SIX(120, -140)),
]

describe('normalCdf', () => {
  it('matches known values', () => {
    expect(normalCdf(0)).toBeCloseTo(0.5, 6)
    expect(normalCdf(1.96)).toBeCloseTo(0.975, 3)
    expect(normalCdf(-1)).toBeCloseTo(0.1587, 3)
  })
})

describe('modelSideProb', () => {
  const priced = (extra = {}) => ({ game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: '2026-11-02', markets: [], ...extra })
  it('prefers the feed ladder and drops the push mass so it compares with a two-way price', () => {
    const m = buildGameModel('nhl', 'DET', 'FLA', priced({ ladder: { spread: [{ line: -1.5, p_home: 0.40, p_away: 0.55, push: 0.05 }] } }), undefined, null)
    expect(modelSideProb(m, 'spread', -1.5, 'b')).toBeCloseTo(0.40 / 0.95, 10)
    expect(modelSideProb(m, 'spread', -1.5, 'a')).toBeCloseTo(0.55 / 0.95, 10)
  })
  it('falls back to a normal curve from mu / sigma (home cover rises with the home line)', () => {
    const m = buildGameModel('nba', 'BOS', 'MIA', priced({ mu_margin: 3, sigma_margin: 12, mu_total: 224, sigma_total: 17 }), undefined, null)
    const lo = modelSideProb(m, 'spread', -3.5, 'b')!
    const hi = modelSideProb(m, 'spread', 2.5, 'b')!
    expect(hi).toBeGreaterThan(lo)
    expect(modelSideProb(m, 'spread', -3.5, 'a')! + lo).toBeCloseTo(1, 10)
    expect(modelSideProb(m, 'total', 224, 'a')).toBeCloseTo(0.5, 6)
    expect(modelSideProb(m, 'total', 232, 'a')!).toBeLessThan(0.5)
    expect(modelSideProb(m, 'ml', null, 'b')).toBeCloseTo(normalCdf(3 / 12), 10)
  })
  it('has no probability, rather than a guess, when the model has no curve for the rung', () => {
    const m = buildGameModel('nhl', 'DET', 'FLA', priced({ lambda_h: 3.1, lambda_a: 2.4 }), undefined, null)
    expect(modelSideProb(m, 'spread', -1.5, 'b')).toBeNull()
    expect(modelSideProb(null, 'ml', null, 'a')).toBeNull()
  })
})

describe('buildGameModel', () => {
  it('NHL: projected score is the two expected-goal rates; drivers list only what exists', () => {
    const m = buildGameModel('nhl', 'DET', 'FLA', { game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: 'x', lambda_h: 3.1, lambda_a: 2.4, markets: [] },
      { game_key: GK, away: 'DET', home: 'FLA', start: 'x', home_goalie: 'Bobrovsky', away_goalie: 'Lyon', is_b2b_road: true }, 'dixon_coles_eng_matrix_v1')!
    expect(m.projHome).toBe(3.1)
    expect(m.projAway).toBe(2.4)
    expect(m.margin).toBeCloseTo(0.7, 10)
    expect(m.total).toBeCloseTo(5.5, 10)
    expect(m.drivers.map(d => d.label)).toEqual(['Expected goals', 'Goalies', 'Rest'])
  })
  it('NBA: the projected score comes from margin and total', () => {
    const m = buildGameModel('nba', 'BOS', 'MIA', { game_key: 'k', home_team: 'MIA', away_team: 'BOS', game_date: 'x', mu_margin: 4, sigma_margin: 12, mu_total: 224, exp_pace: 99.8, markets: [] }, undefined, null)!
    expect(m.projHome).toBe(114)
    expect(m.projAway).toBe(110)
    expect(m.drivers.map(d => d.label)).toEqual(['Expected pace'])
  })
  it('NBA schedule and injury drivers are listed only when the feed carries them', () => {
    const m = buildGameModel('nba', 'BOS', 'MIA', { game_key: 'k', home_team: 'MIA', away_team: 'BOS', game_date: 'x', mu_margin: 4, sigma_margin: 12, mu_total: 224, markets: [] },
      { game_key: 'k', away: 'BOS', home: 'MIA', start: 's', rest_home: 2, rest_away: 1, b2b_away: true, b2b_home: false, away_points_lost: 9.5, home_points_lost: 0 }, null)!
    expect(m.drivers.map(d => d.label)).toEqual(['Rest days', 'Back-to-back', 'Injuries (pts lost)'])
    expect(m.drivers[0].value).toBe('BOS 1 \u00b7 MIA 2')
    expect(m.drivers[1].value).toBe('BOS')
    expect(m.drivers[2].value).toBe('BOS 9.5 \u00b7 MIA 0.0')
  })
  it('no model data at all is null', () => {
    expect(buildGameModel('nhl', 'A', 'B', undefined, undefined, null)).toBeNull()
  })
})

describe('buildGameBoard', () => {
  const board = () => buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: { nhl: projFeed([]) } }, base)

  it('one entry per game, grouped by market, rungs ordered hardest cover first for the home side', () => {
    const [g] = board()
    expect(board()).toHaveLength(1)
    expect(g).toMatchObject({ key: GK, away: 'DET', home: 'FLA', sport: 'nhl' })
    expect(g.rows.spread.map(r => r.row.line)).toEqual([-3.5, -1.5, 2.5])
    expect(g.rows.total).toHaveLength(1)
    expect(g.rows.ml).toHaveLength(1)
  })
  it('the main spread is the line most books quote, not an alt rung', () => {
    const [g] = board()
    expect(g.main.spread?.row.line).toBe(-1.5)
    expect(g.main.total?.row.line).toBe(6.5)
    expect(g.nBooks).toBe(6)
  })
  it('an alt rung\'s EV never headlines the card', () => {
    const rows = [
      ...ROWS,
      // a long-shot alt rung with one big outlier price that would dominate a Best EV sort
      mk('spread', 3.5, [q('draftkings', 800, -1200), q('fanduel', 300, -400), q('betmgm', 290, -380)]),
    ]
    const [g] = buildGameBoard({ odds: { nhl: oddsFeed(rows) }, proj: {} }, base)
    const mainEv = [g.main.ml, g.main.spread, g.main.total].map(r => r?.bestEv).filter((x): x is number => x != null)
    expect(g.bestEv).toBe(Math.max(...mainEv))
  })
  it('joins the model feed by game key and reads a model gap off the main lines', () => {
    const pg = { game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: 'x', lambda_h: 3.1, lambda_a: 2.4, markets: [], ladder: { ml: { p_home: 0.7, p_away: 0.3 }, spread: [{ line: -1.5, p_home: 0.5, p_away: 0.5, push: 0 }] } }
    const [g] = buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: { nhl: projFeed([pg]) } }, base)
    expect(g.model?.pHome).toBeCloseTo(0.7, 10)
    expect(g.modelGap).not.toBeNull()
    expect(g.modelGap!).toBeGreaterThan(0.05)
  })
  it('a game with no model entry has no model and no gap', () => {
    const [g] = board()
    expect(g.model).toBeNull()
    expect(g.modelGap).toBeNull()
  })
  it('two sports and two games stay separate entries', () => {
    const other = ROWS.map(r => ({ ...r, key: r.key.replace('FLA', 'MTL'), game_key: 'nhl:2026-11-02:TOR@MTL', away: 'TOR', home: 'MTL', commence: '2026-11-03T02:00:00Z' }))
    const g = buildGameBoard({ odds: { nhl: oddsFeed([...ROWS, ...other]) }, proj: {} }, base)
    expect(g.map(x => x.key).sort()).toEqual([GK, 'nhl:2026-11-02:TOR@MTL'].sort())
  })
})

describe('sortBoard', () => {
  const mkEntry = (key: string, start: string, bestEv: number | null, modelGap: number | null, moveScore = 0) =>
    ({ key, start, bestEv, modelGap, moveScore, move: { ml: null, spread: null, total: null }, hasHistory: false, sport: 'nhl', away: 'A', home: 'B', rows: { ml: [], spread: [], total: [] }, main: { ml: null, spread: null, total: null }, nBooks: 3, arbLive: false, model: null })
  const games = [mkEntry('late', '2026-11-03T03:00:00Z', 0.01, 0.09, 0.05), mkEntry('early', '2026-11-03T00:00:00Z', null, null), mkEntry('mid', '2026-11-03T01:00:00Z', 0.04, 0.02, 0.01)]
  it('time is the default order: earliest first', () => {
    expect(sortBoard(games, 'time').map(g => g.key)).toEqual(['early', 'mid', 'late'])
  })
  it('EV and model gap put the biggest first and games with none last', () => {
    expect(sortBoard(games, 'ev').map(g => g.key)).toEqual(['mid', 'late', 'early'])
    expect(sortBoard(games, 'gap').map(g => g.key)).toEqual(['late', 'mid', 'early'])
  })
  it('movement puts the biggest mover first and games that have not moved last', () => {
    expect(sortBoard(games, 'move').map(g => g.key)).toEqual(['late', 'mid', 'early'])
  })
  it('never mutates its input', () => {
    const keys = games.map(g => g.key)
    sortBoard(games, 'ev')
    expect(games.map(g => g.key)).toEqual(keys)
  })
})

describe('restrictProjToOdds', () => {
  it('drops the model of a game whose prices were withheld, and tolerates a missing feed', () => {
    const pg = (k: string) => ({ game_key: k, home_team: 'B', away_team: 'A', game_date: 'd', markets: [] })
    const proj = projFeed([pg(GK), pg('nhl:other')], [{ game_key: GK, away: 'DET', home: 'FLA', start: 's' }, { game_key: 'nhl:other', away: 'A', home: 'B', start: 's' }])
    const out = restrictProjToOdds(proj, oddsFeed(ROWS))!
    expect(out.priced_games.map(g => g.game_key)).toEqual([GK])
    expect(out.games.map(g => g.game_key)).toEqual([GK])
    expect(restrictProjToOdds(null, oddsFeed(ROWS))).toBeNull()
    expect(restrictProjToOdds(proj, null)!.priced_games).toEqual([])
  })
})

describe('leanOf: where the model is furthest above the market', () => {
  const pg = (ladder: object) => ({ game_key: GK, home_team: 'FLA', away_team: 'DET', game_date: 'x', markets: [], ladder })
  it('picks the main-line side with the biggest model-minus-market gap', () => {
    const [g] = buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: { nhl: projFeed([pg({ ml: { p_home: 0.7, p_away: 0.3 }, spread: [{ line: -1.5, p_home: 0.5, p_away: 0.5, push: 0 }], total: [{ line: 6.5, p_over: 0.5, p_under: 0.5, push: 0 }] })]) } }, base)
    const lean = leanOf(g)!
    expect(lean.market).toBe('ml')
    expect(lean.side).toBe('b')
    expect(lean.label).toBe('FLA ML')
    expect(lean.gap).toBeGreaterThan(0.1)
    expect(lean.gap).toBeCloseTo(lean.pModel - lean.pMarket, 10)
  })
  it('has no lean without a model, or when the model never beats the market', () => {
    const [none] = buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: {} }, base)
    expect(leanOf(none)).toBeNull()
    const [low] = buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: { nhl: projFeed([pg({ ml: { p_home: 0.01, p_away: 0.01 } })]) } }, base)
    // 0.5 / 0.5 after renormalising is not below a ~58% / ~42% market on both sides
    expect(leanOf(low)?.gap ?? 0).toBeLessThan(0.2)
  })
})

describe('buildGameBoard: movement from the feed history', () => {
  const pt = (min: number, line: number | null, p_a: number | null) => ({ t: new Date(Date.UTC(2026, 10, 3, 14, min)).toISOString(), line, p_a, best_a: -110, best_b: -110, n: 6 })
  it('attaches the series of each market and scores the game by its biggest mover', () => {
    const feed = { ...oddsFeed(ROWS), history: { [`${GK}|spread`]: { points: [pt(0, -1.5, 0.42), pt(90, -1.5, 0.47)] }, [`${GK}|total`]: { points: [pt(0, 6.5, 0.5)] } } }
    const [g] = buildGameBoard({ odds: { nhl: feed }, proj: {} }, base)
    expect(g.hasHistory).toBe(true)
    expect(g.move.spread?.pDelta).toBeCloseTo(0.05, 10)
    expect(g.move.total).toBeNull()      // one point is not a series
    expect(g.move.ml).toBeNull()
    expect(g.moveScore).toBeCloseTo(0.05, 10)
  })
  it('an older backend with no history says so, and scores zero', () => {
    const [g] = buildGameBoard({ odds: { nhl: oddsFeed(ROWS) }, proj: {} }, base)
    expect(g.hasHistory).toBe(false)
    expect(g.moveScore).toBe(0)
  })
})
