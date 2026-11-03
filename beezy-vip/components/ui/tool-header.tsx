import Link from 'next/link'

const CALCULATORS = [
  { href: '/tools/odds-calculator', label: 'Odds & Vig' },
  { href: '/tools/kelly-calculator', label: 'Kelly' },
  { href: '/tools/boost-ev', label: 'Boost EV' },
]

/**
 * One-row toolbar header shared by every /tools page: plain h1 on the left; on the right a hint, the three free
 * calculators as sibling tabs (so they read as one toolset) and the way back to the index.
 */
export function ToolHeader({ title, hint, current }: { title: string; hint?: string; current?: string }) {
  return (
    <div className="flex items-center justify-between gap-y-2 gap-x-4 flex-wrap mb-3.5">
      <h1 className="text-[20px] font-bold text-text-primary m-0">{title}</h1>
      <span className="flex items-center gap-2.5 flex-wrap">
        {hint && <span className="mono text-compact text-text-secondary">{hint}</span>}
        {current && (
          <nav aria-label="Calculators" className="inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control">
            {CALCULATORS.map(c => (
              <Link className="text-compact font-semibold py-[5px] px-[10px] rounded-[6px] no-underline whitespace-nowrap" key={c.href} href={c.href} aria-current={c.href === current ? 'page' : undefined}
                style={{
                  color: c.href === current ? 'var(--color-text-primary)' : 'var(--color-neutral-300)',
                  background: c.href === current ? 'var(--color-background-tertiary-default)' : 'transparent',
                }}>
                {c.label}
              </Link>
            ))}
          </nav>
        )}
        <Link className="text-[13px] text-link no-underline" href="/tools">All tools &rarr;</Link>
      </span>
    </div>
  )
}
