export const dynamic = 'force-dynamic'

import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import Link from 'next/link'
import { ContestClient } from '@/components/contest/contest-client'
import { fetchFeed } from '@/lib/contest/backend'
import { contestEnabled } from '@/lib/contest/flag'
import { sampleFeed } from '@/lib/contest/fixture'
import { contestUser } from '@/lib/contest/identity'
import './contest.css'

export const metadata: Metadata = {
  title: 'Bankroll Challenge',
  description: 'A play-money betting contest: straights, parlays, teasers, round robins and same-game parlays, picks hidden until kickoff.',
  robots: { index: false, follow: false },
}

function Notice({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="max-w-[1400px] mx-auto pt-4 px-4 sm:px-6 pb-16">
      <section aria-label={title} style={{ maxWidth: 460, background: '#11131a', border: '1px solid #1e2230', borderRadius: 8, padding: 16 }}>
        <h1 style={{ fontSize: 20, fontWeight: 700, margin: '0 0 6px' }}>{title}</h1>
        <p style={{ fontSize: 13, color: '#9ca3af', margin: 0 }}>{children}</p>
      </section>
    </div>
  )
}

/**
 * The Bankroll Challenge. OFF by default: it answers 404 unless CONTEST_ENABLED is on (0 / false / off / no / empty all mean off).
 *
 * Two modes. With CONTEST_SLUG set the page is LIVE: it needs a signed-in player and reads that player's feed from the contest API
 * (contest/api.py, through lib/contest/backend.ts). Without it the page runs on SAMPLE data (lib/contest/fixture.ts) and says so in its header.
 */
export default async function ContestPage({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> } = {}) {
  if (!contestEnabled()) notFound()
  const slug = (process.env.CONTEST_SLUG ?? '').trim()
  if (slug) {
    const user = await contestUser()
    if (!user) return <Notice title="Bankroll Challenge">This contest is invite only. <Link href="/login?redirect_url=/contest" style={{ color: 'var(--signal)' }}>Sign in</Link> to join or to place your bets.</Notice>
    const r = await fetchFeed(slug, user.id, user.suggestedName)
    if (r.status === 404) return <Notice title="Bankroll Challenge">This contest is not open.</Notice>
    if (r.status !== 200) return <Notice title="Bankroll Challenge">The contest could not be loaded right now. Try again in a minute.</Notice>
    return <ContestClient feed={r.body} slug={slug} />
  }
  // ?preview=broke shows the hidden blackjack lifeline on the sample data (a player down to $40); it changes nothing else
  const sp = (await searchParams) ?? {}
  return <ContestClient feed={sampleFeed(new Date().toISOString(), { broke: sp.preview === 'broke' })} />
}
