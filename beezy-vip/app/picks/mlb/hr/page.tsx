import { SystemPicksPage } from '@/components/picks/system-picks-page'
import type { Metadata }   from 'next'

export const dynamic = 'force-dynamic'

export const revalidate = 60

export const metadata: Metadata = {
  title:       'HR Picks Today — MLB HR System',
  description: 'Today\'s HR picks from the Beezy.VIP machine learning model. Model probability, edge, and Kelly stake sizing.',
}

export default function HRPage() {
  return <SystemPicksPage system="HR" />
}
