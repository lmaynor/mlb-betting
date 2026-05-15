import Link from 'next/link'

export default function NotFound() {
  return (
    <div className="min-h-[70vh] flex items-center justify-center px-4">
      <div className="text-center">
        <p className="mono text-xs text-accent uppercase tracking-widest mb-4">404</p>
        <h1 className="text-4xl font-extrabold uppercase tracking-tight mb-4">
          Page Not Found
        </h1>
        <p className="mono text-sm text-muted mb-8 max-w-sm mx-auto">
          This page doesn&apos;t exist or has moved. Use the links below.
        </p>
        <div className="flex flex-wrap justify-center gap-3">
          {[
            { label: 'Home',    href: '/'        },
            { label: 'Picks',   href: '/picks'   },
            { label: 'Tools',   href: '/tools'   },
            { label: 'Results', href: '/results' },
          ].map(l => (
            <Link
              key={l.href}
              href={l.href}
              className="mono text-xs uppercase tracking-widest px-4 py-2.5 border border-[var(--border)] text-muted hover:border-accent hover:text-accent transition-colors"
            >
              {l.label}
            </Link>
          ))}
        </div>
      </div>
    </div>
  )
}
