import '@testing-library/jest-dom'
import { render, screen, within } from '@testing-library/react'
import { buildMlbProjections, parseSideLine, toProjectionRow } from '../lib/mlb-projections'
import type { Bet } from '../lib/types'

jest.mock('next/link', () => ({
  __esModule: true,
  default: ({ children, href, 'aria-current': current }: { children: React.ReactNode; href: string; 'aria-current'?: 'page' }) => <a href={href} aria-current={current}>{children}</a>,
}))

import { SportProjectionsBoard } from '../components/picks/sport-projections-board'
import { SportTabs } from '../components/picks/sport-tabs'
import { applyPickFilters, fromMlbProjections } from '../lib/board-rows'
import { fromBets } from '../lib/mlb-board'
import { parsePickFilters } from '../lib/picks-filters'

let id = 0
const bet = (o: Partial<Bet>): Bet => ({
  id: ++id, system: 'K', game_date: '2026-10-16', game_pk: 1, bet_type: 'K_OVER_5.5', player: 'Gerrit Cole', away_team: 'NYY', home_team: 'BOS',
  odds: 110, stake: 25, model_prob: 0.6, market_prob: 0.5, edge: 0.06, kelly_pct: 0.01, kelly_triggered: true, result: null, profit: null,
  paper: true, book: 'fanduel', notes: null, created_at: '', proj_mean: 6.4, ...o,
})

describe('MLB projections', () => {
  it('parses the side and line, and treats the one-sided 2+/3+ thresholds as alt lines', () => {
    expect(parseSideLine('K_OVER_5.5')).toEqual({ side: 'OVER', line: 5.5 })
    expect(parseSideLine('BATTER_TB_UNDER_1.5')).toEqual({ side: 'UNDER', line: 1.5 })
    expect(parseSideLine('K_2PLUS_2.0')).toBeNull()
    expect(parseSideLine('HR')).toBeNull()
    expect(parseSideLine(null)).toBeNull()
  })

  it('only count markets with a stored projection are on the board; HR, game markets, +EV rows and projection-less rows are not', () => {
    const rows = buildMlbProjections([
      bet({}),
      bet({ system: 'HR', bet_type: 'HR', proj_mean: null, player: 'Judge' }),
      bet({ system: 'NRFI', bet_type: 'NRFI', player: null, proj_mean: null }),
      bet({ system: 'EV', bet_type: 'K_OVER_5.5_fanduel', player: 'Ev Guy', proj_mean: 5 }),
      bet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_OVER_1.5', player: 'No Proj', proj_mean: null }),
      bet({ system: 'K', bet_type: 'K_2PLUS_2.0', player: 'Alt Line', proj_mean: 6 }),
    ])
    expect(rows.map(r => r.player)).toEqual(['Gerrit Cole'])
  })

  it('one row per player and market: a tracked pick beats an unstaked prediction, then the higher score', () => {
    const rows = buildMlbProjections([
      bet({ bet_type: 'K_UNDER_6.5', kelly_triggered: false, edge: 0.2, odds: -120 }),
      bet({ bet_type: 'K_OVER_5.5', kelly_triggered: true, edge: 0.05 }),
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ side: 'OVER', line: 5.5, isPick: true })
  })

  it('an unstaked row with no edge scores zero, and ranking is by score then edge', () => {
    const none = toProjectionRow(bet({ kelly_triggered: false, edge: -0.03 }))!
    expect(none.score).toBe(0)
    const rows = buildMlbProjections([
      bet({ player: 'Low', game_pk: 1, edge: 0.03 }),
      bet({ player: 'High', game_pk: 2, edge: 0.1 }),
    ])
    expect(rows.map(r => r.player)).toEqual(['High', 'Low'])
  })

  it('normalises percent-stored edge and probability', () => {
    const r = toProjectionRow(bet({ edge: 6, model_prob: 60 }))!
    expect(r.edge).toBeCloseTo(0.06)
    expect(r.pModel).toBeCloseTo(0.6)
  })
})

