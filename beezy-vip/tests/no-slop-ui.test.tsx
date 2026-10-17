/** Guardrails from NO_SLOP_CHECKLIST.md: static source checks for the patterns the checklist bans on work surfaces. */
import fs from 'fs'
import path from 'path'
import { TIER_META } from '../lib/beezy-score'
import { SPORT_LIST } from '../lib/sports'

const root = path.resolve(__dirname, '..')
const read = (f: string) => fs.readFileSync(path.join(root, f), 'utf8')

describe('no-slop-ui', () => {
  it('tier styles carry no neon glow', () => {
    for (const m of Object.values(TIER_META)) expect(m.glow).toBe('none')
  })
  it('sport nav links carry no static decorative badges', () => {
    for (const p of SPORT_LIST) expect(p).not.toHaveProperty('navBadge')
    expect(read('components/layout/nav.tsx')).not.toMatch(/navBadge|badge=/)
  })
  it('no hover transform on .card-hover or the /ev table', () => {
    const css = read('app/globals.css')
    const hover = css.split('\n').filter(l => /:hover/.test(l))
    for (const l of hover) expect(l).not.toMatch(/transform/)
  })
  it.each([
    'app/ev/ev-client.tsx', 'app/analytics/analytics-client.tsx', 'components/picks/market-prices.tsx', 'components/picks/sport-projections-board.tsx', 'components/picks/sport-tabs.tsx',
    'components/picks/sport-board-page.tsx', 'components/picks/system-page.tsx', 'components/picks/system-methodology.tsx',
    'components/picks/systems-table.tsx', 'components/picks/filter-bar.tsx', 'components/picks/games-strip.tsx', 'components/picks/day-toggle.tsx',
  ])('%s: no eyebrow+mood header, no gradient header, no card-per-row stack', (f) => {
    const src = read(f)
    expect(src).not.toMatch(/dell-display[^}]*clamp\(/)   // the mood-headline h1 style
    expect(src).not.toMatch(/dell-heading[^>]*>\s*(ANALYTICS|\+EV TERMINAL)</)
    expect(src).not.toMatch(/linear-gradient/)
    expect(src).not.toMatch(/kpi-grid|<Kpi\b/)
    expect(src).not.toMatch(/<article/)
  })
  it('top nav has no bankroll pill and the ribbon has no Daily Card link', () => {
    const nav = read('components/layout/nav.tsx')
    expect(nav).not.toMatch(/BANKROLL|useBankroll/)
    expect(nav).not.toMatch(/DAILY CARD/)
  })
  it('the shared ticker and nav are pinned together in one sticky chrome wrapper', () => {
    expect(read('app/layout.tsx')).toMatch(/site-chrome/)
    expect(read('app/globals.css')).toMatch(/\.site-chrome \{ position: sticky/)
  })
})
