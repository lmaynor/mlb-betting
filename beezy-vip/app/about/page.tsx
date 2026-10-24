import type { Metadata } from 'next'
import { PLANS } from '@/lib/plans'
import { PRE_LAUNCH } from '@/lib/launch'
import { SITE_CONFIG } from '@/lib/site-config'

export const metadata: Metadata = {
  title: 'What is beezy.fyi?',
  description:
    'beezy.fyi is an independent quantitative sports betting analytics site: a +EV odds scanner, player prop projections and a public results ledger for NFL, NBA, NHL and MLB. Not affiliated with PicksBeezy, Breezy HR or beezy.net.',
  alternates: { canonical: '/about' },
}

export default function AboutPage() {
  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '32px 20px 48px' }}>
      <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#f3f4f6', margin: '0 0 16px' }}>What is beezy.fyi?</h1>
      <div className="article-body">
        <p>
          <strong>beezy.fyi</strong> is a quantitative sports betting analytics site covering the NFL, NBA, NHL and MLB. It
          publishes a live +EV odds scanner, model-based player prop projections and a public results ledger.{' '}
          {SITE_CONFIG.operatorStatement}
        </p>
        <p>
          beezy.fyi is a standalone site. It is <strong>not</strong> affiliated with PicksBeezy (beezypicks.com), Breezy HR,
          beezy.net or any scheduling or HR software. It is not a sportsbook and never accepts wagers.
        </p>

        <h2>What it does</h2>
        <ul>
          <li><strong>+EV odds scanner.</strong> Compares prices across 14+ sportsbooks plus Kalshi and Novig every 15 minutes and flags prices that sit off the market consensus.</li>
          <li><strong>Player prop projections.</strong> Models project minutes, snaps and time on ice, teammate-injury redistribution and full outcome distributions, rather than averaging recent box scores.</li>
          <li><strong>Public results ledger.</strong> Every flagged play is paper-tracked and graded on the <a href="/results">results page</a>, wins and losses alike. Paper results are simulations, not real stakes.</li>
        </ul>

        <h2>Pricing</h2>
        <ul>
          <li>Single-Sport Pass: ${PLANS.single.price} per month</li>
          <li>All-Access Pro: ${PLANS.monthly.price} per month</li>
          <li>All-Access Pro Annual: ${PLANS.annual.price} per year</li>
        </ul>
        <p>
          {PRE_LAUNCH ? 'beezy.fyi is pre-launch: join the waitlist on the ' : 'Details and checkout are on the '}
          <a href="/pricing">pricing page</a>{PRE_LAUNCH ? ' to lock these prices.' : '.'}
        </p>

        <p>See also: <a href="/compare/oddsjam-alternative">beezy.fyi compared with OddsJam and other tools</a>.</p>
      </div>
    </div>
  )
}
