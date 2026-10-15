import fs from 'fs'
import path from 'path'
import { NAV, SECTIONS, navActive, sectionFor, sportHref, tabActive } from '../lib/ia'

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

  it('/picks/mlb is the Systems tab and does not also light up Today', () => {
    const [today, systems] = SECTIONS[1].tabs
    expect(tabActive('/picks/mlb/hr', null, systems)).toBe(true)
    expect(tabActive('/picks/mlb/hr', null, today)).toBe(false)
  })

  it('MLB has no hub of its own any more', () => {
    expect(sportHref('mlb')).toBe('/picks')
    expect(sportHref('nfl')).toBe('/sports/nfl')
  })
})
