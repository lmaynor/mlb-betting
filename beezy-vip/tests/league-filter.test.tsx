import '@testing-library/jest-dom'
import { fireEvent, render, screen } from '@testing-library/react'
import { LeagueFilter, LeagueNote } from '@/components/ui/league-filter'
import { EXTRA_LEAGUES, LIVE_SPORTS, leaguePills } from '@/lib/sports'

const ALL_LEAGUES = [...LIVE_SPORTS, ...EXTRA_LEAGUES]

describe('leaguePills', () => {
  it('lists every live sport in registry order whatever has rows, and tags each state', () => {
    const p = leaguePills(['nfl', 'nba'], ['nhl'], new Date('2026-12-01T12:00:00Z'))
    expect(p.map(x => x.slug)).toEqual(ALL_LEAGUES.map(x => x.slug))
    const state = (slug: string) => p.find(x => x.slug === slug)!
    expect(state('mlb').state).toBe('empty')
    expect(state('nfl').state).toBe('live')
    expect(state('nhl').state).toBe('soon')
    expect(state('nba').state).toBe('live')
    expect(state('mlb').note).toMatch(/offseason and resumes in March/)
    expect(state('nhl').note).toMatch(/NHL is not on this page yet/)
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
    expect(names).toEqual(['All', ...ALL_LEAGUES.map(p => p.slug === 'nba' ? p.shortName : `${p.shortName}off`)])
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
