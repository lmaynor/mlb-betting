export const dynamic = 'force-dynamic'

import { NextRequest, NextResponse } from 'next/server'
import Stripe                        from 'stripe'
import { auth, currentUser }         from '@clerk/nextjs/server'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!)
}

const PRICE_MAP: Record<string, string | undefined> = {
  starter: process.env.STRIPE_PRICE_STARTER,
  pro:     process.env.STRIPE_PRICE_PRO,
  season:  process.env.STRIPE_PRICE_SEASON,
}

export async function POST(req: NextRequest) {
  const { userId } = await auth()
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { tier } = await req.json().catch(() => ({})) as { tier?: string }
  const priceId  = tier ? PRICE_MAP[tier] : undefined

  if (!priceId) {
    return NextResponse.json(
      { error: 'Invalid tier. Must be starter | pro | season' },
      { status: 400 }
    )
  }

  const user     = await currentUser()
  const email    = user?.emailAddresses[0]?.emailAddress
  const base     = process.env.NEXT_PUBLIC_BASE_URL ?? 'https://beezy.vip'

  // Reuse existing Stripe customer if they have one
  const existingCustomerId = user?.privateMetadata?.stripeCustomerId as string | undefined

  const stripe = getStripe()
  const session = await stripe.checkout.sessions.create({
    mode:               'subscription',
    line_items:         [{ price: priceId, quantity: 1 }],
    customer:           existingCustomerId,
    customer_email:     existingCustomerId ? undefined : email,
    success_url:        `${base}/dashboard/picks?checkout=success`,
    cancel_url:         `${base}/signup`,
    metadata:           { clerkUserId: userId, priceId },
    subscription_data:  { metadata: { clerkUserId: userId } },
    allow_promotion_codes: true,
  })

  return NextResponse.json({ url: session.url })
}
