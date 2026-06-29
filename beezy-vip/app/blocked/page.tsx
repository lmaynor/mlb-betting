import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Service Not Available in Your Region',
  robots: { index: false, follow: false },
}

const B = '1px solid var(--basalt)'

export default function BlockedPage({
  searchParams,
}: {
  searchParams: { state?: string; country?: string }
}) {
  const region = searchParams.state ?? searchParams.country ?? 'your region'

  return (
    <div style={{ minHeight: '70vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '64px 16px' }}>
      <div style={{ width: '100%', maxWidth: '440px', textAlign: 'center' }}>
        <p className="mono" style={{ fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--fog)', marginBottom: '16px' }}>
          Unavailable
        </p>
        <h1 style={{ fontSize: '22px', fontWeight: 600, color: 'var(--ash)', letterSpacing: '-0.01em', marginBottom: '16px' }}>
          Not Available Here
        </h1>
        <p style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.65, marginBottom: '24px' }}>
          Beezy.FYI is not currently available in <strong style={{ color: 'var(--ash)' }}>{region}</strong>{' '}
          due to local regulations. We&apos;re working to expand availability.
        </p>
        <p className="times" style={{ fontSize: '11px', color: 'var(--fog)' }}>
          If you believe this is an error or are accessing via VPN, contact us to resolve it.
        </p>
      </div>
    </div>
  )
}
