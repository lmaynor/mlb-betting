'use client'

import { useState } from 'react'

const B = '1px solid var(--basalt)'

export function BankrollInput({ defaultValue }: { defaultValue: number }) {
  const [value,  setValue]  = useState(String(defaultValue))
  const [saving, setSaving] = useState(false)
  const [saved,  setSaved]  = useState(false)
  const [error,  setError]  = useState('')

  async function handleSave() {
    const n = parseFloat(value)
    if (isNaN(n) || n <= 0) { setError('Enter a valid amount'); return }
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/user/bankroll', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ bankroll: n }),
      })
      if (!res.ok) throw new Error('Save failed')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: '8px' }}>
        <input
          type="number"
          value={value}
          onChange={e => { setValue(e.target.value); setSaved(false) }}
          placeholder="1000"
          className="mono"
          style={{
            flex: 1, background: 'var(--slate)', border: B,
            borderRadius: 'var(--radius)',
            color: 'var(--ash)', fontSize: '13px',
            padding: '9px 12px', outline: 'none',
          }}
        />
        <button
          onClick={handleSave}
          disabled={saving}
          className="mono"
          style={{
            padding: '9px 18px', background: 'var(--signal)', color: 'var(--carbon)',
            fontSize: '13px', fontWeight: 600, letterSpacing: '0.01em',
            borderRadius: 'var(--radius)', cursor: saving ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.5 : 1, border: '1px solid var(--signal-led)',
          }}
        >
          {saving ? '...' : saved ? 'Saved' : 'Save'}
        </button>
      </div>
      {error && (
        <p className="mono" style={{ fontSize: '11px', color: 'var(--loss)', marginTop: '6px' }}>
          {error}
        </p>
      )}
    </div>
  )
}
