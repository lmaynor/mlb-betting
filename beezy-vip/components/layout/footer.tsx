import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

import { DISCORD_URL } from '@/lib/links'
import { X_URL } from '@/lib/links'
import { SITE_CONFIG } from '@/lib/site-config'

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
            <div style={{ marginBottom: '12px' }}>
              <BrandWordmark size="sm" showDomain />
            </div>
            <p className="times" style={{ fontSize: '14px', color: 'var(--fog)', lineHeight: 1.65, marginBottom: '16px', maxWidth: '34ch' }}>
              Model picks and a live +EV scanner, with every result public.
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
            { title: 'Picks',   links: [['+EV Terminal','/ev'],['Prop Research','/edge'],['Top 10 Picks','/picks'],['Picks History','/picks/history'],['Daily Card','/cheat-sheet'],['Results','/results'],['Analytics','/analytics']] },
            { title: 'Sports',  links: [['MLB','/picks/mlb'],['NFL','/sports/nfl'],['NHL','/sports/nhl'],['NBA','/sports/nba']] },
            { title: 'Tools',   links: [['Odds & Vig Calculator','/tools/odds-calculator'],['Kelly Calculator','/tools/kelly-calculator'],['Boost EV Calculator','/tools/boost-ev'],['Slate','/tools/slate']] },
            { title: 'Company', links: [['Models','/models'],['Results','/results'],['Pricing','/pricing'],['Learn','/learn']] },
            { title: 'Legal',   links: [['Terms of Service','/legal/terms'],['Privacy Policy','/legal/privacy'],['Responsible Gambling','/legal/responsible-gambling'],['Refund Policy','/legal/refunds'],['Support',`mailto:${SITE_CONFIG.supportEmail}`],[SITE_CONFIG.xHandle,SITE_CONFIG.xUrl]] },
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

        <div style={{ borderTop: '1px solid var(--basalt)', paddingTop: '20px' }}>
          <p className="times" style={{ fontSize: '11px', color: '#7d8596', marginBottom: '6px', lineHeight: 1.55 }}>{SITE_CONFIG.riskDisclaimer} Plans and results are paper-tracked and hypothetical; no real money is wagered through beezy.fyi.</p>
          <p className="times" style={{ fontSize: '12px', color: 'var(--fog)', marginTop: '14px' }}>
            &copy; <span suppressHydrationWarning>{new Date().getFullYear()}</span> beezy.fyi. Independent quantitative sports analytics.
          </p>
        </div>
      </div>
    </footer>
  )
}
