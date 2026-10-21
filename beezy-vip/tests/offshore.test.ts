import { isOffshore, onshoreQuotes } from '@/lib/offshore'

describe('offshore books are never shown', () => {
  it('flags the offshore set case-insensitively and nothing onshore', () => {
    for (const b of ['bovada', 'Bovada', 'PINNACLE', 'betonline', 'mybookie']) expect(isOffshore(b)).toBe(true)
    for (const b of ['draftkings', 'fanduel', 'novig', 'hardrock', null, undefined, '']) expect(isOffshore(b)).toBe(false)
  })
  it('drops offshore quotes from a per-book list', () => {
    const q = [{ book: 'bovada' }, { book: 'fanduel' }, { book: 'pinnacle' }, { book: 'draftkings' }]
    expect(onshoreQuotes(q).map(x => x.book)).toEqual(['fanduel', 'draftkings'])
    expect(onshoreQuotes(null)).toEqual([])
  })
})
