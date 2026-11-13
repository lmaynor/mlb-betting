/**
 * React Aria pilot (UI audit areas 1-2): the MatchupSelect listbox and the tab strips in Edge/Cockpit.
 * Behavior contract under test: the listbox opens on interaction, ArrowDown moves the active option,
 * Enter selects and closes, Escape closes without selecting, and focus returns to the trigger on close;
 * every tab strip keeps a roving tabindex (one Tab stop), arrow keys move selection AND focus together,
 * and the cockpit NBA tabs are programmatically linked to their panel.
 */
import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { CockpitFeed, CockpitForm, CockpitRow } from '../lib/types'

jest.mock('@/lib/betting-api', () => ({
  apiGetEdgeCockpit: jest.fn(() => new Promise(() => {})),
  apiGetShotChart: jest.fn(() => new Promise(() => {})),
  apiGetTrends: jest.fn(() => new Promise(() => {})),
}))

import { EdgeClient, type EdgePick } from '../app/edge/edge-client'

// -- picks fixtures: two games so the matchup filter has something to choose between --

const pick = (over: Partial<EdgePick>): EdgePick => ({
  id: 1, system: 'HR', game_date: '2026-10-14', game_pk: 101, bet_type: 'player_prop', player: 'Alpha Bat',
  away_team: 'New York Yankees', home_team: 'Boston Red Sox',
  odds: 300, stake: 10, model_prob: 0.3, market_prob: 0.25, edge: 0.05, kelly_pct: 1.2, kelly_triggered: true,
  result: null, profit: null, paper: true, book: 'fanduel', notes: null, created_at: '2026-10-14T12:00:00Z',
  headshotUrl: null, awayLogoUrl: null, homeLogoUrl: null, modelProbPct: 30, marketProbPct: 25, edgePctValue: 5,
  ...over,
})

const twoGamePicks: EdgePick[] = [
  pick({}),
  pick({ id: 2, game_pk: 102, player: 'Beta Bat', away_team: 'Los Angeles Dodgers', home_team: 'San Francisco Giants' }),
]

// -- cockpit feed fixtures (same builders as edge-cockpit.test.tsx) --

const form = (vals: number[], line: number, over: Partial<CockpitForm> = {}): CockpitForm => ({
  line, n_games: vals.length,
  games: vals.map((v, i) => ({ date: `W${i + 1}`, opp: i % 2 ? 'WAS' : 'DAL', val: v, usage: 6 + (i >= vals.length - 3 ? 3 : 0), hit: v > line })),
  l5_rate: 0.8, l10_rate: 0.6, szn_rate: 0.55, szn_avg: 71.4, szn_games: 12,
  usage_label: 'Targets', avg_val: 70, avg_usage: 7, ...over,
})

const VALS = [40, 55, 62, 70, 64, 80, 91, 66, 75, 88]

const row = (over: Partial<CockpitRow>): CockpitRow => ({
  id: 'nfl|1', sport: 'nfl', source: 'sink_a', shadow: true, player: 'Test Receiver', player_id: null, team: 'IND',
  matchup: 'IND @ WAS', commence: null, mins_to_kick: 120, stat: 'rec_yds', side: 'OVER', line: 63.5, book: 'fanduel',
  american: 115, decimal: 2.15, ev: 0.071, p_fair: 0.52, n_books: 5, flag: true, reason: null,
  all_books: [
    { book: 'draftkings', over: -108, under: -112, fair: 0.5 },
    { book: 'fanduel', over: 115, under: -135, fair: 0.49 },
    { book: 'novig', over: 110, under: null, fair: null },
  ],
  recentForm: form(VALS, 63.5), ...over,
})

