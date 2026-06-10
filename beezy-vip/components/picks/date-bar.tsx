'use client'

import { useRouter, useSearchParams, usePathname } from 'next/navigation'
import { addDaysToDateKey, formatDateKey, siteDateKey } from '@/lib/dates'

const B = '1px solid #1f1f24'

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
      padding: '7px 16px', borderBottom: B, background: '#0a0a0c', gap: '10px',
    }}>
      <button onClick={() => navigate(prev)}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '4px 10px', cursor: 'pointer', border: B, background: 'transparent', color: '#888890' }}>
        Prev
      </button>
<button onClick={() => navigate(todayStr)}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', fontWeight: isToday ? 600 : 400, padding: '4px 13px', cursor: 'pointer', border: `1px solid ${isToday ? '#a5b8c0' : '#1f1f24'}`, background: isToday ? '#131a1e' : 'transparent', color: isToday ? '#a5b8c0' : '#f5f5f7', letterSpacing: '0.04em', flex: '0 1 auto', whiteSpace: 'nowrap' }}>
        {isToday ? `TODAY / ${fmtDisplay(current)} CT` : `${fmtDisplay(current)} CT`}
      </button>
      <button onClick={() => navigate(next)} disabled={isFuture}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '4px 10px', cursor: isFuture ? 'default' : 'pointer', border: B, background: 'transparent', color: isFuture ? '#2a2a31' : '#888890' }}>
        Next
      </button>
    </div>
  )
}
