/**
 * Daily Card: the card and the POST TO X boxes must list the SAME picks (the page feeds both from one set of lists), every post is
 * <= 280 characters, NHL appears through its own chip even when the overall Top 10 is all NBA, and the panel is visible on a phone.
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { buildSheets, LINK_REPLY, POST_LIMIT, SHEET_SIZE } from '../lib/cheat-sheet-post'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))

import { CheatSheetClient, type CardPick } from '../app/cheat-sheet/cheat-sheet-client'

const cp = (o: Partial<CardPick>): CardPick => ({
  key: `${o.sport ?? 'nba'}|${o.who ?? 'Jalen Williams'}`, sport: 'nba', who: 'Jalen Williams', what: 'Under 4.5 Rebounds', game: 'BOS v NYK',
  odds: -110, book: 'espnbet', edge: 0.146, score: 65, basis: 'projection', tracked: false, result: null,
  headshotUrl: null, awayLogoUrl: null, homeLogoUrl: null, ...o,
})

// Live shape on 2026-10-20: the overall Top 10 was ALL NBA (score 65 vs NHL 57), so NHL existed only as its own list.
const NBA: CardPick[] = [
  cp({ who: 'Jalen Williams', score: 65, basis: 'projection' }),
  cp({ who: 'Karl-Anthony Towns', what: 'Under 4.5 Assists', odds: 100, book: 'novig', edge: 0.137, score: 63 }),
  cp({ who: 'Tyrese Haliburton', what: 'Over 3.5 Rebounds', odds: -104, book: 'draftkings', edge: 0.144, score: 62 }),
  cp({ who: 'Daniss Jenkins', what: 'Over 3.5 Assists', odds: 156, book: 'prophetx', edge: 0.09, score: 61, basis: 'confluence' }),
  cp({ who: 'Egor Demin', what: 'Under 11.5 Points', odds: 125, book: 'novig', edge: 0.075, score: 57, basis: 'confluence' }),
  cp({ who: 'Pelle Larsson', what: 'Over 18.5 Pts+Reb+Ast', odds: 120, book: 'novig', edge: 0.055, score: 47, basis: 'confluence' }),
]
const NHL: CardPick[] = [
  cp({ sport: 'nhl', who: 'Pavel Zacha', what: 'Under 1.5 Shots on Goal', odds: 140, book: 'hardrock', edge: 0.079, score: 56, basis: 'confluence' }),
  cp({ sport: 'nhl', who: 'Kyle Connor', what: 'Over 3.5 Shots on Goal', odds: 125, book: 'fanduel', edge: 0.069, score: 52, basis: 'confluence' }),
  cp({ sport: 'nhl', who: 'Filip Chytil', what: 'Over 0.5 Points', odds: 160, book: 'caesars', edge: 0.061, score: 51, basis: 'confluence' }),
]
const SPORT_PICKS = { nba: NBA, nhl: NHL }
const SHEETS = buildSheets(NBA.slice(0, SHEET_SIZE), { nba: NBA.slice(0, SHEET_SIZE), nhl: NHL.slice(0, SHEET_SIZE) }, 'Tuesday')

function renderCard() {
  const view = render(<CheatSheetClient sheets={SHEETS} sportPicks={SPORT_PICKS} players={NBA} games={[]} gamesLocked={0} today="TUESDAY, OCTOBER 20" yesterday={[]} />)
  const card = view.container.querySelector('.cheat-sheet-layout > div') as HTMLElement
  const mobile = view.container.querySelector('.cheat-sheet-mobile-post') as HTMLElement
  const sidebar = view.container.querySelector('.cheat-sheet-sidebar') as HTMLElement
  return { ...view, card, mobile, sidebar }
}

/** Names in the order they appear in `text`. */
const orderIn = (text: string, names: string[]) => names.filter(n => text.includes(n)).sort((a, b) => text.indexOf(a) - text.indexOf(b))
const lastName = (who: string) => who.split(' ').slice(-1)[0]

