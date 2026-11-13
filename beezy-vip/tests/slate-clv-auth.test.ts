/**
 * @jest-environment node
 *
 * Finding 954a0a3a regression: /api/slate/today and /api/stats/clv are login-gated data routes,
 * so their handlers must refuse anonymous callers with 401 (and never reach the upstream API,
 * which would spend the server-side key) while passing signed-in callers through.
 */
import { NextRequest } from 'next/server'

const getUserIdMock = jest.fn()
jest.mock('@/lib/auth', () => ({ getUserId: () => getUserIdMock() }))

let fetchMock: jest.Mock
const PAYLOAD = { ok: true, rows: [1, 2, 3] }

async function call(path: string): Promise<{ res: Response; fetchMock: jest.Mock }> {
  jest.resetModules()
  process.env.BETTING_API_URL = 'https://api.example.test'
  process.env.BETTING_API_KEY = 'server-side-key'
  fetchMock = jest.fn(async () => ({ ok: true, json: async () => PAYLOAD }))
  global.fetch = fetchMock as unknown as typeof fetch
  const route = path.includes('slate')
    ? await import('@/app/api/slate/today/route')
    : await import('@/app/api/stats/clv/route')
  const res = await route.GET(new NextRequest(`http://localhost${path}`))
  return { res, fetchMock }
}

describe('slate + clv route auth', () => {
  it.each(['/api/slate/today', '/api/stats/clv?days=30'])('returns 401 for anonymous callers: %s', async (path) => {
    getUserIdMock.mockResolvedValue(null)
    const { res, fetchMock: fm } = await call(path)
    expect(res.status).toBe(401)
    expect(await res.json()).toEqual({ error: 'Unauthorized' })
    expect(fm).not.toHaveBeenCalled()
  })

  it.each(['/api/slate/today', '/api/stats/clv?days=30'])('passes signed-in callers through: %s', async (path) => {
    getUserIdMock.mockResolvedValue('user_1')
    const { res, fetchMock: fm } = await call(path)
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual(PAYLOAD)
    expect(fm).toHaveBeenCalledTimes(1)
  })

  it('forwards the query string upstream for the signed-in CLV caller', async () => {
    getUserIdMock.mockResolvedValue('user_1')
    const { fetchMock: fm } = await call('/api/stats/clv?days=30')
    expect(fm.mock.calls[0][0]).toBe('https://api.example.test/api/public/stats/clv?days=30')
  })
})
