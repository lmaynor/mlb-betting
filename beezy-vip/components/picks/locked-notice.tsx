import Link from 'next/link'
import { PRE_LAUNCH } from '@/lib/launch'

/** One plain row under a table that was cut by the access policy (lib/access.ts). Says how many rows are withheld. */
export function LockedNotice({ locked, shown = 0, what, plan }: { locked: number; shown?: number; what: string; plan: string }) {
  if (locked <= 0) return null
  return (
    <div role="note" className="flex items-baseline justify-between gap-y-[8px] gap-x-[16px] flex-wrap mt-[10px] py-[10px] px-[14px] bg-[var(--color-background-primary-default)] border border-[color:var(--color-separator-border)] rounded-[8px] text-[13px]">
      <span className="text-[var(--color-neutral-300)]">
        <span className="mono text-text-primary font-bold">{locked}</span>{shown > 0 ? ' more' : ''} {what} today. {plan}
      </span>
      <Link href="/pricing" style={{ color: 'var(--link)', fontSize: '12.5px', whiteSpace: 'nowrap' }}>{PRE_LAUNCH ? 'See plans' : 'Upgrade'} &rarr;</Link>
    </div>
  )
}
