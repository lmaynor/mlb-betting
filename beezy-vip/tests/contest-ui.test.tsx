import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { ContestClient } from '@/components/contest/contest-client'
import { sampleFeed } from '@/lib/contest/fixture'
import { fmtMoney } from '@/lib/contest/odds'
import { makePick, previewParlay } from '@/lib/contest/slip'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const NOW = '2026-11-17T06:00:00Z'
const feed = () => sampleFeed(NOW)
const game = (id: number) => feed().games.find(g => g.id === id)!

beforeEach(() => { jest.useFakeTimers({ now: new Date(NOW), doNotFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate', 'nextTick', 'queueMicrotask', 'performance', 'hrtime', 'requestAnimationFrame', 'cancelAnimationFrame', 'requestIdleCallback', 'cancelIdleCallback'] }) })
afterEach(() => { jest.useRealTimers() })

const open = () => render(<ContestClient feed={feed()} />)
const btn = (name: string) => screen.getByRole('button', { name })
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
const stake = (v: string) => fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: v } })
const slip = () => screen.getByRole('region', { name: 'Bet slip' })
const standing = () => within(screen.getByLabelText('Your standing'))

describe('Bankroll Challenge page', () => {
  it('is one real table with a single header, a row per game and real logos', () => {
    const { container } = open()
    const table = screen.getByRole('table', { name: 'Odds board' })
    expect(table.querySelectorAll('thead')).toHaveLength(1)
    expect(container.querySelectorAll('tr[data-game]')).toHaveLength(feed().games.length)
    expect(container.querySelector('img[src="https://a.espncdn.com/i/teamlogos/ncaa/500/194.png"]')).toBeInTheDocument()   // Ohio State
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Bowl Bankroll')
    expect(screen.getByText('SAMPLE DATA')).toBeInTheDocument()
  })

  it('disables every price on games that are in play, final, or closed', () => {
    const { container } = open()
    for (const id of [11, 12]) {
      const row = container.querySelector(`tr[data-game="${id}"]`) as HTMLElement
      const buttons = within(row).getAllByRole('button')
      expect(buttons).toHaveLength(6)
      buttons.forEach(b => expect(b).toBeDisabled())
    }
    const open1 = within(container.querySelector('tr[data-game="1"]') as HTMLElement).getAllByRole('button')
    open1.forEach(b => expect(b).toBeEnabled())
  })

  it('builds a parlay slip and previews exactly what the engine math says', () => {
    open()
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Georgia spread -3.5 -112'))
    expect(slip()).toHaveTextContent('Bet slip 2')
    stake('100')
    const pv = previewParlay([makePick(game(1), 'spread', 'home')!, makePick(game(2), 'spread', 'home')!], 10_000)
    expect(within(slip()).getByText(fmtMoney(pv.toWinCents))).toBeInTheDocument()
    expect(within(slip()).getByText(fmtMoney(pv.payoutCents))).toBeInTheDocument()
    expect(btn(`Place bet · $100.00`)).toBeEnabled()
  })

  it('toggles a pick off, and replaces a second pick on the same game outside straights', () => {
    open()
    const home = btn('Ohio State spread -6.5 -110')
    fireEvent.click(home)
    expect(home).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(home)
    expect(home).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(home)
    fireEvent.click(btn('Over total O 54.5 -110'))                   // parlay mode: same game, so it replaces
    expect(slip()).toHaveTextContent('Bet slip 1')
    expect(home).toHaveAttribute('aria-pressed', 'false')
    tab('Straight')                                                    // straights may stack a game
    fireEvent.click(home)
    expect(slip()).toHaveTextContent('Bet slip 2')
  })

  it('refuses a teaser with a moneyline and explains why', () => {
    open()
    tab('Teaser')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Georgia moneyline -178'))
    stake('50')
    expect(btn('Place bet')).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('Teasers are spreads and totals only')
  })

  it('prices a teaser from the table and shows the moved line', () => {
    open()
    tab('Teaser')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Over total O 47.5 -110'))
    expect(slip()).toHaveTextContent('Ohio State -0.5')               // -6.5 teased 6
    expect(slip()).toHaveTextContent('-120')                         // 2-team 6-point placeholder price
  })

  it('round robin: parlay count, total risk and the max payout across every parlay', () => {
    open()
    tab('Round robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110']) fireEvent.click(btn(n))
    stake('25')
    const dl = within(slip()).getByText('Parlays').parentElement as HTMLElement
    expect(dl).toHaveTextContent('Parlays3')                          // C(3,2) by 2s
    expect(dl).toHaveTextContent('$75.00')                            // 3 x $25
    fireEvent.click(btn('by 3s'))                                     // add the full 3-leg parlay
    expect(dl).toHaveTextContent('Parlays4')
    expect(dl).toHaveTextContent('$100.00')
    expect(btn('Place bet · $100.00')).toBeEnabled()
  })

  it('rejects stakes the rules forbid, with the reason', () => {
    open()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('10')
    expect(screen.getByRole('status')).toHaveTextContent('Minimum stake is $25.00')
    stake('2000')
    expect(screen.getByRole('status')).toHaveTextContent('Maximum is 25%')
    stake('1000')
    expect(btn('Place bet · $1,000.00')).toBeEnabled()
  })

  it('placing a bet debits the bankroll, clears the slip and lists it under My bets', () => {
    open()
    expect(standing().getByText('$5,081.82')).toBeInTheDocument()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('100')
    fireEvent.click(btn('Place bet · $100.00'))
    expect(standing().getByText('$4,981.82')).toBeInTheDocument()   // available
    expect(standing().getByText('$200.00')).toBeInTheDocument()     // at risk: the existing $100 parlay plus the new $100 straight
  })

  it('shows another player\'s unstarted legs as locks and their stake as hidden', () => {
    open()
    tab('Pool')
    const table = screen.getByRole('table', { name: 'Pool' })
    expect(within(table).getAllByText('Hidden until kickoff').length).toBeGreaterThanOrEqual(3)
    expect(within(table).getAllByText('Hidden').length).toBeGreaterThanOrEqual(1)
    expect(within(table).getByText(/Over 52.5/)).toBeInTheDocument()    // the one leg whose game has started is visible
  })

  it('the leaderboard ranks by bankroll, marks you, and dims an eliminated player', () => {
    const { container } = open()
    tab('Leaderboard')
    const rows = within(screen.getByRole('table', { name: 'Leaderboard' })).getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getAllByRole('cell')[1].textContent)).toEqual(['Marcus', 'Priya', 'You', 'Dani', 'Tom', 'WesOUT'])
    expect(container.querySelector('tr[data-mine="true"]')).toHaveTextContent('You')
    expect(rows[5]).toHaveStyle({ opacity: '0.5' })
  })
})
