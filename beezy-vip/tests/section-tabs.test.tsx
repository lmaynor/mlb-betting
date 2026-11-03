import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { SectionTabs } from '@/components/layout/section-tabs'

let path = '/ev/games'
jest.mock('next/navigation', () => ({ usePathname: () => path, useSearchParams: () => new URLSearchParams() }))

describe('section tabs on a phone', () => {
  it('four Odds tabs share the row evenly, with short names and full accessible names, and do not scroll', () => {
    path = '/ev/games'
    render(<SectionTabs />)
    const row = screen.getByRole('navigation').firstElementChild as HTMLElement
    expect(row).toHaveClass('grid', 'auto-cols-fr')
    expect(row).not.toHaveClass('overflow-x-auto')
    expect(screen.getByRole('link', { name: 'Odds Terminal' })).toHaveTextContent('OddsOdds Terminal')
    expect(screen.getByRole('link', { name: 'Prop Research' })).toHaveTextContent('PropsProp Research')
    expect(screen.getByRole('link', { name: 'Game Lines' })).toHaveAttribute('aria-current', 'page')
  })
  it('a longer row (Results has six) still scrolls, with the edge fade', () => {
    path = '/results'
    render(<SectionTabs />)
    const row = screen.getByRole('navigation').firstElementChild as HTMLElement
    expect(row).toHaveClass('overflow-x-auto')
    expect(row.className).toMatch(/mask-image/)
  })
})
