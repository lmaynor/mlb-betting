'use client'

import { Button, LiveDot } from '@/components/ui/primitives'

export function Hero() {
  return (
    <section className="relative grid-bg border-b border-[var(--border)] overflow-hidden">
      <div className="max-w-7xl mx-auto px-4 py-24 md:py-36">
        {/* Live badge */}
        <div className="flex items-center gap-3 mb-8">
          <LiveDot />
          <span className="mono text-xs tracking-widest uppercase text-muted">
            5 Models Live · Paper Mode · 87 Settled Bets
          </span>
        </div>

        {/* H1 */}
        <h1 className="text-5xl md:text-7xl lg:text-8xl font-extrabold tracking-tight leading-[0.9] uppercase mb-6">
          <span className="block text-text">MLB Picks</span>
          <span className="block text-text">Backed By</span>
          <span className="block text-accent">Data.</span>
        </h1>

        {/* Subhead */}
        <p className="mono text-sm md:text-base text-muted max-w-lg mb-10 leading-relaxed">
          Five machine learning models. Daily signals. Built on 946k+ pitch rows.
          Every bet logged. Every loss shown.
        </p>

        {/* CTAs */}
        <div className="flex flex-wrap items-center gap-4">
          <Button href="/signup" variant="primary">
            Join Beezy.VIP
          </Button>
          <Button href="/results" variant="ghost">
            View Results →
          </Button>
        </div>

        {/* Background accent line */}
        <div
          className="absolute right-0 top-0 h-full w-px bg-accent/10"
          aria-hidden
        />
        <div
          className="absolute right-24 top-0 h-full w-px bg-accent/5"
          aria-hidden
        />
      </div>
    </section>
  )
}
