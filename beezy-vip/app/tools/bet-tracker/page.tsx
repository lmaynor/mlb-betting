export const dynamic = 'force-dynamic'
import Link from 'next/link'
import type { Metadata } from 'next'
import { ToolHeader } from '@/components/ui/tool-header'

export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }

export default function BetTrackerPage() {
  return (
    <div className="max-w-[680px] mx-auto p-6">
      <ToolHeader title="Personal Bet Tracker" hint="Coming soon" />
      <div className="border border-separator-border rounded-control bg-background-primary-default py-8 px-6">
        <p className="text-[14px] text-neutral-300" style={{ margin: '0 0 6px' }}>Under construction.</p>
        <p className="text-cozy text-text-secondary leading-[1.55]" style={{ margin: '0 0 18px' }}>
          Log your own bets and compare your ROI to the model. It will be a Pro feature at launch.
        </p>
        <Link href="/pricing" className="btn btn-primary">See pricing</Link>
      </div>
    </div>
  )
}
