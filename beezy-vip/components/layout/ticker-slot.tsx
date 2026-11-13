'use client'

import { usePathname } from 'next/navigation'
import { sectionFor } from '@/lib/ia'

/**
 * The LiveTicker's home after the chrome ruling (lib/ia.ts Q1(e)): it is NOT pinned chrome, so it leaves the sticky
 * stack and renders as a plain strip at the top of the body, only where the ruling puts it: Home and the Scanner
 * section. The ticker itself stays a server component and is passed in as children; this gate only decides
 * placement, so the strip scrolls away with the page like any other body content.
 */
export function TickerSlot({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const show = pathname === '/' || sectionFor(pathname)?.id === 'scanner'
  return show ? <>{children}</> : null
}
