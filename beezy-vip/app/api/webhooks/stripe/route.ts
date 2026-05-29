export const dynamic = 'force-dynamic'

import { NextRequest, NextResponse } from 'next/server'
import Stripe from 'stripe'
import { clerkClient } from '@clerk/nextjs/server'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!, {
    apiVersion: '2026-05-27.dahlia',
  })
}

const PRICE_TO_TIER: Record<string, string> = {
  // Set these to your actual Stripe price IDs
  [process.env.STRIPE_PRICE_STARTER  ?? 'price_starter']:  'starter',
  [process.env.STRIPE_PRICE_PRO      ?? 'price_pro']:      'pro',
  [process.env.STRIPE_PRICE_SEASON   ?? 'price_season']:   'season',
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

  switch (event.type) {
    case 'checkout.session.completed': {
      const session    = event.data.object as Stripe.Checkout.Session
      const clerkId    = session.metadata?.clerkUserId
      const priceId    = session.metadata?.priceId
      const tier       = PRICE_TO_TIER[priceId ?? '']

      if (clerkId && tier) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier, stripeCustomerId: session.customer },
        })
      }
      break
    }

    case 'customer.subscription.deleted': {
      const sub      = event.data.object as Stripe.Subscription
      const clerkId  = sub.metadata?.clerkUserId

      if (clerkId) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier: 'public' },
        })
      }
      break
    }

    case 'customer.subscription.updated': {
      const sub      = event.data.object as Stripe.Subscription
      const clerkId  = sub.metadata?.clerkUserId
      const priceId  = sub.items.data[0]?.price.id
      const tier     = PRICE_TO_TIER[priceId ?? '']

      if (clerkId && tier) {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier },
        })
      }
      break
    }
  }

  return NextResponse.json({ received: true })
}
