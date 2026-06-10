import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const BORDER_HARD = '1px solid #000'
const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/beezy_fyi'

// Dell 1996 icon-label nav items
const ICON_NAV = [
  { label: 'Picks',    href: '/picks',   icon: '◈' },
  { label: 'Tools',    href: '/tools',   icon: '⚙' },
  { label: 'Results',  href: '/results', icon: '▤' },
  { label: 'Support',  href: '/learn',   icon: '?' },
]

export function Footer() {
  return (
    <footer style={{ borderTop: BORDER_HARD, background: '#111114', marginTop: '48px', paddingBottom: 'env(safe-area-inset-bottom)' }}>

      {/* Dell icon-label nav strip */}
      <div style={{ borderBottom: BORDER_HARD, padding: '12px 20px', background: '#0a0a0c' }}>
        <div style={{ maxWidth: '1200px', margin: '0 auto', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0' }}>
          {ICON_NAV.map((item, i) => (
            <Link
              key={item.label}
              href={item.href}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '4px',
                padding: '8px 24px',
                borderRight: i < ICON_NAV.length - 1 ? '1px solid #1f1f24' : 'none',
                textDecoration: 'none',
                color: '#9999ff',
              }}
            >
              <span style={{ fontSize: '20px', color: '#a1a1aa', lineHeight: 1 }}>{item.icon}</span>
              <span
                className="dell-heading"
                style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#a1a1aa' }}
              >
                {item.label}
              </span>
            </Link>
          ))}
        </div>
      </div>

      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '32px 20px' }}>
        <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', gap: '32px', marginBottom: '32px' }}>
          <div>
            <div className="dell-display" style={{ fontSize: '16px', color: '#f5f5f7', marginBottom: '8px', letterSpacing: '0.02em' }}>
              BEEZY<span style={{ color: '#fcc20f' }}>.FYI</span>
            </div>
            <p className="times" style={{ fontSize: '13px', color: '#888890', lineHeight: 1.6, marginBottom: '12px' }}>
              Machine learning models for sports betting. Built on data, not gut feelings.
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <a
                href={DISCORD_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '32px', height: '32px', border: BORDER_HARD, color: '#5865f2', textDecoration: 'none' }}
                aria-label="Join Discord"
              >
                <DiscordMark size={18} color="currentColor" />
              </a>
              <a
                href={X_URL}
                target="_blank"
                rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '32px', height: '32px', border: BORDER_HARD, color: '#f5f5f7', textDecoration: 'none' }}
                aria-label="Follow Beezy.FYI on X"
              >
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
                style={{ fontSize: '9px', letterSpacing: '0.1em', color: '#a1a1aa', marginBottom: '10px', borderBottom: '1px solid #1f1f24', paddingBottom: '6px' }}
              >
                {col.title}
              </div>
              {col.links.map(([label, href]) => (
                <Link
                  key={href}
                  href={href}
                  style={{ display: 'block', fontSize: '12px', color: '#9999ff', textDecoration: 'underline', marginBottom: '6px', fontFamily: 'Georgia, Times New Roman, Times, serif' }}
                >
                  {label}
                </Link>
              ))}
            </div>
          ))}
        </div>

        {/* Paper mode callout -- Dell CTA panel style */}
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', padding: '12px', border: '1px solid #000', marginBottom: '20px', background: '#0a0a0c' }}>
          <span
            className="dell-heading"
            style={{ fontSize: '10px', color: '#fcc20f', flexShrink: 0, background: '#000', padding: '2px 6px' }}
          >
            PAPER MODE
          </span>
          <p className="times" style={{ fontSize: '12px', color: '#888890', lineHeight: 1.5 }}>
            Beezy.FYI is in pre-launch paper mode. No real money is being wagered or transacted. Paid access opens when the first system clears its 200-bet validation gate.
          </p>
        </div>

        <div style={{ borderTop: '1px solid #1f1f24', paddingTop: '16px' }}>
          <p className="times" style={{ fontSize: '12px', color: '#888890', marginBottom: '4px', lineHeight: 1.5 }}>All figures are paper-mode results. Past performance is not indicative of future results. This is not financial advice.</p>
          <p className="times" style={{ fontSize: '12px', color: '#888890', marginBottom: '4px', lineHeight: 1.5 }}>Sports betting availability varies by jurisdiction. Verify legality in your location. Must be 21+ to bet.</p>
          <p className="times" style={{ fontSize: '12px', color: '#888890', marginTop: '12px' }}>
            &copy; {new Date().getFullYear()} <Link href="/" style={{ color: '#9999ff', textDecoration: 'underline' }}>Beezy.FYI</Link> &middot; All rights reserved
          </p>
        </div>
      </div>
    </footer>
  )
}
