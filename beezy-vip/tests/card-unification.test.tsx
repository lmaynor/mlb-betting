import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { PropCard } from '@/components/picks/prop-card'
import { OutlierCard } from '@/app/ev/odds-cards'
import { LadderCard } from '@/app/ev/ladder-card'
import type { BoardRow as OddsBoardRow, OutlierRow } from '@/lib/odds-screen'
import { outlierQuoteState } from '@/lib/odds-screen'
import type { BoardRow as PickBoardRow } from '@/lib/board-rows'
import { propCardStatus, evBadgeHighlighted, PROP_DISP_MAX } from '@/lib/prop-cards'
import { groupLadderRows, type LadderGroup } from '@/lib/ladders'

const NOW = Date.parse('2026-11-15T12:00:00Z')
const TS = '2026-11-15T11:58:00Z'

function makePickRow(overrides: Partial<PickBoardRow> = {}): PickBoardRow {
  return {
    key: 'nba:game1|player_one|pts',
    sport: 'nba',
    system: 'NBA_PTS',
    marketLabel: 'Points',
    player: 'Player One',
    headshotId: 100,
    team: 'BOS',
    matchup: 'BOS @ MIA',
    gameKey: 'nba:game1',
    away: 'BOS',
    home: 'MIA',
    start: '2026-11-17T20:00:00Z',
    side: 'over',
    line: 20.5,
    hasLine: true,
    proj: 22.4,
    avg10: 21.0,
    avgSzn: 20.5,
    pModel: 0.58,
    pFair: 0.52,
    pBlend: 0.55,
    nBooks: 4,
    l5: 0.6,
    l10: 0.7,
    odds: -110,
    book: 'draftkings',
    modelEdge: 0.06,
    marketEv: 0.04,
    confluence: true,
    score: 60,
    isPick: false,
    result: null,
    engine: 'Hierarchical',
    tier: null,
    disp: null,
    p10: 14.0,
    p90: 30.0,
    ...overrides,
  }
}

function makeOutlierRow(
  overrides: Partial<OutlierRow> = {},
  rowOverrides: Partial<OddsBoardRow> = {}
): OutlierRow {
  const baseBoardRow: OddsBoardRow = {
    key: 'nba:2026-11-17_BOS@MIA:jayson_tatum:pts:26.5',
    game_key: 'nba:2026-11-17_BOS@MIA',
    commence: '2026-11-17T20:00:00Z',
    away: 'BOS',
    home: 'MIA',
    player: 'Jayson Tatum',
    player_norm: 'jayson tatum',
    stat: 'pts',
    line: 26.5,
    headshot_id: 100,
    books: [
      {
        book: 'draftkings',
        over: 150,
        under: -180,
        ts: '2026-11-17T19:00:00Z',
        stale_opener: true,
      },
      {
        book: 'fanduel',
        over: 110,
        under: -130,
        ts: '2026-11-17T19:38:00Z',
      },
      {
        book: 'betmgm',
        over: 105,
        under: -125,
        ts: '2026-11-17T19:35:00Z',
      },
    ],
    ...rowOverrides,
  }

  return {
    key: 'outlier:nba:2026-11-17_BOS@MIA:jayson_tatum:pts:26.5:OVER',
    row: baseBoardRow,
    side: 'OVER',
    label: 'Points',
    outlierBook: 'draftkings',
    outlierAmerican: 150,
    outlierDecimal: 2.5,
    outlierImplied: 0.4,
    peers: [
      { book: 'fanduel', american: 110, decimal: 2.1, impliedProb: 0.476, barRatio: 0.84 },
      { book: 'betmgm', american: 105, decimal: 2.05, impliedProb: 0.487, barRatio: 0.82 },
    ],
    nPeers: 2,
    marketAvgAmerican: 107,
    marketAvgDecimal: 2.07,
    payoutPremiumPct: 15.0,
    trueEvPct: 5.2,
    fairAmerican: 120,
    fairSource: 'consensus',
    disp: 0.032,
    ...overrides,
  }
}

function makeLadderBoardRow(overrides: Partial<OddsBoardRow>): OddsBoardRow {
  return {
    game_key: 'nfl:2026-11-15_KC@BUF',
    commence: '2026-11-15T18:00:00Z',
    away: 'KC',
    home: 'BUF',
    player: 'Travis Kelce',
    player_norm: 'travis kelce',
    stat: 'anytime_td',
    line: 0.5,
    modal_point: 0.5,
    modal_open: 0.5,
    modal_first_ts: '2026-11-12T15:00:00Z',
    books: [],
    n_ref: 0,
    n_fresh_ref: 0,
    ...overrides,
  } as OddsBoardRow
}

function ladderGroupOf(rows: OddsBoardRow[]): LadderGroup {
  const groups = groupLadderRows(rows, NOW)
  expect(groups).toHaveLength(1)
  return groups[0]
}

