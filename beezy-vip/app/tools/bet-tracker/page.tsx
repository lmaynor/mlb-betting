export const dynamic = 'force-dynamic'
import Link from 'next/link'
import type { Metadata } from 'next'
import { ToolHeader } from '@/components/ui/tool-header'

export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }

export default function BetTrackerPage() {
  return (
    <div style={{ maxWidth: '680px', margin: '0 auto', padding: '24px' }}>
      <ToolHeader title="Personal Bet Tracker" hint="Coming soon" />
      <div style={{ border: '1px solid #1e2230', borderRadius: '8px', background: '#11131a', padding: '32px 24px' }}>
        <p style={{ fontSize: '14px', color: 'var(--silver)', margin: '0 0 6px' }}>Under construction.</p>
        <p style={{ fontSize: '13px', color: 'var(--fog)', margin: '0 0 18px', lineHeight: 1.55 }}>
          Log your own bets and compare your ROI to the model. It will be a Pro feature at launch.
        </p>
        <Link href="/pricing" className="btn btn-primary">See pricing</Link>
      </div>
    </div>
  )
}
