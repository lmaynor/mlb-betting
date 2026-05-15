// Daily Stripe <-> Clerk reconciliation.
// Catches cases where a webhook fired but Clerk wasn't updated (network blip, cold start, etc).
// Runs at 03:00 UTC daily (see vercel.json).
// Stripe is source of truth for billing status.

import { NextResponse }  from 'next/server'
import Stripe            from 'stripe'
import { clerkClient }   from '@clerk/nextjs/server'

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  apiVersion: '2026-04-22.dahlia',
})

const PRICE_TO_TIER: Record<string, string> = {
  [process.env.STRIPE_PRICE_STARTER ?? 'price_starter']: 'starter',
  [process.env.STRIPE_PRICE_PRO     ?? 'price_pro']:     'pro',
  [process.env.STRIPE_PRICE_SEASON  ?? 'price_season']:  'season',
}

function tierForSubscription(sub: Stripe.Subscription): string {
  if (sub.status !== 'active' && sub.status !== 'trialing') return 'public'
  const priceId = sub.items.data[0]?.price.id ?? ''
  return PRICE_TO_TIER[priceId] ?? 'public'
}

export async function GET(req: Request) {
  // Vercel cron auth
  const authHeader = req.headers.get('authorization')
  if (authHeader !== `Bearer ${process.env.CRON_SECRET}`) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }

  if (!process.env.STRIPE_SECRET_KEY) {
    return NextResponse.json({ skipped: 'STRIPE_SECRET_KEY not set' })
  }

  const clerk     = await clerkClient()
  const fixed:    string[] = []
  const errors:   string[] = []
  let   checked = 0

  try {
    // Fetch all active Stripe subscriptions
    for await (const sub of stripe.subscriptions.list({ status: 'all', limit: 100, expand: ['data.customer'] })) {
      checked++
      const customer   = sub.customer as Stripe.Customer
      const clerkId    = (sub.metadata?.clerkUserId ?? customer.metadata?.clerkUserId) as string | undefined
      if (!clerkId) continue

      const stripeTier = tierForSubscription(sub)

      try {
        const user      = await clerk.users.getUser(clerkId)
        const clerkTier = (user.privateMetadata?.tier as string | undefined) ?? 'public'

        if (clerkTier !== stripeTier) {
          await clerk.users.updateUserMetadata(clerkId, {
            privateMetadata: { tier: stripeTier },
          })
          fixed.push(`${clerkId}: ${clerkTier} -> ${stripeTier}`)
          console.log(`[stripe-reconcile] fixed ${clerkId}: ${clerkTier} -> ${stripeTier}`)
        }
      } catch (err) {
        errors.push(`${clerkId}: ${String(err)}`)
      }
    }
  } catch (err) {
    console.error('[stripe-reconcile] fatal:', err)
    return NextResponse.json({ error: String(err) }, { status: 500 })
  }

  console.log(`[stripe-reconcile] checked=${checked} fixed=${fixed.length} errors=${errors.length}`)

  // Post to Discord if anything was out of sync
  if ((fixed.length > 0 || errors.length > 0) && process.env.DISCORD_WEBHOOK_URL) {
    const msg = [
      `**Stripe reconcile** | checked ${checked} subs`,
      fixed.length  > 0 ? `Fixed (${fixed.length}): ${fixed.join(', ')}` : null,
      errors.length > 0 ? `Errors (${errors.length}): ${errors.join(', ')}` : null,
    ].filter(Boolean).join('\n')

    await fetch(process.env.DISCORD_WEBHOOK_URL, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ content: msg }),
    }).catch(() => {})
  }

  return NextResponse.json({ checked, fixed, errors })
}
