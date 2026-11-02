import Link from 'next/link'

export type ResearchView = 'cockpit' | 'projections'

const TABS: { id: ResearchView; label: string; href: string; title: string }[] = [
  { id: 'cockpit', label: 'PROP COCKPIT', href: '/edge', title: 'Flagged lines with last-10 game logs and every book price' },
  { id: 'projections', label: 'ALL PROJECTIONS', href: '/edge/projections', title: 'The model number for every player prop on the slate, priced or not' },
]

/** The two halves of Prop Research: the flagged-line cockpit, and the model's projection for every player prop. */
export function ResearchTabs({ active }: { active: ResearchView }) {
  return (
    <nav aria-label="Prop research views" className="inline-flex gap-0.5 p-[3px] bg-background-secondary-default border border-separator-border rounded-control">
      {TABS.map(t => (
        <Link className="text-compact font-bold tracking-[0.05em] py-[7px] px-[13px] no-underline whitespace-nowrap" key={t.id} href={t.href} title={t.title} aria-current={active === t.id ? 'page' : undefined}
          style={{
            borderRadius: '6px',
            color: active === t.id ? '#ffffff' : 'var(--color-neutral-300)',
            background: active === t.id ? 'var(--color-background-tertiary-default)' : 'transparent',
            border: `1px solid ${active === t.id ? 'var(--color-border-button-hover)' : 'transparent'}`,
          }}>
          {t.label}
        </Link>
      ))}
    </nav>
  )
}
