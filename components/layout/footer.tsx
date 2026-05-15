import Link from 'next/link'

export function Footer() {
  return (
    <footer className="border-t border-[var(--border)] bg-[var(--surface)] mt-24">
      <div className="max-w-7xl mx-auto px-4 py-12">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-8 mb-12">
          <div>
            <span className="mono text-lg font-bold text-accent">BEEZY<span className="text-text">.VIP</span></span>
            <p className="text-muted text-xs mt-3 leading-relaxed">
              Machine learning models for sports betting. Built on data, not gut feelings.
            </p>
          </div>

          <div>
            <p className="mono text-xs tracking-widest uppercase text-muted mb-4">Picks</p>
            {[
              ['MLB', '/picks/mlb'],
              ['NRFI', '/picks/mlb/nrfi'],
              ['Home Runs', '/picks/mlb/hr'],
              ['F5', '/picks/mlb/f5'],
              ['Strikeouts', '/picks/mlb/k'],
            ].map(([label, href]) => (
              <Link key={href} href={href} className="block text-xs text-muted hover:text-text py-1 transition-colors">
                {label}
              </Link>
            ))}
          </div>

          <div>
            <p className="mono text-xs tracking-widest uppercase text-muted mb-4">Tools</p>
            {[
              ['Odds Calculator', '/tools/odds-calculator'],
              ['Kelly Calculator', '/tools/kelly-calculator'],
              ['Edge Finder', '/tools/edge-finder'],
              ['NRFI Conditions', '/tools/nrfi-conditions'],
              ['Pitcher Matchups', '/tools/pitcher-matchups'],
            ].map(([label, href]) => (
              <Link key={href} href={href} className="block text-xs text-muted hover:text-text py-1 transition-colors">
                {label}
              </Link>
            ))}
          </div>

          <div>
            <p className="mono text-xs tracking-widest uppercase text-muted mb-4">Company</p>
            {[
              ['Models',  '/models'],
              ['Results', '/results'],
              ['Learn',   '/learn'],
              ['Discord', 'https://discord.gg/beezy'],
            ].map(([label, href]) => (
              <a key={href} href={href} className="block text-xs text-muted hover:text-text py-1 transition-colors">
                {label}
              </a>
            ))}
          </div>

          <div>
            <p className="mono text-xs tracking-widest uppercase text-muted mb-4">Legal</p>
            {[
              ['Terms of Service',       '/legal/terms'],
              ['Privacy Policy',         '/legal/privacy'],
              ['Responsible Gambling',   '/legal/responsible-gambling'],
              ['Refund Policy',          '/legal/refunds'],
            ].map(([label, href]) => (
              <Link key={href} href={href} className="block text-xs text-muted hover:text-text py-1 transition-colors">
                {label}
              </Link>
            ))}
          </div>
        </div>

        {/* Pre-launch paper-mode notice -- remove when PRE_LAUNCH = false */}
        <div className="border border-[var(--border-bright)]/40 bg-[var(--surface)] px-4 py-3 mb-8 flex items-start gap-3">
          <span className="mono text-xs text-accent font-bold shrink-0">PAPER MODE</span>
          <p className="mono text-xs text-muted">
            Beezy.VIP is in pre-launch paper mode. No real money is being wagered or transacted.
            Paid access opens when the first system clears its 200-bet validation gate.
          </p>
        </div>

        <div className="border-t border-[var(--border)] pt-8 space-y-2">
          <p className="mono text-xs text-muted leading-relaxed">
            All figures are paper-mode results. Past performance is not indicative of future results.
            This is not financial advice.
          </p>
          <p className="mono text-xs text-muted">
            Sports betting availability varies by jurisdiction. Verify legality in your location.
            Must be 21+ to bet.
          </p>
          <p className="mono text-xs text-muted mt-4">
            © {new Date().getFullYear()} Beezy.VIP · All rights reserved
          </p>
        </div>
      </div>
    </footer>
  )
}
