/** @jest-environment node */
import { apiGetPicks, apiGetSportProjections } from '@/lib/betting-api'
import { loadLedgerBets } from '../lib/top-picks-data'
import type { Bet } from '../lib/types'

jest.mock('@/lib/betting-api', () => ({
  apiGetPicks: jest.fn(),
  apiGetSportProjections: jest.fn(),
}))

const mockApiGetPicks = apiGetPicks as unknown as jest.Mock

const makeBet = (id: number, extra: Record<string, unknown> = {}): Bet => ({
  id,
  system: 'PROPS',
  game_date: '2026-11-15',
  created_at: '2026-11-15T12:00:00Z',
  ...extra,
} as any)

const makeBets = (startId: number, count: number, extra: Record<string, unknown> = {}): Bet[] =>
  Array.from({ length: count }, (_, i) => makeBet(startId + i, extra))

beforeEach(() => {
  jest.clearAllMocks()
})

describe('loadLedgerBets', () => {
  it('pages until a short page arrives and returns all accumulated rows (completeness)', async () => {
    const mlbBets = [
      makeBet(1, { system: 'K', sport: 'mlb' }),
      makeBet(2, { system: 'K', sport: 'mlb' }),
    ]
    const propsPage0 = makeBets(100, 200, { system: 'PROPS', sport: 'nba' })
    const propsPage1 = makeBets(300, 200, { system: 'PROPS', sport: 'nfl' })
    const propsPage2 = makeBets(500, 37, { system: 'PROPS', sport: 'nhl' })

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        if (params.offset === 0) return propsPage0
        if (params.offset === 200) return propsPage1
        if (params.offset === 400) return propsPage2
        return []
      }
      // MLB query returns a short page immediately
      return mlbBets
    })

    const result = await loadLedgerBets('2026-11-15')

    // Returns all 437 PROPS bets plus the 2 MLB rows
    expect(result).toHaveLength(439)
    expect(result).toEqual([...mlbBets, ...propsPage0, ...propsPage1, ...propsPage2])

    // apiGetPicks was called with offsets 0, 200, 400 for the PROPS query
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 0,
    })
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 200,
    })
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 400,
    })

    // MLB query was called once with offset 0
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      limit: 200,
      offset: 0,
    })
  })

  it('stops after one call per query when the first page is short', async () => {
    const mlbBets = [makeBet(10, { system: 'K', sport: 'mlb' })]
    const propsBets = [makeBet(20, { system: 'PROPS', sport: 'nba' }), makeBet(21, { system: 'PROPS', sport: 'nhl' })]

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') return propsBets
      return mlbBets
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toEqual([...mlbBets, ...propsBets])
    expect(mockApiGetPicks).toHaveBeenCalledTimes(2)
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      limit: 200,
      offset: 0,
    })
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 0,
    })
  })

  it('contributes [] when the first page throws, while the other query rows are still returned', async () => {
    const mlbBets = [makeBet(1, { system: 'K', sport: 'mlb' })]

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        throw new Error('PROPS backend failure')
      }
      return mlbBets
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toEqual(mlbBets)
  })

  it('contributes [] when the MLB query throws on first page, while PROPS rows are still returned', async () => {
    const propsBets = [makeBet(2, { system: 'PROPS', sport: 'nba' })]

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        return propsBets
      }
      throw new Error('MLB backend timeout')
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toEqual(propsBets)
  })

  it('keeps rows accumulated so far and stops paging if a later page throws', async () => {
    const propsPage0 = makeBets(1, 200, { system: 'PROPS', sport: 'nba' })

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        if (params.offset === 0) return propsPage0
        throw new Error('Later page network error')
      }
      return []
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toHaveLength(200)
    expect(result).toEqual(propsPage0)
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 0,
    })
    expect(mockApiGetPicks).toHaveBeenCalledWith({
      date: '2026-11-15',
      system: 'PROPS',
      sport: 'all',
      limit: 200,
      offset: 200,
    })
  })

  it('dedupes rows with duplicate ids across page boundaries', async () => {
    const propsPage0 = makeBets(1, 200, { system: 'PROPS', sport: 'nba' })
    const propsPage1 = [
      makeBet(200, { system: 'PROPS', sport: 'nba' }),
      makeBet(201, { system: 'PROPS', sport: 'nba' }),
      makeBet(202, { system: 'PROPS', sport: 'nba' }),
    ]

    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        if (params.offset === 0) return propsPage0
        if (params.offset === 200) return propsPage1
        return []
      }
      return []
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toHaveLength(202)
    const ids = result.map(b => b.id)
    expect(ids.filter(id => id === 200)).toHaveLength(1)
    expect(ids).toEqual(Array.from({ length: 202 }, (_, i) => i + 1))
  })

  it('stops paging after 50 pages (runaway guard safety bound)', async () => {
    mockApiGetPicks.mockImplementation(async (params: any) => {
      if (params.system === 'PROPS') {
        const offset = params.offset ?? 0
        return makeBets(offset + 1, 200, { system: 'PROPS' })
      }
      return []
    })

    const result = await loadLedgerBets('2026-11-15')

    expect(result).toHaveLength(10000)
    const propsCalls = mockApiGetPicks.mock.calls.filter(([params]) => params.system === 'PROPS')
    expect(propsCalls).toHaveLength(50)
  })
})
