import Link from 'next/link'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

const B = '0.5px solid #1f1f24'
const DISCORD_URL = 'https://discord.gg/HfMYCmbmE'
const X_URL = 'https://x.com/BeezyVIP'

export function Footer() {
  return (
    <footer style={{ borderTop: B, background: '#111114', marginTop: '48px', paddingBottom: 'env(safe-area-inset-bottom)' }}>
      <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
        <div className="footer-grid" style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr 1fr', gap: '32px', marginBottom: '32px' }}>
          <div>
            <div className="mono" style={{ fontSize: '14px', fontWeight: 600, color: '#f5f5f7', marginBottom: '8px' }}>
              BEEZY<span style={{ color: '#10b981' }}>.VIP</span>
            </div>
            <p style={{ fontSize: '11px', color: '#71717a', lineHeight: 1.6, marginBottom: '12px' }}>
              Machine learning models for sports betting. Built on data, not gut feelings.
            </p>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '32px', height: '32px', border: B, color: '#5865f2', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}
                aria-label="Join Discord">
                <DiscordMark size={18} color="currentColor" />
              </a>
              <a href={X_URL} target="_blank" rel="noopener noreferrer"
                style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: '32px', height: '32px', border: B, color: '#f5f5f7', textDecoration: 'none', borderRadius: 'var(--radius-sm)' }}
                aria-label="Follow Beezy.VIP on X">
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
              <div className="mono" style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: '#a1a1aa', marginBottom: '10px' }}>{col.title}</div>
              {col.links.map(([label, href]) => (
                <Link key={href} href={href} style={{ display: 'block', fontSize: '11px', color: '#71717a', textDecoration: 'none', marginBottom: '6px' }}>{label}</Link>
              ))}
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', alignItems: 'flex-start', gap: '12px', padding: '12px', border: B, marginBottom: '20px' }}>
          <span className="mono" style={{ fontSize: '10px', fontWeight: 700, color: '#10b981', flexShrink: 0 }}>PAPER MODE</span>
          <p className="mono" style={{ fontSize: '11px', color: '#71717a' }}>
            Beezy.VIP is in pre-launch paper mode. No real money is being wagered or transacted. Paid access opens when the first system clears its 200-bet validation gate.
          </p>
        </div>

        <div style={{ borderTop: B, paddingTop: '16px' }}>
          <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginBottom: '4px' }}>All figures are paper-mode results. Past performance is not indicative of future results. This is not financial advice.</p>
          <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginBottom: '4px' }}>Sports betting availability varies by jurisdiction. Verify legality in your location. Must be 21+ to bet.</p>
          <p className="mono" style={{ fontSize: '11px', color: '#71717a', marginTop: '12px' }}>&copy; {new Date().getFullYear()} Beezy.VIP &middot; All rights reserved</p>
        </div>
      </div>
    </footer>
  )
}
