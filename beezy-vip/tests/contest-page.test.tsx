import fs from 'fs'
import path from 'path'
import { renderToStaticMarkup } from 'react-dom/server'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })
// the live page reads the Clerk user and the contest API; the page test supplies both
const mockUser = jest.fn()
const mockFeed = jest.fn()
jest.mock('@/lib/contest/identity', () => ({ contestUser: () => mockUser() }))
jest.mock('@/lib/contest/backend', () => ({ fetchFeed: (...a: unknown[]) => mockFeed(...a) }))

describe('/contest page', () => {
  const OLD = process.env.CONTEST_ENABLED
  afterEach(() => { if (OLD === undefined) delete process.env.CONTEST_ENABLED; else process.env.CONTEST_ENABLED = OLD })

  it('answers 404 unless the kill switch is on (0, false, off, no and empty are all off)', async () => {
    const { default: ContestPage } = await import('@/app/contest/page')
    for (const v of [undefined, '', '0', 'false', 'off', 'no']) {
      if (v === undefined) delete process.env.CONTEST_ENABLED; else process.env.CONTEST_ENABLED = v
      await expect(ContestPage()).rejects.toThrow()               // next/navigation notFound() throws NEXT_HTTP_ERROR_FALLBACK;404
    }
    process.env.CONTEST_ENABLED = '1'
    await expect(ContestPage()).resolves.toBeTruthy()
    const broke = await ContestPage({ searchParams: Promise.resolve({ preview: 'broke' }) }) as { props: { feed: { balanceCents: number } } }
    expect(broke.props.feed.balanceCents).toBe(4_000)                // the hidden lifeline preview
  })

  describe('live mode (CONTEST_SLUG set)', () => {
    const OLD_SLUG = process.env.CONTEST_SLUG
    beforeEach(() => { process.env.CONTEST_ENABLED = '1'; process.env.CONTEST_SLUG = 'bowls'; mockUser.mockReset(); mockFeed.mockReset() })
    afterEach(() => { if (OLD_SLUG === undefined) delete process.env.CONTEST_SLUG; else process.env.CONTEST_SLUG = OLD_SLUG })
    const page = async () => (await import('@/app/contest/page')).default()
    const text = (n: unknown): string => renderToStaticMarkup(n as React.ReactElement)

    it('asks an anonymous visitor to sign in and never calls the API', async () => {
      mockUser.mockResolvedValue(null)
      expect(text(await page())).toContain('Sign in')
      expect(mockFeed).not.toHaveBeenCalled()
    })
    it('passes the signed-in player and the slug to the API and renders the live client with the slug', async () => {
      mockUser.mockResolvedValue({ id: 'user_1', suggestedName: 'alex' })
      mockFeed.mockResolvedValue({ status: 200, body: { name: 'Bowl Bankroll', sample: false } })
      const el = await page() as { props: { feed: { sample: boolean }; slug: string } }
      expect(mockFeed).toHaveBeenCalledWith('bowls', 'user_1', 'alex')
      expect(el.props.slug).toBe('bowls')
      expect(el.props.feed.sample).toBe(false)
    })
    it('says so when the contest does not exist or the service is down, and never falls back to sample data', async () => {
      mockUser.mockResolvedValue({ id: 'user_1', suggestedName: '' })
      mockFeed.mockResolvedValue({ status: 404, body: {} })
      expect(text(await page())).toContain('not open')
      mockFeed.mockResolvedValue({ status: 503, body: {} })
      expect(text(await page())).toContain('could not be loaded')
    })
  })

  const FILES = ['app/contest/page.tsx', 'components/contest/board.tsx', 'components/contest/bets-table.tsx', 'components/contest/contest-client.tsx',
    'components/contest/leaderboard.tsx', 'components/contest/receipt.tsx', 'components/contest/slip-panel.tsx', 'components/contest/team-logo.tsx']
  const src = (f: string) => fs.readFileSync(path.resolve(__dirname, '..', f), 'utf8')

  it('uses only the repo type scale (10/11/12/13/14/16, 20 for the h1) and never text under 10px', () => {
    for (const f of FILES) {
      for (const m of src(f).matchAll(/fontSize: *(\d+(?:\.\d+)?)/g)) expect([10, 11, 12, 13, 14, 16, 20]).toContain(Number(m[1]))
    }
  })

  it('has no exclamation points, emoji, tracked or CSS-uppercase text, glows or gradients in its source', () => {
    for (const f of FILES) {
      const s = src(f)
      expect(s).not.toMatch(/(?<![!=])![ '"`<}](?!=)/)               // "Bet placed!" style copy; not `!x` or `!==`
      expect(s).not.toMatch(/textTransform|text-transform|letterSpacing|letter-spacing|tracking-|gradient\(|boxShadow|box-shadow|drop-shadow/)
      expect(/[^\x00-\x7F]/.test(s)).toBe(false)
    }
  })

  it('is noindex and its stylesheet only changes colours on hover (no transform)', async () => {
    const { metadata } = await import('@/app/contest/page')
    expect(metadata.robots).toEqual({ index: false, follow: false })
    const css = fs.readFileSync(path.resolve(__dirname, '../app/contest/contest.css'), 'utf8')
    for (const l of css.split('\n').filter(x => /:hover/.test(x))) expect(l).not.toMatch(/transform|translate|scale/)
    expect(css).not.toMatch(/gradient|box-shadow/)
  })
})
