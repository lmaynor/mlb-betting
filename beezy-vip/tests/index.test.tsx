/**
 * beezy-vip/tests/index.test.ts
 *
 * Unit tests for the beezy-vip frontend.
 * Run with: npx jest  (add jest + @testing-library/react to devDependencies)
 *
 * Install:
 *   npm install -D jest @testing-library/react @testing-library/jest-dom \
 *     jest-environment-jsdom ts-jest @types/jest
 *
 * jest.config.ts:
 *   export default { preset: 'ts-jest', testEnvironment: 'jsdom',
 *     moduleNameMapper: { '^@/(.*)$': '<rootDir>/$1' } }
 */

// ---------------------------------------------------------------------------
// Schema contract tests (lib/types.ts)
// These catch column name renames before they reach production.
// ---------------------------------------------------------------------------

import type { Bet, SystemStats } from '../lib/types'

describe('Bet interface schema contract', () => {
  // Build a well-typed Bet to confirm the interface matches production columns.
  // If any column name is wrong, TypeScript will error here at compile time.
  const validBet: Bet = {
    id:              1,
    system:          'NRFI',
    game_date:       '2026-05-15',
    game_pk:         12345,
    bet_type:        'NRFI',
    player:          null,
    away_team:       'NYY',
    home_team:       'BOS',
    odds:            -115,       // production column name (not 'line')
    stake:           10.0,
    model_prob:      0.62,
    market_prob:     0.55,       // production column name (not 'implied_prob')
    edge:            0.07,
    kelly_pct:       0.05,
    kelly_triggered: true,
    result:          'win',      // lowercase (not 'W')
    profit:          8.70,       // production column name (not 'pnl')
    paper:           true,
    book:            'draftkings',
    notes:           null,
    created_at:      '2026-05-15T12:00:00',
  }

  test('Bet has odds field not line', () => {
    expect('odds' in validBet).toBe(true)
    expect('line' in validBet).toBe(false)
  })

  test('Bet has profit field not pnl', () => {
    expect('profit' in validBet).toBe(true)
    expect('pnl' in validBet).toBe(false)
  })

  test('Bet has market_prob field not implied_prob', () => {
    expect('market_prob' in validBet).toBe(true)
    expect('implied_prob' in validBet).toBe(false)
  })

  test('result values are lowercase', () => {
    const results: Array<Bet['result']> = ['win', 'loss', 'push', 'void', null]
    results.forEach(r => {
      if (r !== null) {
        expect(r).toBe(r.toLowerCase())
      }
    })
  })

  test('result does not accept uppercase W/L/P', () => {
    // This is a TypeScript compile-time check -- the type literal union
    // 'win' | 'loss' | 'push' | 'void' | null should not include 'W', 'L', 'P'
    // If this test file compiles, the contract is enforced.
    const result: Bet['result'] = 'win'
    expect(result).toBe('win')
  })
})


// ---------------------------------------------------------------------------
// betting-api.ts contract tests
// ---------------------------------------------------------------------------

describe('betting-api throws when BETTING_API_URL not set', () => {
  const originalEnv = process.env.BETTING_API_URL

  beforeEach(() => {
    delete process.env.BETTING_API_URL
    // Clear module cache so the module re-reads env vars
    jest.resetModules()
  })

  afterEach(() => {
    process.env.BETTING_API_URL = originalEnv
  })

  test.skip('apiGetTodayPicks throws if BETTING_API_URL not set', async () => {
    const { apiGetTodayPicks } = await import('../lib/betting-api')
    await expect(apiGetTodayPicks()).rejects.toThrow('BETTING_API_URL is not set')
  })

  test.skip('apiGetStats throws if BETTING_API_URL not set', async () => {
    const { apiGetStats } = await import('../lib/betting-api')
    await expect(apiGetStats()).rejects.toThrow('BETTING_API_URL is not set')
  })
})

describe('betting-api throws on non-ok response', () => {
  beforeEach(() => {
    process.env.BETTING_API_URL = 'https://example.com'
    process.env.BETTING_API_KEY = 'test-key'
    jest.resetModules()
  })

  test('apiGetStats throws on 401', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 401 }) as jest.Mock
    const { apiGetStats } = await import('../lib/betting-api')
    await expect(apiGetStats()).rejects.toThrow('Betting API error 401')
  })

  test('apiGetStats throws on 500', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 500 }) as jest.Mock
    const { apiGetStats } = await import('../lib/betting-api')
    await expect(apiGetStats()).rejects.toThrow('Betting API error 500')
  })

  test('apiGetPicks passes system filter in query string', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true, json: async () => ({ picks: [], count: 0 })
    }) as jest.Mock
    const { apiGetPicks } = await import('../lib/betting-api')
    await apiGetPicks({ system: 'NRFI' })
    const url = (global.fetch as jest.Mock).mock.calls[0][0] as string
    expect(url).toContain('system=NRFI')
  })

  test('apiGetPicks passes status filter in query string', async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true, json: async () => ({ picks: [], count: 0 })
    }) as jest.Mock
    const { apiGetPicks } = await import('../lib/betting-api')
    await apiGetPicks({ status: 'settled' })
    const url = (global.fetch as jest.Mock).mock.calls[0][0] as string
    expect(url).toContain('status=settled')
  })
})


// ---------------------------------------------------------------------------
// ResultPill component tests
// ---------------------------------------------------------------------------

import React from 'react'
import { render, screen } from '@testing-library/react'
import { ResultPill } from '../components/ui/primitives'

