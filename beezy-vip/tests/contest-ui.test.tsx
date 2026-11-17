import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ContestClient } from '@/components/contest/contest-client'
import { sampleFeed } from '@/lib/contest/fixture'
import { fmtMoney } from '@/lib/contest/odds'
import { makePick, previewParlay } from '@/lib/contest/slip'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

const MID = String.fromCharCode(183)                       // the middle dot the UI joins labels with (kept out of the source as a glyph)
const NOW = '2026-11-17T06:00:00Z'
const feed = () => sampleFeed(NOW)
const game = (id: number) => feed().games.find(g => g.id === id)!

beforeEach(() => { jest.useFakeTimers({ now: new Date(NOW), doNotFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate', 'nextTick', 'queueMicrotask', 'performance', 'hrtime', 'requestAnimationFrame', 'cancelAnimationFrame', 'requestIdleCallback', 'cancelIdleCallback'] }) })
afterEach(() => { jest.useRealTimers() })

const open = () => render(<ContestClient feed={feed()} />)
const btn = (name: string | RegExp) => screen.getByRole('button', { name })
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name }))
const stake = (v: string) => fireEvent.change(screen.getByLabelText(/^Stake/), { target: { value: v } })
const slip = () => screen.getByRole('region', { name: 'Bet Slip' })
const standing = () => within(screen.getByLabelText('Your standing'))
const place = (amount: string) => btn(`Place Bet ${MID} ${amount}`)

/** The home-spread button of each game whose betting is open (11 of the 13 sample games). */
const openHomeSpreads = (container: HTMLElement) => [...container.querySelectorAll('tr[data-game]')]
  .filter(r => !['11', '12'].includes((r as HTMLElement).dataset.game!))
  .map(r => within(r as HTMLElement).getAllByRole('button')[1])

describe('Bankroll Challenge page', () => {
  it('is one real table with a single header, a row per game and real logos', () => {
    const { container } = open()
    const table = screen.getByRole('table', { name: 'Odds board' })
    expect(table.querySelectorAll('thead')).toHaveLength(1)
    expect(container.querySelectorAll('tr[data-game]')).toHaveLength(feed().games.length)
    expect(container.querySelector('img[src="https://a.espncdn.com/i/teamlogos/ncaa/500-dark/194.png"]')).toBeInTheDocument()   // Ohio State
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Bowl Bankroll')
    expect(screen.getByText('SAMPLE DATA')).toBeInTheDocument()
  })

  it('labels every moneyline button ML so all three price cells line up', () => {
    const { container } = open()
    const row = container.querySelector('tr[data-game="1"]') as HTMLElement
    const [, , , , mlAway, mlHome] = within(row).getAllByRole('button')
    expect(mlAway).toHaveTextContent('ML')
    expect(mlHome).toHaveTextContent('ML')
    expect(mlAway).toHaveTextContent('+195')
    // every button has a top label line, so no price sits alone
    within(row).getAllByRole('button').forEach(b => expect(b.querySelectorAll('span')).toHaveLength(2))
    expect(mlAway.querySelectorAll('span')[0].textContent).toBe('ML')
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
    expect(slip()).toHaveTextContent('Bet Slip 2')
    stake('100')
    const pv = previewParlay([makePick(game(1), 'spread', 'home')!, makePick(game(2), 'spread', 'home')!], 10_000)
    expect(within(slip()).getByText(fmtMoney(pv.toWinCents))).toBeInTheDocument()
    expect(within(slip()).getByText(fmtMoney(pv.payoutCents))).toBeInTheDocument()
    expect(place('$100.00')).toBeEnabled()
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
    expect(slip()).toHaveTextContent('Bet Slip 1')
    expect(home).toHaveAttribute('aria-pressed', 'false')
    tab('Straight')                                                    // straights may stack a game
    fireEvent.click(home)
    expect(slip()).toHaveTextContent('Bet Slip 2')
  })

  it('quick amounts ADD to the stake instead of replacing it, and Reset clears it', () => {
    open()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    const input = screen.getByLabelText(/^Stake/) as HTMLInputElement
    fireEvent.click(btn('Add $25 to stake'))
    expect(input.value).toBe('25')
    fireEvent.click(btn('Add $25 to stake'))
    expect(input.value).toBe('50')
    fireEvent.click(btn('Add $100 to stake'))
    expect(input.value).toBe('150')
    stake('12.50')                                                     // typed amounts still work, and chips add to them
    fireEvent.click(btn('Add $50 to stake'))
    expect(input.value).toBe('62.5')
    fireEvent.click(btn('Reset'))
    expect(input.value).toBe('')
    expect(screen.getByRole('button', { name: 'Add $25 to stake' })).toHaveTextContent('+$25')
  })

  it('refuses a teaser with a moneyline and explains why', () => {
    open()
    tab('Teaser')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Georgia moneyline -178'))
    stake('50')
    expect(btn('Place Bet')).toBeDisabled()
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

  it('round robin spells out how many bets each size makes', () => {
    open()
    tab('Round Robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110', 'Penn State spread -2.5 -110']) fireEvent.click(btn(n))
    expect(btn('By 2s, 6 bets')).toHaveTextContent('6 bets')          // C(4,2)
    expect(btn('By 3s, 4 bets')).toHaveTextContent('4 bets')
    expect(btn('By 4s, 1 bet')).toHaveTextContent('1 bet')
    expect(screen.getByTestId('robin-breakdown')).toHaveTextContent('By 2s = 6 parlays')    // by 2s is the default
    fireEvent.click(btn('By 3s, 4 bets'))
    expect(screen.getByTestId('robin-breakdown')).toHaveTextContent('By 2s = 6 parlays + By 3s = 4 parlays = 10 parlays in all')
    stake('25')
    const dl = within(slip()).getByText('Parlays').parentElement as HTMLElement
    expect(dl).toHaveTextContent('Parlays10')
    expect(dl).toHaveTextContent('$250.00')                           // 10 x $25 total risk
    expect(slip()).toHaveTextContent('Stake per parlay')
  })

  it('round robin: parlay count, total risk and the place button for 3 picks', () => {
    open()
    tab('Round Robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110']) fireEvent.click(btn(n))
    stake('25')
    const dl = within(slip()).getByText('Parlays').parentElement as HTMLElement
    expect(dl).toHaveTextContent('Parlays3')                          // C(3,2) by 2s
    expect(dl).toHaveTextContent('$75.00')                            // 3 x $25
    fireEvent.click(btn(/^By 3s/))                                    // add the full 3-leg parlay
    expect(dl).toHaveTextContent('Parlays4')
    expect(dl).toHaveTextContent('$100.00')
    expect(place('$100.00')).toBeEnabled()
  })

  it('round robin goes up to 11 picks and by up to 11s', () => {
    const { container } = open()
    tab('Round Robin')
    openHomeSpreads(container).forEach(b => fireEvent.click(b))
    expect(slip()).toHaveTextContent('Bet Slip 11')
    expect(btn('By 2s, 55 bets')).toBeInTheDocument()                 // C(11,2)
    expect(btn('By 5s, 462 bets')).toBeInTheDocument()                // C(11,5)
    expect(btn('By 11s, 1 bet')).toBeInTheDocument()                  // one 11-leg parlay
    stake('5')                                                        // the round robin minimum is $5 per parlay
    expect(screen.getByTestId('robin-breakdown')).toHaveTextContent('By 2s = 55 parlays')
    expect(within(slip()).getByText('Parlays').parentElement).toHaveTextContent('$275.00')   // 55 x $5
    expect(place('$275.00')).toBeEnabled()
    fireEvent.click(btn('By 5s, 462 bets'))                           // 517 parlays is over the 500 limit
    expect(btn('Place Bet')).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('517 parlays is over the limit of 500')
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
    expect(place('$1,000.00')).toBeEnabled()
  })

  it('round robin stake rejection shows the parlay count and total', () => {
    open()
    tab('Round Robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110', 'Penn State spread -2.5 -110']) fireEvent.click(btn(n))
    stake('4')
    expect(screen.getByRole('status')).toHaveTextContent('Minimum stake is $5.00 per parlay')
    stake('500')
    expect(screen.getByRole('status')).toHaveTextContent('6 parlays x $500.00 = $3,000.00 is over 25% of your bankroll')
  })

  it('placing a bet shows a confirmation, debits the bankroll and lists it under My Bets', () => {
    open()
    expect(standing().getByText('$5,081.82')).toBeInTheDocument()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('100')
    fireEvent.click(place('$100.00'))
    const receipt = screen.getByRole('status', { name: 'Bet confirmation' })
    expect(receipt).toHaveTextContent('Bet placed')
    expect(receipt).toHaveTextContent('Straight')
    expect(receipt).toHaveTextContent('OSU -6.5 -110')
    expect(receipt).toHaveTextContent('Slip #1')
    expect(receipt).toHaveTextContent('Risk$100.00')
    expect(receipt).toHaveTextContent('To win$90.91')
    expect(receipt).toHaveTextContent('Payout$190.91')
    expect(receipt).toHaveTextContent('hidden from other players until each game kicks off')
    expect(standing().getByText('$4,981.82')).toBeInTheDocument()     // available
    expect(standing().getByText('$200.00')).toBeInTheDocument()       // at risk: the existing $100 parlay plus the new $100 straight
    expect(screen.queryByLabelText(/^Stake/)).not.toBeInTheDocument() // the slip is replaced, not stacked under the receipt
    fireEvent.click(btn('View My Bets'))
    expect(screen.queryByRole('status', { name: 'Bet confirmation' })).not.toBeInTheDocument()
    expect(screen.getByRole('table', { name: 'My Bets' })).toBeInTheDocument()
  })

  it('Keep Betting dismisses the confirmation and picking a price also clears it', () => {
    open()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('50')
    fireEvent.click(place('$50.00'))
    fireEvent.click(btn('Keep Betting'))
    expect(screen.queryByRole('status', { name: 'Bet confirmation' })).not.toBeInTheDocument()
    expect(slip()).toHaveTextContent('Bet Slip 0')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('25')
    fireEvent.click(place('$25.00'))
    expect(screen.getByRole('status', { name: 'Bet confirmation' })).toBeInTheDocument()
    fireEvent.click(btn('Georgia spread -3.5 -112'))                  // starting a new slip replaces the confirmation
    expect(screen.queryByRole('status', { name: 'Bet confirmation' })).not.toBeInTheDocument()
    expect(slip()).toHaveTextContent('Bet Slip 1')
  })

  it('a round robin confirmation shows one slip, its parlay count and the total', () => {
    open()
    tab('Round Robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110']) fireEvent.click(btn(n))
    stake('25')
    fireEvent.click(place('$75.00'))
    const receipt = screen.getByRole('status', { name: 'Bet confirmation' })
    expect(receipt).toHaveTextContent(`Round Robin ${MID} 3 parlays`)
    expect(receipt).toHaveTextContent('Risk$75.00')
    expect(receipt.querySelectorAll('li')).toHaveLength(3)           // each pick listed once, not once per parlay
    expect(receipt).toHaveTextContent('Slip #1')
    expect(receipt).not.toHaveTextContent('#1-0')
    fireEvent.click(btn('View My Bets'))
    expect(screen.getByText(`Round Robin ${MID} 3 parlays`)).toBeInTheDocument()       // folded into one row
  })

  it('shows another player\'s unstarted legs as locks and their stake as hidden', () => {
    open()
    tab('Pool')
    const table = screen.getByRole('table', { name: 'Pool' })
    expect(within(table).getByText('3 legs hidden until kickoff')).toBeInTheDocument()          // one line, not one per leg
    expect(within(table).getByText('1 more leg hidden until kickoff')).toBeInTheDocument()      // a partly revealed bet says what is left
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
    // one money format on the page: every bankroll cell has cents (no $812,500 next to $5,181.82)
    rows.forEach(r => { const c = within(r).getAllByRole('cell'); expect(c[2].textContent).toMatch(/^\$[\d,]+\.\d{2}$/); expect(c[3].textContent).toMatch(/^[+-]\$[\d,]+\.\d{2}$/) })
  })

  it('starts as a Straight and becomes a Parlay on the second pick, until the player chooses a type', () => {
    open()
    expect(screen.getByRole('tab', { name: 'Straight' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    expect(screen.getByRole('tab', { name: 'Straight' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('status')).not.toHaveTextContent('Parlays need')                  // no warning on the very first pick
    fireEvent.click(btn('Georgia spread -3.5 -112'))
    expect(screen.getByRole('tab', { name: 'Parlay' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(btn('Georgia spread -3.5 -112'))                                          // back to one pick
    expect(screen.getByRole('tab', { name: 'Straight' })).toHaveAttribute('aria-selected', 'true')
    tab('Teaser')                                                                             // a chosen type sticks
    fireEvent.click(btn('Georgia spread -3.5 -112'))
    fireEvent.click(btn('Indiana spread -4 -110'))
    expect(screen.getByRole('tab', { name: 'Teaser' })).toHaveAttribute('aria-selected', 'true')
  })

  it('marks a picked price with a check as well as colour', () => {
    open()
    const b = btn('Ohio State spread -6.5 -110')
    expect(b.querySelector('[data-selected-mark]')).toBeNull()
    fireEvent.click(b)
    expect(b.querySelector('[data-selected-mark]')).not.toBeNull()
    expect(b).toHaveAttribute('aria-pressed', 'true')
  })

  it('labels the slip as play money and keeps its small controls at least 44px wide', () => {
    open()
    expect(slip()).toHaveTextContent('Play money')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    expect(btn('Clear')).toHaveStyle({ minWidth: '44px', minHeight: '44px' })
    expect(btn(/^Remove/)).toHaveStyle({ minWidth: '44px', minHeight: '44px' })
    tab('Straight')
    stake('25')
    expect(btn('Add $25 to stake')).toHaveStyle({ minWidth: '44px' })
    fireEvent.click(place('$25.00'))
    expect(screen.getByRole('status', { name: 'Bet confirmation' })).toHaveTextContent('Play money')
  })

  it('shows the live score beside the team names with the clock, down and distance and who has the ball', () => {
    const { container } = open()
    const live = container.querySelector('tr[data-game="12"]') as HTMLElement
    expect(within(live).getByLabelText('LSU score')).toHaveTextContent('17')
    expect(within(live).getByLabelText('Texas score')).toHaveTextContent('20')
    expect(live).toHaveTextContent('Live')
    expect(live).toHaveTextContent('8:42 - 3rd Quarter')
    expect(live).toHaveTextContent('2nd & 7 at LSU 35')
    expect(within(live).getByLabelText('Texas has the ball')).toBeInTheDocument()
    expect(within(live).queryByLabelText('LSU has the ball')).not.toBeInTheDocument()
    expect(live).not.toHaveTextContent('In play')
    expect(live).not.toHaveTextContent('Betting closed')
  })

  it('shows a final score with the loser dimmed, and no score before kickoff', () => {
    const { container } = open()
    const fin = container.querySelector('tr[data-game="11"]') as HTMLElement
    expect(within(fin).getByLabelText('Iowa score')).toHaveTextContent('17')
    expect(within(fin).getByLabelText('Michigan score')).toHaveTextContent('24')
    expect(within(fin).getByLabelText('Iowa score')).toHaveStyle({ color: '#7d8596' })
    expect(within(fin).getByLabelText('Michigan score')).toHaveStyle({ color: '#f3f4f6' })
    expect(fin).toHaveTextContent('Final')
    const upcoming = container.querySelector('tr[data-game="1"]') as HTMLElement
    expect(upcoming.querySelectorAll('[aria-label$=" score"]')).toHaveLength(0)
    expect(upcoming).toHaveTextContent('12:00 PM ET')
  })

  it('lists games you can bet first and puts finished and in-play games last', () => {
    const { container } = open()
    const order = [...container.querySelectorAll('tr[data-game]')].map(r => (r as HTMLElement).dataset.game)
    expect(order.slice(-2).sort()).toEqual(['11', '12'])
    expect(order.slice(0, -2)).not.toContain('11')
  })

  it('keeps the rules in a disclosure and shows the slip rail only where it is useful', () => {
    open()
    expect(screen.getByText('How it works')).toBeInTheDocument()
    expect(screen.getByText(/Play money only. Nothing is wagered./)).toBeInTheDocument()
    expect(screen.getByLabelText('Bet Slip panel')).toBeInTheDocument()
    tab('Leaderboard')
    expect(screen.queryByLabelText('Bet Slip panel')).not.toBeInTheDocument()                 // empty slip, not the board: no empty rail
    tab('Board')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    tab('Leaderboard')
    expect(screen.getByLabelText('Bet Slip panel')).toBeInTheDocument()                       // a slip in progress stays reachable
  })

  it('My Bets offers a cash out, and a bet bought before the line moved can pay above its stake', () => {
    open()
    tab('My Bets')
    const cash = screen.getByRole('button', { name: /^Cash out \$120\.3\d$/ })              // the sample parlay: both lines have since moved its way
    expect(cash).toHaveTextContent(/^Cash Out \$120\.3\d$/)
    expect(screen.getByText(/^\+\$20\.3\d vs risk$/)).toBeInTheDocument()
  })

  it('cashing out asks first (inline, no modal), then credits the bankroll and closes the bet', () => {
    open()
    tab('My Bets')
    fireEvent.click(screen.getByRole('button', { name: /^Cash out \$/ }))
    expect(screen.getByRole('group', { name: 'Confirm cash out' })).toHaveTextContent(/Cash out for \$120\.3\d\?/)
    fireEvent.click(btn('Cancel'))
    expect(screen.queryByRole('group', { name: 'Confirm cash out' })).not.toBeInTheDocument()
    expect(standing().getByText('$100.00')).toBeInTheDocument()                                  // still at risk
    fireEvent.click(screen.getByRole('button', { name: /^Cash out \$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Confirm \$120/ }))
    expect(standing().getByText('$0.00')).toBeInTheDocument()                                    // nothing at risk any more
    expect(standing().getAllByText('$5,202.17')).toHaveLength(2)                                 // Available and Bankroll now agree: $5,081.82 + $120.35
    expect(screen.getByRole('table', { name: 'My Bets' })).toHaveTextContent('CASHED OUT')
    expect(screen.queryByRole('button', { name: /^Cash out/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/Cashed out Parlay for \$120\.3\d \(\+\$20\.3\d vs risk\)/)
  })

  it('a bet placed at the current prices cashes out below its stake (the vig and the margin)', () => {
    open()
    tab('Straight')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    stake('100')
    fireEvent.click(place('$100.00'))
    fireEvent.click(btn('View My Bets'))
    expect(screen.getAllByRole('button', { name: /^Cash out \$/ })).toHaveLength(2)             // the sample parlay and the new straight
    expect(screen.getByRole('button', { name: /^Cash out \$90\.\d\d$/ })).toBeInTheDocument()
    expect(screen.getByText(/^-\$9\.\d\d vs risk$/)).toBeInTheDocument()
  })

  it('says why a bet cannot be cashed out: a teaser, or a game too close to kickoff', () => {
    open()
    tab('Teaser')
    fireEvent.click(btn('Ohio State spread -6.5 -110'))
    fireEvent.click(btn('Georgia spread -3.5 -112'))
    stake('50')
    fireEvent.click(place('$50.00'))
    fireEvent.click(btn('View My Bets'))
    expect(screen.getByText('Not offered on teasers')).toBeInTheDocument()
    cleanup()
    render(<ContestClient feed={{ ...feed(), nowIso: '2026-12-19T16:50:00Z' }} />)                // Ohio State kicks off at 17:00Z: inside the 15-minute close
    tab('My Bets')
    expect(screen.getByText('Closed for cash out')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Cash out/ })).not.toBeInTheDocument()
  })

  it('a round robin cashes out as one group', () => {
    open()
    tab('Round Robin')
    for (const n of ['Ohio State spread -6.5 -110', 'Georgia spread -3.5 -112', 'Indiana spread -4 -110']) fireEvent.click(btn(n))
    stake('25')
    fireEvent.click(place('$75.00'))
    fireEvent.click(btn('View My Bets'))
    const rr = screen.getAllByRole('button', { name: /^Cash out \$/ })                          // the sample parlay and the round robin
    expect(rr).toHaveLength(2)
    fireEvent.click(rr[1])
    fireEvent.click(screen.getByRole('button', { name: /^Confirm \$/ }))
    expect(screen.getByText(`Round Robin ${MID} 3 parlays`).closest('tr')).toHaveTextContent('CASHED OUT')
    expect(screen.getAllByRole('button', { name: /^Cash out \$/ })).toHaveLength(1)              // only the sample parlay is left
  })

  it('uses Title Case for Bet Slip, Round Robin, My Bets and Place Bet', () => {
    open()
    expect(slip()).toHaveTextContent('Bet Slip')
    expect(screen.getByRole('tab', { name: 'Round Robin' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'My Bets' })).toBeInTheDocument()
  })
})
