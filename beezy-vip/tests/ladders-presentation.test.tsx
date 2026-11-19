import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { LadderCard } from '@/app/ev/ladder-card'
import {
  gateFloorAmerican,
  groupLadderRows,
  orderedRungBars,
  type LadderGroup,
} from '@/lib/ladders'
import type { BoardRow } from '@/lib/odds-screen'

const NOW = Date.parse('2026-11-20T12:00:00Z')
const TS = '2026-11-20T11:58:00Z'

function makeRow(overrides: Partial<BoardRow> = {}): BoardRow {
  return {
    game_key: 'nfl:2026-11-20_KC@BUF',
    commence: '2026-11-20T20:00:00Z',
    away: 'KC',
    home: 'BUF',
    player: 'Test Player',
    player_norm: 'test player',
    stat: 'rec_yds',
    line: 50.5,
    books: [],
    n_ref: 0,
    n_fresh_ref: 0,
    ...overrides,
  } as BoardRow
}

describe('gateFloorAmerican', () => {
  it('passes worked checks', () => {
    expect(gateFloorAmerican(0.16, 'count')).toBe(557)
    expect(gateFloorAmerican(0.60, 'yardage')).toBe(-141)
  })

  it('returns null for milestone', () => {
    expect(gateFloorAmerican(0.16, 'milestone')).toBeNull()
    expect(gateFloorAmerican(0.60, 'milestone')).toBeNull()
  })

  it('returns null when result would be a placeholder cap', () => {
    expect(gateFloorAmerican(0.001, 'count')).toBeNull()
    expect(gateFloorAmerican(0.0001, 'yardage')).toBeNull()
  })

  it('returns null for invalid p', () => {
    expect(gateFloorAmerican(null, 'count')).toBeNull()
    expect(gateFloorAmerican(undefined, 'count')).toBeNull()
    expect(gateFloorAmerican(NaN, 'count')).toBeNull()
    expect(gateFloorAmerican(0, 'count')).toBeNull()
    expect(gateFloorAmerican(-0.1, 'count')).toBeNull()
    expect(gateFloorAmerican(1, 'count')).toBeNull()
    expect(gateFloorAmerican(1.5, 'count')).toBeNull()
  })
})

describe('Novig hero bar', () => {
  it('handles Novig as best over: side not verified, hero repaired to best non-Novig, rendered appropriately', () => {
    const books = [
      { book: 'novig', over: 500, under: -600, ts: TS },
      { book: 'draftkings', over: 400, under: -480, ts: TS },
      { book: 'fanduel', over: 390, under: -470, ts: TS },
      { book: 'betmgm', over: 385, under: -460, ts: TS },
      { book: 'caesars', over: 380, under: -450, ts: TS },
    ]

    const row = makeRow({
      stat: 'rec_yds',
      books,
      n_ref: 5,
      n_fresh_ref: 5,
      disp: 0.02,
      p_cons: 0.25,
      best_over: { book: 'novig', american: 500, p_cons_loo: 0.25, disp_loo: 0.02, n_ref_loo: 4 },
      best_under: { book: 'fanduel', american: -470, p_cons_loo: 0.25, disp_loo: 0.02, n_ref_loo: 4 },
    })

    const groups = groupLadderRows([row], NOW)
    expect(groups).toHaveLength(1)
    const g = groups[0]

    expect(g.rungs[0].over!.verified).toBe(false)
    expect(g.rungs[0].over!.gateFail).toBe('novig_best')
    expect(g.rungs[0].over!.novigBest).toBe(true)
    expect(g.verified).toBe(false)

    expect(g.rungs[0].hero?.american).toBe(400)

    const html = renderToStaticMarkup(<LadderCard group={g} />)

    expect(html).toContain('Novig · Depth n/a')
    expect(html).toContain('+400')

    const novigPos = html.indexOf('Novig · Depth n/a')
    for (const book of ['draftkings', 'fanduel', 'betmgm', 'caesars']) {
      const bookPos = html.indexOf(book)
      expect(bookPos).toBeGreaterThan(-1)
      expect(bookPos).toBeLessThan(novigPos)
    }

    expect(html).not.toContain('Net EV')
  })
})

