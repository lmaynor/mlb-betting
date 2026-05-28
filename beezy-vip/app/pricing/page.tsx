import type { Metadata } from 'next'
import { PricingSection } from '@/components/landing/pricing'

export const metadata: Metadata = {
  title: 'Pricing - Beezy.VIP',
  description: 'Beezy.VIP pre-launch pricing and waitlist.',
}

export default function PricingPage() {
  return <PricingSection />
}
