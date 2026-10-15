'use client'

import Link from 'next/link'
import { usePathname, useSearchParams } from 'next/navigation'
import { sectionFor, tabActive } from '@/lib/ia'

/** One tab strip under the nav on every page that belongs to a multi-page section (see lib/ia.ts). Renders nothing elsewhere. */
export function SectionTabs() {
  const pathname = usePathname()
  const sp = useSearchParams()
  const section = sectionFor(pathname)
  if (!section) return null

  return (
    <nav aria-label={`${section.id} views`} style={{ borderBottom: '1px solid #1e2230', background: '#090a0f' }}>
      <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '0 24px', display: 'flex', gap: '2px', overflowX: 'auto', whiteSpace: 'nowrap' }}>
        {section.tabs.map(t => {
          const active = tabActive(pathname, sp.get('tab'), t)
          return (
            <Link key={t.href} href={t.href} aria-current={active ? 'page' : undefined}
              style={{ fontSize: '12.5px', fontWeight: 600, padding: '10px 12px', textDecoration: 'none', color: active ? 'var(--chalk)' : 'var(--fog)',
                borderBottom: `2px solid ${active ? 'var(--signal)' : 'transparent'}`, marginBottom: '-1px' }}>
              {t.label}
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
