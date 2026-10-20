import fs from 'fs'
import path from 'path'
import { SPORT_SLUGS, type SportSlug } from '../lib/sports'
import { BOOK_META, bookDisplay } from '../lib/tokens'
import { abbrevName, bookPostName, buildSheets, isEvBasis, LINK_REPLY, POST_LIMIT, postLength, shortMarket, SHEET_SIZE, type SheetPick } from '../lib/cheat-sheet-post'

const pick = (o: Partial<SheetPick>): SheetPick => ({
  sport: 'nba', who: 'Jalen Williams', what: 'Under 4.5 Rebounds', odds: -110, book: 'espnbet', edge: 0.146, basis: 'confluence', ...o,
})
const BASKETBALL = String.fromCodePoint(0x1f3c0)

// the owner's own five-line example (full names)
const NBA5 = [
  pick({}),
  pick({ who: 'Shai Gilgeous-Alexander', what: 'Under 1.5 3-Pointers', odds: 100, book: 'betmgm', edge: 0.143 }),
  pick({ who: 'Derrick White', what: 'Under 23.5 Pts+Reb+Ast', odds: -108, book: 'novig', edge: 0.15 }),
  pick({ who: 'Karl-Anthony Towns', what: 'Under 4.5 Assists', odds: 114, book: 'betrivers', edge: 0.146 }),
  pick({ who: 'Daniss Jenkins', what: 'Over 3.5 Assists', odds: 156, book: 'prophetx', edge: 0.09 }),
]
const only = (s: SportSlug, picks: SheetPick[]) => ({ [s]: picks }) as Partial<Record<SportSlug, SheetPick[]>>

describe('shortMarket / names / books', () => {
  it.each([
    ['Under 4.5 Rebounds', 'U 4.5 Reb'], ['Over 1.5 3-Pointers', 'O 1.5 3PM'], ['Under 23.5 Pts+Reb+Ast', 'U 23.5 PRA'],
    ['Over 2.5 Shots on Goal', 'O 2.5 SOG'], ['Over 7.5 Ks', 'O 7.5 Ks'], ['Over 74.5 Rec Yds', 'O 74.5 Rec Yds'],
    ['Over 1.5 Total Bases', 'O 1.5 TB'], ['Over 0.5 Anytime TD', 'O 0.5 Anytime TD'], ['Some Unlisted Market', 'Some Unlisted Market'],
  ])('%s -> %s', (what, expected) => expect(shortMarket(what)).toBe(expected))

  it.each([
    ['Jalen Williams', 'J. Williams'], ['Karl-Anthony Towns', 'K. Towns'], ['Shai Gilgeous-Alexander', 'S. Gilgeous-Alexander'],
    ['P.J. Washington Jr.', 'P.J. Washington Jr.'], ['Nets', 'Nets'], ['  Derrick   White ', 'D. White'],
  ])('abbrevName %s -> %s', (who, expected) => expect(abbrevName(who)).toBe(expected))

  it.each([['draftkings', 'DK'], ['fanduel', 'FD'], ['betmgm', 'MGM'], ['caesars', 'CZR'], ['novig', 'Novig'], ['betrivers', 'BetRivers'], ['hardrock', 'Hard Rock']])(
    'book %s -> %s', (book, expected) => expect(bookPostName(book)).toBe(expected))
  it('shows ESPN BET as theScore Bet whichever key the feed uses', () => {
    expect(bookPostName('espnbet')).toBe('theScore Bet')
    expect(bookPostName('thescore')).toBe('theScore Bet')
    expect(bookDisplay('espnbet').label).toBe('theScore Bet')
    expect(JSON.stringify(Object.values(BOOK_META).map(b => b.label))).not.toMatch(/ESPN/i)
    expect(bookPostName(null)).toBe('')
  })
})

