import { SystemPicksPage } from '@/components/picks/system-picks-page'
import type { Metadata }   from 'next'

export const revalidate = 60

export const metadata: Metadata = {
  title:       'K Picks Today — MLB K System',
  description: 'Today\'s K picks from the Beezy.VIP machine learning model. Model probability, edge, and Kelly stake sizing.',
}

export default function KPage() {
  return <SystemPicksPage system="K" />
}