describe('Bar ordering', () => {
  it('renders a stale sportsbook bar (lag > 30 via ts) carrying the best price below an active sportsbook bar', () => {
    const row = makeRow({
      stat: 'rec_yds',
      books: [
        { book: 'draftkings', over: 300, under: -360, ts: '2026-11-20T11:20:00Z' },
        { book: 'fanduel', over: 200, under: -240, ts: '2026-11-20T11:55:00Z' },
      ],
    })

    const groups = groupLadderRows([row], NOW)
    const rung = groups[0].rungs[0]
    const ordered = orderedRungBars(rung, 'OVER')

    expect(ordered[0].book).toBe('fanduel')
    expect(ordered[0].tier).toBe(0)
    expect(ordered[1].book).toBe('draftkings')
    expect(ordered[1].tier).toBe(1)

    const html = renderToStaticMarkup(<LadderCard group={groups[0]} />)
    const fanduelPos = html.indexOf('fanduel')
    const dkPos = html.indexOf('draftkings')
    expect(fanduelPos).toBeGreaterThan(-1)
    expect(dkPos).toBeGreaterThan(-1)
    expect(fanduelPos).toBeLessThan(dkPos)
  })
})

describe('Verified rung rendering', () => {
  const books = [
    { book: 'draftkings', over: 600, under: -800, ts: TS },
    { book: 'fanduel', over: 500, under: -700, ts: TS },
    { book: 'betmgm', over: 500, under: -700, ts: TS },
    { book: 'caesars', over: 500, under: -700, ts: TS },
    { book: 'betrivers', over: 500, under: -700, ts: TS },
  ]

  const row = makeRow({
    stat: 'receptions',
    line: 3.5,
    books,
    n_ref: 5,
    n_fresh_ref: 5,
    disp: 0.03,
    p_cons: 0.16,
    best_over: { book: 'draftkings', american: 600, p_cons_loo: 0.16, disp_loo: 0.03, n_ref_loo: 4 },
    best_under: { book: 'fanduel', american: -700, p_cons_loo: 0.16, disp_loo: 0.03, n_ref_loo: 4 },
  })

  it('renders Consensus Fair, Gate floor (+5.0%): +557, and does NOT contain Playable to', () => {
    const groups = groupLadderRows([row], NOW)
    expect(groups[0].verified).toBe(true)
    const html = renderToStaticMarkup(<LadderCard group={groups[0]} />)

    expect(html).toContain('Consensus Fair')
    expect(html).toContain('Gate floor (+5.0%): +557')
    expect(html).not.toContain('Playable to')
  })

  it('enforces copy guards on rendered verified card', () => {
    const groups = groupLadderRows([row], NOW)
    const html = renderToStaticMarkup(<LadderCard group={groups[0]} />)

    expect(html).not.toMatch(/playable to/i)
    expect(html).not.toMatch(/kelly/i)
    expect(html).not.toMatch(/units/i)
    expect(html).not.toMatch(/minutes/i)
  })
})

describe('Below-floor rendering', () => {
  it('constructs a LadderGroup literal directly with verified rung worse than floor, asserts Below floor and floor value', () => {
    const belowFloorGroup: LadderGroup = {
      key: 'g1|player|receptions',
      gameKey: 'nfl:2026-11-20:KC@BUF',
      commence: '2026-11-20T20:00:00Z',
      away: 'KC',
      home: 'BUF',
      player: 'Test Player',
      playerNorm: 'test player',
      stat: 'receptions',
      category: 'count',
      kind: 'curves',
      defaultRung: 0,
      verified: true,
      premium: false,
      state: 'verified',
      grpBooksAny: 5,
      grpBooks2sided: 5,
      modalOpen: 4.5,
      modalNow: 4.5,
      rungs: [
        {
          line: 4.5,
          row: makeRow({ stat: 'receptions', line: 4.5 }),
          books: [
            {
              book: 'draftkings',
              over: 500,
              under: -700,
              ts: TS,
              lagMin: 0,
              staleBand: 'fresh',
              staleOpener: false,
              overCap: false,
              underCap: false,
            },
          ],
          nRef: 5,
          disp: 0.03,
          pCons: 0.16,
          rungVig: 0.04,
          over: null,
          under: null,
          hero: {
            side: 'OVER',
            book: 'draftkings',
            american: 500,
            netEvPct: 8.0,
            pConsLoo: 0.16,
            dispLoo: 0.03,
            nRefLoo: 4,
            verified: true,
            gateFail: null,
            novigBest: false,
          },
          verified: true,
          state: 'verified',
          premiumPct: null,
          capsHidden: 0,
          flatRung: false,
        },
      ],
    }

    const html = renderToStaticMarkup(<LadderCard group={belowFloorGroup} />)
    expect(html).toContain('Below floor')
    expect(html).toContain('+557')
  })
})
