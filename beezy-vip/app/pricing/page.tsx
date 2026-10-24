import type { Metadata } from 'next'
import { PricingSection } from '@/components/landing/pricing'
import { jsonLdString, pricingLd } from '@/lib/seo'

export const metadata: Metadata = {
  title: 'Pricing',
  description:
    'beezy.fyi pricing: Single-Sport Pass $19/mo, All-Access Pro $39/mo, All-Access Pro Annual $299/yr. Pre-launch: join the waitlist to lock these prices.',
}

export default function PricingPage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdString(pricingLd()) }} />
      <PricingSection />
    </>
  )
}
