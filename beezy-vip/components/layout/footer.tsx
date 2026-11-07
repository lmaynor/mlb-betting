import Link from 'next/link'
import { BrandWordmark } from '@/components/ui/brand-mark'
import { DiscordMark } from '@/components/ui/discord-mark'
import { XMark } from '@/components/ui/x-mark'

import { DISCORD_URL } from '@/lib/links'
import { X_URL } from '@/lib/links'
import { SITE_CONFIG } from '@/lib/site-config'
import { PRE_LAUNCH } from '@/lib/launch'
import { InstallAppButton } from '@/components/layout/install-app-button'
import { WaitlistForm } from '@/components/ui/waitlist-form'

const SOCIAL_BOX = 'button-press-motion inline-flex size-9 items-center justify-center rounded-2lg border border-separator-border bg-background-primary-default no-underline hover:bg-background-primary-hover'

export function Footer() {
  return (
    <footer className="mt-18 border-t border-separator-border bg-background-secondary-default pb-[env(safe-area-inset-bottom)]">

      <div className="mx-auto max-w-page px-6 py-12">
        <div className="footer-grid mb-10">
          <div>
            <div className="mb-3">
              <BrandWordmark size="sm" showDomain />
            </div>
            <p className="times mb-4 max-w-[34ch] text-body-regular text-text-secondary">
              Model picks and a live +EV scanner, with every result public.
            </p>
            <div className="flex items-center gap-2.5">
              <a href={DISCORD_URL} target="_blank" rel="noopener noreferrer" className={`${SOCIAL_BOX} text-[#8b92f0]`} aria-label="Join Discord">
                <DiscordMark size={18} color="currentColor" />
              </a>
              <a href={X_URL} target="_blank" rel="noopener noreferrer" className={`${SOCIAL_BOX} text-text-secondary`} aria-label="Follow Beezy.FYI on X">
                <XMark size={15} color="currentColor" />
              </a>
              <InstallAppButton />
            </div>
            {PRE_LAUNCH && (
              <div className="mt-6 max-w-[320px]">
                <div className="ui-heading mb-2 text-mini text-text-secondary">Launch waitlist</div>
                <WaitlistForm source="footer" label="Notify me" />
              </div>
            )}
          </div>
          {[
            { title: 'Picks',   links: [['Odds Terminal','/ev'],['Projections','/ev/projections'],['Prop Research','/edge'],['Top Signals','/picks'],['Graded History','/picks/history'],['Daily Card','/cheat-sheet'],['Results','/results'],['Analytics','/analytics']] },
            { title: 'Sports',  links: [['MLB','/picks/mlb'],['NFL','/sports/nfl'],['NHL','/sports/nhl'],['NBA','/sports/nba']] },
            { title: 'Tools',   links: [['Odds & Vig Calculator','/tools/odds-calculator'],['Kelly Calculator','/tools/kelly-calculator'],['Boost EV Calculator','/tools/boost-ev'],['Slate','/tools/slate']] },
            { title: 'Company', links: [['Models','/models'],['Results','/results'],['Pricing','/pricing'],['Learn','/learn'],['Contact',`mailto:${SITE_CONFIG.contactEmail}`]] },
            { title: 'Legal',   links: [['Terms of Service','/legal/terms'],['Privacy Policy','/legal/privacy'],['Responsible Gambling','/legal/responsible-gambling'],['Refund Policy','/legal/refunds'],['Support',`mailto:${SITE_CONFIG.supportEmail}`],[SITE_CONFIG.xHandle,SITE_CONFIG.xUrl]] },
          ].map(col => (
            <div key={col.title}>
              <div className="ui-heading mb-3.5 text-mini text-text-secondary">
                {col.title}
              </div>
              {col.links.map(([label, href]) => (
                <Link
                  key={href}
                  href={href}
                  className="times mb-[9px] block text-body-2-regular text-text-secondary no-underline transition-colors hover:text-text-primary"
                >
                  {label}
                </Link>
              ))}
            </div>
          ))}
        </div>

        <div className="border-t border-separator-border pt-5">
          <p className="times mb-1.5 text-caption-2-regular text-text-secondary">{SITE_CONFIG.riskDisclaimer} Plans and results are paper-tracked and hypothetical; no real money is wagered through beezy.fyi.</p>
          <p className="times mt-3.5 text-caption-1-regular text-text-secondary">
            &copy; <span suppressHydrationWarning>{new Date().getFullYear()}</span> beezy.fyi. Independent quantitative sports analytics.
          </p>
        </div>
      </div>
    </footer>
  )
}