describe('MLB projections on the unified board', () => {
  const rows = buildMlbProjections([
    bet({}),
    bet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_UNDER_1.5', player: 'Mookie Betts', game_pk: 2, proj_mean: 1.31, odds: -105, edge: 0.04, kelly_triggered: false }),
  ])
  const board = (r = rows) => <SportProjectionsBoard sport="mlb" rows={fromMlbProjections(r)} />

  it('shows the projection beside the line, the preferred side, the price, the edge and a PICK marker', () => {
    render(board())
    const cole = within(screen.getByText('Gerrit Cole').closest('tr')!)
    expect(cole.getByText('Over 5.5')).toBeInTheDocument()
    expect(cole.getByText('6.4')).toBeInTheDocument()
    expect(cole.getByText('60%')).toBeInTheDocument()
    expect(cole.getByText('+110')).toBeInTheDocument()
    expect(cole.getByText('+6.0%')).toBeInTheDocument()
    expect(cole.getByText('PICK')).toBeInTheDocument()
    // the market's fair probability is the model's minus the edge: 60% - 6% = 54%
    expect(cole.getByText(/54%/)).toBeInTheDocument()
    const betts = within(screen.getByText('Mookie Betts').closest('tr')!)
    expect(betts.getByText('Under 1.5')).toBeInTheDocument()
    expect(betts.getByText('1.31')).toBeInTheDocument()
    expect(betts.queryByText('PICK')).toBeNull()
    expect(document.querySelectorAll('thead').length).toBe(1)
  })

  it('has no UNVALIDATED badge (MLB is the tracked card) and no form column, since MLB has no hit rates to fill', () => {
    render(board())
    expect(screen.queryByText('UNVALIDATED')).toBeNull()
    const heads = screen.getAllByRole('columnheader').map(h => h.textContent)
    expect(heads).toContain('MARKET FAIR')
    expect(heads).not.toContain('FORM')
    expect(screen.queryByText(/L5 /)).toBeNull()
  })

  it('the shared filter cuts MLB rows by market and by the player\'s game; an empty board is explained', () => {
    const all = fromMlbProjections(rows)
    expect(applyPickFilters(all, parsePickFilters({ market: 'BATTER_TB' })).map(r => r.player)).toEqual(['Mookie Betts'])
    expect(applyPickFilters(all, parsePickFilters({ game: 'mlb:2' })).map(r => r.player)).toEqual(['Mookie Betts'])
    render(<SportProjectionsBoard sport="mlb" rows={[]} emptyBody="Today's projections publish with the ~11:00 AM CT model run." />)
    expect(screen.getByText(/publish with the ~11:00 AM CT model run/)).toBeInTheDocument()
  })

  it('sport tabs mark the current view and link the three views of MLB', () => {
    render(<SportTabs sport="mlb" active="projections" />)
    expect(screen.getByRole('link', { name: 'ALL PROJECTIONS' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'PICKS & +EV' })).not.toHaveAttribute('aria-current')
    expect(screen.getByRole('link', { name: 'PICKS & +EV' })).toHaveAttribute('href', '/picks/mlb')
  })
})

describe('MLB picks on the same board (bets as rows)', () => {
  const picks = [
    bet({ id: 901 }),
    bet({ id: 902, system: 'HR', bet_type: 'HR', player: 'Aaron Judge', odds: 320, edge: 0.04, proj_mean: null }),
    bet({ id: 903, system: 'EV', bet_type: 'K_UNDER_2.5_betrivers', book: 'betrivers', player: 'Nick Martinez', away_team: 'TB', home_team: 'CLE', game_pk: 849817, odds: -122, model_prob: 0.58, market_prob: 0.55, edge: 0.056, proj_mean: null }),
    bet({ id: 904, system: 'F5', bet_type: 'HOME', player: null, away_team: 'TB', home_team: 'CLE', game_pk: 849817, odds: -105, result: 'win' }),
    bet({ id: 905, game_pk: 1, odds: 115, edge: 0.02, book: 'betmgm' }),                    // the same prop as 901 at another book
  ]
  const rows = fromBets(picks)

  it('adapts every kind of pick: prop, HR, +EV alert and game market, one row per prop (the best-edge book wins)', () => {
    expect(rows.map(r => r.key)).toEqual(['901', '902', '903', '904'])
    const cole = rows[0]
    expect(cole).toMatchObject({ sport: 'mlb', system: 'K', player: 'Gerrit Cole', marketLabel: 'Strikeouts', side: 'over', line: 5.5, odds: 110, book: 'fanduel', gameKey: 'mlb:1', away: 'NYY', home: 'BOS', isPick: true, proj: 6.4, nBooks: 2 })
    expect(cole.pModel).toBeCloseTo(0.6)
    expect(cole.pFair).toBeCloseTo(0.5)
    expect(rows[1]).toMatchObject({ system: 'HR', marketLabel: 'to Hit a Home Run', side: null, line: null, hasLine: true })
    // an EV alert is labelled through the market it resolved to, and keeps the EV system for the market filter
    expect(rows[2]).toMatchObject({ system: 'EV', marketLabel: 'Strikeouts', side: 'under', line: 2.5, gameKey: 'mlb:849817' })
    // a game market has no player: the matchup is the subject
    expect(rows[3]).toMatchObject({ system: 'F5', player: 'TB @ CLE', marketLabel: 'CLE First 5 Innings Moneyline', result: 'win' })
  })

  it('renders on the shared table with a RESULT column only when something is settled', () => {
    const { rerender } = render(<SportProjectionsBoard sport="mlb" rows={rows.slice(0, 3)} />)
    expect(screen.queryByRole('columnheader', { name: 'RESULT' })).toBeNull()
    expect(within(screen.getByText('Gerrit Cole').closest('tr')!).getByText('PICK')).toBeInTheDocument()
    rerender(<SportProjectionsBoard sport="mlb" rows={rows} />)
    expect(screen.getByRole('columnheader', { name: 'RESULT' })).toBeInTheDocument()
    expect(screen.getByText('WIN')).toBeInTheDocument()
  })

  it('the shared filters work on them: market, EV alerts, result and a game', () => {
    const f = (o: Record<string, string>) => applyPickFilters(rows, parsePickFilters(o)).map(r => r.key)
    expect(f({ market: 'EV' })).toEqual(['903'])
    expect(f({ market: 'HR' })).toEqual(['902'])
    expect(f({ game: 'mlb:849817' }).sort()).toEqual(['903', '904'])
    expect(f({ result: 'win' })).toEqual(['904'])
    expect(f({ result: 'pending' }).sort()).toEqual(['901', '902', '903'])
  })
})
