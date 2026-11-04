import { isJunkBook, isOffshore, suppressJunkBooks } from '../lib/offshore'

const q = (book: string) => ({ book, over: -110, under: -110 })
const feed = () => ({
  sport: 'nhl',
  rows: [
    { key: 'a', books: [q('draftkings'), q('bovada'), q('pinnacle'), q('betonlineag')] },
    { key: 'only-junk', books: [q('bovada'), q('mybookie')] },
    { key: 'clean', books: [q('draftkings'), q('fanduel')] },
  ],
  books: ['draftkings', 'bovada', 'pinnacle', 'fanduel'],
  offshore_books: ['bovada', 'pinnacle'],
})

describe('junk offshore books', () => {
  it('bovada and friends are junk; pinnacle is offshore but a kept sharp reference', () => {
    expect(isJunkBook('bovada')).toBe(true)
    expect(isJunkBook(' BetOnlineAG ')).toBe(true)
    expect(isJunkBook('pinnacle')).toBe(false)
    expect(isOffshore('pinnacle')).toBe(true)   // still never a place to bet
    expect(isJunkBook('draftkings')).toBe(false)
    expect(isJunkBook(null)).toBe(false)
  })
  it('drops junk quotes, keeps pinnacle, and drops a row that only had junk', () => {
    const out = suppressJunkBooks(feed())
    expect(out.rows.map(r => r.key)).toEqual(['a', 'clean'])
    expect(out.rows[0].books.map(b => b.book)).toEqual(['draftkings', 'pinnacle'])
    expect(out.books).toEqual(['draftkings', 'pinnacle', 'fanduel'])
    expect(out.offshore_books).toEqual(['pinnacle'])
  })
  it('does not mutate its input', () => {
    const f = feed()
    suppressJunkBooks(f)
    expect(f.rows).toHaveLength(3)
    expect(f.rows[0].books).toHaveLength(4)
  })
  it('a clean feed comes back as the same object', () => {
    const f = { rows: [{ books: [q('draftkings'), q('pinnacle')] }], books: ['draftkings', 'pinnacle'], offshore_books: ['pinnacle'] }
    expect(suppressJunkBooks(f)).toBe(f)
  })
  it('a malformed feed passes through untouched', () => {
    const bad = { rows: null } as unknown as { rows: { books: { book: string }[] }[] }
    expect(suppressJunkBooks(bad)).toBe(bad)
  })
})
