import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { Nav } from '@/components/layout/nav'

let path = '/ev/games'
jest.mock('next/navigation', () => ({ usePathname: () => path, useSearchParams: () => new URLSearchParams() }))

describe('phone nav menu', () => {
  it('starts closed, opens from the burger with Install, Discord, X and sign in, and closes again', () => {
    render(<Nav authDisabled />)
    const burger = screen.getByTestId('nav-burger')
    expect(burger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('nav-menu')).toBeNull()
    fireEvent.click(burger)
    const menu = screen.getByTestId('nav-menu')
    expect(burger).toHaveAttribute('aria-expanded', 'true')
    expect(menu).toHaveTextContent('Discord')
    expect(menu).toHaveTextContent('Follow on X')
    expect(menu.querySelector('a[href="/login"]')).not.toBeNull()
    fireEvent.click(burger)
    expect(screen.queryByTestId('nav-menu')).toBeNull()
  })
  it('closes on Escape and on a tap outside the header', () => {
    render(<div><Nav authDisabled /><p>page</p></div>)
    fireEvent.click(screen.getByTestId('nav-burger'))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByTestId('nav-menu')).toBeNull()
    fireEvent.click(screen.getByTestId('nav-burger'))
    fireEvent.mouseDown(screen.getByText('page'))
    expect(screen.queryByTestId('nav-menu')).toBeNull()
  })
})