describe('hard 280 cap', () => {
  it('never exceeds 280: the owner\'s five-line example (310 chars) drops to the four lines that fit, full names kept', () => {
    const [sheet] = buildSheets(NBA5, only('nba', NBA5), 'Tuesday')
    expect(postLength(sheet.text)).toBeLessThanOrEqual(POST_LIMIT)
    expect(sheet.text).toBe([
      `Tuesday +EV NBA Cheat Sheet ${BASKETBALL}`,
      '',
      '1. Jalen Williams U 4.5 Reb -110 theScore Bet | +14.6% EV',
      '2. Shai Gilgeous-Alexander U 1.5 3PM +100 MGM | +14.3% EV',
      '3. Derrick White U 23.5 PRA -108 Novig | +15.0% EV',
      '4. Karl-Anthony Towns U 4.5 Ast +114 BetRivers | +14.6% EV',
    ].join('\n'))
    expect([sheet.lines, sheet.offered]).toEqual([4, 5])
  })

  it('uses first initials before it drops a line, when initials are enough to keep every pick', () => {
    const picks = [
      pick({ who: 'Jalen Williams', book: 'draftkings' }), pick({ who: 'Derrick White', book: 'fanduel', odds: -108 }),
      pick({ who: 'Karl-Anthony Towns', book: 'betrivers' }), pick({ who: 'Daniss Jenkins', book: 'novig' }), pick({ who: 'Tyrese Haliburton', book: 'prophetx' }),
    ]
    const full = buildSheets(picks, only('nba', picks), 'Wednesday')[0]
    expect(postLength(full.text)).toBeLessThanOrEqual(POST_LIMIT)
    const longNames = picks.map(p => ({ ...p, who: p.who + ' Wilkinson-Featherstone' }))
    const sheet = buildSheets(longNames, only('nba', longNames), 'Wednesday')[0]
    expect(postLength(sheet.text)).toBeLessThanOrEqual(POST_LIMIT)
    if (sheet.lines === SHEET_SIZE) expect(sheet.text).toMatch(/1\. J\. Williams/)        // all five kept by shortening names
  })

  it('holds for every sheet over many shapes, including absurd names, never fewer than one line', () => {
    const books = ['draftkings', 'fanduel', 'betmgm', 'espnbet', 'novig', 'prophetx', 'betrivers', 'hardrock', 'bet365', null]
    const whats = ['Under 4.5 Rebounds', 'Over 23.5 Pts+Reb+Ast', 'Over 2.5 Shots on Goal', 'Over 74.5 Rec Yds', 'Over 0.5 Anytime TD']
    let seed = 7
    const rnd = (n: number) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n }
    for (let i = 0; i < 300; i++) {
      const sport = SPORT_SLUGS[rnd(SPORT_SLUGS.length)]
      const picks = Array.from({ length: 1 + rnd(7) }, () => pick({
        sport, who: ['A', 'Jalen Williams', 'Shai Gilgeous-Alexander', 'X'.repeat(1 + rnd(120))][rnd(4)] + (rnd(2) ? ' Jr.' : ''),
        what: whats[rnd(whats.length)], odds: [-110, 100, 156, -1050][rnd(4)], book: books[rnd(books.length)], edge: 0.01 + rnd(150) / 1000,
        basis: rnd(2) ? 'confluence' : 'projection',
      }))
      for (const s of buildSheets(picks, { [sport]: picks }, 'Wednesday')) {
        expect(postLength(s.text)).toBeLessThanOrEqual(POST_LIMIT)
        expect(s.lines).toBeGreaterThanOrEqual(1)
        expect(s.lines).toBeLessThanOrEqual(s.offered)
      }
    }
    const absurd = [pick({ who: 'Z'.repeat(400) })]
    expect(postLength(buildSheets(absurd, only('nba', absurd), 'Wednesday')[0].text)).toBeLessThanOrEqual(POST_LIMIT)
  })

  it('counts an emoji as two characters, like X', () => {
    expect(postLength(BASKETBALL)).toBe(2)
  })
})

describe('the sheets are built from the lists the card shows', () => {
  const nhl = (who: string) => pick({ sport: 'nhl', who, what: 'Over 2.5 Shots on Goal', odds: 120, book: 'draftkings', edge: 0.08 })
  const NHL = ['Pavel Zacha', 'Kyle Connor', 'Filip Chytil'].map(nhl)
  const NBA = NBA5.slice(0, 3)
  const ALL = [NBA[0], NHL[0], NBA[1], NHL[1], NBA[2], NHL[2]]            // the card's "All" order

  it('OVERALL lists exactly the card\'s All picks in the card\'s order; each sport box lists that sport\'s chip picks', () => {
    const sheets = buildSheets(ALL, { nhl: NHL, nba: NBA }, 'Tuesday')
    expect(sheets.map(s => s.id)).toEqual(['all', ...SPORT_SLUGS.filter(s => s === 'nhl' || s === 'nba')])
    // the same picks in the same order; a name may be shortened to fit 280 ("J. Williams"), never swapped for another pick
    const shown = (t: string) => t.split('\n').slice(2).map(l => l.replace(/^\d+\.\s+(?:[A-Z]{3}\s+)?/, '').split(/ [OU] \d/)[0])
    const same = (sheet: { text: string; lines: number; offered: number }, expected: SheetPick[]) => {
      const got = shown(sheet.text)
      expect(got).toHaveLength(sheet.lines)
      expect(sheet.offered).toBe(expected.length)
      expect(sheet.lines).toBeLessThanOrEqual(expected.length)                // a shortfall is only ever the 280 cap, and it is reported
      got.forEach((g, i) => expect([expected[i].who, abbrevName(expected[i].who)]).toContain(g))   // always a prefix of the card's order
    }
    same(sheets[0], ALL.slice(0, SHEET_SIZE))                            // the card's "All" chip shows its first five
    same(sheets.find(s => s.id === 'nhl')!, NHL)
    same(sheets.find(s => s.id === 'nba')!, NBA)
    expect(sheets[0].text).toMatch(/2\. NHL (Pavel Zacha|P\. Zacha) O 2\.5 SOG \+120 DK \| \+8\.0% EV/)      // overall tags the sport
  })

  it('a sport that is nowhere in the overall list still gets its own box (the "NHL is not wired" case)', () => {
    const sheets = buildSheets(NBA5, { nba: NBA5, nhl: NHL }, 'Tuesday')
    expect(sheets.map(s => s.id)).toContain('nhl')
    expect(sheets.find(s => s.id === 'nhl')!.text).toContain('Pavel Zacha')
    expect(sheets[0].text).not.toContain('Zacha')                                          // and the overall list is the card's list
  })

  it('with picks in a single sport it returns only that sport\'s sheet; with none, nothing', () => {
    expect(buildSheets(NBA, only('nba', NBA), 'Monday').map(s => s.id)).toEqual(['nba'])
    expect(buildSheets([], {}, 'Monday')).toEqual([])
  })
})

