/**
 * beezy-vip/tests/tokens.test.ts
 *
 * Tests for lib/tokens.ts's pickLabel(), specifically the 2+/3+ threshold
 * sub-market formatting added 2026-08-19 (K_2PLUS/OUTS_2PLUS/BATTER_TB_2PLUS/
 * BATTER_HITS_2PLUS bet types, all shaped "{SYSTEM}_{N}PLUS_{N}.0").
 *
 * Regression coverage for a confirmed bug: before this fix, none of these
 * bet_type strings matched any system's OVER/UNDER prefix check, so `side`
 * silently defaulted to 'Under' and the .replace() calls were no-ops --
 * producing genuinely broken user-facing text like
 * "Gerrit Cole (NYY) Under K_3PLUS_3.0 Strikeouts".
 */
import { pickLabel } from '../lib/tokens'
import type { Bet } from '../lib/types'

function makeBet(overrides: Partial<Bet>): Bet {
  return {
    id: 1,
    system: 'K',
    game_date: '2026-08-19',
    game_pk: 12345,
    bet_type: 'K_OVER_7.5',
    player: 'Gerrit Cole',
    away_team: 'NYY',
    home_team: 'BOS',
    odds: -110,
    stake: 10,
    model_prob: 0.55,
    market_prob: 0.5,
    edge: 0.05,
    kelly_pct: 0.02,
    kelly_triggered: true,
    result: null,
    profit: null,
    paper: true,
    book: 'draftkings',
    notes: null,
    created_at: '2026-08-19T16:00:00Z',
    ...overrides,
  }
}

describe('pickLabel threshold sub-markets (2+/3+)', () => {
  test('K_2PLUS renders as "2+ Strikeouts", not "Under"', () => {
    const label = pickLabel(makeBet({ system: 'K', bet_type: 'K_2PLUS_2.0' }))
    expect(label).toContain('2+ Strikeouts')
    expect(label).not.toContain('Under')
    expect(label).not.toContain('PLUS')
  })

  test('OUTS_3PLUS renders as "3+ Outs Recorded"', () => {
    const label = pickLabel(makeBet({ system: 'OUTS', bet_type: 'OUTS_3PLUS_3.0' }))
    expect(label).toContain('3+ Outs Recorded')
    expect(label).not.toContain('Under')
  })

  test('BATTER_TB_2PLUS renders as "2+ Total Bases", not "Under BATTER_TB_2PLUS_2.0"', () => {
    const label = pickLabel(makeBet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_2PLUS_2.0' }))
    expect(label).toBe('Gerrit Cole (NYY) 2+ Total Bases')
  })

  test('BATTER_HITS_2PLUS renders as "2+ Hits", not "Under BATTER_HITS_2PLUS_2.0"', () => {
    const label = pickLabel(makeBet({ system: 'BATTER_HITS', bet_type: 'BATTER_HITS_2PLUS_2.0' }))
    expect(label).toBe('Gerrit Cole (NYY) 2+ Hits')
  })

  test('existing OVER/UNDER labels are unaffected by the fix', () => {
    expect(pickLabel(makeBet({ system: 'K', bet_type: 'K_OVER_7.5' })))
      .toBe('Gerrit Cole (NYY) Over 7.5 Strikeouts')
    expect(pickLabel(makeBet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_UNDER_1.5' })))
      .toBe('Gerrit Cole (NYY) Under 1.5 Total Bases')
  })
})
