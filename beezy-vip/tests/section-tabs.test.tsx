import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { SectionTabs } from '@/components/layout/section-tabs'

let path = '/ev/games'
jest.mock('next/navigation', () => ({ usePathname: () => path, useSearchParams: () => new URLSearchParams() }))

describe('section tabs on a phone', () => {
  it('five Scanner tabs scroll the row, with short names and full accessible names', () => {
    path = '/ev/games'
    render(<SectionTabs />)
    const row = screen.getByRole('navigation').firstElementChild as HTMLElement
    expect(row).not.toHaveClass('grid', 'auto-cols-fr')
    expect(row).toHaveClass('overflow-x-auto')
    expect(screen.getByRole('link', { name: 'Odds Screen' })).toHaveTextContent('OddsOdds Screen')
    expect(screen.getByRole('link', { name: 'Prop Research' })).toHaveTextContent('PropsProp Research')
    expect(screen.getByRole('link', { name: 'Live Candidates' })).toHaveTextContent('LiveLive Candidates')
    expect(screen.getByRole('link', { name: 'Game Lines' })).toHaveAttribute('aria-current', 'page')
  })
  it('Results has three tabs after the Analytics collapse (Q1(g)), so it also shares the row evenly', () => {
    path = '/results'
    render(<SectionTabs />)
    const row = screen.getByRole('navigation').firstElementChild as HTMLElement
    expect(row).toHaveClass('grid', 'auto-cols-fr')
    expect(row).not.toHaveClass('overflow-x-auto')
    expect(screen.getByRole('link', { name: 'Settled Bets' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Analytics' })).toHaveAttribute('href', '/analytics')
  })
})
