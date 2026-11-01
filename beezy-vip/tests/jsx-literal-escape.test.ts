/**
 * In JSX TEXT a backslash-u escape is NOT interpreted: `<span>\u03c1</span>` renders the six characters \u03c1. (Inside a JS
 * string, a template literal or a `{'...'}` expression it is fine.) Source files stay ASCII, so the correct spellings in JSX
 * text are an HTML entity (&rho;, &middot;) or an expression ({'\u2193'}). This walks every .tsx file under app/ and
 * components/ with the TypeScript parser and fails on any JsxText node holding a literal escape.
 */
import fs from 'fs'
import path from 'path'
import ts from 'typescript'

const ROOT = path.resolve(__dirname, '..')
const ESCAPE = /\\u[0-9a-fA-F]{4}|\\u\{[0-9a-fA-F]+\}/

function tsxFiles(dir: string, out: string[] = []): string[] {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name.startsWith('.')) continue
    const p = path.join(dir, e.name)
    if (e.isDirectory()) tsxFiles(p, out)
    else if (e.name.endsWith('.tsx')) out.push(p)
  }
  return out
}

export function literalEscapesInJsxText(source: string, fileName = 'x.tsx'): { line: number; text: string }[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const hits: { line: number; text: string }[] = []
  const visit = (n: ts.Node) => {
    if (ts.isJsxText(n) && ESCAPE.test(n.text)) {
      hits.push({ line: sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1, text: n.text.trim().slice(0, 80) })
    }
    ts.forEachChild(n, visit)
  }
  visit(sf)
  return hits
}

describe('no literal \\uXXXX escape in JSX text', () => {
  it('the scanner flags JSX text but not strings, templates or expressions', () => {
    const src = [
      'export const A = () => <span>\\u03c1 here</span>',
      "export const B = () => <span>{'\\u03c1'} and {`\\u00b7`} and &rho;</span>",
      "export const C = () => <span title=\"\\u03c1\">x</span>",
    ].join('\n')
    expect(literalEscapesInJsxText(src).map(h => h.line)).toEqual([1])
  })

  it('no .tsx file under app/ or components/ has one', () => {
    const offenders: string[] = []
    for (const dir of ['app', 'components']) {
      for (const file of tsxFiles(path.join(ROOT, dir))) {
        for (const h of literalEscapesInJsxText(fs.readFileSync(file, 'utf8'), file)) {
          offenders.push(`${path.relative(ROOT, file)}:${h.line}  ${h.text}`)
        }
      }
    }
    expect(offenders).toEqual([])
  })
})

describe('LockGlyph (the lock icon on the hidden-book rows)', () => {
  it('renders an inline SVG with an accessible name, not escaped text', () => {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { renderToStaticMarkup } = require('react-dom/server') as typeof import('react-dom/server')
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { LockGlyph } = require('@/components/ui/lock-glyph') as typeof import('@/components/ui/lock-glyph')
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const React = require('react') as typeof import('react')
    const html = renderToStaticMarkup(React.createElement(LockGlyph, { size: 12, color: '#7d8596' }))
    expect(html).toContain('<svg')
    expect(html).toContain('aria-label="Locked"')
    expect(html).toContain('stroke="#7d8596"')
    expect(html).not.toMatch(/\\u|\\\\/)
  })
})
