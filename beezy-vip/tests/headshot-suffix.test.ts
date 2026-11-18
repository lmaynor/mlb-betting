import { playerIdByName, stripSuffixSlug } from '@/lib/sport-assets.server'
import { headshotUrlById } from '@/lib/sport-assets'

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

describe('playerIdByName NHL resolution (IA-08 NHL cards)', () => {
  it('resolves an NHL skater by code or full team name to the same id', () => {
    const byCode = playerIdByName('nhl', 'Connor McDavid', 'EDM')
    const byName = playerIdByName('nhl', 'Connor McDavid', 'Edmonton Oilers')
    expect(byCode).toBe(8478402)
    expect(byName).toBe(byCode)
  })

  it('returns null for an NHL player with no map entry, so the card shows the grey silhouette', () => {
    expect(playerIdByName('nhl', 'Nikita Tolopilo', 'VAN')).toBeNull()
  })
})

describe('playerIdByName NFL resolution (IA-08 NFL cards)', () => {
  it('resolves Marvin Harrison Jr. and Marvin Harrison to the same nfl.com image token', () => {
    const withSuffix = playerIdByName('nfl', 'Marvin Harrison Jr.', 'ARI')
    const withoutSuffix = playerIdByName('nfl', 'Marvin Harrison', 'ARI')

    expect(withSuffix).not.toBeNull()
    expect(typeof withSuffix).toBe('string')
    expect(withSuffix).toBe(withoutSuffix)
    expect(headshotUrlById('nfl', withSuffix)).toContain('https://static.www.nfl.com/image/upload/')
  })

  it('returns null for an NFL player with no map entry, so the card shows the grey silhouette', () => {
    expect(playerIdByName('nfl', 'Nonexistent Fakeplayer Zzz', 'HOU')).toBeNull()
    expect(headshotUrlById('nfl', null)).toBeNull()
  })
})
