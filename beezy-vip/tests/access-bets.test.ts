import { limitBets } from '../lib/access-bets'
import { FREE_PREVIEW_ROWS, type Viewer } from '../lib/access'
import type { Bet } from '../lib/types'

const ON = { ENFORCE_TIERS: '1' }
const free: Viewer = { tier: 'public', sport: null }
const pro: Viewer = { tier: 'pro_annual', sport: null }
const TODAY = '2026-10-16'

let id = 0
const bet = (o: Partial<Bet>): Bet => ({
  id: ++id, system: 'K', game_date: TODAY, game_pk: 1, bet_type: 'K_OVER_5.5', player: 'Pitcher', away_team: 'A', home_team: 'B', odds: 100,
  stake: 25, model_prob: 0.55, market_prob: 0.5, edge: 0.05, kelly_pct: 0.01, kelly_triggered: true, result: null, profit: null, paper: true,
  book: 'draftkings', notes: null, created_at: '', ...o,
})

describe('limitBets', () => {
  it('cuts today to the best player props and withholds all game picks for a free viewer', () => {
    const players = Array.from({ length: 9 }, (_, i) => bet({ player: `P${i}`, game_pk: 10 + i, edge: 0.03 + i * 0.01 }))
    const games = [bet({ system: 'NRFI', bet_type: 'NRFI', player: null, game_pk: 99 })]
    const { bets, locked } = limitBets([...players, ...games], free, TODAY, ON)
    expect(bets.filter(b => b.system === 'K')).toHaveLength(FREE_PREVIEW_ROWS.player_board)
    expect(bets.some(b => b.system === 'NRFI')).toBe(false)
    expect(locked).toBe(9 - FREE_PREVIEW_ROWS.player_board + 1)
    // the kept props are the highest-scoring ones
    expect(bets.map(b => b.player).sort()).toEqual(['P4', 'P5', 'P6', 'P7', 'P8'])
  })

  it('counts props, not rows: one prop flagged at several books is one pick and all its book rows survive', () => {
    const rows = [
      ...['draftkings', 'fanduel', 'betmgm'].map(book => bet({ player: 'Same Guy', game_pk: 5, book })),
      ...Array.from({ length: 4 }, (_, i) => bet({ player: `Other${i}`, game_pk: 20 + i })),
    ]
    const { bets, locked } = limitBets(rows, free, TODAY, ON)
    expect(locked).toBe(0) // 5 props, preview is 5
    expect(bets).toHaveLength(7)
  })

  it('never cuts settled or past days', () => {
    const past = Array.from({ length: 12 }, (_, i) => bet({ game_date: '2026-10-15', game_pk: 100 + i, result: 'win' }))
    const { bets, locked } = limitBets(past, free, TODAY, ON)
    expect(bets).toHaveLength(12)
    expect(locked).toBe(0)
  })

  it('leaves a subscriber and a pre-launch site untouched', () => {
    const many = Array.from({ length: 12 }, (_, i) => bet({ player: `P${i}`, game_pk: i }))
    expect(limitBets(many, pro, TODAY, ON).bets).toHaveLength(12)
    expect(limitBets(many, free, TODAY, {}).bets).toHaveLength(12)
  })
})
