export const dynamic = 'force-dynamic'
import Link from 'next/link'
import type { Metadata } from 'next'
import { ToolHeader } from '@/components/ui/tool-header'

export const metadata: Metadata = { title: 'Personal Bet Tracker', robots: { index: false, follow: false } }

export default function BetTrackerPage() {
  return (
    <div className="max-w-[680px] mx-auto p-[24px]">
      <ToolHeader title="Personal Bet Tracker" hint="Coming soon" />
      <div className="border border-[color:var(--color-separator-border)] rounded-[8px] bg-[var(--color-background-primary-default)] py-[32px] px-[24px]">
        <p style={{ fontSize: '14px', color: 'var(--color-neutral-300)', margin: '0 0 6px' }}>Under construction.</p>
        <p style={{ fontSize: '13px', color: 'var(--color-text-secondary)', margin: '0 0 18px', lineHeight: 1.55 }}>
          Log your own bets and compare your ROI to the model. It will be a Pro feature at launch.
        </p>
        <Link href="/pricing" className="btn btn-primary">See pricing</Link>
      </div>
    </div>
  )
}
