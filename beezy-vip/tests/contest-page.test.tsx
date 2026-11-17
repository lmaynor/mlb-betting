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
      expect(() => ContestPage()).toThrow()                       // next/navigation notFound() throws NEXT_HTTP_ERROR_FALLBACK;404
    }
    process.env.CONTEST_ENABLED = '1'
    expect(() => ContestPage()).not.toThrow()
  })

  it('is noindex and its stylesheet only changes colours on hover (no transform)', async () => {
    const { metadata } = await import('@/app/contest/page')
    expect(metadata.robots).toEqual({ index: false, follow: false })
    const css = fs.readFileSync(path.resolve(__dirname, '../app/contest/contest.css'), 'utf8')
    for (const l of css.split('\n').filter(x => /:hover/.test(x))) expect(l).not.toMatch(/transform|translate|scale/)
    expect(css).not.toMatch(/gradient|box-shadow/)
  })
})
