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
 * "Gerrit Cole Under K_3PLUS_3.0 Strikeouts".
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
    expect(label).toBe('Gerrit Cole 2+ Total Bases')
  })

  test('BATTER_HITS_2PLUS renders as "2+ Hits", not "Under BATTER_HITS_2PLUS_2.0"', () => {
    const label = pickLabel(makeBet({ system: 'BATTER_HITS', bet_type: 'BATTER_HITS_2PLUS_2.0' }))
    expect(label).toBe('Gerrit Cole 2+ Hits')
  })

  test('existing OVER/UNDER labels are unaffected by the fix', () => {
    expect(pickLabel(makeBet({ system: 'K', bet_type: 'K_OVER_7.5' })))
      .toBe('Gerrit Cole Over 7.5 Strikeouts')
    expect(pickLabel(makeBet({ system: 'BATTER_TB', bet_type: 'BATTER_TB_UNDER_1.5' })))
      .toBe('Gerrit Cole Under 1.5 Total Bases')
  })

  test('SB_2PLUS renders as "2+ Stolen Bases" (same pattern as K/OUTS/BATTER_TB/BATTER_HITS)', () => {
    const label = pickLabel(makeBet({ system: 'SB', bet_type: 'SB_2PLUS_2.0' }))
    expect(label).toBe('Gerrit Cole 2+ Stolen Bases')
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
    expect(label).toBe('Gerrit Cole Over 7.5 Strikeouts')
    expect(label).not.toContain('draftkings')
  })

  test('EV row on a threshold sub-market still gets the "N+" form, not the line repeated', () => {
    const label = pickLabel(makeBet({
      system: 'EV', bet_type: 'BATTER_TB_2PLUS_2.0_fanduel', book: 'fanduel',
    }))
    expect(label).toBe('Gerrit Cole 2+ Total Bases')
  })

  test('EV row on a bare-word HR alert', () => {
    const label = pickLabel(makeBet({ system: 'EV', bet_type: 'HR_hardrock', book: 'hardrock' }))
    expect(label).toBe('Gerrit Cole to Hit a Home Run')
  })

  test('EV row on the No-HR side (Under 0.5) is labelled as the opposite bet', () => {
    const label = pickLabel(makeBet({ system: 'EV', bet_type: 'HR_NO_hardrock', book: 'hardrock' }))
    expect(label).toBe('Gerrit Cole to Not Hit a Home Run')
  })

  test('EV 1+ HR row on a book starting with "no" is NOT mistaken for the No side', () => {
    const label = pickLabel(makeBet({ system: 'EV', bet_type: 'HR_novig', book: 'novig' }))
    expect(label).toBe('Gerrit Cole to Hit a Home Run')
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

// -- v2 overhaul: EV suffix stripping, score tiers, sport registry, EV math --------
import { beezyscore, scoreTier, TIER_META } from '../lib/beezy-score'
import { BOOK_META, bookDisplay, CARD_STYLE } from '../lib/tokens'
import { SPORT_PILLARS, SPORT_LIST } from '../lib/sports'
import { americanToDecimal, fairAmerican, fullKelly, kellySizing, shrinkEdge } from '../lib/ev-math'

describe('pickLabel on system="EV" rows strips the trailing book tag', () => {
  test('K over', () => {
    expect(pickLabel(makeBet({ system: 'EV', bet_type: 'K_OVER_6.5_draftkings', book: 'draftkings' })))
      .toBe('Gerrit Cole Over 6.5 Strikeouts')
  })
  test('HR No on an exchange', () => {
    expect(pickLabel(makeBet({ system: 'EV', bet_type: 'HR_NO_novig', book: 'novig' })))
      .toBe('Gerrit Cole to Not Hit a Home Run')
  })
  test('HR yes is not mistaken for HR No on book "novig"', () => {
    expect(pickLabel(makeBet({ system: 'EV', bet_type: 'HR_novig', book: 'novig' })))
      .toBe('Gerrit Cole to Hit a Home Run')
  })
  test('NRFI', () => {
    expect(pickLabel(makeBet({ system: 'EV', bet_type: 'NRFI_caesars', book: 'caesars' }))).toBe('No Run 1st Inning')
  })
  test('SB over', () => {
    expect(pickLabel(makeBet({ system: 'EV', bet_type: 'SB_OVER_0.5_betmgm', book: 'betmgm' })))
      .toBe('Gerrit Cole Over 0.5 Stolen Bases')
  })
})

describe('beezyscore / scoreTier', () => {
  test('returns an integer in 0-100 and a valid tier for fraction-valued and percent-valued edges', () => {
    for (const edge of [0.02, 0.08, 0.3, 8, 25, null]) {
      const s = beezyscore(makeBet({ edge, model_prob: 0.6, market_prob: 0.5 }))
      expect(Number.isInteger(s)).toBe(true)
      expect(s).toBeGreaterThanOrEqual(0)
      expect(s).toBeLessThanOrEqual(100)
      expect(['strong', 'lean', 'watch']).toContain(scoreTier(s))
    }
  })
  test('0.08 and 8 (same edge, different units) score identically', () => {
    expect(beezyscore(makeBet({ edge: 0.08 }))).toBe(beezyscore(makeBet({ edge: 8 })))
  })
  test('tier boundaries and metadata', () => {
    expect(scoreTier(70)).toBe('strong')
    expect(scoreTier(69)).toBe('lean')
    expect(scoreTier(45)).toBe('lean')
    expect(scoreTier(44)).toBe('watch')
    expect(TIER_META.strong.color).toBe('#57F287')
  })
})

describe('sport registry and tokens', () => {
  test('SPORT_PILLARS has the four pillars and none is a placeholder', () => {
    expect(Object.keys(SPORT_PILLARS).sort()).toEqual(['mlb', 'nba', 'nfl', 'nhl'])
    expect(SPORT_LIST.map(p => p.status)).toEqual(['live', 'live', 'live', 'live'])
    for (const p of SPORT_LIST) {
      expect(['data_only', 'planned']).not.toContain(p.status)
      for (const m of p.markets) expect(m.status).not.toMatch(/soon|preview|planned/i)
    }
    expect(SPORT_PILLARS.mlb.seasonBadge).toBe('10 MODELS + +EV SCANNER')
    expect(SPORT_PILLARS.nfl.markets.map(m => m.key)).toEqual(['pass_yds', 'pass_tds', 'pass_completions', 'pass_attempts', 'interceptions', 'rush_yds', 'rush_attempts', 'rec_yds', 'receptions', 'anytime_td'])
    expect(SPORT_PILLARS.nhl.markets.map(m => m.key)).toEqual(['sog', 'goals', 'assists', 'points', 'pp_points', 'saves', 'blocked_shots'])
    expect(SPORT_PILLARS.nba.markets.map(m => m.key)).toEqual(['points', 'rebounds', 'assists', 'threes', 'pra', 'pr', 'pa', 'ra', 'steals', 'blocks'])
  })
  test('BOOK_META marks exchanges and bookDisplay falls back for unknown books', () => {
    expect(BOOK_META.kalshi.isExchange).toBe(true)
    expect(BOOK_META.novig.isExchange).toBe(true)
    expect(BOOK_META.draftkings.isExchange).toBeUndefined()
    expect(bookDisplay('somenewbook').label).toBe('Somenewbook')
    expect(bookDisplay(null).label).toBe('--')
  })
  test('CARD_STYLE uses the v2 geometry', () => {
    expect(CARD_STYLE.borderRadius).toBe('10px')
    expect(CARD_STYLE.border).toBe('1px solid #1e2230')
  })
})

describe('ev-math', () => {
  test('americanToDecimal', () => {
    expect(americanToDecimal(100)).toBe(2)
    expect(americanToDecimal(-200)).toBeCloseTo(1.5)
  })
  test('fullKelly is 0 with no edge and positive with edge', () => {
    expect(fullKelly(0.5, 2)).toBe(0)
    expect(fullKelly(0.55, 2)).toBeCloseTo(0.1)
  })
  test('kellySizing: quarter Kelly, capped at 5% of bankroll', () => {
    expect(kellySizing(1000, 0.55, 2).dollars).toBeCloseTo(25)       // 0.1 * 0.25 * 1000
    expect(kellySizing(1000, 0.9, 2).dollars).toBeCloseTo(50)        // capped at 5%
    expect(kellySizing(1000, null, 2).dollars).toBe(0)
    expect(kellySizing(0, 0.55, 2).dollars).toBe(0)
    // shrinkage: edge over the 6% knee is sized off 0.06 + 0.35 * excess, capped at 10%
    expect(kellySizing(1000, 0.62, 2).dollars).toBeCloseTo(kellySizing(1000, 0.5 + shrinkEdge(0.12), 2, 0.25, 0.05, false).dollars)
    expect(kellySizing(1000, 0.62, 2).dollars).toBeLessThan(kellySizing(1000, 0.62, 2, 0.25, 0.05, false).dollars)
  })

  test('shrinkEdge: identity below the knee, concave above, capped, odd', () => {
    expect(shrinkEdge(0.05)).toBeCloseTo(0.05)
    expect(shrinkEdge(0.10)).toBeCloseTo(0.074)
    expect(shrinkEdge(0.6)).toBeCloseTo(0.10)
    expect(shrinkEdge(-0.12)).toBeCloseTo(-shrinkEdge(0.12))
    expect(shrinkEdge(NaN)).toBe(0)
  })
  test('fairAmerican', () => {
    expect(fairAmerican(0.478)).toBe('+109')
    expect(fairAmerican(0.6)).toBe('-150')
    expect(fairAmerican(null)).toBe('--')
  })
})

describe('learn links', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { ARTICLES } = require('../lib/articles-static')
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { PICK_SYSTEMS } = require('../lib/pick-systems')
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { MODEL_SPECS } = require('../lib/model-specs')
  const slugs = new Set(ARTICLES.map((a: { slug: string }) => a.slug))
  it('every learnSlug on a system or model page points at a real article', () => {
    const used = [
      ...PICK_SYSTEMS.map((s: { learnSlug?: string }) => s.learnSlug),
      ...Object.values(MODEL_SPECS ?? {}).map((s) => (s as { learnSlug?: string }).learnSlug),
    ].filter(Boolean)
    expect(used.length).toBeGreaterThan(0)
    for (const u of used) expect(slugs.has(u)).toBe(true)
  })
})
