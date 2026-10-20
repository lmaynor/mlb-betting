import fs from 'fs'
import path from 'path'
import { SPORT_SLUGS } from '../lib/sports'
import { BOOK_META, bookDisplay } from '../lib/tokens'
import { bookPostName, buildSheets, isEvPick, LINK_REPLY, POST_LIMIT, postLength, shortMarket, SHEET_SIZE, type SheetPick } from '../lib/cheat-sheet-post'

const pick = (o: Partial<SheetPick>): SheetPick => ({
  sport: 'nba', who: 'Jalen Williams', what: 'Under 4.5 Rebounds', odds: -110, book: 'espnbet', edge: 0.146, basis: 'ev', ...o,
})

const NBA = [
  pick({}),
  pick({ who: 'Shai Gilgeous-Alexander', what: 'Under 1.5 3-Pointers', odds: 100, book: 'betmgm', edge: 0.143 }),
  pick({ who: 'Derrick White', what: 'Under 23.5 Pts+Reb+Ast', odds: -108, book: 'novig', edge: 0.15 }),
  pick({ who: 'Karl-Anthony Towns', what: 'Under 4.5 Assists', odds: 114, book: 'betrivers', edge: 0.146 }),
  pick({ who: 'Daniss Jenkins', what: 'Over 3.5 Assists', odds: 156, book: 'prophetx', edge: 0.09 }),
]
const BASKETBALL = String.fromCodePoint(0x1f3c0)

describe('shortMarket', () => {
  it.each([
    ['Under 4.5 Rebounds', 'U 4.5 Reb'],
    ['Over 1.5 3-Pointers', 'O 1.5 3PM'],
    ['Under 23.5 Pts+Reb+Ast', 'U 23.5 PRA'],
    ['Over 2.5 Shots on Goal', 'O 2.5 SOG'],
    ['Over 7.5 Ks', 'O 7.5 Ks'],
    ['Over 74.5 Rec Yds', 'O 74.5 Rec Yds'],
    ['Over 1.5 Total Bases', 'O 1.5 TB'],
    ['Over 0.5 Anytime TD', 'O 0.5 Anytime TD'],
    ['Some Unlisted Market', 'Some Unlisted Market'],
  ])('%s -> %s', (what, expected) => expect(shortMarket(what)).toBe(expected))
})

