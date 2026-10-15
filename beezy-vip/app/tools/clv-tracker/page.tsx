export const dynamic = 'force-dynamic'

import { redirect } from 'next/navigation'
import { getUserId } from '@/lib/auth'
import { AUTH_DISABLED } from '@/lib/auth-mode'
import { apiGetCLVData } from '@/lib/betting-api'
import { CLVClient } from './clv-client'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'CLV Tracker -- Closing Line Value',
  robots: { index: false, follow: false },
}

export default async function CLVTrackerPage() {
  const userId = await getUserId()
  if (!userId && !AUTH_DISABLED) redirect('/login')

  const initial = await apiGetCLVData(90).catch(() => [])

  return <CLVClient initial={initial} />
}
