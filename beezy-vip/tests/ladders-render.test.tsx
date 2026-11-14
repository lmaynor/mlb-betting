/**
 * Rendered smoke check for the three LadderCard display states (Argon ladders ruling Q2/Q3):
 * VERIFIED DISLOCATION (emerald, mandatory Net EV + labeled consensus), MARKET LADDER (prices-only
 * pill under the four-book floor), and MILESTONE-THIN (one-sided pill, no fair anywhere).
 */
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { LadderCard } from '@/app/ev/ladder-card'
import { groupLadderRows, type LadderGroup } from '@/lib/ladders'
import type { BoardRow } from '@/lib/odds-screen'

const NOW = Date.parse('2026-11-15T12:00:00Z')
const TS = '2026-11-15T11:58:00Z'

function row(overrides: Partial<BoardRow>): BoardRow {
  return {
    game_key: 'nfl:2026-11-15_KC@BUF',
    commence: '2026-11-15T18:00:00Z',
    away: 'KC',
    home: 'BUF',
    player: 'Test Player',
    player_norm: 'test player',
    stat: 'rec_yds',
    line: 99.5,
    modal_point: 64.5,
    modal_open: 58.5,
    modal_first_ts: '2026-11-12T15:00:00Z',
    books: [],
    n_ref: 0,
    n_fresh_ref: 0,
    ...overrides,
  } as BoardRow
}

function twoSided(book: string, over: number, under: number) {
  return { book, over, under, ts: TS }
}

function groupOf(rows: BoardRow[]): LadderGroup {
  const groups = groupLadderRows(rows, NOW)
  expect(groups).toHaveLength(1)
  return groups[0]
}

describe('LadderCard display states', () => {
  it('VERIFIED DISLOCATION: emerald badge, mandatory Net EV, labeled leave-one-out consensus, rung vig', () => {
    const books = [
      twoSided('draftkings', 441, -600),
      twoSided('fanduel', -110, -110),
      twoSided('betmgm', -115, -105),
      twoSided('caesars', -108, -112),
      twoSided('espnbet', -112, -108),
    ]
    const g = groupOf([
      row({
        books,
        n_ref: 5,
        n_fresh_ref: 5,
        disp: 0.02,
        p_cons: 0.24,
        rung_vig: 0.045,
        grp_books_any: 5,
        grp_books_2sided: 5,
        best_over: { book: 'draftkings', american: 441, p_cons_loo: 0.22, disp_loo: 0.02, n_ref_loo: 4 },
        best_under: { book: 'fanduel', american: -110, p_cons_loo: 0.22, disp_loo: 0.02, n_ref_loo: 4 },
      }),
    ])
    expect(g.verified).toBe(true)
    const html = renderToStaticMarkup(<LadderCard group={g} />)
    expect(html).toContain('VERIFIED DISLOCATION')
    expect(html).toContain('Net EV')
    expect(html).toContain('Consensus of 4 books (leave-one-out)')
    expect(html).toContain('Rung Vig: 4.5%')
    expect(html).toContain('Main: Open 58.5 -&gt; Now 64.5 (+6)')
    expect(html).not.toContain('mispriced')
  })

  it('MARKET LADDER: under the four-book floor the card shows prices only, no fair or EV', () => {
    const books = [twoSided('draftkings', 150, -170), twoSided('fanduel', 145, -165), twoSided('betmgm', 140, -160)]
    const g = groupOf([
      row({
        stat: 'rush_yds',
        line: 79.5,
        books,
        n_ref: 3,
        n_fresh_ref: 3,
        disp: 0.03,
        p_cons: 0.4,
        grp_books_any: 3,
        grp_books_2sided: 3,
        best_over: { book: 'draftkings', american: 150, p_cons_loo: 0.4, disp_loo: 0.03, n_ref_loo: 2 },
        best_under: null,
      }),
    ])
    expect(g.verified).toBe(false)
    const html = renderToStaticMarkup(<LadderCard group={g} />)
    expect(html).toContain('MARKET LADDER')
    expect(html).toContain('3/4 ref books · Prices only')
    expect(html).not.toContain('Net EV')
    expect(html).not.toContain('Consensus of')
    expect(html).not.toContain('mispriced')
  })

  it('MILESTONE-THIN: one-sided anytime TD shows the one-sided pill and the payout premium, never a fair', () => {
    const books = [
      { book: 'draftkings', over: 500, under: null, ts: TS },
      { book: 'fanduel', over: 340, under: null, ts: TS },
      { book: 'betmgm', over: 320, under: null, ts: TS },
      { book: 'caesars', over: 310, under: null, ts: TS },
    ]
    const g = groupOf([
      row({
        stat: 'anytime_td',
        line: 0.5,
        modal_point: 0.5,
        modal_open: 0.5,
        books,
        n_ref: 0,
        n_fresh_ref: 0,
        grp_books_any: 4,
        grp_books_2sided: 0,
      }),
    ])
    expect(g.kind).toBe('milestone')
    expect(g.premium).toBe(true)
    const html = renderToStaticMarkup(<LadderCard group={g} />)
    expect(html).toContain('MILESTONE · THIN')
    expect(html).toContain('1-Sided · Overround Unremovable')
    expect(html).toContain('vs Mkt')
    expect(html).toContain('Main: 0.5 · Unchanged')
    expect(html).not.toContain('Net EV')
    expect(html).not.toContain('Fair')
    expect(html).not.toContain('mispriced')
  })
})
