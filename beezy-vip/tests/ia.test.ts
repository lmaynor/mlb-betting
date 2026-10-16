import fs from 'fs'
import path from 'path'
import { NAV, SECTIONS, navActive, sectionFor, sportActive, sportHref, tabActive } from '../lib/ia'

const app = path.resolve(__dirname, '..', 'app')
const routeExists = (href: string) => {
  const p = href.split('?')[0]
  return fs.existsSync(path.join(app, p === '/' ? '' : p, 'page.tsx'))
}

describe('information architecture', () => {
  it('every nav item and section tab points at a real page (no link to a retired route)', () => {
    for (const n of NAV) expect(routeExists(n.href)).toBe(true)
    for (const s of SECTIONS) for (const t of s.tabs) expect(routeExists(t.href)).toBe(true)
  })

  it('the five nav questions are the only top-level items', () => {
    expect(NAV.map(n => n.label)).toEqual(['Home', '+EV Terminal', 'Picks', 'Results', 'Tools'])
  })

  it('Prop Research is a view of the Scanner, Analytics and Models are views of Results, Daily Card of Picks', () => {
    expect(navActive('/edge', NAV[1])).toBe(true)
    expect(navActive('/analytics', NAV[3])).toBe(true)
    expect(navActive('/models/hr', NAV[3])).toBe(true)
    expect(navActive('/cheat-sheet', NAV[2])).toBe(true)
    expect(sectionFor('/edge')?.id).toBe('scanner')
    expect(sectionFor('/tools')).toBeNull()
  })

  it('analytics tabs are chosen by ?tab= and default to bankroll', () => {
    const books = SECTIONS[2].tabs.find(t => t.href.includes('books'))!
    const bankroll = SECTIONS[2].tabs.find(t => t.href.includes('bankroll'))!
    expect(tabActive('/analytics', 'books', books)).toBe(true)
    expect(tabActive('/analytics', null, bankroll)).toBe(true)
    expect(tabActive('/analytics', 'books', bankroll)).toBe(false)
  })

  it('Picks tabs are the all-sports views only: Top 10 player picks, Game picks, History, Daily Card', () => {
    const picks = SECTIONS.find(x => x.id === 'picks')!
    expect(picks.tabs.map(t => t.label)).toEqual(['Top 10', 'Game picks', 'History', 'Daily Card'])
    expect(sectionFor('/picks')?.id).toBe('picks')
    expect(sectionFor('/picks/games')?.id).toBe('picks')
    expect(sectionFor('/picks/history')?.id).toBe('picks')
    // MLB and the other sports are chosen in the ribbon and carry their own headers, not the strip
    expect(sectionFor('/picks/mlb')).toBeNull()
    expect(sectionFor('/picks/mlb/hr')).toBeNull()
    expect(sectionFor('/sports/nfl')).toBeNull()
    expect(tabActive('/picks', null, picks.tabs[0])).toBe(true)
    expect(tabActive('/picks/history', null, picks.tabs[0])).toBe(false)
    expect(tabActive('/picks/games', null, picks.tabs[1])).toBe(true)
    expect(tabActive('/picks/history', null, picks.tabs[2])).toBe(true)
  })

  it('Picks stays lit in the top nav on MLB and the other sport pages', () => {
    for (const p of ['/picks', '/picks/mlb', '/picks/mlb/systems', '/picks/history', '/sports/nhl', '/cheat-sheet']) expect(navActive(p, NAV[2])).toBe(true)
  })

  it('the sport ribbon: ALL SPORTS is the Top 10, MLB is its own section, the rest share /sports', () => {
    expect(sportHref('all')).toBe('/picks')
    expect(sportHref('mlb')).toBe('/picks/mlb')
    expect(sportHref('nfl')).toBe('/sports/nfl')
    expect(routeExists(sportHref('all'))).toBe(true)
    expect(routeExists(sportHref('mlb'))).toBe(true)
    expect(sportActive('/', 'all')).toBe(false)
    expect(sportActive('/picks/history', 'all')).toBe(true)
    expect(sportActive('/picks/mlb', 'all')).toBe(false)
    expect(sportActive('/picks/mlb/hr', 'mlb')).toBe(true)
    expect(sportActive('/sports/nba', 'nba')).toBe(true)
    expect(sportActive('/sports/nba', 'nhl')).toBe(false)
  })
})