const feed: CockpitFeed = {
  as_of: '2026-10-14T21:00:00Z', sport: 'all', min_ev: 0.02,
  sports: {
    mlb: { status: 'live', count: 1, as_of: null, age_minutes: null, stale: false, validated: true },
    nfl: { status: 'shadow_live', count: 1, as_of: null, age_minutes: 4, stale: false, validated: false },
    nhl: { status: 'shadow_live', count: 1, as_of: null, age_minutes: 4, stale: false, validated: false },
    nba: { status: 'unavailable', count: 0, as_of: null, age_minutes: null, stale: true, validated: false },
  },
  rows: [
    row({}),
    row({ id: 'mlb|model|1', sport: 'mlb', source: 'model', shadow: false, player: 'Slugger Jones', stat: 'home_runs', line: 0.5, book: 'draftkings', american: 380, ev: 0.04,
      p_fair: 0.24, all_books: null, system: 'HR', recentForm: form([0, 1, 0, 0, 1, 0, 0, 0, 1, 0], 0.5, { usage_label: null }),
      ev_la: [{ ev: 101, la: 24, hit: true }, { ev: 88, la: 5, hit: false }], spray: [{ x: 100, y: 120, hit: true }, { x: 150, y: 90, hit: false }] }),
  ],
}

const nbaFeed: CockpitFeed = {
  ...feed,
  sports: {
    mlb: { status: 'unavailable', count: 0, as_of: null, age_minutes: null, stale: true, validated: false },
    nfl: { status: 'unavailable', count: 0, as_of: null, age_minutes: null, stale: true, validated: false },
    nhl: { status: 'unavailable', count: 0, as_of: null, age_minutes: null, stale: true, validated: false },
    nba: { status: 'live', count: 1, as_of: null, age_minutes: 2, stale: false, validated: false },
  },
  rows: [
    row({ id: 'nba|1', sport: 'nba', player: 'Test Guard', team: 'LAL', matchup: 'LAL @ BOS', stat: 'points', line: 25.5,
      recentForm: form([18, 22, 30, 27, 24, 31, 29, 26, 33, 28], 25.5, { usage_label: 'Minutes' }) }),
  ],
}

const inList = () => within(screen.getByRole('list', { name: 'Prop candidates' }))

describe('MatchupSelect listbox', () => {
  it('opens on click with All games selected and one option per matchup', () => {
    render(<EdgeClient picks={twoGamePicks} updated="now" cockpit={null} />)
    const trigger = screen.getByRole('button', { name: 'Matchup filter' })
    expect(trigger).toHaveTextContent('All games')
    fireEvent.click(trigger)
    const listbox = screen.getByRole('listbox')
    const options = within(listbox).getAllByRole('option')
    expect(options).toHaveLength(3)
    expect(options[0]).toHaveTextContent('All games')
    expect(options[0]).toHaveAttribute('aria-selected', 'true')
    expect(options[1]).toHaveAttribute('aria-selected', 'false')
    expect(options[2]).toHaveAttribute('aria-selected', 'false')
  })

  it('ArrowDown moves the active option, Enter selects it, closes, and returns focus to the trigger', async () => {
    render(<EdgeClient picks={twoGamePicks} updated="now" cockpit={null} />)
    const trigger = screen.getByRole('button', { name: 'Matchup filter' })
    // open the way a keyboard user does: focus the trigger, ArrowDown opens and lands on the selected option
    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const listbox = screen.getByRole('listbox')
    const options = within(listbox).getAllByRole('option')
    expect(document.activeElement).toBe(options[0])
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'ArrowDown' })
    expect(document.activeElement).toBe(options[1])
    expect(options[1]).toHaveAttribute('data-focused', 'true')
    fireEvent.keyDown(document.activeElement as HTMLElement, { key: 'Enter' })
    expect(screen.queryByRole('listbox')).toBeNull()
    // React Aria restores focus to the trigger on the next frame after the popover closes
    await waitFor(() => expect(screen.getByRole('button', { name: 'Matchup filter' })).toHaveFocus())
    expect(screen.getByRole('button', { name: 'Matchup filter' })).toHaveTextContent('LAD')
    // the board is now filtered to the chosen game: the only 'Alpha Bat' left in the document is the
    // slate-wide best-pick HUD banner (edge-hud-bestname), which is not scoped by the matchup filter
    const remaining = screen.queryAllByText('Alpha Bat')
    expect(remaining).toHaveLength(1)
    expect(remaining[0]).toHaveClass('edge-hud-bestname')
    expect(screen.queryAllByText('Beta Bat').length).toBeGreaterThan(0)
  })

  it('Escape closes without changing the selection and returns focus to the trigger', async () => {
    render(<EdgeClient picks={twoGamePicks} updated="now" cockpit={null} />)
    const trigger = screen.getByRole('button', { name: 'Matchup filter' })
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole('listbox'), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Matchup filter' })).toHaveFocus())
    expect(screen.getByRole('button', { name: 'Matchup filter' })).toHaveTextContent('All games')
    expect(screen.queryAllByText('Alpha Bat').length).toBeGreaterThan(0)
    expect(screen.queryAllByText('Beta Bat').length).toBeGreaterThan(0)
  })
})

