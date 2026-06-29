export const dynamic = 'force-dynamic'

import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Free MLB Betting Tools',
  description: 'Odds calculator, Kelly criterion calculator, edge finder, NRFI conditions, and pitcher matchup dashboard.',
}

const TOOLS = [
  { href: '/tools/clv-tracker',   title: 'CLV + Edge Tracker',           description: 'Each pick plotted by model edge vs. closing line value. Positive CLV in the top-right proves the model finds real inefficiencies.', tag: 'Pro',     keywords: 'closing line value · model edge · clv scatter' },
  { href: '/tools/slate',         title: 'Slate Command Center',         description: "Today's full MLB slate with every Beezy pick, starters, and start times. One screen covers everything.",                            tag: 'Pro',     keywords: 'todays mlb slate · picks dashboard · command center' },
  { href: '/tools/odds-calculator',  title: 'Odds & Vig Calculator',      description: 'Input American odds for both sides. Get implied probability, vig percentage, and fair value odds instantly.', tag: 'Free',    keywords: 'implied probability · vig removal · fair odds' },
  { href: '/tools/kelly-calculator', title: 'Kelly Criterion Calculator',  description: 'Enter your bankroll, the line, and your estimated win probability. Get full and half Kelly stake sizes.',        tag: 'Free',    keywords: 'bankroll management · stake sizing · Kelly %' },
  { href: '/tools/nrfi-conditions',  title: 'NRFI Conditions Dashboard',   description: "Today's full MLB slate with starter ERA, umpire K-rate, weather, and park factors. Model probability for members.", tag: 'Pro',     keywords: 'nrfi conditions today · first inning betting' },
  { href: '/tools/pitcher-matchups', title: 'Pitcher Matchup Dashboard',   description: "Today's starters with SwStr%, zone rate, and opponent K%. Beezy strikeout projection for Pro members.",         tag: 'Pro',     keywords: 'mlb strikeout props today · pitcher matchups' },
  { href: '/tools/edge-finder',      title: 'Edge Finder',                 description: 'Input the line you see at your book. Get implied probability and -- for Pro members -- the Beezy model number.',    tag: 'Partial', keywords: 'sports betting edge · model vs market' },
  { href: '/tools/bet-tracker',      title: 'Personal Bet Tracker',        description: 'Log your own bets, track ROI, and compare your performance against the Beezy model. Members only.',              tag: 'Pro',     keywords: 'bet tracking · personal ROI · P&L' },
]

const TAG: Record<string, { color: string }> = {
  Free:    { color: 'var(--signal)' },
  Partial: { color: 'var(--warn)' },
  Pro:     { color: 'var(--lilac)' },
}

export default function ToolsPage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 24px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 className="dell-display" style={{ fontSize: '32px', color: 'var(--chalk)', marginBottom: '8px' }}>Betting tools</h1>
        <p className="times" style={{ fontSize: '15px', color: 'var(--fog)' }}>Free calculators. Pro dashboards for members.</p>
      </div>

      <div className="tools-grid">
        {TOOLS.map((tool) => {
          const c = (TAG[tool.tag] ?? TAG.Free).color
          return (
            <Link key={tool.href} href={tool.href} className="card-hover" style={{
              display: 'flex', flexDirection: 'column', padding: '20px', textDecoration: 'none',
              background: 'var(--graphite)', border: '1px solid var(--basalt)', borderRadius: 'var(--radius-lg)',
            }}>
              <span className="dell-heading" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 9px', borderRadius: 'var(--radius-pill)', color: c, background: `color-mix(in oklab, ${c} 15%, var(--carbon))`, border: `1px solid color-mix(in oklab, ${c} 40%, var(--carbon))`, display: 'inline-block', alignSelf: 'flex-start', marginBottom: '14px' }}>
                {tool.tag.toUpperCase()}
              </span>
              <div className="dell-display" style={{ fontSize: '17px', color: 'var(--chalk)', marginBottom: '8px', letterSpacing: '-0.01em' }}>{tool.title}</div>
              <div className="times" style={{ fontSize: '13px', color: 'var(--silver)', lineHeight: 1.55, marginBottom: '14px', flex: 1 }}>{tool.description}</div>
              <div className="mono" style={{ fontSize: '9px', color: 'var(--fog)', letterSpacing: '0.02em' }}>{tool.keywords}</div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
