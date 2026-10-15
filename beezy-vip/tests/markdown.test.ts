import { renderMarkdown, plainSummary } from '../lib/markdown'
import { ARTICLES } from '../lib/articles-static'

describe('renderMarkdown', () => {
  it('renders tables, ordered lists, blockquotes and escapes html', () => {
    const html = renderMarkdown('| a | b |\n|---|---|\n| 1 | 2 |\n\n1. one\n2. two\n\n> quoted <b>x</b>\n')
    expect(html).toContain('<table>')
    expect(html).toContain('<th>a</th>')
    expect(html).toContain('<td>2</td>')
    expect(html).toContain('<ol><li>one</li><li>two</li></ol>')
    expect(html).toContain('<blockquote>quoted &lt;b&gt;x&lt;/b&gt;</blockquote>')
  })

  it('keeps math like p * d intact and only emphasises real *words*', () => {
    const html = renderMarkdown('so p * d - 1 and *this* matters')
    expect(html).toContain('p * d - 1')
    expect(html).toContain('<em>this</em>')
  })

  it('leaves no raw markdown markers in any article body', () => {
    for (const a of ARTICLES) {
      const html = renderMarkdown(a.content.replace(/^#\s+.*\n+/, ''))
      expect(html).not.toMatch(/\|\s*-{3,}/)       // unrendered table separator
      expect(html).not.toMatch(/<p>#/)             // unrendered heading
      expect(html).not.toMatch(/<h1>/)             // the page header owns the only h1
    }
  })

  it('plainSummary strips markers and cuts at a word', () => {
    const s = plainSummary('The **Kelly Criterion** is a *fraction* of your bankroll ' + 'word '.repeat(60), 60)
    expect(s).not.toMatch(/\*/)
    expect(s.length).toBeLessThanOrEqual(64)
    expect(s.endsWith('...')).toBe(true)
  })
})
