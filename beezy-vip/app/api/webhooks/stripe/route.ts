export const dynamic = 'force-dynamic'

import { NextRequest, NextResponse } from 'next/server'
import Stripe from 'stripe'
import { clerkClient } from '@clerk/nextjs/server'
import { isLeague, priceToTierMap, type Tier } from '@/lib/tiers'
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
        // Downgrade only when no other subscription still entitles this user: a remaining active/trialing
        // subscription (on this customer, or on the customer bound in Clerk metadata) keeps its tier.
        const customerIds = new Set<string>()
        if (typeof sub.customer === 'string') customerIds.add(sub.customer)
        const owner = await clerk.users.getUser(clerkId).catch(() => null)
        const bound = owner?.privateMetadata?.stripeCustomerId
        if (typeof bound === 'string') customerIds.add(bound)

        let remTier: Tier | undefined
        let remSport: string | null = null
        for (const customer of customerIds) {
          const subs = await stripe.subscriptions.list({ customer, status: 'all', limit: 10 }).catch(() => null)
          if (!subs) continue
          for (const s of subs.data) {
            const t = PRICE_TO_TIER[s.items.data[0]?.price.id ?? '']
            if (s.id !== sub.id && s.metadata?.clerkUserId === clerkId
              && (s.status === 'active' || s.status === 'trialing') && t) {
              remTier  = t
              remSport = isLeague(s.metadata?.sport) ? s.metadata?.sport : null
              break
            }
          }
          if (remTier) break
        }

        if (remTier) {
          await clerk.users.updateUserMetadata(clerkId, {
            privateMetadata: { tier: remTier, ...(remSport ? { sportChoice: remSport } : {}) },
          })
          await pushRoles(clerkId, remTier, remSport)
        } else {
          await clerk.users.updateUserMetadata(clerkId, {
            privateMetadata: { tier: 'public', sportChoice: null },
          })
          await pushRoles(clerkId, 'public', null)
        }
      }
      break
    }

    case 'customer.subscription.updated': {
      const sub      = event.data.object as Stripe.Subscription
      const clerkId  = sub.metadata?.clerkUserId
      if (!clerkId) break

      // Entitlement follows live billing state, the rule both reconcilers implement: a subscription that is not
      // active or trialing does not hold a paid tier, whatever price its items still carry.
      if (sub.status !== 'active' && sub.status !== 'trialing') {
        await clerk.users.updateUserMetadata(clerkId, {
          privateMetadata: { tier: 'public', sportChoice: null },
        })
        await pushRoles(clerkId, 'public', null)
        break
      }

      const priceId  = sub.items.data[0]?.price.id
      const tier     = PRICE_TO_TIER[priceId ?? '']

      const sport    = isLeague(sub.metadata?.sport) ? sub.metadata?.sport : null

      if (tier) {
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
