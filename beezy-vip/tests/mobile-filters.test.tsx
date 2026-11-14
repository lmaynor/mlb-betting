import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'

const pushMock = jest.fn()
let mockSearchParams = new URLSearchParams()

jest.mock('next/link', () => ({
  __esModule: true,
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}))

jest.mock('next/navigation', () => ({
  usePathname: () => '/picks',
  useRouter: () => ({ push: pushMock }),
  useSearchParams: () => mockSearchParams,
}))

jest.mock('@/components/ui/mobile-sheet', () => {
  const actual = jest.requireActual('@/components/ui/mobile-sheet')
  return {
    ...actual,
    useIsMobile: () => true, // force mobile mode
  }
})

import { BottomNav } from '@/components/layout/bottom-nav'
import { FilterBar } from '@/components/picks/filter-bar'

describe('Mobile navigation and filters', () => {
  beforeEach(() => {
    pushMock.mockClear()
    mockSearchParams = new URLSearchParams()
  })

  it('the bottom nav is the five ruled slots: Home, Scanner, Daily Card, Results, Menu', () => {
    render(<BottomNav />)
    expect(screen.getByText('Scanner')).toBeInTheDocument()
    expect(screen.getByText('Scanner').closest('a')).toHaveAttribute('href', '/ev')
    expect(screen.getByText('Daily Card').closest('a')).toHaveAttribute('href', '/picks')
    expect(screen.getByRole('button', { name: 'Open menu' })).toBeInTheDocument()
  })

  it('renders mobile-native filter controls with None and Only buttons', () => {
    render(<FilterBar sport="nba" showResult />)

    // Open mobile filter sheet
    const filtersBtn = screen.getByRole('button', { name: /filters/i })
    fireEvent.click(filtersBtn)

    // Verify sheet title
    expect(screen.getByText('Filter Picks')).toBeInTheDocument()

    // Verify SHOW signal segmented control
    expect(screen.getByText('Everything')).toBeInTheDocument()
    expect(screen.getByText('+EV only')).toBeInTheDocument()

    // Verify BOOK list has All, None, and Only buttons
    const noneBtn = screen.getByRole('button', { name: 'None' })
    expect(noneBtn).toBeInTheDocument()

    const onlyButtons = screen.getAllByRole('button', { name: /only/i })
    expect(onlyButtons.length).toBeGreaterThan(0)

    // Verify RESULT segmented control exists
    expect(screen.getByRole('button', { name: 'win' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'loss' })).toBeInTheDocument()

    // Verify SORT controls exist
    expect(screen.getByText(/high to low/i)).toBeInTheDocument()
    expect(screen.getByText(/low to high/i)).toBeInTheDocument()
  })

  it('tapping ONLY on a sportsbook isolates that book', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    // Click ONLY on DraftKings
    const dkOnlyBtn = screen.getByRole('button', { name: 'Only DraftKings' })
    fireEvent.click(dkOnlyBtn)

    expect(pushMock).toHaveBeenCalledWith('/picks?book=draftkings', { scroll: false })
  })

  it('tapping None on sportsbook filter selects none', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    const noneBtn = screen.getByRole('button', { name: 'None' })
    fireEvent.click(noneBtn)

    expect(pushMock).toHaveBeenCalledWith('/picks?book=__none__', { scroll: false })
  })

  it('tapping ONLY on a market isolates that market', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    const ptsOnlyBtn = screen.getByRole('button', { name: /only pts/i })
    fireEvent.click(ptsOnlyBtn)

    expect(pushMock).toHaveBeenCalledWith(expect.stringContaining('market=NBA_PTS'), { scroll: false })
  })

  it('a market row toggles (tapping the active market clears it) while ONLY always selects just that market', () => {
    mockSearchParams = new URLSearchParams('market=NBA_PTS')
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    // row button on the ACTIVE market: toggles off
    const row = screen.getByRole('button', { name: 'PTS', pressed: true })
    fireEvent.click(row)
    expect(pushMock).toHaveBeenLastCalledWith('/picks', { scroll: false })

    // ONLY on the same, already-active market: stays selected (never a toggle)
    fireEvent.click(screen.getByRole('button', { name: /only pts/i }))
    expect(pushMock).toHaveBeenLastCalledWith('/picks?market=NBA_PTS', { scroll: false })
  })

  it('a market row on an inactive market selects it', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))
    fireEvent.click(screen.getByRole('button', { name: 'PTS', pressed: false }))
    expect(pushMock).toHaveBeenLastCalledWith(expect.stringContaining('market=NBA_PTS'), { scroll: false })
  })
})

