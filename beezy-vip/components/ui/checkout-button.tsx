'use client'

import { useState } from 'react'

const B = '1px solid var(--basalt)'

export function CheckoutButton({
  tier,
  label,
  featured = false,
}: {
  tier:      'starter' | 'pro' | 'season'
  label:     string
  featured?: boolean
}) {
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  async function handleClick() {
    setLoading(true)
    setError('')
    try {
      const res  = await fetch('/api/stripe/checkout', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ tier }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error ?? 'Checkout failed')
      if (data.url) window.location.href = data.url
    } catch (err) {
      setError(String(err))
      setLoading(false)
    }
  }

  return (
    <div>
      <button
        onClick={handleClick}
        disabled={loading}
        className="mono"
        style={{
          display: 'block', width: '100%', textAlign: 'center',
          fontSize: '13px', letterSpacing: '0.01em',
          padding: '11px 16px', fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading ? 0.5 : 1, transition: 'opacity 0.15s',
          borderRadius: 'var(--radius)',
          background: featured ? 'var(--signal)' : 'var(--slate)',
          color:      featured ? 'var(--carbon)' : 'var(--ash)',
          border:     featured ? '1px solid var(--signal-led)' : B,
          boxShadow:  featured ? 'var(--shadow-inset)' : 'none',
        }}
      >
        {loading ? 'Redirecting...' : label}
      </button>
      {error && (
        <p className="mono" style={{ fontSize: '11px', color: 'var(--loss)', marginTop: '8px', textAlign: 'center' }}>
          {error}
        </p>
      )}
    </div>
  )
}
