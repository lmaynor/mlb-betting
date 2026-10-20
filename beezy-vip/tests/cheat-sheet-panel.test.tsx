/**
 * Daily Card "POST TO X": one box per sheet (overall, then each sport), each with its own COPY, plus the link reply. The
 * sidebar is hidden on phones (globals.css), so the same panel must also sit under the card in the mobile column.
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { buildSheets, LINK_REPLY, type SheetPick } from '../lib/cheat-sheet-post'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))

import { CheatSheetClient } from '../app/cheat-sheet/cheat-sheet-client'

const pick = (o: Partial<SheetPick>): SheetPick => ({
  sport: 'nba', who: 'Jalen Williams', what: 'Under 4.5 Rebounds', odds: -110, book: 'espnbet', edge: 0.146, basis: 'confluence', ...o,
})
const PICKS: SheetPick[] = [
  pick({}),
  pick({ sport: 'nhl', who: 'Pavel Zacha', what: 'Under 1.5 Shots on Goal', odds: 135, book: 'draftkings', edge: 0.079 }),
  pick({ who: 'Derrick White', what: 'Under 23.5 Pts+Reb+Ast', odds: -108, book: 'novig', edge: 0.15 }),
  pick({ sport: 'nhl', who: 'Kyle Connor', what: 'Over 3.5 Shots on Goal', odds: 125, book: 'fanduel', edge: 0.069 }),
]
const SHEETS = buildSheets(PICKS, 'Tuesday')

function renderCard(sheets = SHEETS) {
  return render(<CheatSheetClient sheets={sheets} players={[]} games={[]} gamesLocked={0} today="TUESDAY, OCTOBER 20" yesterday={[]} />)
}

describe('Daily Card POST TO X', () => {
  it('has an overall box and one box per sport, in the mobile column AND the desktop sidebar', () => {
    const { container } = renderCard()
    expect(SHEETS.map(s => s.id)).toEqual(['all', 'nhl', 'nba'])
    const mobile = container.querySelector('.cheat-sheet-mobile-post') as HTMLElement
    const sidebar = container.querySelector('.cheat-sheet-sidebar') as HTMLElement
    for (const root of [mobile, sidebar]) {
      expect(within(root).getByText('POST TO X')).toBeInTheDocument()
      for (const label of ['OVERALL post', 'NHL post', 'NBA post']) expect(within(root).getByLabelText(label)).toBeInTheDocument()
      expect(within(root).getByLabelText('Link reply')).toHaveValue(LINK_REPLY)
    }
  })

  it('shows each sheet\'s text and a per-box character count', () => {
    const { container } = renderCard()
    const mobile = container.querySelector('.cheat-sheet-mobile-post') as HTMLElement
    const overall = within(mobile).getByLabelText('OVERALL post') as HTMLTextAreaElement
    expect(overall.value.split('\n')[0]).toBe('Tuesday +EV Cheat Sheet')
    expect(overall.value).toContain('1. NBA Jalen Williams U 4.5 Reb -110 ESPN BET | +14.6% EV')
    expect(overall.value).toContain('NHL Pavel Zacha U 1.5 SOG +135 DraftKings | +7.9% EV')
    const nhl = within(mobile).getByLabelText('NHL post') as HTMLTextAreaElement
    expect(nhl.value.split('\n')[0]).toMatch(/^Tuesday \+EV NHL Cheat Sheet /)
    expect(within(mobile).getAllByText(new RegExp(`^${overall.value.length}/280$`)).length).toBeGreaterThan(0)
  })

  it('COPY puts exactly that sheet on the clipboard, and the REPLY copy puts the link text', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const { container } = renderCard()
    const mobile = container.querySelector('.cheat-sheet-mobile-post') as HTMLElement
    const buttons = within(mobile).getAllByRole('button', { name: 'COPY' })
    expect(buttons).toHaveLength(SHEETS.length + 1)                    // one per sheet + the reply
    fireEvent.click(buttons[1])                                          // the NHL box
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SHEETS[1].text))
    await waitFor(() => expect(within(mobile).getByRole('button', { name: 'COPIED' })).toBeInTheDocument())
    fireEvent.click(within(mobile).getAllByRole('button', { name: 'COPY' }).slice(-1)[0])
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(LINK_REPLY))
  })

  it('flags a sheet over 280 and keeps the posts themselves link-free', () => {
    const five = buildSheets(Array.from({ length: 5 }, (_, i) => pick({ who: `Player Number ${i}`, what: 'Under 23.5 Pts+Reb+Ast' })), 'Tuesday')
    const { container } = renderCard(five)
    expect(within(container.querySelector('.cheat-sheet-mobile-post') as HTMLElement).getByText(/Over 280: post it as an X Premium long post/)).toBeInTheDocument()
    for (const ta of screen.getAllByRole('textbox')) {
      if (ta.getAttribute('aria-label') !== 'Link reply') expect((ta as HTMLTextAreaElement).value).not.toMatch(/https?:\/\//)
    }
  })

  it('renders no POST TO X panel when there are no real +EV picks, and the old single-box text is gone', () => {
    const { container } = renderCard([])
    expect(screen.queryByText('POST TO X')).not.toBeInTheDocument()
    expect(container.textContent).not.toContain('Top player picks - ')
  })
})

describe('Daily Card layout CSS', () => {
  const css = fs.readFileSync(path.resolve(__dirname, '../app/globals.css'), 'utf8')
  it('hides the mobile copy of the panel on desktop and shows it (and hides the sidebar) at 640px and below', () => {
    expect(css).toMatch(/\.cheat-sheet-mobile-post\s*\{\s*display:\s*none;\s*\}/)
    const mobileBlock = css.slice(css.indexOf('@media (max-width: 640px)', css.indexOf('.cheat-sheet-layout')))
    expect(mobileBlock.slice(0, mobileBlock.indexOf('\n}'))).toMatch(/\.cheat-sheet-sidebar\s*\{\s*display:\s*none\s*!important;\s*\}/)
    expect(mobileBlock.slice(0, mobileBlock.indexOf('\n}'))).toMatch(/\.cheat-sheet-mobile-post\s*\{\s*display:\s*block;/)
  })
})
