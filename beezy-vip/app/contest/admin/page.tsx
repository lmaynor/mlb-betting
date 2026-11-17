export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { AdminConsole } from '@/components/contest/admin-console'
import { fetchAdmin } from '@/lib/contest/backend'
import { contestEnabled } from '@/lib/contest/flag'
import { contestUser, isContestAdmin } from '@/lib/contest/identity'
import '../contest.css'

export const metadata: Metadata = { title: 'Bankroll Challenge organiser', robots: { index: false, follow: false } }

/** The organiser console. 404 unless the contest is on, a slug is configured and the signed-in user is an organiser of it (Clerk private metadata). */
export default async function ContestAdminPage() {
  const slug = (process.env.CONTEST_SLUG ?? '').trim()
  if (!contestEnabled() || !slug) notFound()
  const user = await contestUser()
  if (!user || !(await isContestAdmin(slug))) notFound()
  const r = await fetchAdmin(slug, user.id)
  if (r.status !== 200) return <p className="mono" style={{ padding: 24, fontSize: 13, color: '#9ca3af' }}>The contest service did not answer. Try again in a minute.</p>
  return <AdminConsole slug={slug} initial={r.body} />
}
