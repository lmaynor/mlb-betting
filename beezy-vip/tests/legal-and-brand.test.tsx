/** Solo-operator legal copy and the single-SVG brand kit: no placeholders, one support address, one mark everywhere. */
import fs from 'fs'
import path from 'path'
import { render } from '@testing-library/react'
import { BrandMark, BrandTile, BrandWordmark } from '@/components/ui/brand-mark'
import { BRAND_PATHS } from '@/lib/brand-paths'
import { SITE_CONFIG } from '@/lib/site-config'

const root = path.resolve(__dirname, '..')
const read = (f: string) => fs.readFileSync(path.join(root, f), 'utf8')
const LEGAL = ['terms', 'privacy', 'refunds', 'responsible-gambling'].map(n => `app/legal/${n}/page.tsx`)
const SHARED = ['components/legal/legal-page.tsx', 'lib/site-config.ts']

describe('legal pages', () => {
  it.each(LEGAL)('%s has no placeholders, fabricated entities or legacy hosts', (f) => {
    const text = read(f)
    for (const bad of ['LAWYER_REVIEW', 'Beezy LLC', 'Beezy, Inc.', 'beezy.vip', 'mlb-betting-rose.vercel.app', '[Operator', '[legal@', '[support@', 'legal@beezy', 'privacy@beezy', 'IN LEGAL REVIEW']) {
      expect(text).not.toContain(bad)
    }
  })

  it.each(LEGAL)('%s routes contact to support@beezy.fyi through the shared contact line', (f) => {
    const text = read(f)
    expect(text.includes('support@beezy.fyi') || text.includes('<ContactLine />')).toBe(true)
  })

  it('the shared contact line and config carry the support address, X handle and Discord', () => {
    expect(SITE_CONFIG.supportEmail).toBe('support@beezy.fyi')
    expect(SITE_CONFIG.xHandle).toBe('@beezy_fyi')
    expect(read('components/legal/legal-page.tsx')).toContain('SITE_CONFIG.supportEmail')
    expect(SITE_CONFIG.discordInviteUrl).toMatch(/^https:\/\/discord\.gg\//)
  })

  it('terms state the sole-proprietor operator, the $0 / 30-day cap and Illinois law', () => {
    const t = read('app/legal/terms/page.tsx')
    expect(t).toContain('sole proprietor')
    expect(t).toContain('THIRTY (30) DAYS')
    expect(t).toContain('governingState')
  })

  it('the legal pages and brand files are ASCII', () => {
    for (const f of [...LEGAL, ...SHARED, 'components/ui/brand-mark.tsx', 'lib/brand-paths.ts', 'lib/og-brand.tsx', 'app/manifest.ts', 'app/apple-icon.tsx']) {
      expect([...read(f)].filter(c => c.charCodeAt(0) > 127)).toEqual([])
    }
  })
})

describe('site-wide copy', () => {
  it('footer carries the risk disclaimer and the new copyright line, not the old one', () => {
    const f = read('components/layout/footer.tsx')
    expect(f).toContain('SITE_CONFIG.riskDisclaimer')
    expect(f).toContain('Independent quantitative sports analytics')
    expect(f).not.toContain('All rights reserved')
    for (const href of ['/legal/terms', '/legal/privacy', '/legal/refunds', '/legal/responsible-gambling']) expect(f).toContain(href)
  })

  it('pricing no longer promises a 7-day money-back guarantee', () => {
    expect(read('components/landing/pricing.tsx')).not.toMatch(/money-back/i)
  })
})

describe('brand primitives', () => {
  it('BrandMark renders the three source paths in #57F287', () => {
    const { container } = render(<BrandMark size={40} />)
    const svg = container.querySelector('svg')!
    expect(svg.getAttribute('viewBox')).toBe('0 0 1071 787')
    const paths = [...container.querySelectorAll('path')]
    expect(paths).toHaveLength(BRAND_PATHS.length)
    expect(paths.every(p => p.getAttribute('fill') === '#57F287')).toBe(true)
  })

  it('BrandMark takes a monochrome override', () => {
    const { container } = render(<BrandMark color="#f3f4f6" />)
    expect(container.querySelector('path')!.getAttribute('fill')).toBe('#f3f4f6')
  })

  it('BrandTile is a dark badge around the mark', () => {
    const { container } = render(<BrandTile size={32} />)
    expect(container.querySelector('svg')).not.toBeNull()
    const tile = container.firstElementChild as HTMLElement
    expect(tile.style.background).toBe('rgb(9, 10, 15)')
  })

  it('BrandWordmark renders the lowercase beezy.fyi lockup, and can drop the domain', () => {
    const { container, unmount } = render(<BrandWordmark size="md" />)
    expect(container.textContent).toBe('beezy.fyi')
    expect(container.querySelector('svg')).not.toBeNull()
    unmount()
    const bare = render(<BrandWordmark showDomain={false} />)
    expect(bare.container.textContent).toBe('beezy')
  })

  it('the source SVG, brand paths and OG helper agree on the geometry', () => {
    const svg = read('public/logo-vector.svg')
    const ds = [...svg.matchAll(/ d="([^"]+)"/g)].map(m => m[1])
    expect(ds).toEqual([...BRAND_PATHS])
    expect(svg).toContain('#57F287')
    expect(read('lib/og-brand.tsx')).toContain('BRAND_PATHS')
  })
})

describe('brand wiring', () => {
  it('nav uses the brand mark and keeps no placeholder emblem', () => {
    const nav = read('components/layout/nav.tsx')
    expect(nav).toContain('<BrandWordmark')
    expect(nav).not.toContain('Emblem')
    expect(nav).not.toContain('dashed')
  })

  it('footer, blocked and not-found show the wordmark', () => {
    for (const f of ['components/layout/footer.tsx', 'app/blocked/page.tsx', 'app/not-found.tsx']) expect(read(f)).toContain('<BrandWordmark')
  })

  it.each(['route.tsx', 'picks-card/route.tsx', 'props-card/route.tsx', 'games-card/route.tsx', 'settlement-card/route.tsx', 'ev-recap-card/route.tsx'])(
    'OG route %s carries the shared badge',
    (r) => {
      const src = read(`app/api/og/${r}`)
      expect(src).toContain('<OgBrandBadge')
      expect(src).not.toContain('>BEEZY<')
    },
  )

  it('manifest and root metadata are set for the brand', () => {
    expect(read('app/manifest.ts')).toContain("background_color: '#090a0f'")
    expect(read('app/manifest.ts')).toContain('/icon-512.png')
    const layout = read('app/layout.tsx')
    expect(layout).toContain("new URL('https://beezy.fyi')")
    expect(layout).toContain("site: '@beezy_fyi'")
  })

  it('icon.svg and the generated PNGs exist', () => {
    expect(read('app/icon.svg')).toContain('#57F287')
    for (const f of ['public/icon-192.png', 'public/icon-512.png', 'public/apple-touch-icon.png', 'public/brand/discord-avatar-512.png', 'public/brand/x-avatar-400.png', 'public/brand/x-banner-1500x500.png']) {
      expect(fs.existsSync(path.join(root, f))).toBe(true)
    }
  })
})
