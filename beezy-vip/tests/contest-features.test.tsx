import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ContestClient } from '@/components/contest/contest-client'
import { fmtOdds, toFractional } from '@/lib/contest/format'
import { sampleFeed } from '@/lib/contest/fixture'
import type { PlacedBet } from '@/lib/contest/types'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const NOW = '2026-11-17T06:00:00Z'
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
afterEach(() => { cleanup(); try { localStorage.clear() } catch { /* none */ } })

describe('odds formats', () => {
  it('converts exactly', () => {
    expect(toFractional(150)).toBe('3/2')
    expect(toFractional(-110)).toBe('10/11')
    expect(toFractional(100)).toBe('1/1')
    expect(toFractional(-200)).toBe('1/2')
    expect(fmtOdds(-110, 'decimal')).toBe('1.91')
    expect(fmtOdds(195, 'decimal')).toBe('2.95')
    expect(fmtOdds(-110, 'american')).toBe('-110')
    expect(fmtOdds(125)).toBe('+125')
    expect(fmtOdds(null, 'decimal')).toBe('--')
  })

  it('the select changes every price on the board and the slip, and is remembered', () => {
    const { unmount } = render(<ContestClient feed={sampleFeed(NOW)} />)
    expect(screen.getByRole('button', { name: 'Ohio State spread -6.5 -110' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Odds format'), { target: { value: 'decimal' } })
    const b = screen.getByRole('button', { name: 'Ohio State spread -6.5 1.91' })
    expect(b).toHaveTextContent('1.91')
    fireEvent.click(b)
    expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent('1.91')
    expect(localStorage.getItem('contest-odds-format')).toBe('decimal')
    unmount()
    render(<ContestClient feed={sampleFeed(NOW)} />)
    expect((screen.getByLabelText('Odds format') as HTMLSelectElement).value).toBe('decimal')
    fireEvent.change(screen.getByLabelText('Odds format'), { target: { value: 'fractional' } })
    expect(screen.getByRole('button', { name: 'Ohio State spread -6.5 10/11' })).toBeInTheDocument()
  })
})

describe('My Bets', () => {
  const lost: PlacedBet = {
    id: 'x1', kind: 'straight', groupId: null, stakeCents: 5_000, toWinCents: 4_545, status: 'lost', placedAt: '2026-11-16T12:00:00Z', teaserPoints: 0,
    legs: [{ gameId: 1, market: 'spread', side: 'home', line: -3.5, price: -110, result: 'loss' }],
  }
  const withLost = () => { const f = sampleFeed(NOW); return { ...f, myBets: [lost, ...f.myBets] } }

  it('filters open and settled bets with counts', () => {
    render(<ContestClient feed={withLost()} />)
    tab('My Bets')
    const table = () => screen.getByRole('table', { name: 'My Bets' })
    expect(screen.getByRole('button', { name: 'All 3' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open 1' }))
    expect(within(table()).getAllByRole('row')).toHaveLength(2)                 // header + the open parlay
    expect(table()).toHaveTextContent('PENDING')
    fireEvent.click(screen.getByRole('button', { name: 'Settled 2' }))
    expect(table()).not.toHaveTextContent('PENDING')
    expect(table()).toHaveTextContent('LOST')
  })

  it('Bet Again puts the settled bet back on the slip at the current board price', () => {
    render(<ContestClient feed={withLost()} />)
    tab('My Bets')
    const rows = within(screen.getByRole('table', { name: 'My Bets' })).getAllByRole('row')
    const lostRow = rows.find(r => r.textContent?.includes('LOST'))!
    fireEvent.click(within(lostRow).getByRole('button', { name: 'Bet Again' }))
    expect(screen.getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'true')
    const slip = screen.getByRole('region', { name: 'Bet Slip' })
    expect(slip).toHaveTextContent('Ohio State -6.5')                            // the board's line today, not the -3.5 that lost
  })

  it('a settled bet on a game that is closed offers no Bet Again', () => {
    render(<ContestClient feed={sampleFeed(NOW)} />)
    tab('My Bets')
    expect(screen.queryByRole('button', { name: 'Bet Again' })).not.toBeInTheDocument()
    expect(screen.getByText('Games closed')).toBeInTheDocument()
  })
})

describe('bankroll chart', () => {
  it('draws the run with a text summary next to it, on My Bets', () => {
    render(<ContestClient feed={sampleFeed(NOW)} />)
    tab('My Bets')
    const chart = screen.getByRole('region', { name: 'Bankroll history' })
    expect(chart).toHaveTextContent('Peak$5,409.09')
    expect(chart).toHaveTextContent('Start$5,000.00')
    expect(chart.querySelector('polyline')).toHaveAttribute('stroke', '#57f287')
    expect(chart.querySelectorAll('polyline')).toHaveLength(1)
  })
  it('is red when the player is below the start, and absent with fewer than two points', () => {
    const f = sampleFeed(NOW)
    render(<ContestClient feed={{ ...f, history: [{ ts: 'a', cents: 500_000 }, { ts: 'b', cents: 450_000 }] }} />)
    tab('My Bets')
    expect(screen.getByRole('region', { name: 'Bankroll history' }).querySelector('polyline')).toHaveAttribute('stroke', '#ef4444')
    cleanup()
    render(<ContestClient feed={{ ...f, history: [{ ts: 'a', cents: 500_000 }] }} />)
    tab('My Bets')
    expect(screen.queryByRole('region', { name: 'Bankroll history' })).not.toBeInTheDocument()
  })
})
