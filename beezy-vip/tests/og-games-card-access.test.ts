/**
 * @jest-environment node
 *
 * Finding 11f5fa70 regression: the public OG games card must go through the free-preview policy
 * (lib/access.ts: FREE_PREVIEW_ROWS.game_picks = 0). While tier gating is on, the card carries none of
 * today's paid game picks; with gating off (pre-launch default) the cut is a pass-through.
 */
import type { ReactElement } from 'react'

jest.mock('next/og', () => ({
  ImageResponse: class {
    el: unknown
    constructor(el: unknown) { this.el = el }
  },
}))

const GAME_PICK = {
  system: 'GAME', bet_type: 'NYY ML', odds: -110, edge: 0.09, kelly_triggered: true,
  away_team: 'NYY', home_team: 'HOU', stake: 1.5, notes: '',
}

function collectStrings(node: unknown, out: string[] = []): string[] {
  if (node == null) return out
  if (typeof node === 'string' || typeof node === 'number') { out.push(String(node)); return out }
  if (Array.isArray(node)) { node.forEach(n => collectStrings(n, out)); return out }
  if (typeof node === 'object' && 'props' in (node as Record<string, unknown>)) {
    const props = (node as ReactElement).props as Record<string, unknown>
    for (const [key, value] of Object.entries(props)) {
      if (key === 'children') collectStrings(value, out)
      else if (typeof value === 'string') out.push(value)
    }
  }
  return out
}

let fetchMock: jest.Mock

async function call() {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  fetchMock = jest.fn(async (url: string) => ({
    json: async () => (url.includes('/picks/today')
      ? [GAME_PICK]
      : { overall: { roi: '12.3', total_bets: '321' } }),
  }))
  global.fetch = fetchMock as unknown as typeof fetch
  const { GET } = await import('@/app/api/og/games-card/route')
  const res = await GET()
  return collectStrings((res as unknown as { el: unknown }).el)
}

afterEach(() => {
  delete process.env.ENFORCE_TIERS
})

describe('OG games card preview policy', () => {
  it('exposes no paid game pick while tier gating is on', async () => {
    process.env.ENFORCE_TIERS = '1'
    const strings = await call()
    expect(strings.join(' ')).toContain('NO GAME PICKS TODAY')
    expect(strings).not.toContain('NYY')
    expect(strings).not.toContain('/logos/nyy.png')
  })

  it('renders the top game picks while gating is off (pre-launch pass-through)', async () => {
    delete process.env.ENFORCE_TIERS
    const strings = await call()
    expect(strings).toContain('NYY')
    expect(strings).toContain('HOU')
  })
})
