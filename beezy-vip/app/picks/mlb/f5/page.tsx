import { SystemPicksPage } from '@/components/picks/system-picks-page'
import type { Metadata }   from 'next'

export const revalidate = 60

export const metadata: Metadata = {
  title:       'F5 Picks Today — MLB F5 System',
  description: 'Today\'s F5 picks from the Beezy.VIP machine learning model. Model probability, edge, and Kelly stake sizing.',
}

export default function F5Page() {
  return <SystemPicksPage system="F5" />
}
