'use client'

import { useRouter, useSearchParams, usePathname } from 'next/navigation'
import { addDaysToDateKey, formatDateKey, siteDateKey } from '@/lib/dates'

const B = '1px solid var(--basalt)'

function fmtDisplay(dateKey: string) {
  return formatDateKey(dateKey, { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase()
}

export function DateBar() {
  const router = useRouter()
  const sp = useSearchParams()
  const pathname = usePathname()

  const todayStr = siteDateKey()
  const current = sp.get('date') ?? todayStr
  const prev = addDaysToDateKey(current, -1)
  const next = addDaysToDateKey(current, 1)

  const isToday = current === todayStr
  const isFuture = current > todayStr

  function navigate(date: string) {
    const params = new URLSearchParams(sp.toString())
    if (date === todayStr) params.delete('date')
    else params.set('date', date)
    const query = params.toString()
    router.push(query ? `${pathname}?${query}` : pathname)
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '10px 16px', borderBottom: B, background: 'var(--carbon)', gap: '10px',
    }}>
      <button onClick={() => navigate(prev)}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '6px 12px', borderRadius: 'var(--radius)', cursor: 'pointer', border: B, background: 'var(--graphite)', color: 'var(--silver)' }}>
        &larr; Prev
      </button>
<button onClick={() => navigate(todayStr)}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', fontWeight: isToday ? 600 : 400, padding: '6px 14px', borderRadius: 'var(--radius)', cursor: 'pointer', border: `1px solid ${isToday ? 'var(--win-border)' : 'var(--basalt)'}`, background: isToday ? 'var(--win-wash)' : 'var(--graphite)', color: isToday ? 'var(--signal)' : 'var(--ash)', letterSpacing: '0.04em', flex: '0 1 auto', whiteSpace: 'nowrap' }}>
        {isToday ? `TODAY · ${fmtDisplay(current)} CT` : `${fmtDisplay(current)} CT`}
      </button>
      <button onClick={() => navigate(next)} disabled={isFuture}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '6px 12px', borderRadius: 'var(--radius)', cursor: isFuture ? 'default' : 'pointer', border: B, background: 'var(--graphite)', color: isFuture ? 'var(--iron)' : 'var(--silver)', opacity: isFuture ? 0.5 : 1 }}>
        Next &rarr;
      </button>
    </div>
  )
}
