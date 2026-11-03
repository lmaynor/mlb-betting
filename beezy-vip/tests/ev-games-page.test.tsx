import { isMlbOffseason } from '@/lib/dates'

describe('isMlbOffseason (Central, Nov 5 - Mar 19)', () => {
  const at = (iso: string) => isMlbOffseason(new Date(iso))
  it('is true through the winter and false in season', () => {
    expect(at('2026-11-05T18:00:00Z')).toBe(true)
    expect(at('2026-12-25T18:00:00Z')).toBe(true)
    expect(at('2027-03-19T18:00:00Z')).toBe(true)
  })
  it('is false for the World Series window and from opening day on', () => {
    expect(at('2026-11-04T18:00:00Z')).toBe(false)
    expect(at('2026-10-20T18:00:00Z')).toBe(false)
    expect(at('2027-03-20T18:00:00Z')).toBe(false)
    expect(at('2027-07-01T18:00:00Z')).toBe(false)
  })
})
