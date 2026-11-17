import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ContestClient } from '@/components/contest/contest-client'
import { sampleFeed } from '@/lib/contest/fixture'
import { altPrice } from '@/lib/contest/margin'
import { fmtPrice } from '@/lib/contest/odds'
import { sgpPrice } from '@/lib/contest/sgp'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const MID = String.fromCharCode(183)
const NOW = '2026-11-17T06:00:00Z'
const feed = () => sampleFeed(NOW)
const game = feed().games.find(g => g.id === 1)!          // Oregon @ Ohio State: home -6.5, total 54.5, both -110
const open = () => render(<ContestClient feed={feed()} />)
const btn = (name: string | RegExp) => screen.getByRole('button', { name })
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
const slip = () => screen.getByRole('region', { name: 'Bet Slip' })
afterEach(cleanup)

describe('alt lines on the slip', () => {
  it('steps the spread half a point and reprices it from the board price, and Main puts it back', () => {
    open()
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    expect(screen.getByRole('group', { name: 'Alt line for spread' })).toHaveTextContent('Main line')
    const want = altPrice(game, 'spread', 'home', -7, feed().rules)!
    fireEvent.click(btn('Lower the spread to -7'))
    expect(slip()).toHaveTextContent('Ohio State -7')
    expect(slip()).toHaveTextContent(fmtPrice(want))
    expect(screen.getByRole('group', { name: 'Alt line for spread' })).toHaveTextContent('Alt line')
    fireEvent.click(btn('Main'))
    expect(slip()).toHaveTextContent('Ohio State -6.5')
    expect(screen.getByRole('group', { name: 'Alt line for spread' })).toHaveTextContent('Main line')
  })

  it('moves a total in the same way and keeps the pick selected on the board', () => {
    open()
    fireEvent.click(btn('Over total O 54.5 -110'))
    fireEvent.click(btn('Raise the total to 55'))
    expect(slip()).toHaveTextContent('Over 55')
    expect(btn('Over total O 54.5 -110')).toHaveAttribute('aria-pressed', 'true')
  })

  it('is not offered on a moneyline or in a teaser, which already moves the number', () => {
    open()
    fireEvent.click(btn(/^Ohio State moneyline/))
    expect(screen.queryByRole('group', { name: /Alt line/ })).not.toBeInTheDocument()
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Lower the spread to -7'))
    tab('Teaser')
    expect(slip()).toHaveTextContent('was -6.5')                              // back on the main line, then moved by the teaser's points
    expect(screen.queryByRole('group', { name: /Alt line for/ })).not.toBeInTheDocument()
  })

  it('a bet placed on an alt line keeps the line and price it was quoted', () => {
    open()
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Raise the spread to -6'))
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    fireEvent.click(btn(`Place Bet ${MID} $100.00`))
    expect(screen.getByRole('region', { name: 'Bet Slip' })).toHaveTextContent(/OSU -6 -115/)             // -6 is a half point better than the board's -6.5; the contest quoted -115
  })
})

describe('same-game parlays on the slip', () => {
  const pickPair = () => {
    open()
    fireEvent.click(screen.getByRole('tab', { name: 'SGP' }))               // choose the bet type first: elsewhere a second pick on a game replaces the first
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Over total O 54.5 -110'))
  }

  it('prices the pair as a ticket, shorter than the plain parlay when the legs move together', () => {
    pickPair()
    const price = sgpPrice([{ gameId: 1, market: 'spread', side: 'home', line: -6.5, price: -110 }, { gameId: 1, market: 'total', side: 'over', line: 54.5, price: -110 }], game, feed().rules)!
    expect(screen.getByTestId('sgp-price')).toHaveTextContent(fmtPrice(price))
    expect(price).toBeLessThan(264)                                       // a plain two-leg -110 parlay pays +264
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    expect(slip()).toHaveTextContent('Payout')
  })

  it('needs both legs: one leg says what to add, and a pick on another game starts over', () => {
    open()
    fireEvent.click(screen.getByRole('tab', { name: 'SGP' }))
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    expect(slip()).toHaveTextContent('Pick a spread or the moneyline, and the total, from one game')
    fireEvent.click(btn('Over total O 54.5 -110'))
    expect(within(slip()).getAllByRole('listitem')).toHaveLength(2)
    fireEvent.click(btn(/^Georgia spread/))                               // another game: the pair starts over
    expect(within(slip()).getAllByRole('listitem')).toHaveLength(1)
  })

  it('placing it shows the receipt and My Bets lists an SGP with its ticket price', () => {
    pickPair()
    fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: '100' } })
    fireEvent.click(btn(`Place Bet ${MID} $100.00`))
    expect(slip()).toHaveTextContent('SGP')
    fireEvent.click(btn('View My Bets'))
    expect(screen.getByRole('table', { name: 'My Bets' })).toHaveTextContent('SGP')
  })

  it('a moneyline replaces the spread, so a nested pair cannot be built on the slip', () => {
    open()
    fireEvent.click(screen.getByRole('tab', { name: 'SGP' }))
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn(/^Ohio State moneyline/))
    expect(within(slip()).getAllByRole('listitem')).toHaveLength(1)
    expect(slip()).toHaveTextContent('Ohio State to win')
    fireEvent.click(btn('Over total O 54.5 -110'))
    expect(within(slip()).getAllByRole('listitem')).toHaveLength(2)         // moneyline + total is a legal pair
    expect(screen.getByTestId('sgp-price')).not.toHaveTextContent('--')
  })
})
