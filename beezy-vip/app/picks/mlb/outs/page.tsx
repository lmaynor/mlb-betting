import { SystemPicksPage } from '@/components/picks/system-picks-page'
import type { Metadata }   from 'next'

export const dynamic = 'force-dynamic'

export const revalidate = 60

export const metadata: Metadata = {
  title:       'OUTS Picks Today — MLB OUTS System',
  description: 'Today\'s OUTS picks from the Beezy.VIP machine learning model. Model probability, edge, and Kelly stake sizing.',
}

export default function OUTSPage() {
  return <SystemPicksPage system="OUTS" />
}
