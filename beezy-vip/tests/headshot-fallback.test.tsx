import '@testing-library/jest-dom'
import fs from 'fs'
import path from 'path'
import { fireEvent, render, screen } from '@testing-library/react'
import { HeadshotImg, Matchup, PlayerHeadshot } from '@/components/ui/sport-assets'
import { BOOK_META, bookDisplay, canonBook } from '@/lib/tokens'

describe('headshot fallback', () => {
  it('shows the silhouette when a player has no id', () => {
    render(<PlayerHeadshot sport="nba" id={null} name="Nobody" size={30} />)
    expect(screen.getByRole('img', { name: 'No photo' })).toBeInTheDocument()
  })
  it('swaps to the silhouette when the photo fails to load', () => {
    render(<PlayerHeadshot sport="nba" id={123} name="Some Player" size={30} />)
    fireEvent.error(screen.getByAltText('Some Player'))
    expect(screen.getByRole('img', { name: 'No photo' })).toBeInTheDocument()
  })
  it('HeadshotImg falls back for a null URL and on error', () => {
    const { rerender } = render(<HeadshotImg src={null} />)
    expect(screen.getByRole('img', { name: 'No photo' })).toBeInTheDocument()
    rerender(<HeadshotImg src="/x.png" alt="x" />)
    fireEvent.error(screen.getByAltText('x'))
    expect(screen.getByRole('img', { name: 'No photo' })).toBeInTheDocument()
  })
  it('Matchup keeps the AWAY @ HOME text for both teams', () => {
    const { container } = render(<Matchup sport="nhl" away="DET" home="FLA" />)
    expect(container.querySelector('[data-matchup="DET @ FLA"]')).not.toBeNull()
    expect(container.textContent).toBe('DET@FLA')
  })
})

describe('book icons', () => {
  it.each(['polymarket', 'ballybet', 'bet105', 'rebet', 'betway'])('%s has metadata and a shipped logo', key => {
    expect(BOOK_META[key]).toBeDefined()
    expect(bookDisplay(key)).toBe(BOOK_META[key])
    expect(fs.existsSync(path.resolve(__dirname, `../public/logos/books/${key}.png`))).toBe(true)
  })
  it('folds feed aliases onto the shipped logos', () => {
    expect(canonBook('polymarket_us')).toBe('polymarket')
    expect(canonBook('Bally Bet')).toBe('ballybet')
  })
})
