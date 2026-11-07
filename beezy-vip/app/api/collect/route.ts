import { classifyAnonTraffic } from '@/lib/anon-traffic'

/**
 * POST /api/collect -- first-party beacon sink for header-level anonymous traffic identification.
 *
 * The beacon sends only { path, referrer }. Everything else in the event is classified server-side
 * from the request headers (see lib/anon-traffic.ts): no identity, no cookies, no IP storage, no
 * fingerprinting. Accepted events are emitted as ONE structured log line (event "anon_traffic");
 * durable storage is a follow-up that belongs to the Python backend, which owns DB writes.
 *
 * Opt-outs are honored before anything else: a Sec-GPC: 1 header drops the event silently.
 * This route is excluded from Clerk middleware (see middleware.ts matcher), like /api/analytics.
 */

const MAX_BODY_BYTES = 2048
const MAX_PATH_CHARS = 256
const MAX_REFERRER_CHARS = 2048

function noContent(): Response {
  return new Response(null, { status: 204, headers: { 'cache-control': 'no-store' } })
}

function reject(status: number): Response {
  return new Response(null, { status, headers: { 'cache-control': 'no-store' } })
}

function cleanPath(value: unknown): string | null {
  if (typeof value !== 'string') return null
  // Keep the pathname only: query strings and fragments can carry tokens and are never collected.
  const pathname = value.split(/[?#]/)[0]
  if (!pathname.startsWith('/')) return null
  if (/[\u0000-\u0020\u007f]/.test(pathname)) return null
  return pathname.slice(0, MAX_PATH_CHARS)
}

function referrerHost(value: unknown): string | null {
  if (value === undefined || value === null) return null
  if (typeof value !== 'string') return null
  const raw = value.trim().slice(0, MAX_REFERRER_CHARS)
  if (raw === '') return null
  try {
    const url = new URL(raw)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    return url.host.toLowerCase().slice(0, 253) || null
  } catch {
    return null
  }
}

export async function POST(req: Request): Promise<Response> {
  // Global Privacy Control: drop the event, before parsing or logging anything.
  if (req.headers.get('sec-gpc') === '1') return noContent()

  // Cheap shape guards first: declared size, then content type, then actual size.
  const declaredLength = Number(req.headers.get('content-length') ?? '')
  if (Number.isFinite(declaredLength) && declaredLength > MAX_BODY_BYTES) return reject(413)
  const contentType = req.headers.get('content-type') ?? ''
  if (!contentType.toLowerCase().includes('application/json')) return reject(415)

  let raw: string
  try {
    raw = await req.text()
  } catch {
    return reject(400)
  }
  if (raw.length > MAX_BODY_BYTES) return reject(413)

  let body: unknown
  try {
    body = JSON.parse(raw)
  } catch {
    return reject(400)
  }
  if (typeof body !== 'object' || body === null || Array.isArray(body)) return reject(400)

  // The beacon's whole vocabulary is { path, referrer }; anything else is not accepted.
  const fields = Object.keys(body)
  if (fields.some(field => field !== 'path' && field !== 'referrer')) return reject(400)

  const record = body as { path?: unknown; referrer?: unknown }
  const path = cleanPath(record.path)
  if (path === null) return reject(400)
  if (record.referrer !== undefined && record.referrer !== null && typeof record.referrer !== 'string') {
    return reject(400)
  }

  const classified = classifyAnonTraffic(req.headers)
  console.info(JSON.stringify({
    event: 'anon_traffic',
    ts: new Date().toISOString(),
    kind: classified.kind,
    bot: classified.bot,
    uaFamily: classified.uaFamily,
    deviceClass: classified.deviceClass,
    country: classified.country,
    region: classified.region,
    language: classified.language,
    path,
    referrerHost: referrerHost(record.referrer),
  }))
  return noContent()
}
