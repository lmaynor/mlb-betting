/** @jest-environment node */
/**
 * T-4 player-entity search (Decision 22): a name resolves to that player's props on today's Prop
 * Research universe, each prop carrying its source and the cockpit row id the deep link selects.
 */
import { GET } from '@/app/api/player-search/route'
import { loadCockpit } from '@/lib/cockpit-loader'
import type { CockpitFeed, CockpitRow } from '@/lib/types'

jest.mock('@/lib/cockpit-loader', () => ({ loadCockpit: jest.fn() }))
jest.mock('@/lib/auth', () => ({ getViewer: jest.fn(async () => ({})) }))
jest.mock('@/lib/access-feeds', () => ({ limitCockpit: (rows: CockpitRow[]) => ({ rows, locked: 0 }) }))

const row = (over: Partial<CockpitRow>): CockpitRow => ({
  id: 'x', sport: 'nfl', source: 'model', shadow: false, player: 'Test Receiver', player_id: null, team: 'IND',
  matchup: 'IND @ WAS', commence: null, mins_to_kick: 120, stat: 'rec_yds', side: 'OVER', line: 63.5,
  book: 'fanduel', american: 115, decimal: 2.15, ev: 0.07, p_fair: 0.52, n_books: 5, flag: false,
  reason: null, all_books: [], recentForm: null, ...over,
} as CockpitRow)

const feed = {
  rows: [
    row({ id: 'proj|nfl|g1|test receiver|rec_yds', source: 'model' }),
    row({ id: 'sweep|9', source: 'ev_alert', stat: 'receptions', line: 4.5 }),
    row({ id: 'proj|nfl|g1|other guy|rec_yds', player: 'Other Guy' }),
  ],
} as unknown as CockpitFeed

const req = (q: string) => ({ nextUrl: new URL(`http://localhost/api/player-search?q=${q}`) }) as Parameters<typeof GET>[0]

describe('player search (T-4)', () => {
  beforeEach(() => { (loadCockpit as jest.Mock).mockResolvedValue(feed) })

  it('resolves a name to every prop for that player, with source labels and row ids', async () => {
    const res = await GET(req('receiv'))
    const data = await res.json()
    expect(data.players).toHaveLength(1)
    const p = data.players[0]
    expect(p.player).toBe('Test Receiver')
    expect(p.props).toHaveLength(2)
    const bySource = Object.fromEntries(p.props.map((x: { source: string; id: string }) => [x.source, x.id]))
    expect(bySource.model).toBe('proj|nfl|g1|test receiver|rec_yds')
    expect(bySource.sweep).toBe('sweep|9')
    expect(p.props[0].label).toContain('Over 63.5')
  })

  it('matches case-insensitively and needs two characters', async () => {
    const res = await GET(req('TEST'))
    expect((await res.json()).players).toHaveLength(1)
    const short = await GET(req('t'))
    expect((await short.json()).players).toEqual([])
  })

  it('returns empty when nobody matches', async () => {
    const res = await GET(req('zzzzz'))
    expect((await res.json()).players).toEqual([])
  })
})
