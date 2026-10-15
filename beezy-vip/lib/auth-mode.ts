/**
 * Local-review switch for Clerk. `AUTH_DISABLED=1` (server env, e.g. in .env.local) runs the site with NO Clerk at all:
 * no ClerkProvider, no clerkMiddleware, no publishable key needed. Use it to render pages locally or on a preview that
 * has no Clerk keys.
 *
 * FAIL-CLOSED: the switch is ignored on a Vercel production deployment (`VERCEL_ENV=production`), so a stray env var can
 * never open /dashboard or the member tools on beezy.fyi. While it is on, every protected page is viewable, `getUserTier()`
 * reports 'pro' (so tier-gated UI can be reviewed), and every route that needs a real user (bankroll, Stripe checkout /
 * portal) answers 401 because there is no user.
 */
export const AUTH_DISABLED: boolean =
  process.env.AUTH_DISABLED === '1' && (process.env.VERCEL_ENV ?? process.env.NEXT_PUBLIC_VERCEL_ENV) !== 'production'
