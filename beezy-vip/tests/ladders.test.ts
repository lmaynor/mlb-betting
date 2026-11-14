import type { BoardBook, BoardRow } from '@/lib/odds-screen'
import {
  groupLadderRows,
  ladderCategoryOf,
  ladderGroupFor,
  ladderInlineAllowed,
  selectLadderFeed,
  type LadderGroup,
} from '@/lib/ladders'

const B = (
  book: string,
  over: number | null,
  under: number | null,
  opts: Partial<BoardBook> = {}
): BoardBook => ({
  book,
  over,
  under,
  ...opts,
})

const makeRow = (overrides: Partial<BoardRow> = {}): BoardRow => ({
  key: 'g1|player_one|rec_yds|50.5',
  game_key: 'nfl:2026-11-20:KC@BUF',
  commence: '2026-11-20T20:00:00Z',
  away: 'KC',
  home: 'BUF',
  player: 'Player One',
  player_norm: 'player_one',
  stat: 'rec_yds',
  line: 50.5,
  books: [],
  ...overrides,
})

describe('ladderCategoryOf', () => {
  it('maps stat keys to category or null', () => {
    expect(ladderCategoryOf('rec_yds')).toBe('yardage')
    expect(ladderCategoryOf('rush_yds')).toBe('yardage')
    expect(ladderCategoryOf('pass_yds')).toBe('yardage')
    expect(ladderCategoryOf('receptions')).toBe('count')
    expect(ladderCategoryOf('pass_tds')).toBe('count')
    expect(ladderCategoryOf('anytime_td')).toBe('milestone')
    expect(ladderCategoryOf('points')).toBeNull()
  })
})

describe('1. Grouping', () => {
  it('groups rows by player and stat, drops non-ladder stats, and drops started games', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const rows: BoardRow[] = [
      makeRow({ player: 'Player A', player_norm: 'player_a', stat: 'rec_yds', line: 40.5 }),
      makeRow({ player: 'Player A', player_norm: 'player_a', stat: 'rec_yds', line: 50.5 }),
      makeRow({ player: 'Player B', player_norm: 'player_b', stat: 'rush_yds', line: 65.5 }),
      makeRow({ player: 'Player A', player_norm: 'player_a', stat: 'points', line: 15.5 }),
      makeRow({ player: 'Player A', player_norm: 'player_a', stat: 'rec_yds', line: 70.5, commence: '2026-11-20T11:00:00Z' }),
    ]
    const groups = groupLadderRows(rows, nowMs)
    expect(groups).toHaveLength(2)

    const grpA = groups.find(g => g.playerNorm === 'player_a')!
    expect(grpA).toBeDefined()
    expect(grpA.stat).toBe('rec_yds')
    expect(grpA.category).toBe('yardage')
    expect(grpA.rungs).toHaveLength(2)
    expect(grpA.rungs.map(r => r.line)).toEqual([40.5, 50.5])

    const grpB = groups.find(g => g.playerNorm === 'player_b')!
    expect(grpB).toBeDefined()
    expect(grpB.stat).toBe('rush_yds')
    expect(grpB.rungs).toHaveLength(1)
  })
})

