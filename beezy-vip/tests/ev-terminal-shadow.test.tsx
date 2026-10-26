/**
 * Source files that carry typographic characters must stay ASCII (the repo is edited from Windows PowerShell / cp1252
 * shells that turn raw multibyte characters into mojibake). The +EV Terminal UI that used to share this file was folded
 * into the Odds Terminal (app/ev/page.tsx).
 */
import fs from 'fs'
import path from 'path'

describe('source files with typographic characters stay ASCII', () => {
  const root = path.resolve(__dirname, '..')
  const files = [
    'lib/sport-assets.ts', 'lib/sports.ts', 'lib/types.ts', 'components/ui/sport-assets.tsx', 'app/ev/odds-client.tsx', 'app/ev/ledger-strip.tsx', 'lib/odds-headshots.ts',
    'components/picks/market-prices.tsx', 'components/picks/sport-projections-board.tsx', 'components/picks/sport-tabs.tsx',
    'components/picks/sport-board-page.tsx', 'components/picks/system-page.tsx', 'components/picks/system-methodology.tsx',
    'components/picks/games-strip.tsx', 'components/picks/day-toggle.tsx', 'components/picks/filter-bar.tsx', 'lib/team-directory.ts', 'lib/board-rows.ts', 'lib/board-games.ts',
    'app/sports/[sport]/page.tsx',
    'app/api/og/route.tsx', 'app/api/og/ev-recap-card/route.tsx', 'app/api/og/games-card/route.tsx', 'app/api/og/picks-card/route.tsx', 'app/api/og/props-card/route.tsx',
    'app/edge/page.tsx', 'app/edge/edge-client.tsx', 'app/edge/cockpit.tsx', 'app/api/edge-cockpit/route.ts',
    'components/layout/nav.tsx', 'components/landing/hero.tsx', 'lib/betting-api.ts', 'app/analytics/analytics-client.tsx', 'lib/beezy-score.ts',
    'tests/sport-assets.test.ts', 'tests/tokens.test.ts', 'tests/ev-terminal-shadow.test.tsx', 'tests/edge-cockpit.test.tsx',
  ]
  it.each(files)('%s', (f) => {
    const text = fs.readFileSync(path.join(root, f), 'utf8')
    const bad = [...text].filter(c => c.charCodeAt(0) > 127)
    expect(bad).toEqual([])
  })
  it('fold() strips combining marks via an escaped range', () => {
    const src = fs.readFileSync(path.join(root, 'lib/sport-assets.ts'), 'utf8')
    expect(src).toContain('.replace(/[\\u0300-\\u036f]/g, \'\')')
  })
})

