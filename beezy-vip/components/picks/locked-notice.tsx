import Link from 'next/link'
import { PRE_LAUNCH } from '@/lib/launch'

/** One plain row under a table that was cut by the access policy (lib/access.ts). Says how many rows are withheld. */
export function LockedNotice({ locked, shown = 0, what, plan }: { locked: number; shown?: number; what: string; plan: string }) {
  if (locked <= 0) return null
  return (
    <div role="note" style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '8px 16px', flexWrap: 'wrap', marginTop: '10px', padding: '10px 14px', background: '#11131a', border: '1px solid #1e2230', borderRadius: '8px', fontSize: '13px' }}>
      <span style={{ color: 'var(--silver)' }}>
        <span className="mono" style={{ color: 'var(--chalk)', fontWeight: 700 }}>{locked}</span>{shown > 0 ? ' more' : ''} {what} today. {plan}
      </span>
      <Link href="/pricing" style={{ color: 'var(--link)', fontSize: '12.5px', whiteSpace: 'nowrap' }}>{PRE_LAUNCH ? 'See plans' : 'Upgrade'} &rarr;</Link>
    </div>
  )
}
