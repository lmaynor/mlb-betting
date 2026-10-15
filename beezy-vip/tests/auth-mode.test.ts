/** AUTH_DISABLED is a local-review switch: it must never open the member areas on a Vercel production deployment. */
function load(env: Record<string, string | undefined>): boolean {
  const saved = { ...process.env }
  for (const k of ['AUTH_DISABLED', 'VERCEL_ENV', 'NEXT_PUBLIC_VERCEL_ENV']) delete process.env[k]
  Object.assign(process.env, env)
  let v = false
  jest.isolateModules(() => { v = require('../lib/auth-mode').AUTH_DISABLED })
  process.env = saved
  return v
}

describe('AUTH_DISABLED', () => {
  it('is off unless explicitly set to 1', () => {
    expect(load({})).toBe(false)
    expect(load({ AUTH_DISABLED: '0' })).toBe(false)
    expect(load({ AUTH_DISABLED: 'true' })).toBe(false)
  })
  it('is on for local dev and non-production Vercel deployments', () => {
    expect(load({ AUTH_DISABLED: '1' })).toBe(true)
    expect(load({ AUTH_DISABLED: '1', VERCEL_ENV: 'preview' })).toBe(true)
    expect(load({ AUTH_DISABLED: '1', VERCEL_ENV: 'development' })).toBe(true)
  })
  it('is IGNORED on a Vercel production deployment (fail-closed)', () => {
    expect(load({ AUTH_DISABLED: '1', VERCEL_ENV: 'production' })).toBe(false)
    expect(load({ AUTH_DISABLED: '1', NEXT_PUBLIC_VERCEL_ENV: 'production' })).toBe(false)
  })
})