describe('Card Unification Step 1', () => {
  describe('OutlierCard', () => {
    it('renders forward shadow badge, cons fair, dispersion, unmoved chip, and quote lag', () => {
      const item = makeOutlierRow()
      const html = renderToStaticMarkup(
        <OutlierCard item={item} isLocked={false} onReveal={() => {}} />
      )

      expect(html).toContain('FORWARD SHADOW · UNGRADED')
      expect(html).toContain('Cons fair')
      expect(html).toContain('Disp 0.032')
      expect(html).toContain('Unmoved since open')
      expect(html).toContain('behind mkt')
    })

    it('does not render "% EV" when trueEvPct is null (disp-blocked)', () => {
      const item = makeOutlierRow({
        disp: 0.09,
        trueEvPct: null,
        fairAmerican: null,
      })
      const html = renderToStaticMarkup(
        <OutlierCard item={item} isLocked={false} onReveal={() => {}} />
      )

      expect(html).toContain('FORWARD SHADOW · UNGRADED')
      expect(html).not.toContain('% EV')
    })
  })

  describe('PropCard', () => {
    it('renders "OFFICIAL PLAY" when tier is official', () => {
      const row = makePickRow({ tier: 'official' })
      const html = renderToStaticMarkup(<PropCard r={row} />)
      expect(html).toContain('OFFICIAL PLAY')
      expect(html).not.toContain('CANDIDATE · CONFLUENCE')
      expect(html).not.toContain('MODEL PROJECTION')
    })

    it('renders "CANDIDATE · CONFLUENCE" when tier is null and confluence is true', () => {
      const row = makePickRow({ tier: null, confluence: true })
      const html = renderToStaticMarkup(<PropCard r={row} />)
      expect(html).toContain('CANDIDATE · CONFLUENCE')
      expect(html).not.toContain('OFFICIAL PLAY')
      expect(html).not.toContain('MODEL PROJECTION')
    })

    it('renders "MODEL PROJECTION" when tier is null and confluence is false', () => {
      const row = makePickRow({ tier: null, confluence: false })
      const html = renderToStaticMarkup(<PropCard r={row} />)
      expect(html).toContain('MODEL PROJECTION')
      expect(html).not.toContain('OFFICIAL PLAY')
      expect(html).not.toContain('CANDIDATE · CONFLUENCE')
    })

    it('handles EV badge highlight based on disp threshold', () => {
      const greenBg = 'rgba(87, 242, 135, 0.15)'

      // disp 0.03 <= 0.06 -> highlighted
      const highlightedRow = makePickRow({ marketEv: 0.04, disp: 0.03 })
      const highlightedHtml = renderToStaticMarkup(<PropCard r={highlightedRow} />)
      expect(highlightedHtml).toContain(greenBg)
      expect(highlightedHtml).toContain('+4.0% EV')

      // disp 0.09 > 0.06 -> not highlighted, but still shows +4.0% EV
      const mutedRow = makePickRow({ marketEv: 0.04, disp: 0.09 })
      const mutedHtml = renderToStaticMarkup(<PropCard r={mutedRow} />)
      expect(mutedHtml).not.toContain(greenBg)
      expect(mutedHtml).toContain('+4.0% EV')
    })
  })

  describe('LadderCard anytime_td treatment', () => {
    it('renders 1-SIDED · FORWARD CONFIRMATION (NO FAIR) and vig included, without Net EV', () => {
      const books = [
        { book: 'draftkings', over: 150, under: null, ts: TS },
        { book: 'fanduel', over: 120, under: null, ts: TS },
        { book: 'betmgm', over: 110, under: null, ts: TS },
        { book: 'caesars', over: 105, under: null, ts: TS },
      ]
      const g = ladderGroupOf([
        makeLadderBoardRow({
          stat: 'anytime_td',
          line: 0.5,
          modal_point: 0.5,
          modal_open: 0.5,
          books,
          n_ref: 0,
          n_fresh_ref: 0,
          grp_books_any: 4,
          grp_books_2sided: 0,
          best_over: {
            book: 'draftkings',
            american: 150,
            p_cons_loo: null,
            disp_loo: null,
            n_ref_loo: 0,
          },
        }),
      ])
      const html = renderToStaticMarkup(<LadderCard group={g} />)

      expect(html).toContain('1-SIDED · FORWARD CONFIRMATION (NO FAIR)')
      expect(html).toContain('vig included')
      expect(html).not.toContain('Net EV')
    })
  })

  describe('Copy sweep', () => {
    it('contains no forbidden copy across PropCard, OutlierCard, and LadderCard', () => {
      const twoSided = (book: string, over: number, under: number) => ({ book, over, under, ts: TS })
      const curveBooks = [
        twoSided('draftkings', 441, -600),
        twoSided('fanduel', -110, -110),
        twoSided('betmgm', -115, -105),
        twoSided('caesars', -108, -112),
        twoSided('espnbet', -112, -108),
      ]
      const curveGroup = ladderGroupOf([
        makeLadderBoardRow({
          stat: 'rec_yds',
          line: 99.5,
          modal_point: 64.5,
          modal_open: 58.5,
          books: curveBooks,
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

      const anytimeBooks = [
        { book: 'draftkings', over: 150, under: null, ts: TS },
        { book: 'fanduel', over: 120, under: null, ts: TS },
        { book: 'betmgm', over: 110, under: null, ts: TS },
        { book: 'caesars', over: 105, under: null, ts: TS },
      ]
      const milestoneGroup = ladderGroupOf([
        makeLadderBoardRow({
          stat: 'anytime_td',
          line: 0.5,
          modal_point: 0.5,
          modal_open: 0.5,
          books: anytimeBooks,
          n_ref: 0,
          n_fresh_ref: 0,
          grp_books_any: 4,
          grp_books_2sided: 0,
          best_over: {
            book: 'draftkings',
            american: 150,
            p_cons_loo: null,
            disp_loo: null,
            n_ref_loo: 0,
          },
        }),
      ])

      const copyTargets = [
        renderToStaticMarkup(<PropCard r={makePickRow()} />),
        renderToStaticMarkup(<OutlierCard item={makeOutlierRow()} isLocked={false} onReveal={() => {}} />),
        renderToStaticMarkup(<LadderCard group={curveGroup} />),
        renderToStaticMarkup(<LadderCard group={milestoneGroup} />),
      ]

      for (const html of copyTargets) {
        expect(html).not.toMatch(/mispriced/i)
        expect(html).not.toMatch(/hit rate/i)
        expect(html).not.toMatch(/streak/i)
        expect(html).not.toMatch(/\bStrong\b/)
        expect(html).not.toMatch(/\bLean\b/)
        expect(html).not.toMatch(/\bWatch\b/)
      }
    })
  })

  describe('Unit assertions', () => {
    describe('propCardStatus', () => {
      it('prioritizes tier official over confluence', () => {
        expect(propCardStatus(makePickRow({ tier: 'official', confluence: true }))).toBe('OFFICIAL PLAY')
        expect(propCardStatus(makePickRow({ tier: 'official', confluence: false }))).toBe('OFFICIAL PLAY')
      })

      it('returns CANDIDATE · CONFLUENCE when tier is not official and confluence is true', () => {
        expect(propCardStatus(makePickRow({ tier: null, confluence: true }))).toBe('CANDIDATE · CONFLUENCE')
        expect(propCardStatus(makePickRow({ tier: 'tracked', confluence: true }))).toBe('CANDIDATE · CONFLUENCE')
      })

      it('returns MODEL PROJECTION when tier is not official and confluence is false', () => {
        expect(propCardStatus(makePickRow({ tier: null, confluence: false }))).toBe('MODEL PROJECTION')
        expect(propCardStatus(makePickRow({ tier: 'tracked', confluence: false }))).toBe('MODEL PROJECTION')
      })
    })

    describe('evBadgeHighlighted', () => {
      it('returns true only when marketEv > 0, disp != null, and disp <= PROP_DISP_MAX', () => {
        expect(PROP_DISP_MAX).toBe(0.06)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0.04, disp: 0.03 }))).toBe(true)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0.04, disp: 0.06 }))).toBe(true)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0.04, disp: 0.061 }))).toBe(false)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0.04, disp: null }))).toBe(false)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0.04, disp: undefined }))).toBe(false)
        expect(evBadgeHighlighted(makePickRow({ marketEv: 0, disp: 0.03 }))).toBe(false)
        expect(evBadgeHighlighted(makePickRow({ marketEv: -0.02, disp: 0.03 }))).toBe(false)
        expect(evBadgeHighlighted(makePickRow({ marketEv: null, disp: 0.03 }))).toBe(false)
      })
    })

    describe('outlierQuoteState', () => {
      it('computes lagMin and unmoved accurately', () => {
        const item = makeOutlierRow()
        const state = outlierQuoteState(item)
        expect(state.lagMin).toBe(38)
        expect(state.unmoved).toBe(true)
      })

      it('returns lagMin null when outlier book ts is missing', () => {
        const item = makeOutlierRow({}, {
          books: [
            { book: 'draftkings', over: 150, under: -180, ts: null, stale_opener: false },
            { book: 'fanduel', over: 110, under: -130, ts: '2026-11-17T19:38:00Z' },
          ],
        })
        const state = outlierQuoteState(item)
        expect(state.lagMin).toBeNull()
        expect(state.unmoved).toBe(false)
      })

      it('returns lagMin null when freshest book ts cannot be determined', () => {
        const item = makeOutlierRow({}, {
          books: [
            { book: 'draftkings', over: 150, under: -180, ts: 'invalid-date', stale_opener: true },
          ],
        })
        const state = outlierQuoteState(item)
        expect(state.lagMin).toBeNull()
        expect(state.unmoved).toBe(true)
      })

      it('returns unmoved false when stale_opener is false or undefined', () => {
        const item = makeOutlierRow({}, {
          books: [
            { book: 'draftkings', over: 150, under: -180, ts: '2026-11-17T19:00:00Z', stale_opener: false },
          ],
        })
        const state = outlierQuoteState(item)
        expect(state.unmoved).toBe(false)
      })
    })
  })
})