describe('2. Verified gate', () => {
  const nowMs = Date.parse('2026-11-20T12:00:00Z')
  const baseTs = '2026-11-20T11:55:00Z'
  const baseBooks = [
    B('draftkings', 100, -120, { ts: baseTs }),
    B('fanduel', -110, -110, { ts: baseTs }),
    B('betmgm', -110, -110, { ts: baseTs }),
    B('caesars', -110, -110, { ts: baseTs }),
    B('betrivers', -110, -110, { ts: baseTs }),
  ]
  const baseRow = makeRow({
    stat: 'rec_yds',
    books: baseBooks,
    n_fresh_ref: 5,
    n_ref: 5,
    best_over: {
      book: 'draftkings',
      american: 100,
      p_cons_loo: 0.55,
      disp_loo: 0.03,
      n_ref_loo: 4,
    },
  })

  it('passes verified gate when all conditions are satisfied', () => {
    const groups = groupLadderRows([baseRow], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(true)
    expect(groups[0].rungs[0].over!.gateFail).toBeNull()
    expect(groups[0].rungs[0].verified).toBe(true)
    expect(groups[0].verified).toBe(true)
  })

  it('fails disp gate when disp_loo > 0.06', () => {
    const row = {
      ...baseRow,
      best_over: { ...baseRow.best_over!, disp_loo: 0.07 },
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('disp')
  })

  it('fails refs gate when n_ref_loo < 4', () => {
    const row = {
      ...baseRow,
      best_over: { ...baseRow.best_over!, n_ref_loo: 3 },
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('refs')
  })

  it('fails stale_opener gate when target book has stale_opener flag', () => {
    const row = {
      ...baseRow,
      books: baseRow.books.map(b => (b.book === 'draftkings' ? { ...b, stale_opener: true } : b)),
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('stale_opener')
  })

  it('fails floor gate when netEv is below category noise floor', () => {
    // american 100 -> decimal 2.0. p 0.51 -> netEv = 2.0% < 2.5% yardage floor
    const row = {
      ...baseRow,
      best_over: { ...baseRow.best_over!, p_cons_loo: 0.51 },
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('floor')
  })

  it('fails lag gate when target book lagMin > 30 via timestamp spread', () => {
    // draftkings is 35 min behind freshest (11:20 vs 11:55)
    const row = {
      ...baseRow,
      books: baseRow.books.map(b => (b.book === 'draftkings' ? { ...b, ts: '2026-11-20T11:20:00Z' } : b)),
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('lag')
  })

  it('fails fresh gate when fresh leave-one-out reference count < 4', () => {
    // n_fresh_ref 4 with target book being fresh and two-sided -> freshLoo = 4 - 1 = 3 < 4
    const row = {
      ...baseRow,
      n_fresh_ref: 4,
    }
    const groups = groupLadderRows([row], nowMs)
    expect(groups[0].rungs[0].over!.verified).toBe(false)
    expect(groups[0].rungs[0].over!.gateFail).toBe('fresh')
  })
})

describe('3. Count floor', () => {
  it('receptions rung with netEv 4.0 is not verified, but at 5.5 is verified', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const baseBooks = [
      B('draftkings', 100, -120),
      B('fanduel', -110, -110),
      B('betmgm', -110, -110),
      B('caesars', -110, -110),
      B('betrivers', -110, -110),
    ]

    // american 100 -> dec 2.0. netEv 4.0% -> p = 1.04 / 2 = 0.52
    const row4 = makeRow({
      stat: 'receptions',
      books: baseBooks,
      n_fresh_ref: 5,
      best_over: { book: 'draftkings', american: 100, p_cons_loo: 0.52, disp_loo: 0.03, n_ref_loo: 4 },
    })
    const g4 = groupLadderRows([row4], nowMs)
    expect(g4[0].rungs[0].over!.verified).toBe(false)
    expect(g4[0].rungs[0].over!.gateFail).toBe('floor')

    // netEv 5.5% -> p = 1.055 / 2 = 0.5275
    const row55 = makeRow({
      stat: 'receptions',
      books: baseBooks,
      n_fresh_ref: 5,
      best_over: { book: 'draftkings', american: 100, p_cons_loo: 0.5275, disp_loo: 0.03, n_ref_loo: 4 },
    })
    const g55 = groupLadderRows([row55], nowMs)
    expect(g55[0].rungs[0].over!.verified).toBe(true)
    expect(g55[0].rungs[0].over!.gateFail).toBeNull()
  })
})

describe('4. Prices-only', () => {
  it('rung with n_ref 1 is thin, and n_ref 3 without verified is market', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const rowThin = makeRow({
      n_ref: 1,
      books: [B('draftkings', 100, -120)],
    })
    const gThin = groupLadderRows([rowThin], nowMs)
    expect(gThin[0].rungs[0].state).toBe('thin')

    const rowMarket = makeRow({
      n_ref: 3,
      books: [B('draftkings', 100, -120), B('fanduel', -110, -110), B('betmgm', -110, -110)],
    })
    const gMarket = groupLadderRows([rowMarket], nowMs)
    expect(gMarket[0].rungs[0].state).toBe('market')
  })
})

describe('5. Milestone', () => {
  it('anytime_td group is milestone kind, never verified, and calculates premium with >= 4 books', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const books4 = [
      B('draftkings', 165, null),
      B('fanduel', 100, null),
      B('betmgm', 100, null),
      B('caesars', 100, null),
    ]
    const row4 = makeRow({
      stat: 'anytime_td',
      books: books4,
    })
    const g4 = groupLadderRows([row4], nowMs)
    expect(g4[0].kind).toBe('milestone')
    expect(g4[0].verified).toBe(false)
    expect(g4[0].rungs[0].premiumPct).toBeCloseTo(((2.65 / 2.0) - 1) * 100, 2)
    expect(g4[0].premium).toBe(true)

    // With only 3 books
    const row3 = makeRow({
      stat: 'anytime_td',
      books: books4.slice(0, 3),
    })
    const g3 = groupLadderRows([row3], nowMs)
    expect(g3[0].rungs[0].premiumPct).toBeNull()
    expect(g3[0].premium).toBe(false)
  })
})

describe('6. Caps', () => {
  it('a book with over_cap true is excluded from premium math and counted in capsHidden', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const books = [
      B('draftkings', 165, null),
      B('fanduel', 100, null),
      B('betmgm', 100, null),
      B('caesars', 10000, null, { over_cap: true }),
    ]
    const row = makeRow({
      stat: 'anytime_td',
      books,
    })
    const g = groupLadderRows([row], nowMs)
    expect(g[0].rungs[0].capsHidden).toBe(1)
    expect(g[0].rungs[0].premiumPct).toBeNull()
  })
})

describe('7. Feed', () => {
  it('filters by segment and action-grade status, and sorts verified first within game', () => {
    const makeGroup = (overrides: Partial<LadderGroup>): LadderGroup => ({
      key: 'key',
      gameKey: 'g1',
      commence: '2026-11-20T20:00:00Z',
      away: 'KC',
      home: 'BUF',
      player: 'Player',
      playerNorm: 'player',
      stat: 'rec_yds',
      category: 'yardage',
      kind: 'curves',
      rungs: [],
      modalOpen: null,
      modalNow: null,
      grpBooksAny: 4,
      grpBooks2sided: 4,
      verified: false,
      premium: false,
      state: 'market',
      defaultRung: 0,
      ...overrides,
    })

    const grpCurvesVerified = makeGroup({
      key: 'g1|p1|rec_yds',
      player: 'Aaron',
      kind: 'curves',
      verified: true,
      commence: '2026-11-20T20:00:00Z',
    })
    const grpCurvesUnverified = makeGroup({
      key: 'g1|p2|rec_yds',
      player: 'Bob',
      kind: 'curves',
      verified: false,
      commence: '2026-11-20T20:00:00Z',
    })
    const grpMilestonePremium = makeGroup({
      key: 'g1|p3|anytime_td',
      player: 'Charlie',
      stat: 'anytime_td',
      category: 'milestone',
      kind: 'milestone',
      verified: false,
      premium: true,
      commence: '2026-11-20T20:00:00Z',
    })
    const grpMilestoneNotPremium = makeGroup({
      key: 'g1|p4|anytime_td',
      player: 'Dave',
      stat: 'anytime_td',
      category: 'milestone',
      kind: 'milestone',
      verified: false,
      premium: false,
      commence: '2026-11-20T20:00:00Z',
    })
    const grpGame2Verified = makeGroup({
      key: 'g2|p5|rush_yds',
      player: 'Evan',
      stat: 'rush_yds',
      kind: 'curves',
      verified: true,
      commence: '2026-11-20T23:00:00Z',
    })

    const allGroups = [
      grpCurvesUnverified,
      grpMilestoneNotPremium,
      grpGame2Verified,
      grpCurvesVerified,
      grpMilestonePremium,
    ]

    // showAll: false -> only verified curves and premium milestones
    const defaultFeed = selectLadderFeed(allGroups, 'all', false)
    expect(defaultFeed).toHaveLength(3)
    expect(defaultFeed.map(g => g.key)).toEqual([
      grpCurvesVerified.key,
      grpMilestonePremium.key,
      grpGame2Verified.key,
    ])

    // showAll: true -> all groups in segment
    const allFeed = selectLadderFeed(allGroups, 'all', true)
    expect(allFeed).toHaveLength(5)

    // segment 'curves' excludes milestone
    const curvesOnly = selectLadderFeed(allGroups, 'curves', true)
    expect(curvesOnly.every(g => g.kind === 'curves')).toBe(true)
    expect(curvesOnly).toHaveLength(3)

    // sort pins verified first within the same game:
    const game1Curves = selectLadderFeed([grpCurvesUnverified, grpCurvesVerified], 'curves', true)
    expect(game1Curves[0].verified).toBe(true)
    expect(game1Curves[1].verified).toBe(false)
  })
})

describe('8. flatRung', () => {
  it('marks flatRung when a rung best over equals the previous rung best over', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const row1 = makeRow({
      line: 40.5,
      books: [B('draftkings', 150, -180), B('fanduel', 140, -170)],
    })
    const row2 = makeRow({
      line: 50.5,
      books: [B('draftkings', 150, -180), B('fanduel', 130, -160)],
    })
    const row3 = makeRow({
      line: 60.5,
      books: [B('draftkings', 180, -220), B('fanduel', 170, -210)],
    })
    const groups = groupLadderRows([row1, row2, row3], nowMs)
    expect(groups[0].rungs[0].flatRung).toBe(false)
    expect(groups[0].rungs[1].flatRung).toBe(true)
    expect(groups[0].rungs[2].flatRung).toBe(false)
  })
})

describe('9. ladderInlineAllowed', () => {
  it('allows inline for ev and outliers but blocks for longshots', () => {
    expect(ladderInlineAllowed('longshots')).toBe(false)
    expect(ladderInlineAllowed('ev')).toBe(true)
    expect(ladderInlineAllowed('outliers')).toBe(true)
  })
})

describe('10. Grey band', () => {
  it('marks staleBand grey at 150 min lag, stale at 45 min, and fresh otherwise', () => {
    const nowMs = Date.parse('2026-11-20T14:00:00Z')
    const freshestTs = '2026-11-20T13:00:00Z'
    const lag150Ts = new Date(Date.parse(freshestTs) - 150 * 60000).toISOString()
    const lag45Ts = new Date(Date.parse(freshestTs) - 45 * 60000).toISOString()
    const lag10Ts = new Date(Date.parse(freshestTs) - 10 * 60000).toISOString()

    const row = makeRow({
      books: [
        B('bFreshest', -110, -110, { ts: freshestTs }),
        B('bGrey', -110, -110, { ts: lag150Ts }),
        B('bStale', -110, -110, { ts: lag45Ts }),
        B('bFresh', -110, -110, { ts: lag10Ts }),
      ],
    })
    const groups = groupLadderRows([row], nowMs)
    const bars = groups[0].rungs[0].books
    expect(bars.find(b => b.book === 'bGrey')!.staleBand).toBe('grey')
    expect(bars.find(b => b.book === 'bStale')!.staleBand).toBe('stale')
    expect(bars.find(b => b.book === 'bFresh')!.staleBand).toBe('fresh')
    expect(bars.find(b => b.book === 'bFreshest')!.staleBand).toBe('fresh')
  })
})

describe('ladderGroupFor', () => {
  it('returns single group or null for specified player and game', () => {
    const nowMs = Date.parse('2026-11-20T12:00:00Z')
    const row = makeRow({
      game_key: 'g1',
      player_norm: 'target_player',
      stat: 'rec_yds',
    })
    const found = ladderGroupFor([row], 'g1', 'target_player', 'rec_yds', nowMs)
    expect(found).not.toBeNull()
    expect(found!.playerNorm).toBe('target_player')

    const notFound = ladderGroupFor([row], 'g1', 'other_player', 'rec_yds', nowMs)
    expect(notFound).toBeNull()
  })
})
