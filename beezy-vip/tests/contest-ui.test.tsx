import '@testing-library/jest-dom'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import * as bjLib from '@/lib/contest/blackjack'
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
    expect(rows.map(r => within(r).getAllByRole('cell')[1].textContent)).toEqual(['Marcus', 'Priya', 'Alex you', 'Dani', 'Tom', 'WesOUT'])
    expect(container.querySelector('tr[data-mine="true"]')).toHaveTextContent('Alex')
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
    expect(live).toHaveTextContent('3rd Quarter 8:42 \u00b7 2nd & 7 at LSU 35')                   // period and clock first, then the situation
    expect(live).not.toHaveTextContent('Live')                                                     // implied by the score and the clock
    expect(live).not.toHaveTextContent('8:42 - 3rd Quarter')
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
    const cash = screen.getByRole('button', { name: /^Cash out \$(113\.99|114\.00)$/ })              // the sample parlay: both lines have since moved its way
    expect(cash).toHaveTextContent(/^Cash Out \$(113\.99|114\.00)$/)
    expect(screen.getByText(/^\+\$(13\.99|14\.00) vs risk$/)).toBeInTheDocument()
  })

  it('cashing out asks first (inline, no modal), then credits the bankroll and closes the bet', () => {
    open()
    tab('My Bets')
    fireEvent.click(screen.getByRole('button', { name: /^Cash out \$/ }))
    expect(screen.getByRole('group', { name: 'Confirm cash out' })).toHaveTextContent(/Cash out for \$(113\.99|114\.00)\?/)
    fireEvent.click(btn('Cancel'))
    expect(screen.queryByRole('group', { name: 'Confirm cash out' })).not.toBeInTheDocument()
    expect(standing().getByText('$100.00')).toBeInTheDocument()                                  // still at risk
    fireEvent.click(screen.getByRole('button', { name: /^Cash out \$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Confirm \$11[34]/ }))
    expect(standing().getByText('$0.00')).toBeInTheDocument()                                    // nothing at risk any more
    expect(standing().getAllByText(/^\$5,195\.8[12]$/)).toHaveLength(2)                                 // Available and Bankroll now agree: $5,081.82 + $113.99 (Python) or $114.00 (this mirror, one cent apart)
    expect(screen.getByRole('table', { name: 'My Bets' })).toHaveTextContent('CASHED OUT')
    expect(screen.queryByRole('button', { name: /^Cash out/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/Cashed out Parlay for \$(113\.99|114\.00) \(\+\$(13\.99|14\.00) vs risk\)/)
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

  it('a new player joins with an invite code and a display name, validated as they type', async () => {
    render(<ContestClient feed={{ ...feed(), me: { joined: false, displayName: '', renamesLeft: 3, suggestedName: 'Jordan L' } }} />)
    expect(screen.getByRole('form', { name: 'Join Bowl Bankroll' })).toBeInTheDocument()
    expect(screen.queryByRole('table', { name: 'Odds board' })).not.toBeInTheDocument()          // nothing to bet on until you have joined
    const name = screen.getByLabelText('Display name') as HTMLInputElement
    const code = screen.getByLabelText('Invite code')
    const join = screen.getByRole('button', { name: 'Join contest' })
    expect(name.value).toBe('Jordan L')                                                          // suggested from the account, editable
    expect(join).toBeDisabled()                                                                  // no code yet
    fireEvent.change(code, { target: { value: 'WELCOME' } })
    expect(join).toBeEnabled()
    fireEvent.change(name, { target: { value: 'A' } })
    expect(screen.getAllByRole('status').some(n => /2 to 20 characters/.test(n.textContent ?? ''))).toBe(true)
    expect(join).toBeDisabled()
    fireEvent.change(name, { target: { value: 'Admin' } })
    expect(screen.getAllByRole('status').some(n => /reserved/.test(n.textContent ?? ''))).toBe(true)
    fireEvent.change(name, { target: { value: 'marcus' } })                                      // valid, but another player already has it (by key)
    fireEvent.click(join)
    await waitFor(() => expect(screen.getAllByRole('status').some(n => /taken/.test(n.textContent ?? ''))).toBe(true))
    expect(screen.queryByRole('table', { name: 'Odds board' })).not.toBeInTheDocument()
    fireEvent.change(name, { target: { value: 'Jordan Lee' } })
    fireEvent.click(join)
    expect(await screen.findByRole('table', { name: 'Odds board' })).toBeInTheDocument()
    expect(screen.getByText('Jordan Lee')).toBeInTheDocument()                                   // "Playing as Jordan Lee"
  })

  it('a player can rename themselves a limited number of times; everyone sees the new name', async () => {
    open()
    expect(screen.getByText('Alex')).toBeInTheDocument()
    fireEvent.click(btn('Edit name'))
    const input = screen.getByLabelText('Display name') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'marcus' } })
    fireEvent.click(btn('Save'))
    await waitFor(() => expect(screen.getByRole('form', { name: 'Edit display name' })).toHaveTextContent('taken'))    // collides with Marcus by key
    fireEvent.change(input, { target: { value: 'A' } })
    expect(btn('Save')).toBeDisabled()
    fireEvent.change(input, { target: { value: 'Alexander' } })
    fireEvent.click(btn('Save'))
    expect(await screen.findByText('Alexander')).toBeInTheDocument()
    tab('Leaderboard')
    expect(screen.getByRole('table', { name: 'Leaderboard' })).toHaveTextContent('Alexander you')
    tab('Board')
    for (const n of ['Al', 'Lex']) {
      fireEvent.click(btn('Edit name'))
      fireEvent.change(screen.getByLabelText('Display name'), { target: { value: n } })
      fireEvent.click(btn('Save'))
      await waitFor(() => expect(screen.queryByRole('form', { name: 'Edit display name' })).not.toBeInTheDocument())
    }
    expect(screen.queryByRole('button', { name: 'Edit name' })).not.toBeInTheDocument()          // three changes used
    expect(screen.getByText('No renames left')).toBeInTheDocument()
  })

  it('the blackjack table does not exist for a healthy bankroll', () => {
    open()
    expect(screen.queryByRole('tab', { name: 'Blackjack' })).not.toBeInTheDocument()
    expect(screen.queryByText(/blackjack table is open/)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Blackjack table' })).not.toBeInTheDocument()
  })

  describe('the hidden blackjack lifeline (bankroll under $100)', () => {
    const broke = () => render(<ContestClient feed={sampleFeed(NOW, { broke: true })} />)
    const rig = (...decks: string[][]) => { const spy = jest.spyOn(bjLib, 'newDeck'); decks.forEach(d => spy.mockReturnValueOnce(d)); return spy }
    afterEach(() => { jest.restoreAllMocks() })
    const sit = () => { fireEvent.click(screen.getByRole('button', { name: /blackjack table is open/ })) }
    const shows = (amount: string) => expect(standing().getAllByText(amount).length).toBeGreaterThan(0)       // Available and Bankroll agree when nothing is at risk
    const wager = (v: string) => fireEvent.change(screen.getByLabelText('Wager'), { target: { value: v } })

    it('appears as a tab and a quiet nudge, and the nudge opens it', () => {
      broke()
      expect(screen.getByRole('tab', { name: 'Blackjack' })).toBeInTheDocument()
      expect(screen.getByText('Under $100: the blackjack table is open')).toBeInTheDocument()
      sit()
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toBeVisible()
      expect(screen.queryByText(/blackjack table is open/)).not.toBeInTheDocument()            // the nudge goes once you are at the table
    })

    it('stakes from the balance, hides the hole card until the hand ends, and pays a win', () => {
      rig(['TS', '7H', 'TD', 'TD'])                                                              // you 20, dealer 17 (hole card ten)
      broke()
      sit()
      wager('20')
      fireEvent.click(btn('Deal'))
      shows('$20.00')                                // $40 - $20 staked
      expect(screen.getByRole('img', { name: 'Face-down card' })).toBeInTheDocument()
      expect(screen.queryByRole('img', { name: '10 of diamonds' })).toBeInTheDocument()          // your own ten; the dealer's hole card is the face-down one
      fireEvent.click(btn('Stand'))
      expect(screen.queryByRole('img', { name: 'Face-down card' })).not.toBeInTheDocument()
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toHaveTextContent('You win. $40.00 returned.')
      shows('$60.00')                                // $20 + $40 back
    })

    it('a loss costs only the stake, and doubling stakes a second time', () => {
      rig(['TS', 'TH', '8D', '9D'], ['5S', 'TH', '6D', '7D', 'TC'])
      broke()
      sit()
      wager('10')
      fireEvent.click(btn('Deal'))
      fireEvent.click(btn('Stand'))                                                              // 18 against 19
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toHaveTextContent('Dealer wins.')
      shows('$30.00')
      wager('10')
      fireEvent.click(btn('Deal again'))
      fireEvent.click(btn('Double'))                                                             // 11 + 10 = 21 against 17, $20 in play
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toHaveTextContent('You win.')
      shows('$50.00')                                // $30 - $20 + $40
    })

    it('winnings stop at a $100 bankroll, and the table then closes', () => {
      rig(['TS', '7H', 'TD', 'TD'], ['TS', '7H', 'TD', 'TD'])
      broke()
      sit()
      wager('40')
      fireEvent.click(btn('Deal'))
      fireEvent.click(btn('Stand'))                                                              // $0 + $80 back: bankroll $80
      shows('$80.00')
      wager('60')
      fireEvent.click(btn('Deal again'))
      fireEvent.click(btn('Stand'))                                                              // payout $120, but only $80 is allowed: bankroll lands on exactly $100
      const table = screen.getByRole('region', { name: 'Blackjack table' })
      expect(table).toHaveTextContent('Winnings stop at a $100 bankroll, so $80.00 of $120.00 was paid.')
      shows('$100.00')
      expect(table).toHaveTextContent('The table is closed. You are back at $100.')
      tab('Board')
      expect(screen.queryByRole('tab', { name: 'Blackjack' })).not.toBeInTheDocument()          // the hidden feature goes back into hiding
    })

    it('validates the wager and cannot be dealt a bet it cannot afford', () => {
      broke()
      sit()
      expect(btn('Deal')).toBeDisabled()
      wager('0.50')
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toHaveTextContent('Minimum wager is $1.00')
      wager('41')
      expect(screen.getByRole('region', { name: 'Blackjack table' })).toHaveTextContent('Maximum wager is $40.00')
      expect(btn('Deal')).toBeDisabled()
      fireEvent.click(btn('Add $5.00 to wager'))
      wager('5')
      expect(btn('Deal')).toBeEnabled()
    })
  })

  it('uses Title Case for Bet Slip, Round Robin, My Bets and Place Bet', () => {
    open()
    expect(slip()).toHaveTextContent('Bet Slip')
    expect(screen.getByRole('tab', { name: 'Round Robin' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'My Bets' })).toBeInTheDocument()
  })
})
