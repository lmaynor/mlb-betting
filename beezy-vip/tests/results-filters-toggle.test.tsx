import '@testing-library/jest-dom'
/** The phone Results page keeps its filter chips behind one Filters row; the chips are always in the DOM for md and up. */
import { fireEvent, render, screen } from '@testing-library/react'
import { ResultsClient } from '@/app/results/results-client'

jest.mock('next/navigation', () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  usePathname: () => '/results',
}))

describe('Results filters on a phone', () => {
  it('starts collapsed and opens from the Filters row', () => {
    render(<ResultsClient initialPicks={[]} initialStats={[]} />)
    const toggle = screen.getByTestId('results-filters-toggle')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById('results-filters')).toHaveClass('hidden', 'md:block')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById('results-filters')).toHaveClass('block')
  })
})
