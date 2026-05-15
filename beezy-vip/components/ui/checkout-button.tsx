'use client'

import { useState } from 'react'

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
        className={`
          block w-full text-center mono text-xs tracking-widest uppercase py-3 font-semibold
          transition-colors disabled:opacity-50 disabled:cursor-not-allowed
          ${featured
            ? 'bg-accent text-bg hover:bg-accent/90'
            : 'border border-[var(--border-bright)] text-text hover:border-accent hover:text-accent'}
        `}
      >
        {loading ? 'Redirecting…' : label}
      </button>
      {error && <p className="mono text-xs text-loss mt-2 text-center">{error}</p>}
    </div>
  )
}
