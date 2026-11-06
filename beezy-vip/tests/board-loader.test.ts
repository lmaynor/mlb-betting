/** @jest-environment node */
/**
 * loadSportBoardForPage is the picks pages' loader: while tier gating is OFF it must never resolve the
 * Clerk session (the access policy ignores the viewer, and the board result is cached instead); the moment
 * ENFORCE_TIERS=1 turns gating on it must load per-viewer through getViewer().
 */
const getViewerMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))
jest.mock('@/lib/betting-api', () => ({
  apiGetPicks: async () => [],
  apiGetTodayAllPredictions: async () => [],
  apiGetTodaySlate: async () => null,
  apiGetEvFeed: async () => null,
  apiGetSportProjections: async () => null,
}))

import { loadSportBoardForPage } from '../lib/board-page-loader'

beforeEach(() => {
  jest.clearAllMocks()
  delete process.env.ENFORCE_TIERS
  getViewerMock.mockResolvedValue({ tier: 'public', sport: null })
})

describe('loadSportBoardForPage', () => {
  it('gating OFF: loads the board without resolving the Clerk session', async () => {
    const board = await loadSportBoardForPage('mlb', { day: 'today', view: 'picks' })
    expect(board.rows).toEqual([])
    expect(getViewerMock).not.toHaveBeenCalled()
  })

  it('gating ON (ENFORCE_TIERS=1): resolves the viewer and loads per-viewer', async () => {
    process.env.ENFORCE_TIERS = '1'
    const board = await loadSportBoardForPage('mlb', { day: 'today', view: 'picks' })
    expect(board.rows).toEqual([])
    expect(getViewerMock).toHaveBeenCalled()
  })
})
