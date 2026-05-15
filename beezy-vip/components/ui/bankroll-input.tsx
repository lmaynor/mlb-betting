'use client'

import { useState } from 'react'

export function BankrollInput({ defaultValue }: { defaultValue: number }) {
  const [value,   setValue]   = useState(String(defaultValue))
  const [saving,  setSaving]  = useState(false)
  const [saved,   setSaved]   = useState(false)
  const [error,   setError]   = useState('')

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
      <div className="flex gap-3">
        <input
          type="number"
          value={value}
          onChange={e => { setValue(e.target.value); setSaved(false) }}
          placeholder="1000"
          className="flex-1 bg-[var(--bg)] border border-[var(--border)] text-text mono text-sm px-4 py-2.5 focus:outline-none focus:border-accent placeholder:text-muted"
        />
        <button
          onClick={handleSave}
          disabled={saving}
          className="mono text-xs uppercase tracking-widest px-4 py-2.5 bg-accent text-bg font-semibold hover:bg-accent/90 transition-colors disabled:opacity-50"
        >
          {saving ? '…' : saved ? 'Saved ✓' : 'Save'}
        </button>
      </div>
      {error && <p className="mono text-xs text-loss mt-2">{error}</p>}
    </div>
  )
}
