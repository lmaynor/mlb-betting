/**
 * @jest-environment node
 *
 * Finding 4a33f607 regression: checkout must bind one Stripe customer to the Clerk user BEFORE the session is
 * created (so concurrent checkouts converge instead of splitting customers), must refuse a second subscription
 * while one is active/trialing, and must make retried session creation idempotent.
 */
import { NextRequest } from 'next/server'

const customersCreateMock = jest.fn()
const subsListMock        = jest.fn()
const sessionsCreateMock  = jest.fn()
const updateMetaMock      = jest.fn(async () => ({}))
const currentUserMock     = jest.fn()

jest.mock('stripe', () => ({
  __esModule: true,
  default: class {
    customers     = { create: (...args: unknown[]) => customersCreateMock(...(args as [])) }
    subscriptions = { list: (...args: unknown[]) => subsListMock(...(args as [])) }
    checkout      = { sessions: { create: (...args: unknown[]) => sessionsCreateMock(...(args as [])) } }
  },
}))
jest.mock('@clerk/nextjs/server', () => ({
  currentUser: () => currentUserMock(),
  clerkClient: async () => ({ users: { updateUserMetadata: updateMetaMock } }),
}))
jest.mock('@/lib/auth', () => ({ getUserId: async () => 'user_1' }))

async function call(body: Record<string, unknown> = { tier: 'pro_monthly' }) {
  jest.resetModules()
  process.env.STRIPE_SECRET_KEY = 'sk_test_x'
  process.env.STRIPE_PRICE_PRO_MONTHLY = 'price_pro'
  const { POST } = await import('@/app/api/stripe/checkout/route')
  const req = new NextRequest('http://localhost/api/stripe/checkout', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  return POST(req)
}

const clerkUser = (privateMetadata: Record<string, unknown>) => ({
  emailAddresses: [{ emailAddress: 'luc@example.com' }],
  privateMetadata,
})

beforeEach(() => {
  jest.clearAllMocks()
  customersCreateMock.mockResolvedValue({ id: 'cus_new' })
  subsListMock.mockResolvedValue({ data: [] })
  sessionsCreateMock.mockResolvedValue({ url: 'https://checkout.example/session' })
})

describe('POST /api/stripe/checkout', () => {
  it('creates and persists the Stripe customer before the session when none is bound', async () => {
    currentUserMock.mockResolvedValue(clerkUser({}))
    const res = await call()
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ url: 'https://checkout.example/session' })

    expect(customersCreateMock).toHaveBeenCalledWith({ email: 'luc@example.com', metadata: { clerkUserId: 'user_1' } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { stripeCustomerId: 'cus_new' } })
    // Binding is durable before any session exists: a retry after this point reuses cus_new.
    expect(updateMetaMock.mock.invocationCallOrder[0]).toBeLessThan(sessionsCreateMock.mock.invocationCallOrder[0])

    const [params, options] = sessionsCreateMock.mock.calls[0]
    expect(params.customer).toBe('cus_new')
    expect('customer_email' in params).toBe(false)
    // Key format: checkout:<user>:<price>:<sport|'all'>:<5-minute bucket>. No sport on a pro tier -> 'all'.
    expect(options.idempotencyKey).toMatch(/^checkout:user_1:price_pro:all:\d+$/)
    const bucket = Number(String(options.idempotencyKey).split(':').pop())
    expect(Math.abs(bucket - Math.floor(Date.now() / 300_000))).toBeLessThanOrEqual(1)
  })

  it('scopes the idempotency key by sport so a single_sport sport switch is not a key replay', async () => {
    process.env.STRIPE_PRICE_SINGLE_SPORT = 'price_single'
    currentUserMock.mockResolvedValue(clerkUser({ stripeCustomerId: 'cus_9' }))
    await call({ tier: 'single_sport', sport: 'nfl' })
    await call({ tier: 'single_sport', sport: 'nba' })
    const keys = sessionsCreateMock.mock.calls.map(c => (c[1] as { idempotencyKey: string }).idempotencyKey)
    expect(keys[0]).toMatch(/^checkout:user_1:price_single:nfl:\d+$/)
    expect(keys[1]).toMatch(/^checkout:user_1:price_single:nba:\d+$/)
    expect(keys[0]).not.toBe(keys[1])
  })

  it('returns 409 and creates no session when an active subscription already exists', async () => {
    currentUserMock.mockResolvedValue(clerkUser({ stripeCustomerId: 'cus_9' }))
    subsListMock.mockResolvedValue({ data: [{ status: 'active' }] })
    const res = await call()
    expect(res.status).toBe(409)
    expect(await res.json()).toEqual({ error: 'Already subscribed', portal: '/api/stripe/portal' })
    expect(customersCreateMock).not.toHaveBeenCalled()
    expect(sessionsCreateMock).not.toHaveBeenCalled()
  })

  it('reuses the bound customer when only a canceled subscription remains', async () => {
    currentUserMock.mockResolvedValue(clerkUser({ stripeCustomerId: 'cus_9' }))
    subsListMock.mockResolvedValue({ data: [{ status: 'canceled' }] })
    const res = await call()
    expect(res.status).toBe(200)
    expect(customersCreateMock).not.toHaveBeenCalled()
    expect(sessionsCreateMock.mock.calls[0][0].customer).toBe('cus_9')
  })
})