describe('mode tab strip (Edge)', () => {
  it('keeps a roving tabindex and ArrowRight moves selection and focus together', () => {
    render(<EdgeClient picks={twoGamePicks} updated="now" cockpit={null} />)
    const list = screen.getByRole('tablist', { name: 'Player or game props' })
    const [players, games] = within(list).getAllByRole('tab')
    expect(players).toHaveAttribute('aria-selected', 'true')
    expect(players).toHaveAttribute('tabindex', '0')
    expect(games).toHaveAttribute('tabindex', '-1')
    players.focus()
    fireEvent.keyDown(players, { key: 'ArrowRight' })
    expect(games).toHaveAttribute('aria-selected', 'true')
    expect(games).toHaveAttribute('tabindex', '0')
    expect(players).toHaveAttribute('tabindex', '-1')
    expect(games).toHaveFocus()
  })
})

describe('sport tab strip (Edge)', () => {
  it('keeps a roving tabindex and ArrowRight moves selection and focus together, filtering the list', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={feed} />)
    const list = screen.getByRole('tablist', { name: 'Sport' })
    const tabs = within(list).getAllByRole('tab')
    expect(tabs.map(t => t.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1', '-1'])
    tabs[0].focus()
    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    const mlb = screen.getByRole('tab', { name: /^MLB/ })
    expect(mlb).toHaveAttribute('aria-selected', 'true')
    expect(mlb).toHaveFocus()
    expect(screen.queryByText('Test Receiver')).toBeNull()
    expect(inList().getByText('Slugger Jones')).toBeInTheDocument()
  })
})

describe('NBA view tab strip (Cockpit inspector)', () => {
  it('links tabs to their panel and ArrowRight switches selection, focus, and panel together', () => {
    render(<EdgeClient picks={[]} updated="now" cockpit={nbaFeed} />)
    const aside = screen.getByRole('complementary', { name: 'Prop inspector' })
    const list = within(aside).getByRole('tablist', { name: 'NBA research views' })
    const [trends, shots, prices] = within(list).getAllByRole('tab')
    expect(trends).toHaveAttribute('aria-selected', 'true')
    expect(trends).toHaveAttribute('tabindex', '0')
    expect(shots).toHaveAttribute('tabindex', '-1')
    expect(prices).toHaveAttribute('tabindex', '-1')
    const panel = screen.getByRole('tabpanel')
    expect(trends).toHaveAttribute('aria-controls', panel.id)
    expect(panel).toHaveAttribute('aria-labelledby', trends.id)
    expect(panel).toHaveTextContent('TRENDS')
    trends.focus()
    fireEvent.keyDown(trends, { key: 'ArrowRight' })
    expect(shots).toHaveAttribute('aria-selected', 'true')
    expect(shots).toHaveFocus()
    const shotsPanel = screen.getByRole('tabpanel')
    expect(shots).toHaveAttribute('aria-controls', shotsPanel.id)
    expect(shotsPanel).toHaveAttribute('aria-labelledby', shots.id)
    expect(shotsPanel).toHaveTextContent('SHOT CHART')
  })
})
