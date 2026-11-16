/**
 * OPS-08 (Vercel Function Invocations): the OG card routes render an image per request, and every
 * render is a function invocation (most also fetch the backend uncached). The cards are date-scoped
 * snapshots — today's picks, a settled recap — so the CDN may share one render for an hour and serve
 * the last copy for a day while it re-renders in the background, the same pattern the sitemap route
 * already uses (s-maxage=3600). Without this header nothing was shared, so every crawler or chat-app
 * re-fetch of the same card URL invoked the function again.
 */
export const OG_CACHE_HEADERS = {
  'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=86400',
}
