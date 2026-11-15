import { createHash } from 'node:crypto'
import { NextResponse, type NextRequest } from 'next/server'

/**
 * OPS-05 (Vercel Fast Origin Transfer): browser-revalidatable JSON for the live-board proxy routes.
 *
 * The board pages poll these routes on timers (cockpit every 60s, odds and game boards every 3 min),
 * and the payloads are large (the NFL odds board is ~900 KB raw). The responses are cut per viewer
 * (lib/access-feeds.ts), so they can never be shared at the CDN the way the anonymous routes are
 * (tests/api-proxy-caching.test.ts pins that split) — but the board snapshots themselves only change
 * when a new sweep lands, so most polls re-download byte-identical data. An ETag plus
 * `Cache-Control: private, no-cache` keeps the response in the viewer's own browser cache and makes
 * every poll a revalidation: unchanged body -> 304 with no payload, changed body -> the full 200.
 * `private` because the cut is per viewer; `no-cache` means "store, but revalidate before use", so
 * freshness is exactly what it was without the header — every poll still reaches the route and the
 * per-viewer cut still runs on every request.
 */
export function etagForBody(body: string): string {
  return `"${createHash('sha256').update(body).digest('hex').slice(0, 32)}"`
}

/** Serialize `data` once and answer 304 when the caller's If-None-Match already holds this body. */
export function jsonWithEtag(req: NextRequest, data: unknown): NextResponse {
  const body = JSON.stringify(data)
  const etag = etagForBody(body)
  const headers = { ETag: etag, 'Cache-Control': 'private, no-cache' }
  const inm = req.headers.get('if-none-match')
  if (inm && inm.split(',').map(s => s.trim()).includes(etag)) {
    return new NextResponse(null, { status: 304, headers })
  }
  return new NextResponse(body, { status: 200, headers: { ...headers, 'Content-Type': 'application/json' } })
}
