import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { LeagueFilter, LeagueNote } from '@/components/ui/league-filter'
import { leaguePills } from '@/lib/sports'

describe('leaguePills', () => {
  it('lists MLB, NFL, NHL, NBA in that order whatever has rows, and tags each state', () => {
    const p = leaguePills(['nfl', 'nba'], ['nhl'], new Date('2026-12-01T12:00:00Z'))
    expect(p.map(x => [x.slug, x.state])).toEqual([['mlb', 'empty'], ['nfl', 'live'], ['nhl', 'soon'], ['nba', 'live']])
    expect(p[0].note).toMatch(/offseason and resumes in March/)
    expect(p[2].note).toMatch(/NHL is not on this page yet/)
  })
  it('says there are no games for an in-season league that is empty', () => {
    const p = leaguePills([], [], new Date('2026-07-01T12:00:00Z'))
    expect(p.find(x => x.slug === 'mlb')!.note).toBe('No MLB games with prices right now.')
  })
})

describe('LeagueFilter', () => {
  it('keeps every league in the row, dims the empty ones and still lets you pick them', () => {
    const onChange = jest.fn()
    render(<LeagueFilter value="all" live={['nba']} onChange={onChange} />)
    const names = screen.getAllByRole('button').map(b => b.textContent)
    expect(names).toEqual(['All', 'MLBoff', 'NFLoff', 'NHLoff', 'NBA'])
    fireEvent.click(screen.getByRole('button', { name: /MLB/ }))
    expect(onChange).toHaveBeenCalledWith('mlb')
  })
})

describe('LeagueNote', () => {
  it('names the empty league and offers the leagues that have rows', () => {
    const onPick = jest.fn()
    render(<LeagueNote value="mlb" live={['nfl', 'nba']} onPick={onPick} />)
    expect(screen.getByText('MLB: nothing on the board')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open NFL' }))
    expect(onPick).toHaveBeenCalledWith('nfl')
    expect(screen.queryByRole('button', { name: 'Open MLB' })).toBeNull()
  })
})
