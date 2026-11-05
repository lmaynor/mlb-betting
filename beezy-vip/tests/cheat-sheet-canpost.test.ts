/**
 * @jest-environment node
 *
 * Finding 6e9db589 regression: the cheat sheet's canPost capability must come from the authenticated viewer
 * only. A ?ops=1 query parameter must not grant it to a public viewer.
 */
import type { ReactElement } from 'react'

const getViewerMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getViewer: () => getViewerMock() }))
jest.mock('@/lib/top-picks-data', () => ({
  loadTopPlayerPicks: async () => ({ dailyCard: [] }),
  loadDailyPlaysHistory: async () => [],
}))

import CheatSheetPage from '@/app/cheat-sheet/page'

type PageProps = { searchParams?: Promise<{ ops?: string }> }
const callPage = (props: PageProps) =>
  (CheatSheetPage as unknown as (p: PageProps) => Promise<ReactElement>)(props)

describe('cheat-sheet canPost', () => {
  it('does not grant posting to a public viewer via ?ops=1', async () => {
    getViewerMock.mockResolvedValue({ tier: 'public', sport: null })
    const el = await callPage({ searchParams: Promise.resolve({ ops: '1' }) })
    expect((el.props as { canPost: boolean }).canPost).toBe(false)
  })

  it('grants posting to a paid viewer with no query parameter', async () => {
    getViewerMock.mockResolvedValue({ tier: 'pro_monthly', sport: null })
    const el = await callPage({})
    expect((el.props as { canPost: boolean }).canPost).toBe(true)
  })
})
