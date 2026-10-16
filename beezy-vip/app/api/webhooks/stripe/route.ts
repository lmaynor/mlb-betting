export const dynamic = 'force-dynamic'

import { NextRequest, NextResponse } from 'next/server'
import Stripe from 'stripe'
import { clerkClient } from '@clerk/nextjs/server'
import { isLeague, priceToTierMap } from '@/lib/tiers'
import { syncUserRoles } from '@/lib/discord-sync'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!)
}

export async function POST(req: NextRequest) {
  const body      = await req.text()
  const signature = req.headers.get('stripe-signature')!

  const stripe = getStripe()
  let event: Stripe.Event
  try {
    event = stripe.webhooks.constructEvent(
      body,
      signature,
      process.env.STRIPE_WEBHOOK_SECRET!
    )
  } catch (err) {
    return NextResponse.json({ error: `Webhook error: ${err}` }, { status: 400 })
  }

  const clerk = await clerkClient()
  const PRICE_TO_TIER = priceToTierMap()

  // Best-effort Discord role push: a Discord outage must never fail (and so retry-storm) a Stripe webhook.
  const pushRoles = async (clerkId: string, tier: string, sport?: unknown) => {
    try {
      const user = await clerk.users.getUser(clerkId)
      const r = await syncUserRoles(user, clerkId, tier, sport)
      if (!r.ok && !r.skipped) console.warn('[stripe-webhook] discord role sync failed:', r.reason)
    } catch (err) {
      console.warn('[stripe-webhook] discord role sync error:', String(err))
    }
  }

  switch (event.type) {
    case 'checkout.session.completed': {
      const session    = event.data.object as Stripe.Checkout.Session
      const clerkId    = session.metadata?.clerkUserId
      const priceId    = session.metadata?.priceId
      const tier       = PRICE_TO_TIER[priceId ?? '']

      const sport      = isLeague(session.metadata?.sport) ? session.metadata?.sport : null

      if (clerkId && tier) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier, stripeCustomerId: session.customer, sportChoice: sport },
        })
        await pushRoles(clerkId, tier, sport)
      }
      break
    }

    case 'customer.subscription.deleted': {
      const sub      = event.data.object as Stripe.Subscription
      const clerkId  = sub.metadata?.clerkUserId

      if (clerkId) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier: 'public', sportChoice: null },
        })
        await pushRoles(clerkId, 'public', null)
      }
      break
    }

    case 'customer.subscription.updated': {
      const sub      = event.data.object as Stripe.Subscription
      const clerkId  = sub.metadata?.clerkUserId
      const priceId  = sub.items.data[0]?.price.id
      const tier     = PRICE_TO_TIER[priceId ?? '']

      const sport    = isLeague(sub.metadata?.sport) ? sub.metadata?.sport : null

      if (clerkId && tier) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier, ...(sport ? { sportChoice: sport } : {}) },
        })
        await pushRoles(clerkId, tier, sport)
      }
      break
    }
  }

  return NextResponse.json({ received: true })
}
