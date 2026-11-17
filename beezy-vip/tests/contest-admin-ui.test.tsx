import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AdminConsole } from '@/components/contest/admin-console'
import type { AdminState } from '@/lib/contest/backend'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const state = (over: Partial<AdminState> = {}): AdminState => ({
  name: 'Bowl Bankroll', status: 'open', ledgerMismatches: [], log: [],
  entries: [{ entry_id: 1, user_id: 'user_abcdefghijklmnop', display_name: 'Alex', balance_cents: 490_000, status: 'active', name_changes: 0, joined_at: '2026-12-01T00:00:00Z' }],
  invites: [{ code: 'WELCOME', max_uses: 10, uses: 3 }],
  games: [{ id: 5, away: 'BYU', home: 'Kansas', commence: '2026-12-19T17:00:00Z', status: 'scheduled', homeScore: null, awayScore: null }], ...over,
})
let sent: { url: string; body: unknown }[] = []
function mock(reply: (op: string) => { status: number; body: unknown }) {
  sent = []
  global.fetch = jest.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    sent.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    const r = reply(String(url).split('/').pop()!)
    return { ok: r.status < 300, status: r.status, json: async () => r.body } as Response
  }) as unknown as typeof fetch
}
afterEach(cleanup)

it('shows entries, games, invites and the ledger check', () => {
  render(<AdminConsole slug="bowls" initial={state()} />)
  expect(screen.getByRole('table', { name: 'Entries' })).toHaveTextContent('$4,900.00')
  expect(screen.getByRole('table', { name: 'Games' })).toHaveTextContent('BYU at Kansas')
  expect(screen.getByRole('table', { name: 'Invites' })).toHaveTextContent('3 of 10')
  expect(screen.getByText('Ledger balanced')).toBeInTheDocument()
})

it('renames through the API and shows the server state', async () => {
  mock(() => ({ status: 200, body: state({ entries: [{ ...state().entries[0], display_name: 'Alexander' }] }) }))
  render(<AdminConsole slug="bowls" initial={state()} />)
  fireEvent.change(screen.getByLabelText('New name for Alex'), { target: { value: 'Alexander' } })
  fireEvent.click(within(screen.getByRole('table', { name: 'Entries' })).getByRole('button', { name: 'Rename' }))
  await waitFor(() => expect(screen.getByRole('table', { name: 'Entries' })).toHaveTextContent('Alexander'))
  expect(sent[0]).toEqual({ url: '/api/contest/bowls/admin/rename', body: { entryId: 1, displayName: 'Alexander' } })
})

it('an adjustment needs an amount and a reason, and sends signed cents', async () => {
  mock(() => ({ status: 200, body: state() }))
  render(<AdminConsole slug="bowls" initial={state()} />)
  fireEvent.change(screen.getByLabelText('Amount for Alex'), { target: { value: '-50.00' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(sent).toHaveLength(0)                                               // no reason: nothing sent
  fireEvent.change(screen.getByLabelText('Reason for Alex'), { target: { value: 'duplicate bet' } })
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  await waitFor(() => expect(sent).toHaveLength(1))
  expect(sent[0].body).toEqual({ entryId: 1, cents: -5_000, note: 'duplicate bet' })
})

it('settles and voids a game and shows the server message when refused', async () => {
  mock(op => op === 'void-game' ? { status: 409, body: { error: { code: 'GAME_FINAL', message: 'a final game cannot be voided' } } } : { status: 200, body: state() })
  render(<AdminConsole slug="bowls" initial={state()} />)
  fireEvent.change(screen.getByLabelText('Home score for Kansas'), { target: { value: '21' } })
  fireEvent.change(screen.getByLabelText('Away score for BYU'), { target: { value: '20' } })
  fireEvent.click(screen.getByRole('button', { name: 'Settle' }))
  await waitFor(() => expect(sent[0].body).toEqual({ gameId: 5, homeScore: 21, awayScore: 20 }))
  fireEvent.click(screen.getByRole('button', { name: 'Void' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('a final game cannot be voided'))
})

it('creates an invite and toggles the contest closed', async () => {
  mock(op => ({ status: 200, body: op === 'status' ? state({ status: 'closed' }) : state() }))
  render(<AdminConsole slug="bowls" initial={state()} />)
  fireEvent.change(screen.getByLabelText('Invite code'), { target: { value: 'SECOND' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create invite' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Invite SECOND created.'))
  expect(sent[0].body).toEqual({ code: 'SECOND', maxUses: 10 })
  fireEvent.click(screen.getByRole('button', { name: 'Close to new bets' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Reopen' })).toBeInTheDocument())
})
