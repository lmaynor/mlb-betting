'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { sectionFor, tabActive } from '@/lib/ia'
import { cx } from '@/utils/cx'

/** One tab strip under the nav on every page that belongs to a multi-page section (see lib/ia.ts). Renders nothing elsewhere. */
export function SectionTabs() {
  const pathname = usePathname()
  const sp = useSearchParams()
  const section = sectionFor(pathname)
  if (!section) return null

  return (
    <nav aria-label={`${section.id} views`} className="border-b border-separator-border bg-background-secondary-default">
      {/* A right-edge fade tells a phone the row scrolls; it is a mask on the scroller, so nothing sits on top of the last tab. */}
      <div className="mx-auto flex max-w-[1280px] gap-0.5 overflow-x-auto whitespace-nowrap px-6 [mask-image:linear-gradient(to_right,#000_calc(100%-32px),transparent)] md:[mask-image:none]">
        {section.tabs.map(t => {
          const active = tabActive(pathname, sp.get('tab'), t)
          return (
            <Link key={t.href} href={t.href} aria-current={active ? 'page' : undefined}
              className={cx('-mb-px border-b-2 px-3 py-2.5 text-body-2-semibold no-underline transition-colors',
                active ? 'border-b-[var(--signal)] text-text-primary' : 'border-b-transparent text-text-secondary hover:text-text-primary')}>
              {t.label}
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
