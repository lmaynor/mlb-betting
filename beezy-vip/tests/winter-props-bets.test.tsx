/**
 * The backend now logs NFL / NBA / NHL prop bets to the public bets table: system 'PROPS' (the model's own picks,
 * ev/model_picks.py) and system 'EV' (the sweep's outlier alerts, sport_alerts.py). Both store bet_type as lower-case
 * '<stat>_<side>_<line>' and a JSON `notes`. Pins that none of it reaches a user as a raw string, that a model pick is a
 * 'model' basis (never 'ev', never an MLB official-card play) and that every winter row says unvalidated / paper.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { betTier, isWinterSport, notesBookCount, parseNotesJson, readableNotes } from '../lib/bet-notes'
import { booksBehind } from '../lib/beezy-score'
import { engineLabel, fromSportProjections, rowEngineLabel } from '../lib/board-rows'
import { fromBets } from '../lib/mlb-board'
import { betKind, kindOfSystem } from '../lib/pick-systems'
import { parseWinterBetType, pickLabel, resolveEvUnderlying, SYSTEM_COLOR, SYSTEM_LABEL, SYSTEM_PILL, systemLabel, winterPickLabel } from '../lib/tokens'
import { fromBet, isEvOutlier, isOfficialCardPlay } from '../lib/top-picks'
import type { Bet, SportProjectionRow } from '../lib/types'

jest.mock('next/link', () => ({ __esModule: true, default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a> }))
let mobile = false
jest.mock('@/components/ui/mobile-sheet', () => ({ ...jest.requireActual('@/components/ui/mobile-sheet'), useIsMobile: () => mobile }))

import { SportProjectionsBoard } from '../components/picks/sport-projections-board'
import { TopPicksTable } from '../components/picks/top-picks-table'

const notes = (o: Record<string, unknown>) => JSON.stringify(o)
const bet = (o: Partial<Bet> = {}): Bet => ({
  id: 1, sport: 'nba', system: 'PROPS', game_date: '2026-11-01', game_pk: 0, bet_type: 'rebounds_over_10.5', player: 'Jarrett Allen',
  away_team: 'CLE', home_team: 'BOS', odds: -115, stake: 1, model_prob: 0.58, market_prob: 0.535, edge: 0.045, kelly_pct: null,
  kelly_triggered: true, result: null, profit: null, paper: true, validated: null, book: 'fanduel',
  notes: notes({ stat: 'rebounds', point: 10.5, side: 'over', n_books: 7, tier: 'tracked', game_key: 'nba:2026-11-01:CLE@BOS' }),
  created_at: '2026-11-01T15:00:00Z', proj_mean: 11.4, ...o,
})
const mlbBet = (o: Partial<Bet> = {}): Bet => bet({ sport: 'mlb', system: 'K', bet_type: 'K_OVER_7.5', player: 'Gerrit Cole', game_pk: 123, notes: 'Strong K rate (6 books)', ...o })

describe('winter bet-type parsing and labels', () => {
  it.each([
    ['rebounds_over_10.5', 'Jarrett Allen', 'Jarrett Allen Over 10.5 Rebounds'],
    ['pass_yds_under_250.5', 'Josh Allen', 'Josh Allen Under 250.5 Pass Yds'],
    ['pp_points_over_0.5', 'Nathan MacKinnon', 'Nathan MacKinnon Over 0.5 PP Points'],
    ['blocked_shots_over_1.5', 'Devon Toews', 'Devon Toews Over 1.5 Blocked Shots'],
    ['pra_under_38.5', 'Jalen Brunson', 'Jalen Brunson Under 38.5 Pts+Reb+Ast'],
    ['rush_attempts_over_17.5', 'Bijan Robinson', 'Bijan Robinson Over 17.5 Carries'],
    ['sog_over_2.5', 'Auston Matthews', 'Auston Matthews Over 2.5 Shots on Goal'],
    ['anytime_td_over_0.5', 'Josh Jacobs', 'Josh Jacobs Anytime TD'],
    ['anytime_td_under_0.5', 'Josh Jacobs', 'Josh Jacobs No Anytime TD'],
    ['anytime_td_over', 'Josh Jacobs', 'Josh Jacobs Anytime TD'],
  ])('%s -> %s', (bt, player, expected) => {
    expect(winterPickLabel(player, bt)).toBe(expected)
  })

  it('splits stat / side / line from the right, so a stat with underscores keeps them', () => {
    expect(parseWinterBetType('pass_completions_over_21.5')).toEqual({ stat: 'pass_completions', side: 'over', line: 21.5 })
    expect(parseWinterBetType('K_OVER_7.5')).toMatchObject({ stat: 'k', side: 'over' })   // case-insensitive; MLB rows never reach it
    expect(parseWinterBetType('HR_novig')).toBeNull()
    expect(parseWinterBetType(null)).toBeNull()
  })

  it('an unparseable winter bet type is spelled out, never printed with underscores', () => {
    expect(winterPickLabel('X Y', 'weird_market')).toBe('X Y weird market')
  })

  it('system PROPS reads "Model pick" and has its own pill; no raw PROPS string', () => {
    expect(systemLabel('PROPS')).toBe('Model pick')
    expect(systemLabel('PROPS', true)).toBe('MODEL')
    expect(SYSTEM_LABEL.PROPS).toBe('Model pick')
    expect(SYSTEM_COLOR.PROPS).toBeDefined()
    expect(SYSTEM_PILL.PROPS).toBeDefined()
    expect(SYSTEM_PILL.PROPS.color).not.toBe(SYSTEM_PILL.ALL.color)
    expect(SYSTEM_PILL.PROPS.color).not.toBe(SYSTEM_PILL.EV.color)
  })

  it('pickLabel: a PROPS pick and a winter EV alert both read like "<Player> Over 10.5 Rebounds"', () => {
    expect(pickLabel(bet())).toBe('Jarrett Allen Over 10.5 Rebounds')
    expect(pickLabel(bet({ system: 'EV', bet_type: 'sog_over_2.5', sport: 'nhl', player: 'Auston Matthews', book: 'draftkings' }))).toBe('Auston Matthews Over 2.5 Shots on Goal')
    // a book suffix on a winter EV row is stripped, not shown
    expect(pickLabel(bet({ system: 'EV', bet_type: 'pass_yds_under_250.5_betmgm', sport: 'nfl', player: 'Josh Allen', book: 'betmgm' }))).toBe('Josh Allen Under 250.5 Pass Yds')
  })

  it('MLB labels are untouched: an EV alert still resolves to its MLB market, a K bet to strikeouts', () => {
    expect(pickLabel(mlbBet({ system: 'EV', bet_type: 'K_OVER_7.5_draftkings', book: 'draftkings' }))).toBe('Gerrit Cole Over 7.5 Strikeouts')
    expect(pickLabel(mlbBet())).toBe('Gerrit Cole Over 7.5 Strikeouts')
  })

  it('a winter EV row is never guessed into an MLB system from its prefix', () => {
    expect(resolveEvUnderlying(bet({ system: 'EV', sport: 'nhl', bet_type: 'goals_over_0.5' })).system).toBe('EV')
    expect(resolveEvUnderlying(bet({ system: 'EV', sport: 'nba', bet_type: 'steals_over_1.5' })).system).toBe('EV')
  })

  it('PROPS is a player market (never a game market)', () => {
    expect(kindOfSystem('PROPS')).toBe('player')
    expect(betKind(bet())).toBe('player')
  })
})

describe('notes JSON never reaches the UI', () => {
  it('readableNotes drops machine JSON and keeps an MLB sentence', () => {
    expect(readableNotes(bet().notes)).toBeNull()
    expect(readableNotes('Strong K rate (6 books)')).toBe('Strong K rate (6 books)')
    expect(readableNotes(null)).toBeNull()
    expect(readableNotes('{not json')).toBeNull()
  })
  it('reads the tier and the book count out of the JSON', () => {
    expect(betTier(bet())).toBe('tracked')
    expect(betTier(bet({ notes: notes({ tier: 'official' }) }))).toBe('official')
    expect(betTier(mlbBet())).toBeNull()
    expect(notesBookCount(bet().notes)).toBe(7)
    expect(parseNotesJson('[1]')).toBeNull()
    expect(booksBehind(bet())).toBe(7)                      // the Beezy Score's consensus term sees it
    expect(booksBehind(mlbBet())).toBe(6)
  })
  it('isWinterSport', () => {
    expect(isWinterSport('NBA')).toBe(true)
    expect(isWinterSport('mlb')).toBe(false)
    expect(isWinterSport(null)).toBe(false)
  })
})

describe('top picks: a model PROPS pick is a model basis, labelled unvalidated', () => {
  it('basis model, its own sport, tracked and unvalidated', () => {
    const p = fromBet(bet())
    expect(p).toMatchObject({ sport: 'nba', basis: 'model', tracked: true, unvalidated: true, who: 'Jarrett Allen', what: 'Over 10.5 Rebounds', game: 'CLE @ BOS', key: 'nba|1', nBooks: 7 })
  })
  it('a winter +EV alert stays basis ev; an MLB bet is unchanged (sport mlb, not unvalidated)', () => {
    expect(fromBet(bet({ system: 'EV', sport: 'nhl', bet_type: 'sog_over_2.5' }))).toMatchObject({ basis: 'ev', sport: 'nhl', unvalidated: true, confluence: false })
    expect(fromBet(mlbBet())).toMatchObject({ sport: 'mlb', basis: 'model', unvalidated: false, key: 'mlb|1', confluence: true })
  })
  it('only the backend\'s official tier carries confluence; a tracked-tier pick never passes the official card gate', () => {
    const tracked = fromBet(bet({ edge: 0.05, model_prob: 0.7, notes: notes({ n_books: 8, tier: 'tracked' }) }))
    const official = fromBet(bet({ edge: 0.05, model_prob: 0.7, notes: notes({ n_books: 8, tier: 'official' }) }))
    expect(tracked.confluence).toBe(false)
    expect(official.confluence).toBe(true)
    expect(isOfficialCardPlay({ ...tracked, score: 70 })).toBe(false)
    expect(isOfficialCardPlay({ ...official, score: 70 })).toBe(true)
    expect(official.tier).toBe('official')                                             // the ledger bet's own notes carry the backend's verdict
    expect(isOfficialCardPlay({ ...official, score: 70, nBooks: 3 })).toBe(true)       // the site does not re-derive the gates: the tier is the verdict
    expect(isEvOutlier({ ...tracked, score: 70 })).toBe(true)
  })
  it('the table says UNVALIDATED / PAPER on a winter pick and nothing extra on an MLB pick', () => {
    const { container } = render(<TopPicksTable picks={[fromBet(bet()), fromBet(mlbBet({ id: 2 }))]} />)
    const rows = container.querySelectorAll('tbody tr')
    expect(within(rows[0] as HTMLElement).getByText(/UNVALIDATED/)).toBeInTheDocument()
    expect(within(rows[0] as HTMLElement).getByText('Over 10.5 Rebounds')).toBeInTheDocument()
    expect(within(rows[1] as HTMLElement).queryByText(/UNVALIDATED/)).toBeNull()
    expect(container.textContent).not.toMatch(/PROPS|rebounds_over/)
  })
})

describe('board rows from winter bets (lib/mlb-board.ts)', () => {
  it('a lower-case winter bet type gets its side, line, market and sport', () => {
    const [r] = fromBets([bet({ bet_type: 'pass_yds_under_250.5', player: 'Josh Allen', sport: 'nfl' })])
    expect(r).toMatchObject({ sport: 'nfl', side: 'under', line: 250.5, marketLabel: 'Pass Yds', player: 'Josh Allen', system: 'PROPS' })
  })
  it('an MLB row is unchanged', () => {
    const [r] = fromBets([mlbBet()])
    expect(r).toMatchObject({ sport: 'mlb', side: 'over', line: 7.5, marketLabel: 'Strikeouts' })
  })
})

describe('engine labels (no raw engine id for users)', () => {
  it.each([
    ['empirical_ewm_v2', 'Empirical EWM'],
    ['hierarchical_v1', 'Hierarchical'],
    ['empirical_ewm_v2+hierarchical_v1', 'Empirical EWM + Hierarchical'],
    ['hier_v1', 'Hierarchical'],
    ['brand_new_engine_v3', 'Brand new engine'],
  ])('%s -> %s', (raw, label) => expect(engineLabel(raw)).toBe(label))
  it('empty or missing is null', () => {
    expect(engineLabel('')).toBeNull()
    expect(engineLabel(null)).toBeNull()
  })
  it('NBA / NHL rows are tagged hierarchical by their model field, since their row engine stays empirical_ewm_v2', () => {
    expect(rowEngineLabel({ engine: 'empirical_ewm_v2', nba_model: 'hier_v1' })).toBe('Hierarchical')
    expect(rowEngineLabel({ engine: 'empirical_ewm_v2', nhl_model: 'hierarchical_v1' })).toBe('Hierarchical')
    expect(rowEngineLabel({ engine: 'empirical_ewm_v2' })).toBe('Empirical EWM')
  })
})

const proj = (o: Partial<SportProjectionRow> = {}): SportProjectionRow => ({
  sport: 'nhl', stat: 'pp_points', stat_label: 'PP Points', player: 'Jean-Sebastien Giguere-Montgomery-Vanderbilt III', player_norm: 'jean sebastien giguere', team: 'COL', opp: 'DAL',
  game_key: 'nhl:2026-11-03:DAL@COL', commence: '2026-11-04T01:00:00Z', mins_to_start: 300, has_line: true, line: 0.5, consensus_line: 0.5, side: 'under',
  proj_mean: 0.31, p_over_model: 0.27, p_model_side: 0.73, p_mkt_side: 0.68, l5_hit_rate: 0.2, l10_hit_rate: 0.3, szn_rate: 0.3, szn_avg: 0.3, avg_l10: 0.3,
  usage_label: 'Games', n_games: 40, best_over_odds: 220, best_over_book: 'draftkings', best_under_odds: -280, best_under_book: 'fanduel', p_over_market: 0.32,
  n_books: 6, model_edge: 0.05, market_ev: 0.02, confluence: true, edge_capped: false, beezy_score: 55, score_basis: 'confluence', validated: false,
  engine: 'empirical_ewm_v2', nhl_model: 'hierarchical_v1', headshot_id: null, ...o,
})

describe('projection board on a phone', () => {
  afterEach(() => { mobile = false })

  it('the live pp_points row renders its label and price on every viewport', () => {
    render(<SportProjectionsBoard sport="nhl" rows={fromSportProjections([proj()])} />)
    expect(screen.getByText('Under 0.5')).toBeInTheDocument()
    expect(screen.getByText('PP Points')).toBeInTheDocument()
    expect(screen.getByText('-280')).toBeInTheDocument()
  })

  it('desktop renders no detail row and no toggle button', () => {
    const { container } = render(<SportProjectionsBoard sport="nhl" rows={fromSportProjections([proj()])} />)
    expect(container.querySelector('.pb-detail')).toBeNull()
    expect(container.querySelector('button.pb-name')).toBeNull()
  })

  it('every column the phone drops is marked, and the long name sits in a truncating cell with its full text in the title', () => {
    const { container } = render(<SportProjectionsBoard sport="nhl" rows={fromSportProjections([proj()])} />)
    const heads = [...container.querySelectorAll('th')].map(th => [th.textContent, th.classList.contains('pb-hide-m')])
    expect(heads).toEqual([['Score', false], ['Player', false], ['Team', true], ['Market & line', false], ['Model', false], ['Market fair', true], ['Best price', false], ['Model edge', false], ['Price EV', true]])
    const cells = [...container.querySelectorAll('tbody tr td')]
    expect(cells.map(td => td.classList.contains('pb-hide-m'))).toEqual(heads.map(h => h[1]))
    expect(container.querySelector('td.pb-player .pb-name')).toHaveAttribute('title', proj().player)
  })

  it('a tap opens the detail row: game, market fair, price EV and a readable engine; a second tap closes it', () => {
    mobile = true
    const { container } = render(<SportProjectionsBoard sport="nhl" rows={fromSportProjections([proj()])} />)
    const btn = container.querySelector('button.pb-name') as HTMLElement
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(btn)
    const detail = container.querySelector('.pb-detail') as HTMLElement
    expect(detail).not.toBeNull()
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    const d = within(detail)
    expect(d.getByText('DAL @ COL')).toBeInTheDocument()
    expect(d.getByText('Hierarchical')).toBeInTheDocument()
    expect(d.queryByText(/Unvalidated model output/)).not.toBeInTheDocument()
    expect(d.getByText('Model + market agree')).toBeInTheDocument()
    expect(detail.textContent).not.toContain('L5 80%')          // no Form item in the detail row either
    expect(container.textContent).not.toMatch(/empirical_ewm|hierarchical_v1|hier_v1/)
    fireEvent.click(btn)
    expect(container.querySelector('.pb-detail')).toBeNull()
  })

  it('the detail row of an MLB row carries no unvalidated note', () => {
    mobile = true
    const rows = fromSportProjections([proj()]).map(r => ({ ...r, sport: 'mlb' as const }))
    const { container } = render(<SportProjectionsBoard sport="mlb" rows={rows} />)
    fireEvent.click(container.querySelector('button.pb-name') as HTMLElement)
    expect(container.querySelector('.pb-detail')).not.toBeNull()
    expect(container.querySelector('.pb-detail')!.textContent).not.toMatch(/Unvalidated/)
  })
})
