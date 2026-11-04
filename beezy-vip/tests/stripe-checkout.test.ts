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

async function call() {
  jest.resetModules()
  process.env.STRIPE_SECRET_KEY = 'sk_test_x'
  process.env.STRIPE_PRICE_PRO_MONTHLY = 'price_pro'
  const { POST } = await import('@/app/api/stripe/checkout/route')
  const req = new NextRequest('http://localhost/api/stripe/checkout', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ tier: 'pro_monthly' }),
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
    expect(options).toEqual({ idempotencyKey: 'checkout:user_1:price_pro' })
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
