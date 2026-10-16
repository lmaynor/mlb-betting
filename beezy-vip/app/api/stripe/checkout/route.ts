export const dynamic = 'force-dynamic'

import { NextRequest, NextResponse } from 'next/server'
import Stripe                        from 'stripe'
import { currentUser }               from '@clerk/nextjs/server'
import { getUserId }                 from '@/lib/auth'
import { isLeague, priceIdFor }      from '@/lib/tiers'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!)
}

export async function POST(req: NextRequest) {
  const userId = await getUserId()
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  const { tier, sport } = await req.json().catch(() => ({})) as { tier?: string; sport?: string }
  const priceId  = tier ? priceIdFor(tier) : undefined

  if (!priceId) {
    return NextResponse.json(
      { error: 'Invalid tier. Must be single_sport | pro_monthly | pro_annual (legacy: starter | pro | season)' },
      { status: 400 }
    )
  }
  // A Single-Sport Pass is for ONE league; the choice rides on the subscription so the webhook can grant that league's role.
  if (tier === 'single_sport' && !isLeague(sport)) {
    return NextResponse.json({ error: 'single_sport needs sport = mlb | nfl | nba | nhl' }, { status: 400 })
  }
  const sportMeta: Record<string, string> = tier === 'single_sport' ? { sport: sport as string } : {}

  const user     = await currentUser()
  const email    = user?.emailAddresses[0]?.emailAddress
  const base     = process.env.NEXT_PUBLIC_BASE_URL ?? 'https://beezy.fyi'

  // Reuse existing Stripe customer if they have one
  const existingCustomerId = user?.privateMetadata?.stripeCustomerId as string | undefined

  const stripe = getStripe()
  const session = await stripe.checkout.sessions.create({
    mode:               'subscription',
    line_items:         [{ price: priceId, quantity: 1 }],
    customer:           existingCustomerId,
    customer_email:     existingCustomerId ? undefined : email,
    success_url:        `${base}/dashboard/picks?checkout=success`,
    cancel_url:         `${base}/pricing`,
    metadata:           { clerkUserId: userId, priceId, tier: tier as string, ...sportMeta },
    subscription_data:  { metadata: { clerkUserId: userId, tier: tier as string, ...sportMeta } },
    allow_promotion_codes: true,
  })

  return NextResponse.json({ url: session.url })
}
