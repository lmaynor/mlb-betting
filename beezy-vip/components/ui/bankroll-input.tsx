'use client'

import { useState } from 'react'

const B = '1px solid #1f1f24'

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
            flex: 1, background: '#0a0a0c', border: B,
            color: '#f5f5f7', fontSize: '13px',
            padding: '8px 12px', outline: 'none',
          }}
        />
        <button
          onClick={handleSave}
          disabled={saving}
          className="mono"
          style={{
            padding: '8px 16px', background: '#fcc20f', color: '#0a0a0c',
            fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em',
            textTransform: 'uppercase', cursor: saving ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.5 : 1, border: 'none',
          }}
        >
          {saving ? '...' : saved ? 'Saved' : 'Save'}
        </button>
      </div>
      {error && (
        <p className="mono" style={{ fontSize: '11px', color: '#d77a7a', marginTop: '6px' }}>
          {error}
        </p>
      )}
    </div>
  )
}