describe('honest labels', () => {
  it('says EV only for a real +EV price and edge for a model-only pick; "+EV" in the header only when every line is EV', () => {
    const ev = pick({ who: 'Real Ev', basis: 'confluence', edge: 0.079 })
    const alert = pick({ who: 'Tracked Alert', basis: 'ev', edge: 0.05 })
    const model = pick({ who: 'Model Only', basis: 'projection', edge: 0.146 })
    const mlb = pick({ who: 'Model Pick', basis: 'model', edge: 0.06 })
    expect([ev, alert].every(isEvBasis)).toBe(true)
    expect([model, mlb].some(isEvBasis)).toBe(false)

    const allEv = buildSheets([ev, alert], only('nba', [ev, alert]), 'Monday')[0]
    expect(allEv.text.split('\n')[0]).toBe(`Monday +EV NBA Cheat Sheet ${BASKETBALL}`)
    expect(allEv.text).toContain('Real Ev U 4.5 Reb -110 theScore Bet | +7.9% EV')

    const mixed = buildSheets([ev, model], only('nba', [ev, model]), 'Monday')[0]
    expect(mixed.text.split('\n')[0]).toBe(`Monday NBA Cheat Sheet ${BASKETBALL}`)           // no "+EV" claim over a model-only line
    expect(mixed.text).toContain('Real Ev U 4.5 Reb -110 theScore Bet | +7.9% EV')
    expect(mixed.text).toContain('Model Only U 4.5 Reb -110 theScore Bet | +14.6% edge')
    expect(mixed.text).not.toMatch(/Model Only[^\n]*EV/)
  })

  it('omits the percentage rather than print a missing or non-positive one, and drops a missing book cleanly', () => {
    const picks = [pick({ who: 'No Edge', edge: null }), pick({ who: 'Negative', edge: -0.02 }), pick({ who: 'No Book', book: null, edge: 0.1 })]
    const t = buildSheets(picks, only('nba', picks), 'Monday')[0].text
    expect(t).toContain('1. No Edge U 4.5 Reb -110 theScore Bet\n')
    expect(t).toContain('2. Negative U 4.5 Reb -110 theScore Bet\n')
    expect(t).toContain('3. No Book U 4.5 Reb -110 | +10.0% EV')
  })
})

describe('posts and the link reply', () => {
  it('have no link in any post and are ASCII apart from the sport emoji', () => {
    for (const s of buildSheets(NBA5, { nba: NBA5, nhl: [pick({ sport: 'nhl' })] }, 'Tuesday')) {
      expect(s.text).not.toMatch(/https?:\/\/|www\.|beezy\.fyi/i)
      expect([...s.text].filter(c => c.charCodeAt(0) > 127 && !/\p{Extended_Pictographic}/u.test(c))).toEqual([])
    }
  })
  it('LINK_REPLY is the owner\'s reply with the one site link, and fits 280', () => {
    expect(LINK_REPLY).toBe("For today's full slate of +EV bets and model projections, check out https://beezy.fyi and join our Discord.")
    expect((LINK_REPLY.match(/https?:\/\//g) ?? []).length).toBe(1)
    expect(postLength(LINK_REPLY)).toBeLessThanOrEqual(POST_LIMIT)
  })
  it('the source file is ASCII only (emoji come from code points)', () => {
    const src = fs.readFileSync(path.resolve(__dirname, '../lib/cheat-sheet-post.ts'), 'utf8')
    expect([...src].filter(c => c.charCodeAt(0) > 127)).toEqual([])
  })
})
