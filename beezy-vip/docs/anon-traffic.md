# Anonymous traffic identification (header level)

Purpose: classify anonymous inbound traffic by what it declares in its request headers, so beezy
can tell human readers from declared crawlers and from unidentified automation. Implementation:
`lib/anon-traffic.ts` (classifier), `app/api/collect/route.ts` (beacon sink),
`components/anon-traffic-beacon.tsx` (first-party page-view beacon).

## The reciprocity rule

- "Don't scrape if you don't want to share." Agents that identify themselves in their headers get
  identified and treated accordingly. Agents that do not identify themselves are labeled
  `unknown_automation`, never unmasked.
- Identification is of traffic classes, never of people.

## What is collected (one structured log line per accepted page view)

- Event timestamp and the page path (pathname only; query strings and fragments are stripped).
- Classification: `kind` (`human` | `declared_bot` | `unknown_automation`), declared `bot` name
  when the user-agent names one, `uaFamily`, and `deviceClass` (mobile / desktop / tablet / bot /
  unknown), derived from the user-agent and `sec-ch-ua-mobile` when present.
- Coarse geo from Vercel's edge headers (`x-vercel-ip-country`, `x-vercel-ip-country-region`).
- The first `Accept-Language` tag only, and the referrer's host only (never the full referrer URL).

## What is never collected

- No email addresses, account data, or names; anonymous traffic has none and none is inferred.
- No raw IP addresses are stored or logged by this feature. (No unique-visitor key is computed;
  if one is ever needed it must be a daily-rotating salted hash of IP+UA, by design decision.)
- No cookies are set for this, no local storage, no canvas/font/device fingerprinting, and no
  cross-visit or cross-site identity of any kind.
- No request bodies beyond the beacon's `{ path, referrer }`; the endpoint rejects anything else.

## Opt-outs

- The beacon does not fire when Do Not Track is set or a Global Privacy Control signal is present
  in the browser (`navigator.globalPrivacyControl`).
- The server drops any event carrying `Sec-GPC: 1`, silently, before parsing or logging.

## Coverage limit (read this before quoting any number)

- Pages served from Vercel's CDN/ISR cache do not run origin code per view, and non-JS bots never
  fire the beacon. This feature therefore identifies (a) human page views via the beacon and
  (b) any request that reaches the collect route.
- Full bot coverage of cached public pages exists only in Vercel's edge logs, which application
  code cannot see. Numbers from this feature must never be presented as complete traffic coverage.

## Storage

- Events are structured server logs only (`console.info`, event name `anon_traffic`) in this
  iteration. Durable persistence is a follow-up and belongs to the Python backend, which owns DB
  writes; the website's Postgres access is read-only.
- The collect route is excluded from Clerk middleware, like `/api/analytics`, so it costs no
  middleware (Fluid Active CPU) run per beacon.
