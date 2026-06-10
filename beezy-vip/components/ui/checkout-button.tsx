'use client'

import { useState } from 'react'

const B = '1px solid #1f1f24'

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
          fontSize: '11px', letterSpacing: '0.06em', textTransform: 'uppercase',
          padding: '12px 16px', fontWeight: 600, cursor: loading ? 'not-allowed' : 'pointer',
          opacity: loading ? 0.5 : 1, transition: 'opacity 0.15s',
          background: featured ? '#fcc20f' : 'transparent',
          color:      featured ? '#0a0a0c' : '#f5f5f7',
          border:     featured ? 'none' : B,
        }}
      >
        {loading ? 'Redirecting...' : label}
      </button>
      {error && (
        <p className="mono" style={{ fontSize: '11px', color: '#d77a7a', marginTop: '8px', textAlign: 'center' }}>
          {error}
        </p>
      )}
    </div>
  )
}
