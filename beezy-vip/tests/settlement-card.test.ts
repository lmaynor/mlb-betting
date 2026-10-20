/** The settlement card renders through Satori: it must stay ASCII (escapes only) and use literal hex, not CSS vars. */
import fs from 'fs'
import path from 'path'

const src = fs.readFileSync(path.resolve(__dirname, '../app/api/og/settlement-card/route.tsx'), 'utf8')

describe('settlement-card route source', () => {
  it('is ASCII only', () => {
    expect([...src].filter(c => c.charCodeAt(0) > 127)).toEqual([])
  })
  it('uses no CSS variables (Satori cannot resolve var())', () => {
    expect(src).not.toMatch(/var\(--/)
  })
  it('is an edge route with the spec query params', () => {
    expect(src).toContain('export const runtime = "edge"')
    expect(src).toContain('"date"')
    expect(src).toContain('"sport"')
  })
  it('shows UNITS (profit / stake), never raw dollar profit labelled u', () => {
    // bets.profit and bets.stake are dollars; printing profit as "u" showed a -$190 day as -190u
    expect(src).toContain('const unitsOf')
    expect(src).not.toMatch(/fmtU\(num\(/)
    expect(src).not.toMatch(/reduce\(\(a, r\) => a \+ num\(r\.profit\)/)
  })
})
