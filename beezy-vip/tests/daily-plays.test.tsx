/**
 * Daily plays: the Daily Card, today's list and Graded History are one definition (the backend's official tier, no count cap), the Game Picks
 * tab is hidden, and Projections is its own Scanner tab that draws lambda against the line.
 */
import fs from 'fs'
import path from 'path'
import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { fromSportProjections, isOfficialRow, isOutlierRow } from '../lib/board-rows'
import { SECTIONS, sectionFor } from '../lib/ia'
import { buildDailyCard, dailyPlaysFromBets, fromBet, fromProjection, ledgerRowKey, recordOf } from '../lib/top-picks'
import { dailyBetsFromLedger, todaysDailyCard } from '../lib/top-picks-data'
import { ExpectedScoreBar, LambdaBar, leansListedSide } from '../components/picks/lambda-bar'
import type { Bet, SportProjectionRow } from '../lib/types'

const read = (p: string) => fs.readFileSync(path.resolve(__dirname, '..', p), 'utf8')

const propsBet = (o: Partial<Bet> & Record<string, unknown> = {}): Bet => ({
  id: 1, system: 'PROPS', sport: 'nba', game_date: '2026-11-03', game_pk: null, bet_type: 'rebounds_over_10.5', player: 'Jalen Duren',
  away_team: 'CHI', home_team: 'DET', odds: 105, stake: 1, model_prob: 0.58, market_prob: 0.53, edge: 0.0865, kelly_pct: null, kelly_triggered: true,
  result: null, profit: null, paper: true, book: 'fanduel',
  notes: JSON.stringify({ stat: 'rebounds', point: 10.5, side: 'over', tier: 'official', n_books: 6, row_key: 'nba:2026-11-03:CHI@DET|id4594268|rebounds' }),
  created_at: '2026-11-03T16:00:00Z', ...o,
} as Bet)

const row = (o: Partial<SportProjectionRow> = {}): SportProjectionRow => ({
  sport: 'nba', stat: 'rebounds', stat_label: 'Rebounds', player: 'Jalen Duren', player_norm: 'id4594268', team: 'DET', opp: 'CHI',
  game_key: 'nba:2026-11-03:CHI@DET', commence: null, mins_to_start: 120, has_line: true, line: 10.5, consensus_line: 10.5, side: 'over',
  proj_mean: 11.8, proj_p10: 7.9, proj_p90: 15.6, p_over_model: 0.6, p_model_side: 0.6, p_mkt_side: 0.53, l5_hit_rate: 0.6, l10_hit_rate: 0.7, szn_rate: 0.5,
  szn_avg: 10.1, avg_l10: 11, usage_label: 'Usage', n_games: 20, best_over_odds: 105, best_over_book: 'fanduel', best_under_odds: -125, best_under_book: 'draftkings',
  p_over_market: 0.53, n_books: 6, model_edge: 0.07, market_ev: 0.0865, confluence: true, edge_capped: false, beezy_score: 72,
  score_basis: 'confluence', validated: false, engine: 'empirical_ewm_v2', pick_tier: 'official', ...o,
})

describe('one definition of a daily play', () => {
  it('a board row is official exactly when the backend stamped it so; no stamp fails closed', () => {
    expect(fromProjection(row())!.tier).toBe('official')
    expect(fromProjection(row({ pick_tier: 'tracked' }))!.tier).toBe('tracked')
    expect(fromProjection(row({ pick_tier: undefined }))!.tier).toBeNull()
    const [official, tracked, none] = fromSportProjections([row(), row({ pick_tier: 'tracked' }), row({ pick_tier: undefined })])
    expect([isOfficialRow(official), isOfficialRow(tracked), isOfficialRow(none)]).toEqual([true, false, false])
    expect(isOutlierRow(tracked)).toBe(true)          // a +EV row that is not a daily play is an outlier
    expect(isOutlierRow(official)).toBe(false)
  })

  it('the board table and the card agree on a row: same verdict from TopPick and BoardRow', () => {
    const r = row({ pick_tier: 'official', beezy_score: 50, n_books: 3 })      // weak by the OLD site gate, official by the backend
    expect(isOfficialRow(fromSportProjections([r])[0])).toBe(true)
    expect(buildDailyCard([fromProjection(r)!])).toHaveLength(1)
  })

  it('a ledger bet carries the same verdict in its notes, and only PROPS official bets are plays (not tracked rows, not +EV outliers)', () => {
    const plays = dailyPlaysFromBets([
      propsBet({ id: 1 }),
      propsBet({ id: 2, player: 'Tracked Guy', notes: JSON.stringify({ tier: 'tracked', row_key: 'nba:x|t|rebounds' }) }),
      propsBet({ id: 3, system: 'EV', player: 'Outlier Guy' }),
      propsBet({ id: 4, player: 'Unstaked Guy', kelly_triggered: false }),
    ])
    expect(plays.map(p => p.who)).toEqual(['Jalen Duren'])
    expect(plays[0].tier).toBe('official')
  })

  it('today\'s board row and its logged bet are the same play: they share a key, so the card never lists it twice', () => {
    const bet = propsBet()
    expect(ledgerRowKey(bet)).toBe('nba|nba:2026-11-03:CHI@DET|id4594268|rebounds')
    expect(fromProjection(row())!.key).toBe(ledgerRowKey(bet))
    expect(ledgerRowKey(propsBet({ notes: null }))).toBeNull()
  })

  it('today\'s card keeps an official board play after its game starts', () => {
    const startedRow = row({ pick_tier: 'official', mins_to_start: -10 })
    const plays = [startedRow].map(r => fromProjection(r, { includeStarted: true })).filter((p): p is NonNullable<typeof p> => p != null)
    const card = todaysDailyCard([], plays)
    expect(card).toHaveLength(1)
    expect(card[0].started).toBe(true)
    expect(card[0].who).toBe('Jalen Duren')
  })

  it('dailyBetsFromLedger returns exactly each day\'s card plays across dates', () => {
    const propsOfficial = propsBet({ id: 1, game_date: '2026-11-03' })
    const propsTracked = propsBet({ id: 2, game_date: '2026-11-03', player: 'Tracked Guy', notes: JSON.stringify({ tier: 'tracked', row_key: 'nba:x|t|rebounds' }) })
    const propsEv = propsBet({ id: 3, game_date: '2026-11-03', system: 'EV', player: 'Outlier Guy' })
    const mlbQualifying: Bet = {
      ...propsBet({
        id: 4,
        sport: 'mlb',
        system: 'K',
        game_date: '2026-11-04',
        player: 'Gerrit Cole',
        edge: 0.10,
        model_prob: 0.60,
        notes: null,
      }),
    }
    const mlbExcluded: Bet = {
      ...propsBet({
        id: 5,
        sport: 'mlb',
        system: 'K',
        game_date: '2026-11-04',
        player: 'Other Guy',
        edge: 0.01,
        notes: null,
      }),
    }
    const bets = dailyBetsFromLedger([propsOfficial, propsTracked, propsEv, mlbQualifying, mlbExcluded])
    expect(bets.map(b => b.id)).toEqual([1, 4])
    expect(bets).toMatchObject([propsOfficial, mlbQualifying])
  })

  it('there is no count cap on the card or on a graded day', () => {
    const bets = Array.from({ length: 8 }, (_, i) => propsBet({
      id: 10 + i, player: `Player ${i}`, away_team: `A${i}`, home_team: `H${i}`, odds: 100 + i, result: i % 2 ? 'loss' : 'win',
      notes: JSON.stringify({ tier: 'official', n_books: 6, row_key: `nba:d:A${i}@H${i}|p${i}|rebounds` }),
    }))
    const day = dailyPlaysFromBets(bets)
    expect(day).toHaveLength(8)
    const rec = recordOf(day)
    expect(rec.graded).toBe(8)
    expect(rec.wins).toBe(4)
    expect(fromBet(bets[0]).tier).toBe('official')
  })
})

