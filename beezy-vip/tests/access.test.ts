import { canViewFull, FREE_PREVIEW_ROWS, gatingOn, limitRows, viewerOf, type Viewer } from '../lib/access'
import { shapeHub } from '../lib/hub-data'
import { betKind, kindOfSystem } from '../lib/pick-systems'
import type { Bet, EvFeed, SportProjectionRow, SportProjections } from '../lib/types'

const ON = { ENFORCE_TIERS: '1' }
const free: Viewer = { tier: 'public', sport: null }
const nbaPass: Viewer = { tier: 'single_sport', sport: 'nba' }
const pro: Viewer = { tier: 'pro_monthly', sport: null }

describe('access policy', () => {
  it('nothing is gated while the site is pre-launch', () => {
    expect(gatingOn({})).toBe(false)
    expect(canViewFull(free, 'player_board', 'nba', {})).toBe(true)
    expect(limitRows([1, 2, 3], free, 'game_picks', 'mlb', x => x, {}).locked).toBe(0)
  })

  it('with gating on: free is cut, a single-sport pass unlocks only its league, all-access unlocks everything', () => {
    expect(gatingOn(ON)).toBe(true)
    expect(canViewFull(free, 'player_board', 'nba', ON)).toBe(false)
    expect(canViewFull(nbaPass, 'player_board', 'nba', ON)).toBe(true)
    expect(canViewFull(nbaPass, 'player_board', 'nfl', ON)).toBe(false)
    expect(canViewFull(nbaPass, 'game_picks', 'mlb', ON)).toBe(false)
    expect(canViewFull(pro, 'game_picks', 'mlb', ON)).toBe(true)
    expect(canViewFull({ tier: 'pro_annual', sport: null }, 'player_board', 'nhl', ON)).toBe(true)
    expect(canViewFull({ tier: 'single_sport', sport: null }, 'player_board', 'mlb', ON)).toBe(false) // pass with no league recorded
  })

  it('a free viewer keeps the best rows of a player board, none of the game picks, and the count that was withheld', () => {
    const rows = [3, 9, 1, 7, 5, 8, 2, 6, 4, 10]
    const p = limitRows(rows, free, 'player_board', 'mlb', x => x, ON)
    expect(p.full).toBe(false)
    expect(p.rows).toEqual([10, 9, 8, 7, 6].slice(0, FREE_PREVIEW_ROWS.player_board))
    expect(p.locked).toBe(rows.length - FREE_PREVIEW_ROWS.player_board)
    const g = limitRows(rows, free, 'game_picks', 'mlb', x => x, ON)
    expect(g.rows).toEqual([])
    expect(g.locked).toBe(rows.length)
  })

  it('a full view keeps the caller\'s order', () => {
    expect(limitRows([3, 9, 1], pro, 'player_board', 'mlb', x => x, ON).rows).toEqual([3, 9, 1])
  })

  it('unknown tiers and chosen leagues fall back to free / no league', () => {
    expect(viewerOf('public', 'cricket')).toEqual({ tier: 'public', sport: null })
    expect(viewerOf('single_sport', 'nhl')).toEqual({ tier: 'single_sport', sport: 'nhl' })
  })
})

describe('player vs game picks', () => {
  it('classifies the MLB systems', () => {
    for (const s of ['NRFI', '1I', '1IOU', 'F3', 'F5', 'F1H', 'F7', 'GAME']) expect(kindOfSystem(s)).toBe('game')
    for (const s of ['HR', 'K', 'OUTS', 'BATTER_TB', 'BATTER_HITS', 'SB', 'PITCHER_ER', 'BATTER_K']) expect(kindOfSystem(s)).toBe('player')
  })
  it('an EV alert is classified by the market it alerted on', () => {
    const ev = (bet_type: string, player: string | null) => ({ system: 'EV', bet_type, player, book: 'fanduel' }) as unknown as Bet
    expect(betKind(ev('K_OVER_5.5_fanduel', 'Gerrit Cole'))).toBe('player')
    expect(betKind(ev('NRFI_fanduel', null))).toBe('game')
  })
})

const row = (o: Partial<SportProjectionRow>): SportProjectionRow => ({ sport: 'nba', stat: 'pts', stat_label: 'Points', player: 'P', player_norm: 'p', team: null, opp: null, game_key: 'nba:d:A@B', beezy_score: 10, ...o }) as SportProjectionRow
const proj = (sport: 'nfl' | 'nhl' | 'nba', n: number): SportProjections =>
  ({ sport, rows: Array.from({ length: n }, (_, i) => row({ sport, player: `Pl${i}x`, player_norm: `pl${i}x`, beezy_score: i })) }) as unknown as SportProjections
const out = (player: string, ev: number) => ({ player, ev, game_key: 'x:d:A@B' })

describe('hub data', () => {
  const feed = {
    nfl: { status: 'live', age_minutes: 1, rows: [out('Kalshi Ask', 0.1)], book_outliers: [out('Nfl Only', 0.05)] },
    nhl: { status: 'live', age_minutes: 2, rows: [out('Nhl Only', 0.05)] },
    nba: { status: 'live', age_minutes: 3, rows: [out('Nba Only', 0.05)] },
  } as unknown as EvFeed

  it('each hub is siloed: it only ever gets its own sport\'s outliers and status', () => {
    const nba = shapeHub('nba', feed, proj('nba', 3), pro, ON)
    expect(nba.outliers.map(o => o.player)).toEqual(['Nba Only'])
    expect(nba.block).toEqual({ status: 'live', age_minutes: 3 })
    expect(shapeHub('nfl', feed, proj('nfl', 3), pro, ON).outliers.map(o => o.player)).toEqual(['Nfl Only']) // not the Kalshi ask
    expect(shapeHub('nhl', feed, proj('nhl', 3), pro, ON).outliers.map(o => o.player)).toEqual(['Nhl Only'])
  })

  it('withheld rows are removed from the payload, not hidden: nothing past the preview is in what the client gets', () => {
    const hub = shapeHub('nba', feed, proj('nba', 12), free, ON)
    expect(hub.projections!.rows).toHaveLength(FREE_PREVIEW_ROWS.player_board)
    expect(hub.projections!.rows.map(r => r.beezy_score)).toEqual([11, 10, 9, 8, 7])
    expect(hub.lockedProjections).toBe(12 - FREE_PREVIEW_ROWS.player_board)
    expect(JSON.stringify(hub)).not.toContain('Pl0x')
  })

  it('a pass for the same league sees the whole board, a pass for another league does not', () => {
    expect(shapeHub('nba', feed, proj('nba', 12), nbaPass, ON).lockedProjections).toBe(0)
    expect(shapeHub('nhl', feed, proj('nhl', 12), nbaPass, ON).lockedProjections).toBe(7)
  })
})
