/**
 * @jest-environment node
 *
 * Finding 555aa15d regression: the dashboard picks page must cut today's card with limitBets (the same policy
 * as /api/picks/today) before rendering. The dashboard layout gate checks only the tier name, so a Single-Sport
 * Pass for another league must see only the free preview here, while All-Access sees the full card.
 */
import type { ReactElement } from 'react'
import type { Bet } from '@/lib/types'
import { siteDateKey } from '@/lib/dates'

const getViewerMock = jest.fn()
let picksFixture: Bet[] = []

jest.mock('@/lib/betting-api', () => ({
  apiGetTodayPicks: async () => picksFixture,
  apiGetStats: async () => ({ overall: { total_bets: '10', win_rate: '55', roi: '4.2', avg_edge: '3' } }),
}))
jest.mock('@/lib/auth', () => ({
  getViewer: () => getViewerMock(),
  getCurrentUser: async () => null,
}))

import DashboardPicksPage from '@/app/dashboard/picks/page'

const TODAY = siteDateKey()
let seq = 0
function bet(over: Partial<Bet>): Bet {
  seq += 1
  return {
    id: seq, system: 'K', game_date: TODAY, game_pk: seq, bet_type: 'K_OVER_5.5', player: `Player ${seq}`,
    away_team: 'BOS', home_team: 'NYY', odds: -110, stake: 25, model_prob: 0.6, market_prob: 0.5,
    edge: 0.05 + seq / 1000, kelly_pct: 0.02, kelly_triggered: true, result: null, profit: null,
    paper: true, book: 'draftkings', notes: null, created_at: '', sport: 'mlb',
    ...over,
  }
}

// 9 player props (free preview keeps 5 for the wrong sport) + 1 game pick (no free rows at all).
const FIXTURE: Bet[] = [
  ...Array.from({ length: 9 }, () => bet({})),
  bet({ player: null, system: 'NRFI', bet_type: 'NRFI' }),
]

interface Found { todaysPicks?: string; systems: string[] }
function inspect(node: unknown, found: Found = { systems: [] }): Found {
  if (node == null || typeof node !== 'object') return found
  if (Array.isArray(node)) { node.forEach(n => inspect(n, found)); return found }
  const el = node as ReactElement
  if (el.props && typeof el.props === 'object') {
    const props = el.props as Record<string, unknown>
    if (props.label === "Today's Picks") found.todaysPicks = String(props.value)
    if (typeof props.system === 'string') found.systems.push(props.system)
    inspect(props.children, found)
  }
  return found
}

beforeEach(() => {
  picksFixture = FIXTURE
  process.env.ENFORCE_TIERS = '1'
})
afterEach(() => {
  delete process.env.ENFORCE_TIERS
})

describe('dashboard picks preview cut', () => {
  it("shows only the free preview to a Single-Sport Pass for another league", async () => {
    getViewerMock.mockResolvedValue({ tier: 'single_sport', sport: 'nfl' })
    const found = inspect(await DashboardPicksPage())
    expect(found.todaysPicks).toBe('5')
    expect(found.systems).not.toContain('NRFI')
  })

  it('shows the full card to All-Access', async () => {
    getViewerMock.mockResolvedValue({ tier: 'pro_monthly', sport: null })
    const found = inspect(await DashboardPicksPage())
    expect(found.todaysPicks).toBe('10')
    expect(found.systems).toContain('NRFI')
  })
})
