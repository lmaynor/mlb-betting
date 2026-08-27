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
import { pickLabel, resolveEvUnderlying } from '../lib/tokens'
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

  test('SB_2PLUS renders as "2+ Stolen Bases" (same pattern as K/OUTS/BATTER_TB/BATTER_HITS)', () => {
    const label = pickLabel(makeBet({ system: 'SB', bet_type: 'SB_2PLUS_2.0' }))
    expect(label).toBe('Gerrit Cole (NYY) 2+ Stolen Bases')
  })
})

/**
 * EV rows (system: 'EV', added 2026-08-20) pool +EV alerts from
 * fast_alert_loop/kalshi_alert across every market above. bet_type is the
 * underlying market's own convention suffixed with "_{book}" (the real
 * `book` column, lowercased) -- see settle_bets._settle_ev /
 * _strip_ev_book_suffix, which resolveEvUnderlying mirrors on the frontend.
 * Before this fix, pickLabel had no EV branch at all, so every EV row fell
 * through to the raw `return bt` at the end -- e.g. a settled K alert
 * rendered as the literal string "K_OVER_7.5_draftkings" on the public
 * Results page instead of a real pick label.
 */
describe('pickLabel resolves pooled EV alert rows to their underlying market', () => {
  test('EV row on a K alert renders identically to a native K bet, book suffix stripped', () => {
    const label = pickLabel(makeBet({
      system: 'EV', bet_type: 'K_OVER_7.5_draftkings', book: 'draftkings',
    }))
    expect(label).toBe('Gerrit Cole (NYY) Over 7.5 Strikeouts')
    expect(label).not.toContain('draftkings')
  })

  test('EV row on a threshold sub-market still gets the "N+" form, not the line repeated', () => {
    const label = pickLabel(makeBet({
      system: 'EV', bet_type: 'BATTER_TB_2PLUS_2.0_fanduel', book: 'fanduel',
    }))
    expect(label).toBe('Gerrit Cole (NYY) 2+ Total Bases')
  })

  test('EV row on a bare-word HR alert', () => {
    const label = pickLabel(makeBet({ system: 'EV', bet_type: 'HR_hardrock', book: 'hardrock' }))
    expect(label).toBe('Gerrit Cole (NYY) to Hit a Home Run')
  })

  test('EV row on a bare-string F5 game-line alert (no player -- away/home only)', () => {
    const label = pickLabel(makeBet({
      system: 'EV', bet_type: 'HOME_betmgm', book: 'betmgm',
      home_team: 'BOS', away_team: 'NYY', player: null,
    }))
    expect(label).toBe('BOS First 5 Innings Moneyline')
  })

  test('EV row on an unrecognised market still strips the book suffix instead of leaking it', () => {
    const label = pickLabel(makeBet({ system: 'EV', bet_type: 'SOMETHING_NEW_pinnacle', book: 'pinnacle' }))
    expect(label).not.toContain('pinnacle')
    expect(label).toBe('SOMETHING_NEW')
  })

  test('resolveEvUnderlying exposes the recovered system for non-pickLabel consumers', () => {
    expect(resolveEvUnderlying(makeBet({ system: 'EV', bet_type: 'OUTS_UNDER_14.5_fanduel', book: 'fanduel' })))
      .toEqual({ system: 'OUTS', bet_type: 'OUTS_UNDER_14.5' })
  })
})
