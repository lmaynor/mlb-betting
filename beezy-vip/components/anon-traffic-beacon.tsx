'use client'

import { useEffect, useRef } from 'react'
import { usePathname } from 'next/navigation'

/**
 * First-party page-view beacon for header-level anonymous traffic identification (see
 * docs/anon-traffic.md). Sends only { path, referrer } to /api/collect; the server classifies
 * the request from its headers. No cookies, no storage, no fingerprinting, no identity.
 *
 * Honors opt-outs before sending anything: Do Not Track and Global Privacy Control.
 * No-op in development unless NEXT_PUBLIC_ANON_TRAFFIC_ENABLED=1.
 */

type NavigatorWithGpc = Navigator & { globalPrivacyControl?: boolean }

function optedOut(): boolean {
  const nav = navigator as NavigatorWithGpc
  const dnt = nav.doNotTrack ?? (window as unknown as { doNotTrack?: string }).doNotTrack
  return dnt === '1' || dnt === 'yes' || nav.globalPrivacyControl === true
}

export function AnonTrafficBeacon() {
  const pathname = usePathname()
  const lastPath = useRef<string | null>(null)

  useEffect(() => {
    if (!pathname || lastPath.current === pathname) return
    lastPath.current = pathname
    if (pathname.startsWith('/api') || pathname.startsWith('/admin')) return
    if (process.env.NODE_ENV === 'development' && process.env.NEXT_PUBLIC_ANON_TRAFFIC_ENABLED !== '1') return
    if (optedOut()) return

    const payload = JSON.stringify({ path: pathname, referrer: document.referrer || null })
    try {
      const sent = navigator.sendBeacon?.('/api/collect', new Blob([payload], { type: 'application/json' }))
      if (sent) return
    } catch {
      // Fall through to the fetch beacon below.
    }
    void fetch('/api/collect', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: payload,
      keepalive: true,
    }).catch(() => {})
  }, [pathname])

  return null
}
