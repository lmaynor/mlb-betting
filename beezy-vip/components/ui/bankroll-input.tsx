'use client'

import { useState } from 'react'

const B = '1px solid var(--color-separator-border)'

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
      <div className="flex gap-2">
        <input
          type="number"
          value={value}
          onChange={e => { setValue(e.target.value); setSaved(false) }}
          placeholder="1000"
          className="mono flex-1 text-text-primary text-cozy py-[9px] px-3"
          style={{
            background: 'var(--slate)',
            border: B,
            borderRadius: 'var(--radius)',
            outline: 'none',
          }}
        />
        <button
          onClick={handleSave}
          disabled={saving}
          className="mono py-[9px] px-[18px] text-background-full text-cozy font-semibold tracking-[0.01em] rounded-[var(--radius)] border border-[color:var(--signal-led)]"
          style={{
            background: 'var(--signal)',
            cursor: saving ? 'not-allowed' : 'pointer',
            opacity: saving ? 0.5 : 1,
          }}
        >
          {saving ? '...' : saved ? 'Saved' : 'Save'}
        </button>
      </div>
      {error && (
        <p className="mono text-mini text-loss mt-1.5">
          {error}
        </p>
      )}
    </div>
  )
}