describe('buildSheets', () => {
  it('formats a single-sport sheet exactly like the owner\'s example', () => {
    const [sheet] = buildSheets(NBA, 'Tuesday')
    expect(sheet.text).toBe([
      `Tuesday +EV NBA Cheat Sheet ${BASKETBALL}`,
      '',
      '1. Jalen Williams U 4.5 Reb -110 theScore Bet | +14.6% EV',
      '2. Shai Gilgeous-Alexander U 1.5 3PM +100 MGM | +14.3% EV',
      '3. Derrick White U 23.5 PRA -108 Novig | +15.0% EV',
      '4. Karl-Anthony Towns U 4.5 Ast +114 BetRivers | +14.6% EV',
      '5. Daniss Jenkins O 3.5 Ast +156 ProphetX | +9.0% EV',
    ].join('\n'))
    expect(sheet.lines).toBe(5)
  })

  it('returns an OVERALL sheet first (top 5 across sports, sport-tagged), then one top-5 sheet per sport', () => {
    const nhl = (n: number) => pick({ sport: 'nhl', who: `Skater ${n}`, what: 'Over 2.5 Shots on Goal', odds: 120, book: 'draftkings', edge: 0.08 })
    const picks = [NBA[0], nhl(1), NBA[1], nhl(2), NBA[2], nhl(3), NBA[3], nhl(4), NBA[4], nhl(5), nhl(6)]
    const sheets = buildSheets(picks, 'Tuesday')
    expect(sheets.map(s => s.id)).toEqual(['all', ...SPORT_SLUGS.filter(s => s === 'nhl' || s === 'nba')])   // overall, then registry order
    const all = sheets[0]
    expect(all.text.split('\n')[0]).toBe('Tuesday +EV Cheat Sheet')
    expect(all.lines).toBe(SHEET_SIZE)
    expect(all.text).toContain('1. NBA Jalen Williams U 4.5 Reb -110 theScore Bet | +14.6% EV')
    expect(all.text).toContain('2. NHL Skater 1 O 2.5 SOG +120 DK | +8.0% EV')   // overall keeps the caller's rank order
    const nhlSheet = sheets.find(s => s.id === 'nhl')!
    expect(nhlSheet.text.split('\n')[0]).toContain('Tuesday +EV NHL Cheat Sheet')
    expect(nhlSheet.lines).toBe(SHEET_SIZE)                       // 6 NHL picks in, 5 out
    expect(nhlSheet.text).not.toContain('Skater 6')
    expect(nhlSheet.text).not.toMatch(/\bNHL Skater/)           // per-sport sheets do not tag every line
  })

  it('with only one sport it returns just that sport\'s sheet (no duplicate overall)', () => {
    expect(buildSheets(NBA, 'Tuesday').map(s => s.id)).toEqual(['nba'])
  })

  it('lists only picks whose percentage is a real EV (tracked EV alerts or model+market confluence)', () => {
    const picks = [
      pick({ who: 'Model Only', basis: 'model' }),
      pick({ who: 'Projection Only', basis: 'projection' }),
      pick({ who: 'No Price', odds: null }),
      pick({ who: 'No Edge', edge: null }),
      pick({ who: 'Negative', edge: -0.02 }),
      pick({ who: 'Confluence Guy', basis: 'confluence', edge: 0.079 }),
    ]
    expect(picks.filter(isEvPick).map(p => p.who)).toEqual(['Confluence Guy'])
    const [sheet] = buildSheets(picks, 'Monday')
    expect(sheet.text).toContain('Confluence Guy')
    for (const bad of ['Model Only', 'Projection Only', 'No Price', 'No Edge', 'Negative']) expect(sheet.text).not.toContain(bad)
    expect(buildSheets(picks.slice(0, 5), 'Monday')).toEqual([])
    expect(buildSheets([], 'Monday')).toEqual([])
  })

  it('has no link in any post and uses straight ASCII apart from the sport emoji', () => {
    for (const s of buildSheets([...NBA, pick({ sport: 'nhl' })], 'Tuesday')) {
      expect(s.text).not.toMatch(/https?:\/\/|www\.|beezy\.fyi/i)
      expect([...s.text].filter(c => c.charCodeAt(0) > 127 && !/\p{Extended_Pictographic}/u.test(c))).toEqual([])
    }
  })

  it('reports the length X will count and flags a five-line sheet that is over the 280 limit', () => {
    const [sheet] = buildSheets(NBA, 'Tuesday')
    expect(postLength(sheet.text)).toBeGreaterThan(POST_LIMIT)     // the owner's own 5-line example does not fit 280
    const [short] = buildSheets(NBA.slice(0, 2), 'Tuesday')
    expect(postLength(short.text)).toBeLessThanOrEqual(POST_LIMIT)
  })
})

describe('bookPostName', () => {
  it.each([
    ['draftkings', 'DK'], ['fanduel', 'FD'], ['betmgm', 'MGM'], ['caesars', 'CZR'], ['DraftKings', 'DK'],
  ])('%s -> %s (standard abbreviation)', (book, expected) => expect(bookPostName(book)).toBe(expected))

  it.each([['novig', 'Novig'], ['prophetx', 'ProphetX'], ['betrivers', 'BetRivers'], ['hardrock', 'Hard Rock'], ['bet365', 'bet365'], ['fanatics', 'Fanatics']])(
    '%s keeps its full name (no standard abbreviation, and not the site\'s internal short code)', (book, expected) => expect(bookPostName(book)).toBe(expected))

  it('shows ESPN BET as theScore Bet whichever key the feed uses', () => {
    expect(bookPostName('espnbet')).toBe('theScore Bet')
    expect(bookPostName('thescore')).toBe('theScore Bet')
    expect(bookDisplay('espnbet').label).toBe('theScore Bet')     // the shared site label too, not just the post
    expect(JSON.stringify(Object.values(BOOK_META).map(b => b.label))).not.toMatch(/ESPN/i)
  })

  it('handles a missing or unknown book', () => {
    expect(bookPostName(null)).toBe('')
    expect(bookPostName('somenewbook')).toBe('Somenewbook')
    const [sheet] = buildSheets([{ sport: 'nba', who: 'A B', what: 'Over 1.5 Assists', odds: 100, book: null, edge: 0.1, basis: 'ev' }], 'Monday')
    expect(sheet.text).toContain('1. A B O 1.5 Ast +100 | +10.0% EV')           // no dangling book
  })
})

describe('LINK_REPLY', () => {
  it('is the owner\'s reply with the one site link', () => {
    expect(LINK_REPLY).toBe("For today's full slate of +EV bets and model projections, check out https://beezy.fyi and join our Discord.")
    expect((LINK_REPLY.match(/https?:\/\//g) ?? []).length).toBe(1)
  })
})

describe('cheat-sheet-post.ts source', () => {
  it('is ASCII only (emoji come from code points)', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../lib/cheat-sheet-post.ts'), 'utf8')
    expect([...src].filter(c => c.charCodeAt(0) > 127)).toEqual([])
  })
})
