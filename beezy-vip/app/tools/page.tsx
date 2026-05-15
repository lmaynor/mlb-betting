import Link          from 'next/link'
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title:       'Free MLB Betting Tools',
  description: 'Odds calculator, Kelly criterion calculator, edge finder, NRFI conditions, and pitcher matchup dashboard. Free MLB betting tools.',
}

const TOOLS = [
  {
    href:        '/tools/odds-calculator',
    title:       'Odds & Vig Calculator',
    description: 'Input American odds for both sides. Get implied probability, vig percentage, and fair value odds instantly.',
    tag:         'Free',
    keywords:    'implied probability · vig removal · fair odds',
  },
  {
    href:        '/tools/kelly-calculator',
    title:       'Kelly Criterion Calculator',
    description: 'Enter your bankroll, the line, and your estimated win probability. Get full and half Kelly stake sizes.',
    tag:         'Free',
    keywords:    'bankroll management · stake sizing · Kelly %',
  },
  {
    href:        '/tools/nrfi-conditions',
    title:       'NRFI Conditions Dashboard',
    description: "Today's full MLB slate with starter ERA, umpire K-rate, weather, and park factors. Model probability for members.",
    tag:         'Partial',
    keywords:    'nrfi conditions today · first inning betting',
  },
  {
    href:        '/tools/pitcher-matchups',
    title:       'Pitcher Matchup Dashboard',
    description: "Today's starters with SwStr%, zone rate, and opponent K%. Beezy strikeout projection for Pro members.",
    tag:         'Partial',
    keywords:    'mlb strikeout props today · pitcher matchups',
  },
  {
    href:        '/tools/edge-finder',
    title:       'Edge Finder',
    description: 'Input the line you see at your book. Get implied probability and — for Pro members — the Beezy model number.',
    tag:         'Partial',
    keywords:    'sports betting edge · model vs market',
  },
  {
    href:        '/tools/bet-tracker',
    title:       'Personal Bet Tracker',
    description: 'Log your own bets, track ROI, and compare your performance against the Beezy model. Members only.',
    tag:         'Pro',
    keywords:    'bet tracking · personal ROI · P&L',
  },
]

const TAG_STYLES: Record<string, string> = {
  Free:    'text-win   border-win/30   bg-win/5',
  Partial: 'text-[#FFB800] border-[#FFB800]/30 bg-[#FFB800]/5',
  Pro:     'text-[#BF5FFF] border-[#BF5FFF]/30 bg-[#BF5FFF]/5',
}

export default function ToolsPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 py-12">
      <div className="mb-12">
        <h1 className="text-2xl font-extrabold uppercase tracking-tight mb-2">Betting Tools</h1>
        <p className="mono text-sm text-muted">
          Free tools for sharp bettors. No account required for calculators.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {TOOLS.map(tool => (
          <Link
            key={tool.href}
            href={tool.href}
            className="group block p-6 bg-[var(--surface)] border border-[var(--border)] hover:border-[var(--border-bright)] transition-colors"
          >
            <div className="flex items-start justify-between mb-4">
              <span className={`mono text-xs font-semibold px-2 py-0.5 border tracking-widest ${TAG_STYLES[tool.tag]}`}>
                {tool.tag}
              </span>
            </div>
            <h2 className="font-bold text-sm mb-2 group-hover:text-accent transition-colors">
              {tool.title}
            </h2>
            <p className="text-muted text-xs leading-relaxed mb-4">
              {tool.description}
            </p>
            <p className="mono text-xs text-[var(--border-bright)]">{tool.keywords}</p>
          </Link>
        ))}
      </div>
    </div>
  )
}
