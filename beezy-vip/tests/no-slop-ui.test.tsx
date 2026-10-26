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
    'app/ev/odds-client.tsx', 'app/analytics/analytics-client.tsx', 'components/picks/market-prices.tsx', 'components/picks/sport-projections-board.tsx', 'components/picks/sport-tabs.tsx',
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

/** Every .ts/.tsx/.css file under app/, components/ and lib/, as [relative path, source]. */
function sources(): [string, string][] {
  const out: [string, string][] = []
  const walk = (dir: string) => {
    for (const name of fs.readdirSync(path.join(root, dir))) {
      const rel = path.join(dir, name)
      if (fs.statSync(path.join(root, rel)).isDirectory()) walk(rel)
      else if (/\.(tsx?|css)$/.test(name)) out.push([rel, read(rel)])
    }
  }
  for (const d of ['app', 'components', 'lib']) walk(d)
  return out
}

describe('design-system debt guards', () => {
  it('uses none of the six retired off-palette grays', () => {
    const banned = /#(1a1d27|9aa0aa|8a8a93|0d0d10|d8d8de|5a5a64)\b/i
    const hits = sources().filter(([, src]) => banned.test(src)).map(([f]) => f)
    expect(hits).toEqual([])
  })

  it('renders no text below the 10px floor', () => {
    const small = /(?:fontSize: *'?|font-size: *|fontSize=["{])(?:[0-9]|[0-9]\.[0-9]+)(?:px)?['"}]?\s*[,;} \n]/
    const hits = sources().filter(([, src]) => small.test(src)).map(([f]) => f)
    expect(hits).toEqual([])
  })

  it('the home hero is a solid surface: no gradient, no radius-xl, no paper-mode eyebrow', () => {
    const hero = read('components/landing/hero.tsx')
    expect(hero).not.toMatch(/linear-gradient|radius-xl/)
    expect(hero).toMatch(/SURFACE_CARD/)
    expect(hero).toMatch(/VERIFIED PUBLIC LEDGER &middot; 1\/4 KELLY SIZING/)
    expect(read('app/page.tsx')).not.toMatch(/linear-gradient|radius-xl/)
    // the legal disclosure stays in the footer, as one muted paragraph (no PAPER MODE callout box)
    const footer = read('components/layout/footer.tsx')
    expect(footer).toMatch(/riskDisclaimer/)
    expect(footer).not.toMatch(/PAPER MODE/)
  })

  it('primary buttons shift colour on hover and never glow', () => {
    const css = read('app/globals.css')
    expect(css).not.toMatch(/--glow-signal/)
    const hover = /\.btn-primary:hover[^{]*\{([^}]*)\}/.exec(css)
    expect(hover).not.toBeNull()
    expect(hover![1]).toMatch(/background: #6bf596/)
    expect(hover![1]).not.toMatch(/box-shadow/)
  })

  it('there is one surface spec: CARD_STYLE is SURFACE_CARD at 8px with a 1px shadow, and .ribbon-card-wrap matches', async () => {
    const tokens = await import('../lib/tokens')
    expect(tokens.CARD_STYLE).toBe(tokens.SURFACE_CARD)
    expect(tokens.SURFACE_CARD).toEqual({ background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', boxShadow: '0 1px 2px rgba(0,0,0,0.45)' })
    expect(tokens.SURFACE_ELEVATED).toEqual({ background: '#171a24', border: '1px solid #2e3446', borderRadius: '8px' })
    expect(tokens.TABLE_TH.fontSize).toBe('10px')
    expect(tokens.TABLE_TD.borderBottom).toBe('1px solid #151822')
    const wrap = /\.ribbon-card-wrap \{([^}]*)\}/.exec(read('app/globals.css'))![1]
    expect(wrap).toMatch(/border-radius: 8px/)
    expect(wrap).toMatch(/box-shadow: 0 1px 2px rgba\(0, 0, 0, 0\.45\)/)
  })

  it('tables share the tokens.ts primitives instead of re-declaring them', () => {
    for (const f of ['components/picks/top-picks-table.tsx', 'components/picks/systems-table.tsx', 'components/picks/sport-projections-board.tsx', 'app/picks/history/history-table.tsx']) {
      const src = read(f)
      expect(src).toMatch(/TABLE_TH/)
      expect(src).not.toMatch(/const TH = \{ textAlign/)
    }
  })

  it('canonical .ui-* classes exist and the .dell-* names are aliases of the same rules', () => {
    const css = read('app/globals.css')
    expect(css).toMatch(/\.ui-display,\s*\.dell-display \{/)
    expect(css).toMatch(/\.ui-heading,\s*\.dell-heading \{/)
    expect(css).toMatch(/\.btn-primary,\s*\.dell-btn-primary,\s*\.dell-btn-red \{/)
  })

  it('loads the three-face stack with the variable names the CSS and inline styles read', () => {
    const layout = read('app/layout.tsx')
    expect(layout).toMatch(/Plus_Jakarta_Sans\(\{[^}]*variable: '--font-inter'/)
    expect(layout).toMatch(/Space_Grotesk\(\{[^}]*variable: '--font-display'[^}]*weight: \['600', '700'\]/)
    expect(layout).toMatch(/IBM_Plex_Mono\(\{[^}]*variable: '--font-mono'[^}]*weight: \['400', '500', '600', '700'\]/)
    expect(layout).toMatch(/uiSans\.variable.*displaySans\.variable.*plexMono\.variable/)
    expect(layout).not.toMatch(/JetBrains|\bInter\(/)
    const css = read('app/globals.css')
    expect(css).not.toMatch(/--font-display: var\(--font-inter\)/)
    expect(css).toMatch(/font-variant-numeric: tabular-nums slashed-zero/)
    expect(css).toMatch(/font-feature-settings: "tnum" 1, "zero" 1/)
    expect(read('components/ui/brand-mark.tsx')).toMatch(/var\(--font-display\), var\(--font-inter\), sans-serif[^\n]*fontWeight: 700, letterSpacing: '-0\.035em'/)
  })

  it('OG routes keep Satori-safe system fonts (no next/font, no remote font fetch)', () => {
    const og = sources().filter(([f]) => f.startsWith(path.join('app', 'api', 'og')) || f === path.join('lib', 'og-brand.tsx'))
    expect(og.length).toBeGreaterThan(0)
    for (const [, src] of og) expect(src).not.toMatch(/next\/font|fonts\.googleapis|fonts\.gstatic/)
  })
})
