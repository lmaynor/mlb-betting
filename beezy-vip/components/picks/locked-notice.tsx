import Link from 'next/link'
import { PRE_LAUNCH } from '@/lib/launch'

/** One plain row under a table that was cut by the access policy (lib/access.ts). Says how many rows are withheld. */
export function LockedNotice({ locked, shown = 0, what, plan }: { locked: number; shown?: number; what: string; plan: string }) {
  if (locked <= 0) return null
  return (
    <div role="note" className="flex items-baseline justify-between gap-y-2 gap-x-4 flex-wrap mt-2.5 py-2.5 px-3.5 bg-background-primary-default border border-separator-border rounded-control text-cozy">
      <span className="text-neutral-300">
        <span className="mono text-text-primary font-semibold">{locked}</span>{shown > 0 ? ' more' : ''} {what} today. {plan}
      </span>
      <Link className="text-link text-[13px] whitespace-nowrap" href="/pricing">{PRE_LAUNCH ? 'See plans' : 'Upgrade'} &rarr;</Link>
    </div>
  )
}
