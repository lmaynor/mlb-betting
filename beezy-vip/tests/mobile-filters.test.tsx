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

  it('renders mobile-native filter controls with market and book chips', () => {
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

    expect(screen.getByText('All Markets')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'DraftKings' })).toBeInTheDocument()
    expect(screen.queryByText('ONLY')).not.toBeInTheDocument()

    // Verify RESULT segmented control exists
    expect(screen.getByRole('button', { name: 'win' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'loss' })).toBeInTheDocument()

    // Verify SORT controls exist
    expect(screen.getByText(/high to low/i)).toBeInTheDocument()
    expect(screen.getByText(/low to high/i)).toBeInTheDocument()
  })

  it('tapping a book chip isolates that book', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    // Click DraftKings
    const dkBtn = screen.getByRole('button', { name: 'DraftKings' })
    fireEvent.click(dkBtn)

    expect(pushMock).toHaveBeenCalledWith('/picks?book=draftkings', { scroll: false })
  })

  it('tapping None on sportsbook filter selects none', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    const noneBtn = screen.getByRole('button', { name: 'None' })
    fireEvent.click(noneBtn)

    expect(pushMock).toHaveBeenCalledWith('/picks?book=__none__', { scroll: false })
  })

  it('tapping a market chip isolates that market', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    const ptsBtn = screen.getByRole('button', { name: 'PTS', pressed: false })
    fireEvent.click(ptsBtn)

    expect(pushMock).toHaveBeenCalledWith(expect.stringContaining('market=NBA_PTS'), { scroll: false })
  })

  it('tapping the active market chip clears it, and All Markets clears it too', () => {
    mockSearchParams = new URLSearchParams('market=NBA_PTS')
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))

    // row button on the ACTIVE market: toggles off
    const row = screen.getByRole('button', { name: 'PTS', pressed: true })
    fireEvent.click(row)
    expect(pushMock).toHaveBeenLastCalledWith('/picks', { scroll: false })

    // All Markets clears it too
    fireEvent.click(screen.getByRole('button', { name: 'All Markets' }))
    expect(pushMock).toHaveBeenLastCalledWith('/picks', { scroll: false })
  })

  it('a market chip on an inactive market selects it', () => {
    render(<FilterBar sport="nba" />)
    fireEvent.click(screen.getByRole('button', { name: /filters/i }))
    fireEvent.click(screen.getByRole('button', { name: 'PTS', pressed: false }))
    expect(pushMock).toHaveBeenLastCalledWith(expect.stringContaining('market=NBA_PTS'), { scroll: false })
  })
})

