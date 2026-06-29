export const dynamic = 'force-dynamic'

import Link from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Free MLB Betting Tools',
  description: 'Odds calculator, Kelly criterion calculator, edge finder, NRFI conditions, and pitcher matchup dashboard.',
}

const B  = '1px solid var(--basalt)'
const BH = '1px solid var(--iron)'

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

const TAG: Record<string, { color: string; bg: string; border: string }> = {
  Free:    { color: 'var(--signal)', bg: '#1a2218', border: '1px solid #8e9e78' },
  Partial: { color: '#e6915d', bg: '#1c1207', border: '1px solid #a05d30' },
  Pro:     { color: '#8c9ae0', bg: '#0e0718', border: '1px solid #534ab7' },
}

export default function ToolsPage() {
  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      <div style={{ marginBottom: '32px' }}>
        <h1 className="dell-display" style={{ fontSize: '20px', color: 'var(--ash)', marginBottom: '6px' }}>Betting tools</h1>
        <p className="times" style={{ fontSize: '13px', color: 'var(--fog)' }}>Free calculators. Pro dashboards for members.</p>
      </div>

      <div className="tools-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)', border: B }}>
        {TOOLS.map((tool, i) => {
          const col = i % 3
          const t = TAG[tool.tag]
          return (
            <Link key={tool.href} href={tool.href} style={{
              display: 'block', padding: '18px', textDecoration: 'none', position: 'relative',
              background: 'var(--carbon)',
              borderRight:  col < 2 ? B : undefined,
              borderBottom: i < TOOLS.length - 3 ? B : undefined,
            }}>
              <span className="mono" style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 7px', color: t.color, background: t.bg, border: t.border, display: 'inline-block', marginBottom: '10px' }}>
                {tool.tag.toUpperCase()}
              </span>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--ash)', marginBottom: '6px' }}>{tool.title}</div>
              <div style={{ fontSize: '12px', color: 'var(--fog)', lineHeight: 1.55, marginBottom: '12px' }}>{tool.description}</div>
              <div className="mono" style={{ fontSize: '9px', color: 'var(--iron)' }}>{tool.keywords}</div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