describe('Game Picks is hidden; Projections is a Scanner tab', () => {
  it('no tab, no section, no link to /picks/games, and it is out of the sitemap', () => {
    const picks = SECTIONS.find(s => s.id === 'picks')!
    expect(picks.tabs.some(t => t.href === '/picks/games')).toBe(false)
    expect(sectionFor('/picks/games')).toBeNull()
    expect(read('lib/sitemap.ts')).not.toContain("'/picks/games'")
    for (const f of ['app/picks/page.tsx', 'components/picks/sport-board-page.tsx', 'app/cheat-sheet/cheat-sheet-client.tsx', 'app/ev/games/page.tsx']) {
      expect(read(f)).not.toMatch(/["'`]\/picks\/games["'`?]/)
    }
    expect(read('app/picks/games/page.tsx')).toContain('index: false')
  })

  it('Projections is the second Scanner tab, and the old projection URLs redirect to it', () => {
    const scanner = SECTIONS.find(s => s.id === 'scanner')!
    expect(scanner.tabs[1]).toMatchObject({ label: 'Projections', href: '/ev/projections' })
    expect(read('app/edge/projections/page.tsx')).toContain('permanentRedirect')
    expect(read('app/picks/[sport]/projections/page.tsx')).toContain('permanentRedirect')
  })
})

describe('lambda visuals', () => {
  it('LambdaBar names what it draws, and the tick is bright only when the model leans the listed side', () => {
    const { container, rerender } = render(<LambdaBar mean={11.8} p10={7.9} p90={15.6} line={10.5} side="over" />)
    expect(screen.getByRole('img', { name: /Model expects 11\.8,.*7\.90 to 15\.6.*line is 10\.5/ })).toBeInTheDocument()
    expect(container.querySelector('rect[fill="#57F287"]')).not.toBeNull()
    rerender(<LambdaBar mean={11.8} p10={7.9} p90={15.6} line={10.5} side="under" />)
    expect(container.querySelector('rect[fill="#57F287"]')).toBeNull()
    expect(leansListedSide(11.8, 10.5, 'over')).toBe(true)
    expect(leansListedSide(11.8, 10.5, 'under')).toBe(false)
  })

  it('LambdaBar still draws with no range or no line, and prints dashes with no model number', () => {
    const { container, rerender } = render(<LambdaBar mean={3.2} line={null} side={null} />)
    expect(container.querySelector('svg')).not.toBeNull()
    rerender(<LambdaBar mean={null} line={4.5} side="over" />)
    expect(container.querySelector('svg')).toBeNull()
    expect(container).toHaveTextContent('--')
  })

  it('ExpectedScoreBar splits the bar by expected scoring and says nothing without both numbers', () => {
    const { container, rerender } = render(<ExpectedScoreBar away="BOS" home="NYR" awayMean={2.4} homeMean={3.1} digits={2} />)
    expect(screen.getByRole('img', { name: 'Expected score: BOS 2.40, NYR 3.10' })).toBeInTheDocument()
    rerender(<ExpectedScoreBar away="BOS" home="NYR" awayMean={null} homeMean={3.1} />)
    expect(container.firstChild).toBeNull()
  })

  it('the projections page enables the visuals and the Daily Card links Graded History', () => {
    expect(read('app/ev/projections/page.tsx')).toMatch(/<SportProjectionsBoard[^>]*\bvisuals\b/)
    expect(read('app/cheat-sheet/cheat-sheet-client.tsx')).toContain('href="/picks/history"')
  })
})