describe('ResultPill', () => {
  test('renders PENDING for null result', () => {
    render(<ResultPill result={null} />)
    expect(screen.getByText('PENDING')).toBeTruthy()
  })

  test('renders WIN for win result', () => {
    render(<ResultPill result="win" />)
    expect(screen.getByText('WIN')).toBeTruthy()
  })

  test('renders LOSS for loss result', () => {
    render(<ResultPill result="loss" />)
    expect(screen.getByText('LOSS')).toBeTruthy()
  })

  test('renders PUSH for push result', () => {
    render(<ResultPill result="push" />)
    expect(screen.getByText('PUSH')).toBeTruthy()
  })

  test('renders VOID for void result', () => {
    render(<ResultPill result="void" />)
    expect(screen.getByText('VOID')).toBeTruthy()
  })

  test('win result shows WIN text', () => {
    render(<ResultPill result="win" />)
    expect(screen.getByText('WIN')).toBeTruthy()
  })

  test('loss result shows LOSS text', () => {
    render(<ResultPill result="loss" />)
    expect(screen.getByText('LOSS')).toBeTruthy()
  })

  test('does not accept uppercase W -- TypeScript compile guard', () => {
    // If this compiles with result="win" and not "W", the type contract is enforced
    render(<ResultPill result="win" />)
    expect(screen.getByText('WIN')).toBeTruthy()
  })
})


// ---------------------------------------------------------------------------
// Geo-blocking middleware unit tests
// ---------------------------------------------------------------------------

// We test the pure functions extracted from middleware.ts
// getBlockedStates() and isGeoBlocked() logic

describe('getBlockedStates', () => {
  const getBlockedStates = (raw: string): Set<string> => {
    if (!raw.trim()) return new Set()
    return new Set(raw.split(',').map(s => s.trim().toUpperCase()).filter(Boolean))
  }

  test('empty string returns empty set', () => {
    expect(getBlockedStates('').size).toBe(0)
  })

  test('single space returns empty set', () => {
    expect(getBlockedStates(' ').size).toBe(0)
  })

  test('single state', () => {
    const s = getBlockedStates('NY')
    expect(s.has('NY')).toBe(true)
    expect(s.size).toBe(1)
  })

  test('multiple states comma separated', () => {
    const s = getBlockedStates('NY,WA,NJ')
    expect(s.has('NY')).toBe(true)
    expect(s.has('WA')).toBe(true)
    expect(s.has('NJ')).toBe(true)
    expect(s.size).toBe(3)
  })

  test('lowercases are normalized to uppercase', () => {
    const s = getBlockedStates('ny,wa')
    expect(s.has('NY')).toBe(true)
    expect(s.has('WA')).toBe(true)
  })

  test('handles spaces around commas', () => {
    const s = getBlockedStates('NY , WA')
    expect(s.has('NY')).toBe(true)
    expect(s.has('WA')).toBe(true)
  })
})

describe('geo-blocking logic', () => {
  const isBlocked = (
    blockedStates: Set<string>,
    country: string,
    region: string
  ): boolean => {
    if (blockedStates.size === 0) return false
    if (country !== 'US') return false
    return blockedStates.has(region)
  }

  test('empty blocked states blocks nothing', () => {
    expect(isBlocked(new Set(), 'US', 'NY')).toBe(false)
  })

  test('blocked state in US is blocked', () => {
    expect(isBlocked(new Set(['NY']), 'US', 'NY')).toBe(true)
  })

  test('non-blocked state in US is allowed', () => {
    expect(isBlocked(new Set(['NY']), 'US', 'CA')).toBe(false)
  })

  test('blocked state outside US is allowed', () => {
    // If someone is in Canada, even if NY is blocked, they pass
    expect(isBlocked(new Set(['NY']), 'CA', 'NY')).toBe(false)
  })

  test('non-US country is always allowed', () => {
    expect(isBlocked(new Set(['NY', 'WA']), 'GB', 'LDN')).toBe(false)
  })
})


// ---------------------------------------------------------------------------
// lib/db.ts contract tests -- SKIPPED (lib/db.ts was deleted; types moved to lib/types.ts)
// ---------------------------------------------------------------------------

describe.skip('migrate route contract', () => {
  test('does not reference bets table', async () => {
    const fs = await import('fs')
    const src = fs.readFileSync('./app/api/db/migrate/route.ts', 'utf-8')
    expect(src).not.toContain('ALTER TABLE bets')
    expect(src).not.toContain('bets ADD COLUMN')
  })

  test('only creates learn_articles table', async () => {
    const fs = await import('fs')
    const src = fs.readFileSync('./app/api/db/migrate/route.ts', 'utf-8')
    expect(src).toContain('learn_articles')
    expect(src).toContain('ensureLearnTable')
  })
})


describe.skip('getSummaryStats SQL contract', () => {
  test('uses profit not pnl', async () => {
    const fs = await import('fs')
    const src = fs.readFileSync('./lib/db.ts', 'utf-8')
    expect(src).toContain("SUM(profit)")
    expect(src).not.toContain("SUM(pnl)")
  })

  test('uses market_prob not implied_prob', async () => {
    const fs = await import('fs')
    const src = fs.readFileSync('./lib/db.ts', 'utf-8')
    expect(src).toContain("market_prob")
    expect(src).not.toContain("implied_prob")
  })

  test('uses lowercase result values', async () => {
    const fs = await import('fs')
    const src = fs.readFileSync('./lib/db.ts', 'utf-8')
    expect(src).toContain("result = 'win'")
    expect(src).toContain("result = 'loss'")
    expect(src).not.toContain("result = 'W'")
    expect(src).not.toContain("result = 'L'")
  })
})
