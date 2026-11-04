/**
 * @jest-environment node
 *
 * Finding c988e847 regression: customer.subscription.updated must follow live billing state (the rule both
 * reconcilers implement) - a subscription that is not active or trialing writes tier 'public' and clears
 * sportChoice, whatever price its items still carry. Also covers the subscription.deleted guard: a remaining
 * active/trialing subscription keeps its tier instead of being downgraded by the other subscription's deletion.
 */
import { NextRequest } from 'next/server'

const constructEventMock = jest.fn()
const subsListMock       = jest.fn()
const updateMetaMock     = jest.fn(async () => ({}))
const getUserMock        = jest.fn()

jest.mock('stripe', () => ({
  __esModule: true,
  default: class {
    webhooks = { constructEvent: (...args: unknown[]) => constructEventMock(...(args as [])) }
    subscriptions = { list: (...args: unknown[]) => subsListMock(...(args as [])) }
  },
}))
jest.mock('@clerk/nextjs/server', () => ({
  clerkClient: async () => ({ users: { updateUserMetadata: updateMetaMock, getUser: getUserMock } }),
}))
jest.mock('@/lib/discord-sync', () => ({
  syncUserRoles: jest.fn(async () => ({ ok: true, skipped: false })),
}))

type SubOverrides = Record<string, unknown>
const sub = (o: SubOverrides = {}) => ({
  id: 'sub_1',
  customer: 'cus_1',
  status: 'active',
  metadata: { clerkUserId: 'user_1' },
  items: { data: [{ price: { id: 'price_pro' } }] },
  ...o,
})

async function call(event: unknown) {
  jest.resetModules()
  process.env.STRIPE_SECRET_KEY = 'sk_test_x'
  process.env.STRIPE_WEBHOOK_SECRET = 'whsec_test_x'
  process.env.STRIPE_PRICE_PRO_MONTHLY = 'price_pro'
  constructEventMock.mockReturnValue(event)
  const { POST } = await import('@/app/api/webhooks/stripe/route')
  const req = new NextRequest('http://localhost/api/webhooks/stripe', {
    method: 'POST', body: 'raw', headers: { 'stripe-signature': 'sig' },
  })
  return POST(req)
}

beforeEach(() => {
  jest.clearAllMocks()
  getUserMock.mockResolvedValue({ id: 'user_1', privateMetadata: { stripeCustomerId: 'cus_1' } })
  subsListMock.mockResolvedValue({ data: [] })
})

describe('customer.subscription.updated', () => {
  it('revokes the tier for a past_due subscription still carrying a paid price', async () => {
    const res = await call({ type: 'customer.subscription.updated', data: { object: sub({ status: 'past_due' }) } })
    expect(res.status).toBe(200)
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'public', sportChoice: null } })
    expect(updateMetaMock).not.toHaveBeenCalledWith('user_1', expect.objectContaining({
      privateMetadata: expect.objectContaining({ tier: 'pro_monthly' }),
    }))
  })

  it('revokes the tier for a canceled subscription', async () => {
    await call({ type: 'customer.subscription.updated', data: { object: sub({ status: 'canceled' }) } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'public', sportChoice: null } })
  })

  it('grants the price tier for an active subscription', async () => {
    await call({ type: 'customer.subscription.updated', data: { object: sub({ status: 'active' }) } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'pro_monthly' } })
  })

  it('grants the price tier for a trialing subscription', async () => {
    await call({ type: 'customer.subscription.updated', data: { object: sub({ status: 'trialing' }) } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'pro_monthly' } })
  })
})

describe('customer.subscription.deleted', () => {
  it('downgrades to public when no other subscription remains', async () => {
    subsListMock.mockResolvedValue({ data: [sub()] })
    await call({ type: 'customer.subscription.deleted', data: { object: sub() } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'public', sportChoice: null } })
  })

  it('keeps the tier of a remaining active subscription on the same customer', async () => {
    subsListMock.mockResolvedValue({ data: [sub(), sub({ id: 'sub_2' })] })
    await call({ type: 'customer.subscription.deleted', data: { object: sub() } })
    expect(updateMetaMock).toHaveBeenCalledWith('user_1', { privateMetadata: { tier: 'pro_monthly' } })
    expect(updateMetaMock).not.toHaveBeenCalledWith('user_1', expect.objectContaining({
      privateMetadata: expect.objectContaining({ tier: 'public' }),
    }))
  })
})
