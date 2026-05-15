import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:  'Service Not Available in Your Region',
  robots: { index: false, follow: false },
}

export default function BlockedPage({
  searchParams,
}: {
  searchParams: { state?: string; country?: string }
}) {
  const region = searchParams.state ?? searchParams.country ?? 'your region'

  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md text-center">
        <p className="mono text-xs text-muted uppercase tracking-widest mb-4">Unavailable</p>
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-4">
          Not Available Here
        </h1>
        <p className="text-muted text-sm leading-relaxed mb-8">
          Beezy.VIP is not currently available in <strong className="text-text">{region}</strong>{' '}
          due to local regulations. We&apos;re working to expand availability.
        </p>
        <p className="mono text-xs text-muted">
          If you believe this is an error, or you&apos;re accessing via VPN,{' '}
          contact us to resolve it.
        </p>
      </div>
    </div>
  )
}
