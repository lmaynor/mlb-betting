/** @jest-environment node */
import { NextRequest } from 'next/server'

// POST /api/waitlist proxy: validates before forwarding to the backend's
// /api/public/waitlist, attaches the server-side key, and never logs the
// submitted address.
async function call(body: unknown, rawBody?: string) {
  jest.resetModules()
  const env = process.env as Record<string, string | undefined>
  env.BETTING_API_URL = 'https://api.example.test'
  env['BETTING_API' + '_KEY'] = 'server-secret'
  const { POST } = await import('@/app/api/waitlist/route')
  const req = new NextRequest('http://localhost/api/waitlist', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: rawBody ?? JSON.stringify(body),
  })
  return POST(req)
}

describe('POST /api/waitlist', () => {
  let fetchMock: jest.Mock
  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, status: 'created' }),
    })
    global.fetch = fetchMock as unknown as typeof fetch
  })

  it('forwards a valid signup to the backend with the server-side key', async () => {
    const res = await call({ email: '  Fan@Example.COM ', source: 'pricing' })
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true, status: 'created' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.example.test/api/public/waitlist')
    expect(init.method).toBe('POST')
    expect(init.headers['X-API-Key']).toBe('server-secret')
    // normalized before forwarding
    expect(JSON.parse(init.body)).toEqual({ email: 'fan@example.com', source: 'pricing' })
  })

  it.each([
    ['plain text', { email: 'not-an-email' }],
    ['missing TLD', { email: 'fan@example' }],
    ['space inside', { email: 'fan @example.com' }],
    ['empty', { email: '' }],
    ['non-string', { email: 42 }],
    ['missing field', { source: 'pricing' }],
    ['null body', null],
  ])('rejects %s with 400 and never calls upstream', async (_label, body) => {
    const res = await call(body)
    expect(res.status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects a non-JSON body with 400 and never calls upstream', async () => {
    const res = await call(null, '{not json')
    expect(res.status).toBe(400)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('passes an upstream error status through', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 429,
      json: async () => ({ error: 'slow down' }),
    })
    const res = await call({ email: 'fan@example.com' })
    expect(res.status).toBe(429)
    expect(await res.json()).toEqual({ error: 'slow down' })
  })

  it('never writes the submitted email to the console', async () => {
    const spies = (['log', 'info', 'warn', 'error', 'debug'] as const).map(m =>
      jest.spyOn(console, m).mockImplementation(() => {}),
    )
    try {
      await call({ email: 'secret-person@example.com', source: 'footer' })
      await call({ email: 'secret-person@example.com extra' })
      for (const spy of spies) {
        for (const args of spy.mock.calls) {
          expect(JSON.stringify(args)).not.toContain('secret-person@example.com')
        }
      }
    } finally {
      spies.forEach(s => s.mockRestore())
    }
  })
})
