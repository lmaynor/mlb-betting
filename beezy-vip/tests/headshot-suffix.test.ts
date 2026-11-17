import { playerIdByName, stripSuffixSlug } from '@/lib/sport-assets.server'

describe('stripSuffixSlug', () => {
  it('strips common suffix tokens while preserving base name', () => {
    expect(stripSuffixSlug('wendell_carter_jr')).toBe('wendell_carter')
    expect(stripSuffixSlug('gary_payton_ii')).toBe('gary_payton')
    expect(stripSuffixSlug('sam_hauser')).toBe('sam_hauser')
  })
})

describe('playerIdByName suffix resolution', () => {
  it('resolves Wendell Carter Jr. and Wendell Carter to the same non-null id', () => {
    const withSuffix = playerIdByName('nba', 'Wendell Carter Jr.', 'Orlando Magic')
    const withoutSuffix = playerIdByName('nba', 'Wendell Carter', 'ORL')

    expect(withSuffix).not.toBeNull()
    expect(withSuffix).toBe(withoutSuffix)
  })

  it('resolves Gary Payton II and Gary Payton to the same non-null id', () => {
    const withSuffix = playerIdByName('nba', 'Gary Payton II', 'Golden State Warriors')
    const withoutSuffix = playerIdByName('nba', 'Gary Payton', 'GSW')

    expect(withSuffix).not.toBeNull()
    expect(withSuffix).toBe(withoutSuffix)
  })

  it('returns null for Kenyon Martin Jr. without matching another player', () => {
    expect(playerIdByName('nba', 'Kenyon Martin Jr.', 'Minnesota Timberwolves')).toBeNull()
  })

  it('returns null for a nonsense name', () => {
    expect(playerIdByName('nba', 'Nonexistent Fakeplayer Zzz', 'BOS')).toBeNull()
  })
})
