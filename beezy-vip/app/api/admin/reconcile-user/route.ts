// Manual one-off reconciliation for a single user.
// Use when a user emails saying "I paid but I'm not Pro."
//
// Usage:
//   curl -X POST https://beezy.vip/api/admin/reconcile-user \
//     -H "x-admin-key: $ADMIN_SECRET_KEY" \
//     -H "Content-Type: application/json" \
//     -d '{"stripe_customer_id": "cus_..."}'

import { NextRequest, NextResponse } from 'next/server'
import Stripe                        from 'stripe'
import { clerkClient }               from '@clerk/nextjs/server'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!, {
    apiVersion: '2026-04-22.dahlia',
  })
}

const PRICE_TO_TIER: Record<string, string> = {
  [process.env.STRIPE_PRICE_STARTER ?? 'price_starter']: 'starter',
  [process.env.STRIPE_PRICE_PRO     ?? 'price_pro']:     'pro',
  [process.env.STRIPE_PRICE_SEASON  ?? 'price_season']:  'season',
}

function isAuthorized(req: NextRequest): boolean {
  const key = req.headers.get('x-admin-key') ?? req.nextUrl.searchParams.get('key')
  return key === process.env.ADMIN_SECRET_KEY
}

export async function POST(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const body = await req.json().catch(() => ({})) as { stripe_customer_id?: string; clerk_user_id?: string }
  const { stripe_customer_id, clerk_user_id } = body

  if (!stripe_customer_id && !clerk_user_id) {
    return NextResponse.json(
      { error: 'Provide stripe_customer_id or clerk_user_id' },
      { status: 400 }
    )
  }

  try {
    const clerk = await clerkClient()

    // Resolve Stripe customer ID if given clerk_user_id
    let customerId = stripe_customer_id
    if (!customerId && clerk_user_id) {
      const user = await clerk.users.getUser(clerk_user_id)
      customerId = user.privateMetadata?.stripeCustomerId as string | undefined
      if (!customerId) {
        return NextResponse.json({ error: 'No stripeCustomerId in Clerk metadata for this user' }, { status: 404 })
      }
    }

    // Get active subscriptions for this customer
    const stripe = getStripe()
    const subs = await stripe.subscriptions.list({
      customer: customerId,
      status:   'all',
      limit:    10,
    })

    // Find the most relevant sub (active > trialing > canceled)
    const activeSub = subs.data.find(s => s.status === 'active' || s.status === 'trialing')
    const anySub    = subs.data[0]
    const sub       = activeSub ?? anySub

    let correctTier = 'public'
    if (sub && (sub.status === 'active' || sub.status === 'trialing')) {
      const priceId = sub.items.data[0]?.price.id ?? ''
      correctTier   = PRICE_TO_TIER[priceId] ?? 'public'
    }

    // Find Clerk user ID
    const resolvedClerkId = clerk_user_id ??
      (sub?.metadata?.clerkUserId as string | undefined) ?? null

    if (!resolvedClerkId) {
      return NextResponse.json({
        stripe_customer_id: customerId,
        subscriptions:      subs.data.map(s => ({ id: s.id, status: s.status })),
        correct_tier:       correctTier,
        note:               'Could not resolve Clerk user ID -- set tier manually or provide clerk_user_id',
      })
    }

    const user        = await clerk.users.getUser(resolvedClerkId)
    const currentTier = (user.privateMetadata?.tier as string | undefined) ?? 'public'

    await clerk.users.updateUserMetadata(resolvedClerkId, {
      privateMetadata: { tier: correctTier },
    })

    return NextResponse.json({
      clerk_user_id:      resolvedClerkId,
      stripe_customer_id: customerId,
      was:                currentTier,
      now:                correctTier,
      subscription_status: sub?.status ?? 'none',
    })
  } catch (err) {
    console.error('[reconcile-user]', err)
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }
}
