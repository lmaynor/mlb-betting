import fs from 'fs'
import path from 'path'
import { EV_MODES, NAV, ROUTES, SECTIONS, navActive, sectionFor, sportActive, sportHref, tabActive, type PipelineStage } from '../lib/ia'
import { MODES } from '../app/ev/odds-constants'
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

  it('the nav questions plus Learn are the only top-level items', () => {
    expect(NAV.map(n => n.label)).toEqual(['Home', 'Scanner', 'Picks', 'Results', 'Learn'])
    expect(navActive('/learn/f5-betting-mlb', NAV[4])).toBe(true)
    expect(sectionFor('/learn')).toBeNull()   // a nav item, not a section with tabs
  })

  it('Prop Research is a view of the Scanner, Analytics and Models are views of Results, Today\'s Card of Picks', () => {
    expect(navActive('/edge', NAV[1])).toBe(true)
    expect(navActive('/analytics', NAV[3])).toBe(true)
    expect(navActive('/models/hr', NAV[3])).toBe(true)
    expect(navActive('/cheat-sheet', NAV[2])).toBe(true)
    expect(sectionFor('/edge')?.id).toBe('scanner')
  })

  it('Scanner tabs: Odds Screen (props), Projections (lambda for props and game lines), Game Lines (NBA / NHL prices), Prop Research and Live Candidates; each is exactly one tab', () => {
    const scanner = SECTIONS.find(x => x.id === 'scanner')!
    expect(scanner.tabs.map(t => t.label)).toEqual(['Odds Screen', 'Projections', 'Game Lines', 'Prop Research', 'Live Candidates'])
    expect(sectionFor('/ev/games')?.id).toBe('scanner')
    expect(sectionFor('/ev/projections')?.id).toBe('scanner')
    const [terminal, projections, games, research] = scanner.tabs
    expect(tabActive('/ev/projections', null, projections)).toBe(true)
    expect(tabActive('/ev/projections', null, terminal)).toBe(false)
    expect(tabActive('/ev', null, terminal)).toBe(true)
    expect(tabActive('/ev/games', null, terminal)).toBe(false)   // /ev/games must not also light the Odds Screen tab
    expect(tabActive('/ev/games', null, games)).toBe(true)
    expect(tabActive('/edge', null, research)).toBe(true)
    expect(navActive('/ev/games', NAV[1])).toBe(true)            // the nav item stays Scanner
  })

  it('Results tabs are Settled Bets, Analytics, Models; the single Analytics tab is lit for every ?tab= sub-view (Q1(g))', () => {
    const results = SECTIONS[2]
    expect(results.tabs.map(t => t.label)).toEqual(['Settled Bets', 'Analytics', 'Models'])
    const analytics = results.tabs.find(t => t.href === '/analytics')!
    expect(tabActive('/analytics', 'books', analytics)).toBe(true)
    expect(tabActive('/analytics', null, analytics)).toBe(true)
    expect(tabActive('/analytics', 'health', analytics)).toBe(true)
    expect(tabActive('/results', null, analytics)).toBe(false)
    expect(tabActive('/results', null, results.tabs[0])).toBe(true)
    expect(tabActive('/models', null, results.tabs[2])).toBe(true)
  })

  it("Picks tabs are the all-sports views only: Today's Card, Past Cards (Game Picks is hidden)", () => {
    const picks = SECTIONS.find(x => x.id === 'picks')!
    expect(picks.tabs.map(t => t.label)).toEqual(["Today's Card", 'Past Cards'])
    expect(sectionFor('/picks')?.id).toBe('picks')
    expect(sectionFor('/picks/games')).toBeNull()        // hidden: the page still answers, but it is no tab and lights no section
    expect(sectionFor('/picks/history')?.id).toBe('picks')
    expect(sectionFor('/cheat-sheet')?.id).toBe('picks')
    // MLB and the other sports are chosen in the ribbon and carry their own headers, not the strip
    expect(sectionFor('/picks/mlb')).toBeNull()
    expect(sectionFor('/picks/mlb/hr')).toBeNull()
    expect(sectionFor('/sports/nfl')).toBeNull()
    expect(tabActive('/cheat-sheet', null, picks.tabs[0])).toBe(true)   // the alias lights Today's Card
    expect(tabActive('/picks', null, picks.tabs[0])).toBe(true)
    expect(tabActive('/picks/history', null, picks.tabs[0])).toBe(false)
    expect(tabActive('/picks/history', null, picks.tabs[1])).toBe(true)
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
      expect(routeExists(`${sportHref(s)}/projections`)).toBe(true)   // a redirect to /ev/projections
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

describe('stranger test gate (Q6(c))', () => {
  const STAGES: PipelineStage[] = ['candidate', 'commitment', 'record', 'guide', 'proof', 'utility']

  it('every nav item, section tab, /ev mode, and registered route declares a pipeline stage and a stranger question', () => {
    for (const n of NAV) {
      expect(STAGES).toContain(n.stage)
      expect(n.strangerQuestion.trim().length).toBeGreaterThan(0)
    }
    for (const s of SECTIONS) for (const t of s.tabs) {
      expect(STAGES).toContain(t.stage)
      expect(t.strangerQuestion.trim().length).toBeGreaterThan(0)
    }
    for (const m of EV_MODES) {
      expect(STAGES).toContain(m.stage)
      expect(m.strangerQuestion.trim().length).toBeGreaterThan(0)
    }
    for (const r of ROUTES) {
      expect(STAGES).toContain(r.stage)
      expect(r.strangerQuestion.trim().length).toBeGreaterThan(0)
    }
  })

  it('every app/ page route is registered in ROUTES, and every ROUTES entry has a page behind it', () => {
    const found: string[] = []
    const walk = (dir: string, prefix: string) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        if (e.isDirectory()) walk(path.join(dir, e.name), `${prefix}/${e.name}`)
        else if (e.name === 'page.tsx') found.push(prefix === '' ? '/' : prefix)
      }
    }
    walk(app, '')
    expect(found.sort()).toEqual(ROUTES.map(r => r.path).sort())
  })

  it('no duplicate labels across the nav and the section tabs', () => {
    const labels = [...NAV.map(n => n.label), ...SECTIONS.flatMap(s => s.tabs.map(t => t.label))]
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('Picks registers no candidate view', () => {
    // Absolute since IA stage S3 moved Top Signals to Scanner as Live Candidates: no exceptions remain.
    const picks = SECTIONS.find(s => s.id === 'picks')!
    const candidateTabs = picks.tabs.filter(t => t.stage === 'candidate').map(t => t.label)
    expect(candidateTabs).toEqual([])
  })

  it('the /ev mode set the page renders is exactly the mode set ia.ts registers', () => {
    expect(MODES.map(m => m.key).sort()).toEqual(EV_MODES.map(m => m.key).sort())
    for (const m of MODES) expect(EV_MODES.find(e => e.key === m.key)!.label).toBe(m.label)
  })
})

describe('scanner tabs', () => {
  const scanner = SECTIONS.find(s => s.id === 'scanner')!
  const terminal = scanner.tabs.find(t => t.href === '/ev')!
  const research = scanner.tabs.find(t => t.href === '/edge')!
  it('lists the Odds Screen, Projections, Game Lines, prop research, then Live Candidates', () => {
    expect(scanner.tabs.map(t => t.label)).toEqual(['Odds Screen', 'Projections', 'Game Lines', 'Prop Research', 'Live Candidates'])
  })
  it('lights exactly one tab on /ev and on /edge', () => {
    expect([tabActive('/ev', null, terminal), tabActive('/ev', null, research)]).toEqual([true, false])
    expect([tabActive('/edge', null, terminal), tabActive('/edge', null, research)]).toEqual([false, true])
  })
  it('keeps /ev in the Scanner section and the Scanner nav item', () => {
    expect(sectionFor('/ev')?.id).toBe('scanner')
    expect(navActive('/ev', NAV.find(n => n.href === '/ev')!)).toBe(true)
  })
})
