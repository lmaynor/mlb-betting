export const dynamic = 'force-dynamic'

import { redirect } from 'next/navigation'
import { auth } from '@clerk/nextjs/server'
import { apiGetTodaySlate } from '@/lib/betting-api'
import { SlateClient } from './slate-client'
import type { Metadata } from 'next'
import { formatCentralDate } from '@/lib/dates'

export const metadata: Metadata = {
  title: "Today's MLB Slate -- Beezy Command Center",
  robots: { index: false, follow: false },
}

export default async function SlatePage() {
  const { userId } = await auth()
  if (!userId) redirect('/login')

  const slate = await apiGetTodaySlate().catch(() => ({
    games: [], run_date: '', as_of: '', total_picks: 0, total_games: 0,
  }))

  const dateLabel = formatCentralDate(new Date(), { weekday: 'long', month: 'long', day: 'numeric' })

  return <SlateClient slate={slate} dateLabel={dateLabel} />
}
