/** Models by sport, a model page for every system, Prop Research's all-projections view, and the fin-only icon / new tagline. */
import '@testing-library/jest-dom'
import fs from 'fs'
import path from 'path'
import { render, screen, within } from '@testing-library/react'
import { ProjectionModelDetail } from '../components/models/projection-model-detail'
import { ResearchTabs } from '../components/picks/research-tabs'
import { getModelSpec, MODEL_SPECS } from '../lib/model-specs'
import { getSportSystemBySlug, getSystemsBySport, PICK_SYSTEMS } from '../lib/pick-systems'
import ModelsPage from '../app/models/page'

jest.mock('@/lib/betting-api', () => ({
  apiGetStats: jest.fn(() => Promise.resolve({ bySystem: [] })),
  apiGetSportProjections: jest.fn(() => Promise.reject(new Error('offline'))),
}))
jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href, 'aria-current': current }: { children: React.ReactNode; href: string; 'aria-current'?: 'page' }) => <a href={href} aria-current={current}>{children}</a> }))

const root = path.resolve(__dirname, '..')
const read = (f: string) => fs.readFileSync(path.join(root, f), 'utf8')

describe('a model page for every listed system', () => {
  it('every listed MLB system has a spec, so its /models/<slug> page exists', () => {
    for (const s of getSystemsBySport('mlb')) expect(getModelSpec(s.slug)).toBeDefined()
    expect(MODEL_SPECS.map(m => m.slug)).toEqual(expect.arrayContaining(['game', 'batter-tb', 'batter-hits', 'pitcher-er']))
  })

  it('the new specs are honest about what they have no number for', () => {
    expect(getModelSpec('game')?.oosAUC).toBe(0.5565)
    expect(getModelSpec('batter-tb')?.metricStatus).toBe('pending')
    expect(getModelSpec('batter-hits')?.metricStatus).toBe('pending')
    expect(getModelSpec('pitcher-er')?.metricStatus).toBe('proxy')
    expect(getModelSpec('pitcher-er')?.algorithm).toMatch(/proxy/i)
  })

  it.each(PICK_SYSTEMS.filter(s => s.sport !== 'mlb' && !s.shelved).map(s => [s.sport, s.slug, s]))(
    '%s/%s renders a model page with the shared sections', async (_sport, _slug, system) => {
      const ui = await ProjectionModelDetail({ system: system as (typeof PICK_SYSTEMS)[number] })
      const { container, unmount } = render(ui)
      expect(container.querySelector('h1')).toHaveTextContent(system.name)
      for (const t of ['Model configuration', 'Accuracy', 'Feature List']) expect(screen.getByText(t)).toBeInTheDocument()
      expect(screen.getByText(/No graded history is published/)).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /Every .* projection in Prop Research/ })).toHaveAttribute('href', `/edge/projections?league=${system.sport}`)
      unmount()
    })

  it('a data-gap system says so and shows no performance strip', async () => {
    const { container } = render(await ProjectionModelDetail({ system: getSportSystemBySlug('nhl', 'pp-points')! }))
    expect(screen.getAllByText(/DATA GAP/).length).toBeGreaterThan(0)
    expect(screen.queryByText('Live MAE')).toBeNull()
    expect(container.textContent).toContain('Not projected')
  })
})

describe('/models by sport', () => {
  it('has one expandable section per sport, MLB open, each NFL / NHL / NBA system linking to its model page', async () => {
    const { container } = render(await ModelsPage({ searchParams: Promise.resolve({}) }))
    const sections = [...container.querySelectorAll('details')]
    expect(sections.map(d => d.id)).toEqual(['mlb', 'nfl', 'nhl', 'nba'])
    expect(sections.map(d => d.open)).toEqual([true, false, false, false])
    const hrefs = [...container.querySelectorAll('a')].map(a => a.getAttribute('href'))
    expect(hrefs).toEqual(expect.arrayContaining(['/models/nfl/pass-yds', '/models/nhl/sog', '/models/nba/pts', '/models/game', '/models/batter-tb', '/models/pitcher-er']))
    expect(within(sections[1]).getAllByRole('row').length).toBe(getSystemsBySport('nfl').length + 1)
  })

  it('opens the sport named in ?sport=', async () => {
    const { container } = render(await ModelsPage({ searchParams: Promise.resolve({ sport: 'nba' }) }))
    expect([...container.querySelectorAll('details')].map(d => d.open)).toEqual([false, false, false, true])
  })
})

describe('Prop Research: all projections', () => {
  it('ResearchTabs links the cockpit and the projections view and marks the active one', () => {
    render(<ResearchTabs active="projections" />)
    expect(screen.getByRole('link', { name: 'PROP COCKPIT' })).toHaveAttribute('href', '/edge')
    expect(screen.getByRole('link', { name: 'ALL PROJECTIONS' })).toHaveAttribute('aria-current', 'page')
  })

  it('the cockpit carries the tabs, and the projections page loads every sport\'s unfiltered projection board', () => {
    expect(read('app/edge/edge-client.tsx')).toContain('<ResearchTabs active="cockpit" />')
    const page = read('app/edge/projections/page.tsx')
    expect(page).toContain("view: 'projections'")
    expect(page).toContain('LIVE_SPORTS')
    expect(page).toContain('<SportProjectionsBoard')
    expect(page).toContain('<ResearchTabs active="projections" />')
  })
})

describe('brand and copy cleanup', () => {
  it('the tab icon is the bare fin: no tile, no stroke, no border', () => {
    const svg = read('app/icon.svg')
    expect(svg).not.toMatch(/<rect/)
    expect(svg).not.toMatch(/stroke/)
    expect(svg).toContain('#57F287')
  })

  it('the wordmark and OG badge are the bare fin (the tile stays for the app icon)', () => {
    expect(read('components/ui/brand-mark.tsx')).toMatch(/export function BrandWordmark[\s\S]*<BrandMark size=\{m\.mark\}/)
    expect(read('lib/og-brand.tsx')).not.toContain('BRAND_CANVAS')
  })

  it('the default title and description no longer call the site an MLB-only scanner', () => {
    const layout = read('app/layout.tsx')
    expect(layout).not.toContain('MLB Models')
    expect(layout).not.toContain('+EV Scanner and')
    expect(layout).toContain('MLB, NFL, NHL and NBA')
    expect(read('app/api/og/route.tsx')).not.toContain('MLB Picks')
  })

  it('the new files are ASCII', () => {
    for (const f of ['components/models/projection-model-detail.tsx', 'app/models/page.tsx', 'app/models/[slug]/[system]/page.tsx', 'app/edge/projections/page.tsx',
      'components/picks/research-tabs.tsx', 'components/picks/aligned-bar.tsx', 'components/picks/games-strip.tsx', 'components/picks/day-toggle.tsx', 'lib/board-games.ts', 'lib/board-days.ts', 'lib/sport-board-data.ts', 'scripts/build-brand-kit.mjs']) {
      expect([...read(f)].filter(c => c.charCodeAt(0) > 127)).toEqual([])
    }
  })
})
