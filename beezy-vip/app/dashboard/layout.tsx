import Link           from 'next/link'
import { getUserTier } from '@/lib/auth'
import { redirect }    from 'next/navigation'
import { headers }     from 'next/headers'

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const tier = await getUserTier()
  if (tier === 'public' || tier === 'starter') {
    redirect('/signup')
  }

  // Determine active tab from the request URL via Next.js headers
  const headersList = await headers()
  const url         = headersList.get('x-invoke-path') ?? headersList.get('x-pathname') ?? ''

  const tabs = [
    { label: "Today's Picks", href: '/dashboard/picks'   },
    { label: 'History',       href: '/dashboard/history' },
    { label: 'Settings',      href: '/dashboard/settings'},
  ]

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      {/* Dashboard header */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-1">
          <span className="mono text-xs text-accent uppercase tracking-widest">Pro</span>
          <span className="mono text-xs text-muted">·</span>
          <span className="mono text-xs text-muted uppercase tracking-widest">Dashboard</span>
        </div>
        <h1 className="text-xl font-extrabold uppercase tracking-tight">Beezy.FYI</h1>
      </div>

      {/* Tab bar */}
      <div className="flex gap-0 border-b border-[var(--border)] mb-8">
        {tabs.map(tab => {
          const isActive = url.includes(tab.href)
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={`mono text-xs uppercase tracking-widest px-5 py-3 border-b-2 transition-colors ${
                isActive
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:border-accent hover:text-accent'
              }`}
            >
              {tab.label}
            </Link>
          )
        })}
      </div>

      {children}
    </div>
  )
}
