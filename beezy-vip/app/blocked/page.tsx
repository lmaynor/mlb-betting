import type { Metadata } from 'next'
import Link from 'next/link'

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
    <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '48px 16px' }}>
      <div style={{ width: '100%', maxWidth: '460px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--chalk)', margin: '0 0 12px' }}>Not available{region ? ` in ${region}` : ' in your region'}</h1>
        <p style={{ fontSize: '14px', color: 'var(--silver)', lineHeight: 1.6, margin: '0 0 12px' }}>
          Picks and +EV alerts are not offered where local rules restrict them. The free calculators, guides and the public results record remain available.
        </p>
        <p style={{ fontSize: '13px', color: 'var(--fog)', lineHeight: 1.6, margin: '0 0 16px' }}>
          Think this is a mistake, or on a VPN? Turn it off and reload, or ask in the Discord.
        </p>
        <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', fontSize: '13px' }}>
          <Link href="/tools" style={{ color: 'var(--link)' }}>Calculators</Link>
          <Link href="/learn" style={{ color: 'var(--link)' }}>Guides</Link>
          <Link href="/results" style={{ color: 'var(--link)' }}>Results</Link>
          <Link href="/legal/responsible-gambling" style={{ color: 'var(--link)' }}>Responsible gambling</Link>
        </div>
      </div>
    </div>
  )
}
