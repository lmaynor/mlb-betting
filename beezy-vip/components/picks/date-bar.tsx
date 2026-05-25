'use client'

import { useRouter, useSearchParams, usePathname } from 'next/navigation'

const B = '0.5px solid #1f1f24'

function fmtDisplay(date: Date) {
  return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase()
}

function toDateString(date: Date) {
  return date.toISOString().slice(0, 10)
}

export function DateBar() {
  const router     = useRouter()
  const sp         = useSearchParams()
  const pathname   = usePathname()

  const todayStr   = toDateString(new Date())
  const current    = sp.get('date') ?? todayStr
  const currentDate = new Date(current + 'T12:00:00')

  const prev = new Date(currentDate); prev.setDate(prev.getDate() - 1)
  const next = new Date(currentDate); next.setDate(next.getDate() + 1)

  const isToday   = current === todayStr
  const isFuture  = current > todayStr

  function navigate(date: Date) {
    const params = new URLSearchParams(sp.toString())
    const ds = toDateString(date)
    if (ds === todayStr) params.delete('date')
    else params.set('date', ds)
    router.push(`${pathname}?${params.toString()}`)
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '7px 16px', borderBottom: B, background: '#0a0a0c',
    }}>
      <button onClick={() => navigate(prev)}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '4px 10px', cursor: 'pointer', border: B, background: 'transparent', color: '#71717a' }}>
        ← Prev
      </button>
      <button onClick={() => navigate(new Date())}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', fontWeight: isToday ? 600 : 400, padding: '4px 16px', cursor: 'pointer', border: `0.5px solid ${isToday ? '#10b981' : '#1f1f24'}`, background: isToday ? '#10b98112' : 'transparent', color: isToday ? '#10b981' : '#f5f5f7', letterSpacing: '0.04em' }}>
        {isToday ? `TODAY — ${fmtDisplay(currentDate)}` : fmtDisplay(currentDate)}
      </button>
      <button onClick={() => navigate(next)} disabled={isFuture}
        style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', padding: '4px 10px', cursor: isFuture ? 'default' : 'pointer', border: B, background: 'transparent', color: isFuture ? '#2a2a31' : '#71717a' }}>
        Next →
      </button>
    </div>
  )
}
