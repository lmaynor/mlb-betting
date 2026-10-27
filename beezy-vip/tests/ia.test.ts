import fs from 'fs'
import path from 'path'
import { NAV, SECTIONS, navActive, sectionFor, sportActive, sportHref, tabActive } from '../lib/ia'
import { PICK_SYSTEMS } from '../lib/pick-systems'
import { LIVE_SPORTS, SPORT_SLUGS } from '../lib/sports'

const app = path.resolve(__dirname, '..', 'app')
/** A page exists for the path, counting a dynamic [param] folder where no literal folder matches (as Next resolves it). */
const routeExists = (href: string) => {
  const segs = href.split('?')[0].split('/').filter(Boolean)
  let dir = app
  for (const seg of segs) {
    const literal = path.join(dir, seg)
    if (fs.existsSync(literal)) { dir = literal; continue }
    const dyn = fs.existsSync(dir) ? fs.readdirSync(dir).find(d => /^\[[^\]]+\]$/.test(d)) : undefined
    if (!dyn) return false
    dir = path.join(dir, dyn)
  }
  return fs.existsSync(path.join(dir, 'page.tsx'))
}

describe('information architecture', () => {
  it('every nav item and section tab points at a real page (no link to a retired route)', () => {
    for (const n of NAV) expect(routeExists(n.href)).toBe(true)
    for (const s of SECTIONS) for (const t of s.tabs) expect(routeExists(t.href)).toBe(true)
  })

  it('the five nav questions are the only top-level items', () => {
    expect(NAV.map(n => n.label)).toEqual(['Home', 'Odds Terminal', 'Picks', 'Results', 'Tools'])
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

  it('Picks tabs are the all-sports views only: Daily Card, Top Signals, Game Lines, Graded History', () => {
    const picks = SECTIONS.find(x => x.id === 'picks')!
    expect(picks.tabs.map(t => t.label)).toEqual(['Daily Card', 'Top Signals', 'Game Lines', 'Graded History'])
    expect(sectionFor('/picks')?.id).toBe('picks')
    expect(sectionFor('/picks/games')?.id).toBe('picks')
    expect(sectionFor('/picks/history')?.id).toBe('picks')
    expect(sectionFor('/cheat-sheet')?.id).toBe('picks')
    // MLB and the other sports are chosen in the ribbon and carry their own headers, not the strip
    expect(sectionFor('/picks/mlb')).toBeNull()
    expect(sectionFor('/picks/mlb/hr')).toBeNull()
    expect(sectionFor('/sports/nfl')).toBeNull()
    expect(tabActive('/cheat-sheet', null, picks.tabs[0])).toBe(true)
    expect(tabActive('/picks', null, picks.tabs[1])).toBe(true)
    expect(tabActive('/picks/history', null, picks.tabs[1])).toBe(false)
    expect(tabActive('/picks/games', null, picks.tabs[2])).toBe(true)
    expect(tabActive('/picks/history', null, picks.tabs[3])).toBe(true)
  })

  it('Picks stays lit in the top nav on MLB and the other sport pages', () => {
    for (const p of ['/picks', '/picks/mlb', '/picks/mlb/systems', '/picks/history', '/sports/nhl', '/cheat-sheet']) expect(navActive(p, NAV[2])).toBe(true)
  })

  it('the sport ribbon: ALL SPORTS is the Top 10 and every sport is /picks/[sport]', () => {
    expect(sportHref('all')).toBe('/picks')
    expect(routeExists(sportHref('all'))).toBe(true)
    for (const s of SPORT_SLUGS) {
      expect(sportHref(s)).toBe(`/picks/${s}`)
      expect(routeExists(sportHref(s))).toBe(true)
      expect(routeExists(`${sportHref(s)}/projections`)).toBe(true)
    }
    expect(sportActive('/', 'all')).toBe(false)
    expect(sportActive('/picks/history', 'all')).toBe(true)
    expect(sportActive('/picks/mlb', 'all')).toBe(false)
    expect(sportActive('/picks/mlb/hr', 'mlb')).toBe(true)
    expect(sportActive('/picks/nba/projections', 'nba')).toBe(true)
    expect(sportActive('/picks/nba', 'nhl')).toBe(false)
  })

  it('the ribbon is the live pillars of the registry, in registry order', () => {
    expect(LIVE_SPORTS.map(p => p.slug)).toEqual([...SPORT_SLUGS])
  })

  it('every system page resolves to a route, and the retired /sports hub still has its redirect page', () => {
    for (const s of PICK_SYSTEMS) expect(routeExists(`/picks/${s.sport}/${s.slug}`)).toBe(true)
    expect(routeExists('/sports/nfl')).toBe(true)
  })
})

describe('scanner tabs', () => {
  const scanner = SECTIONS.find(s => s.id === 'scanner')!
  const terminal = scanner.tabs.find(t => t.href === '/ev')!
  const research = scanner.tabs.find(t => t.href === '/edge')!
  it('lists the Odds Terminal then prop research', () => {
    expect(scanner.tabs.map(t => t.label)).toEqual(['Odds Terminal', 'Prop Research'])
  })
  it('lights exactly one tab on /ev and on /edge', () => {
    expect([tabActive('/ev', null, terminal), tabActive('/ev', null, research)]).toEqual([true, false])
    expect([tabActive('/edge', null, terminal), tabActive('/edge', null, research)]).toEqual([false, true])
  })
  it('keeps /ev in the Scanner section and the Odds Terminal nav item', () => {
    expect(sectionFor('/ev')?.id).toBe('scanner')
    expect(navActive('/ev', NAV.find(n => n.href === '/ev')!)).toBe(true)
  })
})
