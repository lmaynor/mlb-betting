import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ContestClient } from '@/components/contest/contest-client'
import { sampleFeed } from '@/lib/contest/fixture'
import type { ContestFeed, PlacedBet } from '@/lib/contest/types'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const MID = String.fromCharCode(183)
const NOW = '2026-11-17T06:00:00Z'
/** A live feed: the sample board, but `sample: false` and a bet list the server owns. */
const liveFeed = (over: Partial<ContestFeed> = {}): ContestFeed => ({ ...sampleFeed(NOW), sample: false, myBets: [], balanceCents: 500_000, ...over })
const btn = (name: string | RegExp) => screen.getByRole('button', { name })

type Reply = { status: number; body: unknown }
let calls: { url: string; init?: RequestInit }[] = []
function mockFetch(handler: (url: string, body: unknown) => Reply) {
  calls = []
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init })
    const r = handler(String(url), init?.body ? JSON.parse(String(init.body)) : undefined)
    return { ok: r.status >= 200 && r.status < 300, status: r.status, json: async () => r.body } as Response
  }) as unknown as typeof fetch
}
const bet = (id: string, over: Partial<PlacedBet> = {}): PlacedBet => ({
  id, kind: 'straight', groupId: null, stakeCents: 10_000, toWinCents: 9_091, status: 'pending', placedAt: NOW, teaserPoints: 0,
  legs: [{ gameId: 1, market: 'spread', side: 'home', line: -6.5, price: -110, result: null }], ...over,
})
afterEach(() => { cleanup(); jest.restoreAllMocks() })

describe('live mode', () => {
  it('places through the API with an idempotency key, then shows the server state and a receipt', async () => {
    mockFetch((url, body) => url.endsWith('/place') && (body as { kind: string }).kind === 'straight'
      ? { status: 200, body: liveFeed({ balanceCents: 490_000, myBets: [bet('7')] }) } : { status: 404, body: {} })
    render(<ContestClient feed={liveFeed()} slug="bowls" />)
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    fireEvent.click(btn(`Place Bet ${MID} $100.00`))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent('Bet placed'))
    const sent = JSON.parse(String(calls[0].init!.body))
    expect(calls[0].url).toBe('/api/contest/bowls/place')
    expect(sent).toMatchObject({ kind: 'straight', stakeCents: 10_000, picks: [{ gameId: 1, market: 'spread', side: 'home', line: -6.5, price: -110 }] })
    expect(typeof sent.key).toBe('string')
    expect(sent.key.length).toBeGreaterThan(8)
    expect(screen.getByLabelText('Your standing')).toHaveTextContent('$4,900.00')
  })

  it('a moved price puts the current price on the slip, keeps the stake, and does not charge', async () => {
    mockFetch(url => url.endsWith('/place')
      ? { status: 409, body: { error: { code: 'PRICE_CHANGED', message: 'the price moved', detail: { game_id: 1, market: 'spread', side: 'home', line: -6.5, price: -125 } } } }
      : url.endsWith('/feed') ? { status: 200, body: liveFeed() } : { status: 404, body: {} })
    render(<ContestClient feed={liveFeed()} slug="bowls" />)
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    fireEvent.click(btn(`Place Bet ${MID} $100.00`))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent('A price moved'))
    expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent('-125')
    expect((screen.getByLabelText(/^Stake/) as HTMLInputElement).value).toBe('100')
    expect(screen.getByLabelText('Your standing')).toHaveTextContent('$5,000.00')
  })

  it('a server refusal shows its message and leaves the slip alone', async () => {
    mockFetch(url => url.endsWith('/place') ? { status: 403, body: { error: { code: 'ELIMINATED', message: 'this entry is out of the contest' } } } : { status: 200, body: liveFeed() })
    render(<ContestClient feed={liveFeed()} slug="bowls" />)
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    fireEvent.click(btn(`Place Bet ${MID} $100.00`))
    await waitFor(() => expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent('out of the contest'))
  })

  it('joins through the API and shows the board', async () => {
    const f = liveFeed({ me: { joined: false, displayName: '', renamesLeft: 3, suggestedName: 'alex' } })
    mockFetch(url => url.endsWith('/join') ? { status: 200, body: liveFeed({ me: { joined: true, displayName: 'Alex', renamesLeft: 3, suggestedName: 'alex' } }) } : { status: 404, body: {} })
    render(<ContestClient feed={f} slug="bowls" />)
    fireEvent.change(screen.getByLabelText('Invite code'), { target: { value: 'WELCOME' } })
    fireEvent.click(btn('Join contest'))
    await waitFor(() => expect(screen.getByRole('table', { name: 'Odds board' })).toBeInTheDocument())
    expect(JSON.parse(String(calls[0].init!.body))).toEqual({ inviteCode: 'WELCOME', displayName: 'alex' })
    expect(screen.queryByText(/Sample mode/)).not.toBeInTheDocument()
  })

  it('a refused join shows the server message', async () => {
    const f = liveFeed({ me: { joined: false, displayName: '', renamesLeft: 3, suggestedName: 'alex' } })
    mockFetch(() => ({ status: 403, body: { error: { code: 'INVITE_INVALID', message: 'invite code is wrong or used up' } } }))
    render(<ContestClient feed={f} slug="bowls" />)
    fireEvent.change(screen.getByLabelText('Invite code'), { target: { value: 'NOPE' } })
    fireEvent.click(btn('Join contest'))
    await waitFor(() => expect(screen.getByText('invite code is wrong or used up')).toBeInTheDocument())
  })

  it('without a slug the page stays on sample data and never calls the network', () => {
    mockFetch(() => ({ status: 500, body: {} }))
    render(<ContestClient feed={sampleFeed(NOW)} />)
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    expect(calls).toHaveLength(0)
  })
})
