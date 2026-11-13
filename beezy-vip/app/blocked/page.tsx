import type { Metadata } from 'next'
import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { SITE_CONFIG } from '@/lib/site-config'

export const metadata: Metadata = {
  title:  'Not available in your region',
  robots: { index: false, follow: false },
}

// Only two-letter codes are echoed back: the query string is attacker-controlled.
const clean = (v: string | undefined) => (v && /^[A-Za-z]{2}$/.test(v) ? v.toUpperCase() : null)

export default async function BlockedPage({ searchParams }: { searchParams: Promise<{ state?: string; country?: string }> }) {
  const sp = await searchParams
  const region = clean(sp.state) ?? clean(sp.country)

  return (
    <div className="flex items-center justify-center py-12 px-4" style={{ minHeight: '60vh' }}>
      <div className="w-full max-w-[460px]">
        <div className="mb-5"><BrandWordmark size="md" /></div>
        <h1 className="text-[20px] font-bold text-text-primary" style={{ margin: '0 0 12px' }}>Not available{region ? ` in ${region}` : ' in your region'}</h1>
        <p className="text-[14px] text-neutral-300 leading-[1.6]" style={{ margin: '0 0 12px' }}>
          Picks and +EV alerts are not offered where local rules restrict them. The guides and the public results record remain available.
        </p>
        <p className="text-cozy text-text-secondary leading-[1.6]" style={{ margin: '0 0 16px' }}>
          Think this is a mistake, or on a VPN? Turn it off and reload, ask in the Discord, or email{' '}
          <a className="text-link" href={`mailto:${SITE_CONFIG.supportEmail}`}>{SITE_CONFIG.supportEmail}</a>.
        </p>
        <div className="flex gap-4 flex-wrap text-cozy">
          <Link className="text-link" href="/learn">Guides</Link>
          <Link className="text-link" href="/results">Results</Link>
          <Link className="text-link" href="/legal/responsible-gambling">Responsible gambling</Link>
        </div>
      </div>
    </div>
  )
}
