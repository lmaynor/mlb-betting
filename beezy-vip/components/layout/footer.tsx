import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'

const socialBox: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  width: '36px', height: '36px', border: '1px solid var(--basalt)',
  borderRadius: 'var(--radius)', textDecoration: 'none', background: 'var(--graphite)',
}

export function Footer() {
  return (
    <footer style={{ borderTop: '1px solid var(--basalt)', background: 'var(--obsidian)', marginTop: '72px', paddingBottom: 'env(safe-area-inset-bottom)' }}>

      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '48px 24px' }}>
        <div className="footer-grid" style={{ marginBottom: '40px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '9px', marginBottom: '12px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: 'var(--signal)', boxShadow: '0 0 8px var(--signal)' }} />
              <span className="dell-display" style={{ fontSize: '17px', fontWeight: 800, color: 'var(--chalk)' }}>
                BEEZY<span style={{ color: 'var(--signal)' }}>.FYI</span>
              </span>
            </div>
            <p className="times" style={{ fontSize: '14px', color: 'var(--fog)', lineHeight: 1.65, marginBottom: '16px', maxWidth: '34ch' }}>
              Machine-learned MLB picks. Built on data, not gut feelings.
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" style={{ ...socialBox, color: '#8b92f0' }} aria-label="Join Discord">
                <DiscordMark size={18} color="currentColor" />
              </a>
              <a href={X_URL} target="_blank" rel="noopener noreferrer" style={{ ...socialBox, color: 'var(--silver)' }} aria-label="Follow Beezy.FYI on X">
                <XMark size={15} color="currentColor" />
              </a>
            </div>
          </div>
          {[
            { title: 'Picks',   links: [['MLB','/picks/mlb'],['NRFI','/picks/mlb/nrfi'],['Home Runs','/picks/mlb/hr'],['F5','/picks/mlb/f5'],['Strikeouts','/picks/mlb/k']] },
            { title: 'Tools',   links: [['Odds Calculator','/tools/odds-calculator'],['Kelly Calculator','/tools/kelly-calculator'],['Edge Finder','/tools/edge-finder'],['NRFI Conditions','/tools/nrfi-conditions'],['Pitcher Matchups','/tools/pitcher-matchups']] },
            { title: 'Company', links: [['Models','/models'],['Results','/results'],['Pricing','/pricing'],['Learn','/learn']] },
            { title: 'Legal',   links: [['Terms of Service','/legal/terms'],['Privacy Policy','/legal/privacy'],['Responsible Gambling','/legal/responsible-gambling'],['Refund Policy','/legal/refunds']] },
          ].map(col => (
            <div key={col.title}>
              <div
                className="dell-heading"
                style={{ fontSize: '11px', letterSpacing: '0.1em', color: 'var(--fog)', marginBottom: '14px' }}
              >
                {col.title}
              </div>
              {col.links.map(([label, href]) => (
                <Link
                  key={href}
                  href={href}
                  className="times"
                  style={{ display: 'block', fontSize: '13.5px', color: 'var(--silver)', textDecoration: 'none', marginBottom: '9px', transition: 'color var(--dur) var(--ease-out)' }}
                >
                  {label}
                </Link>
              ))}
            </div>
          ))}
        </div>

        {/* Paper mode callout */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', padding: '14px 16px', border: '1px solid var(--win-border)', borderRadius: 'var(--radius-lg)', marginBottom: '24px', background: 'var(--win-wash)' }}>
          <span
            className="dell-heading"
            style={{ fontSize: '10px', letterSpacing: '0.08em', color: 'var(--signal)', flexShrink: 0, background: 'color-mix(in oklab, var(--signal) 18%, var(--carbon))', padding: '4px 8px', borderRadius: 'var(--radius-pill)' }}
          >
            PAPER MODE
          </span>
          <p className="times" style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55 }}>
            Beezy.FYI is in pre-launch paper mode. No real money is being wagered or transacted. Paid access opens when the first system clears its 200-bet validation gate.
          </p>
        </div>

        <div style={{ borderTop: '1px solid var(--basalt)', paddingTop: '20px' }}>
          <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginBottom: '6px', lineHeight: 1.55 }}>All figures are paper-mode results. Past performance is not indicative of future results. This is not financial advice.</p>
          <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginBottom: '6px', lineHeight: 1.55 }}>Sports betting availability varies by jurisdiction. Verify legality in your location. Must be 21+ to bet.</p>
          <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '14px' }}>
            &copy; <span suppressHydrationWarning>{new Date().getFullYear()}</span> <Link href="/" style={{ color: 'var(--link)', textDecoration: 'none' }}>Beezy.FYI</Link> &middot; All rights reserved
          </p>
        </div>
      </div>
    </footer>
  )
}
