import fs from 'fs'
import path from 'path'

jest.mock('../app/contest/contest.css', () => ({}), { virtual: true })

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
