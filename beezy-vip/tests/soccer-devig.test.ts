/**
 * Soccer three-way de-vig (lib/ev-math.ts devigThreeWay) against the Python implementation
 * (mlb_core/odds/utils.py devig_three_way): the two must agree to 1e-6 on every method, because the browser
 * de-vigs 1X2 interactively while the backend prices the same books for the Kalshi fair book. Reference values
 * below are the Python outputs, generated 2026-11-03. Also pins: the new soccer source files are ASCII-only
 * (escapes for typographic characters), and the three-way consensus helper's behavior.
 */
import fs from 'fs'
import path from 'path'
import { devigThreeWay } from '../lib/ev-math'
import { threeWayConsensus } from '../lib/soccer-games'
import type { GameOddsRow } from '../lib/game-odds'

const BOOKS: { book: [number, number, number]; expected: Record<string, [number, number, number]> }[] = [
  {
    book: [0.55, 0.28, 0.24],
    expected: {
      proportional: [0.5140186915887851, 0.26168224299065423, 0.22429906542056072],
      power: [0.5272028182545273, 0.25586511643249266, 0.21693206531297998],
      shin: [0.5233837092658818, 0.257942272610638, 0.21867401812348009],
    },
  },
  {
    book: [0.4, 0.3, 0.37],
    expected: {
      proportional: [0.37383177570093457, 0.2803738317757009, 0.34579439252336447],
      power: [0.37648952298467186, 0.277047878621964, 0.3464625983933642],
      shin: [0.3759209927205455, 0.27764784027142814, 0.34643116700802634],
    },
  },
  {
    book: [0.7, 0.2, 0.17],
    expected: {
      proportional: [0.6542056074766355, 0.18691588785046728, 0.1588785046728972],
      power: [0.6791716537605584, 0.17451672627923243, 0.14631161996020908],
      shin: [0.6708683497724696, 0.1792566343611419, 0.14987501586638854],
    },
  },
]

describe('devigThreeWay matches the Python implementation to 1e-6', () => {
  for (const { book, expected } of BOOKS) {
    for (const method of ['proportional', 'power', 'shin'] as const) {
      it(`${method} on ${book.join('/')}`, () => {
        const f = devigThreeWay(book[0], book[1], book[2], method)
        expect(f).not.toBeNull()
        for (let i = 0; i < 3; i++) expect(Math.abs(f![i] - expected[method][i])).toBeLessThan(1e-6)
        expect(Math.abs(f![0] + f![1] + f![2] - 1)).toBeLessThan(1e-9)
      })
    }
  }

  it('power and shin shade the longshot harder than proportional', () => {
    const prop = devigThreeWay(0.55, 0.28, 0.24, 'proportional')!
    for (const method of ['power', 'shin'] as const) {
      const f = devigThreeWay(0.55, 0.28, 0.24, method)!
      expect(f[2]).toBeLessThan(prop[2])
      expect(f[0]).toBeGreaterThan(prop[0])
    }
  })

  it('fails closed on a bad book', () => {
    expect(devigThreeWay(0.3, 0.3, 0.3)).toBeNull()          // sums below 1
    expect(devigThreeWay(0, 0.5, 0.55)).toBeNull()           // a zero leg
    expect(devigThreeWay(Number.NaN, 0.3, 0.3)).toBeNull()
  })
})

describe('threeWayConsensus', () => {
  const row: GameOddsRow = {
    key: 'k', sport: 'soccer', game_key: 'soccer:2026-11-07:CHE@ARS', away: 'CHE', home: 'ARS',
    commence: '2026-11-07T17:30:00Z', market: 'ml', line: null,
    books: [
      { book: 'draftkings', a: 300, b: -140, draw: 260 },
      { book: 'fanduel', a: 290, b: -135, draw: 250 },
      { book: 'thin', a: 280, b: -130, draw: null },
    ],
  }

  it('de-vigs only complete three-way quotes and averages the fairs', () => {
    const c = threeWayConsensus(row, 'power')
    expect(c.nBooks).toBe(2)
    expect(c.books).toHaveLength(2)
    expect(c.fair).not.toBeNull()
    expect(Math.abs(c.fair![0] + c.fair![1] + c.fair![2] - 1)).toBeLessThan(1e-9)
    expect(c.fair![0]).toBeGreaterThan(c.fair![1])
    expect(c.fair![1]).toBeGreaterThan(c.fair![2])
  })

  it('no complete quote -> null fair, never a guess', () => {
    const c = threeWayConsensus({ ...row, books: [{ book: 'x', a: 100, b: -120, draw: null }] })
    expect(c.fair).toBeNull()
    expect(c.nBooks).toBe(0)
  })
})

describe('new soccer source files stay ASCII', () => {
  const files = [
    'lib/soccer-games.ts',
    'lib/ev-math.ts',
    'lib/sports.ts',
    'lib/pick-systems.ts',
    'lib/prop-model-specs.ts',
    'app/ev/games/soccer-games-client.tsx',
    'app/ev/games/games-nav.tsx',
    'app/ev/games/page.tsx',
    'tests/soccer-devig.test.ts',
  ]
  for (const rel of files) {
    it(rel, () => {
      const text = fs.readFileSync(path.resolve(__dirname, '..', rel), 'utf8')
      const bad = [...text].filter(ch => ch.charCodeAt(0) > 127)
      expect(bad).toEqual([])
    })
  }
})
