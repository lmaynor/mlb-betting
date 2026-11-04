'use client'

import Link from 'next/link'
import { useEffect, useRef } from 'react'
import { usePathname, useSearchParams } from 'next/navigation'
import { sectionFor, tabActive } from '@/lib/ia'
import { cx } from '@/utils/cx'

/** One tab strip under the nav on every page that belongs to a multi-page section (see lib/ia.ts). Renders nothing elsewhere. */
export function SectionTabs() {
  const pathname = usePathname()
  const sp = useSearchParams()
  const section = sectionFor(pathname)
  const row = useRef<HTMLDivElement>(null)
  // A scrolling row opens with the current tab centred, so it is never off-screen to the right (Results has six tabs).
  useEffect(() => {
    const el = row.current
    const cur = el?.querySelector<HTMLElement>('[aria-current="page"]')
    if (el && cur && el.scrollWidth > el.clientWidth) el.scrollLeft = cur.offsetLeft - (el.clientWidth - cur.offsetWidth) / 2
  }, [pathname, sp])
  if (!section) return null
  const fit = section.tabs.length <= 4

  return (
    <nav aria-label={`${section.id} views`} className="border-b border-separator-border bg-background-secondary-default">
      {/* Up to four tabs share a phone row evenly, with short labels, so nothing scrolls. More than that scrolls, with a right-edge fade as the cue. */}
      <div ref={row} className={cx('relative mx-auto max-w-[1280px] md:px-6 md:flex md:gap-0.5 md:overflow-x-auto md:whitespace-nowrap md:[mask-image:none]',
        fit ? 'grid grid-flow-col auto-cols-fr px-2' : 'flex gap-0.5 overflow-x-auto whitespace-nowrap pl-6 pr-10 [mask-image:linear-gradient(to_right,#000_calc(100%-32px),transparent)]')}>
        {section.tabs.map(t => {
          const active = tabActive(pathname, sp.get('tab'), t)
          return (
            <Link key={t.href} href={t.href} aria-current={active ? 'page' : undefined} aria-label={t.short ? t.label : undefined}
              className={cx('-mb-px border-b-2 py-2.5 text-body-2-semibold no-underline transition-colors', fit ? 'px-1 text-center md:px-3' : 'px-3',
                active ? 'border-b-[var(--signal)] text-text-primary' : 'border-b-transparent text-text-secondary hover:text-text-primary')}>
              {t.short ? <><span className="md:hidden">{t.short}</span><span className="hidden md:inline">{t.label}</span></> : t.label}
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
