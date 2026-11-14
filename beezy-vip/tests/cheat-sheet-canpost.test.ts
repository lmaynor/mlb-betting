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
  loadTopPlayerPicks: async () => ({ dailyCard: [], candidates: [] }),
  loadDailyPlaysHistory: async () => [],
}))

import TodaysCardPage from '@/app/picks/page'

type PageProps = { searchParams?: Promise<{ ops?: string }> }
const callPage = (props: PageProps) =>
  (TodaysCardPage as unknown as (p: PageProps) => Promise<ReactElement>)(props)

// The /picks page composes the gate receipt, the card client, and the near-miss zone; the capability flag
// under test lives on the card client's props.
const canPostOf = (el: ReactElement): boolean => {
  const kids = (el.props as { children?: unknown }).children
  const list = Array.isArray(kids) ? kids : [kids]
  const client = list.find(c => c != null && typeof c === 'object' && 'props' in c && (c as ReactElement).props != null && 'canPost' in ((c as ReactElement).props as object)) as ReactElement | undefined
  if (!client) throw new Error('card client element not found in the /picks page tree')
  return (client.props as { canPost: boolean }).canPost
}

describe('cheat-sheet canPost', () => {
  it('does not grant posting to a public viewer via ?ops=1', async () => {
    getViewerMock.mockResolvedValue({ tier: 'public', sport: null })
    const el = await callPage({ searchParams: Promise.resolve({ ops: '1' }) })
    expect(canPostOf(el)).toBe(false)
  })

  it('grants posting to a paid viewer with no query parameter', async () => {
    getViewerMock.mockResolvedValue({ tier: 'pro_monthly', sport: null })
    const el = await callPage({})
    expect(canPostOf(el)).toBe(true)
  })
})
