import { redirect } from 'next/navigation'

/**
 * Legacy URL. Every sport's page is /picks/[sport] now (next.config.ts redirects /sports/:sport before this runs; this is
 * the backstop so an old link or a missed redirect still lands in the right place).
 */
export default async function LegacySportRedirect({ params }: { params: Promise<{ sport: string }> }) {
  const { sport } = await params
  redirect(`/picks/${encodeURIComponent(sport)}`)
}