describe('the card and the post text list the same picks', () => {
  it('All chip: the card\'s first five and the OVERALL box are the same picks in the same order', () => {
    const { card, mobile } = renderCard()
    const cardNames = NBA.slice(0, SHEET_SIZE).map(p => p.who)
    for (const n of cardNames) expect(card).toHaveTextContent(n)
    expect(card).not.toHaveTextContent('Pelle Larsson')                             // the sixth is not on the card...
    const text = (within(mobile).getByLabelText('OVERALL post') as HTMLTextAreaElement).value
    expect(text).not.toContain('Larsson')                                           // ...nor in the text
    const inText = NBA.slice(0, SHEET_SIZE).filter(p => text.includes(lastName(p.who)))
    expect(inText.length).toBeGreaterThanOrEqual(1)
    expect(orderIn(text, inText.map(p => lastName(p.who)))).toEqual(inText.map(p => lastName(p.who)))
    // whatever the 280 cap dropped is the LOWEST-ranked pick(s), never a middle one: the text is a prefix of the card's order
    expect(inText.map(p => p.who)).toEqual(cardNames.slice(0, inText.length))
  })

  it('NHL chip exists although no NHL pick is in the overall list, and shows the same picks as the NHL box', () => {
    const { card, mobile } = renderCard()
    expect(within(card).getByRole('button', { name: 'NHL' })).toBeInTheDocument()   // the "NHL is not wired" case
    fireEvent.click(within(card).getByRole('button', { name: 'NHL' }))
    for (const p of NHL) expect(card).toHaveTextContent(p.who)
    expect(card).not.toHaveTextContent('Jalen Williams')
    const text = (within(mobile).getByLabelText('NHL post') as HTMLTextAreaElement).value
    for (const p of NHL) expect(text).toContain(p.who)
    expect(orderIn(text, NHL.map(p => p.who))).toEqual(NHL.map(p => p.who))
  })

  it('every sport chip on the card has a box with the same label', () => {
    const { card, mobile } = renderCard()
    for (const label of ['NBA', 'NHL']) {
      expect(within(card).getByRole('button', { name: label })).toBeInTheDocument()
      expect(within(mobile).getByLabelText(`${label} post`)).toBeInTheDocument()
    }
  })
})

describe('hard 280 cap in the UI', () => {
  it('no box is over 280, no counter says otherwise, and a cut is announced', () => {
    const { mobile } = renderCard()
    for (const label of ['OVERALL', 'NBA', 'NHL']) {
      const text = (within(mobile).getByLabelText(`${label} post`) as HTMLTextAreaElement).value
      expect(text.length).toBeLessThanOrEqual(POST_LIMIT)
      expect(within(mobile).getAllByText(new RegExp(`^${text.length}/${POST_LIMIT}$`)).length).toBeGreaterThan(0)
    }
    for (const sh of SHEETS) {
      if (sh.lines < sh.offered) expect(within(mobile).getByText(new RegExp(`Showing ${sh.lines} of ${sh.offered}`))).toBeInTheDocument()
    }
    expect(mobile.textContent).not.toMatch(/Over 280|Premium/)
  })
})

describe('POST TO X panel', () => {
  it('is in the mobile column and in the desktop sidebar, with the same boxes', () => {
    const { mobile, sidebar } = renderCard()
    for (const root of [mobile, sidebar]) {
      expect(within(root).getByText('POST TO X')).toBeInTheDocument()
      for (const label of ['OVERALL post', 'NBA post', 'NHL post', 'Link reply']) expect(within(root).getByLabelText(label)).toBeInTheDocument()
    }
  })

  it('COPY puts exactly that box on the clipboard; the reply COPY puts the link text', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const { mobile } = renderCard()
    const buttons = within(mobile).getAllByRole('button', { name: 'COPY' })
    expect(buttons).toHaveLength(SHEETS.length + 1)
    fireEvent.click(buttons[1])
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SHEETS[1].text))
    await waitFor(() => expect(within(mobile).getByRole('button', { name: 'COPIED' })).toBeInTheDocument())
    fireEvent.click(buttons[buttons.length - 1])
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(LINK_REPLY))
  })

  it('renders nothing when there are no picks, and the posts carry no link', () => {
    const { container } = render(<CheatSheetClient sheets={[]} sportPicks={{}} players={[]} games={[]} gamesLocked={0} today="T" yesterday={[]} />)
    expect(screen.queryByText('POST TO X')).not.toBeInTheDocument()
    expect(container.textContent).not.toContain('Top player picks - ')
    const { mobile } = renderCard()
    for (const ta of within(mobile).getAllByRole('textbox')) {
      if (ta.getAttribute('aria-label') !== 'Link reply') expect((ta as HTMLTextAreaElement).value).not.toMatch(/https?:\/\//)
    }
  })
})

describe('Daily Card layout CSS', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../app/globals.css'), 'utf8')
  it('hides the mobile copy on desktop and shows it (and hides the sidebar) at 640px and below', () => {
    expect(css).toMatch(/\.cheat-sheet-mobile-post\s*\{\s*display:\s*none;\s*\}/)
    const mobileBlock = css.slice(css.indexOf('@media (max-width: 640px)', css.indexOf('.cheat-sheet-layout')))
    expect(mobileBlock.slice(0, mobileBlock.indexOf('\n}'))).toMatch(/\.cheat-sheet-sidebar\s*\{\s*display:\s*none\s*!important;\s*\}/)
    expect(mobileBlock.slice(0, mobileBlock.indexOf('\n}'))).toMatch(/\.cheat-sheet-mobile-post\s*\{\s*display:\s*block;/)
  })
})
