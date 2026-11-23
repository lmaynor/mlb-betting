import type { Metadata } from 'next'
import { PricingSection } from '@/components/landing/pricing'
import { jsonLdString, pricingLd } from '@/lib/seo'

export const metadata: Metadata = {
  title: 'Pricing: $19/mo single-sport, $39/mo all-access',
  description:
    'beezy.fyi pricing: Single-Sport Pass $19/mo, All-Access Pro $39/mo, All-Access Pro Annual $299/yr.',
}

export default function PricingPage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLdString(pricingLd()) }} />
      <PricingSection />
    </>
  )
}
