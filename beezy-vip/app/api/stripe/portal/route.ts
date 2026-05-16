export const dynamic = 'force-dynamic'

import { NextResponse } from 'next/server'
import Stripe           from 'stripe'
import { currentUser }  from '@clerk/nextjs/server'
import { redirect }     from 'next/navigation'

function getStripe() {
  return new Stripe(process.env.STRIPE_SECRET_KEY!, {
    apiVersion: '2026-04-22.dahlia',
  })
}

export async function GET() {
  const user = await currentUser().catch(() => null)

  if (!user) {
    return NextResponse.redirect('/login')
  }

  const customerId = user.privateMetadata?.stripeCustomerId as string | undefined

  if (!customerId) {
    // No Stripe customer yet — send to pricing/signup
    return NextResponse.redirect('/signup')
  }

  try {
    const stripe = getStripe()
  const session = await stripe.billingPortal.sessions.create({
      customer:   customerId,
      return_url: `${process.env.NEXT_PUBLIC_BASE_URL ?? 'https://beezy.vip'}/dashboard/settings`,
    })
    return NextResponse.redirect(session.url)
  } catch (err) {
    console.error('Stripe portal error:', err)
    return NextResponse.redirect('/dashboard/settings')
  }
}
