import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { StoryBlock } from '@/components/landing/story-block'
import { PricingSummary } from '@/components/landing/pricing-summary'
import { PricingSection } from '@/components/landing/pricing'
import { metadata as pricingMetadata } from '@/app/pricing/page'
import { HOMEPAGE_FRAMING, PRE_LAUNCH } from '@/lib/launch'
import type { LedgerSummary } from '@/lib/ledger-summary'

const fixtureSummary: LedgerSummary = {
  asOfDate: 'Nov 23, 2026',
  startDate: '2026-05-12',
  endDate: '2026-10-28',
  totalGraded: 7367,
  wins: 3990,
  losses: 3327,
  pushes: 50,
  winRate: 54.2,
  roi: 7.7,
  avgClv: 2.15,
  clvCount: 7259,
  gradedLeagues: ['MLB'],
  unsettledLeagues: ['NFL', 'NBA', 'NHL'],
  sports: [
    {
      sport: 'MLB',
      label: 'MLB',
      dateRange: 'May 12, 2026 \u2013 October 28, 2026',
      graded: 7367,
      wins: 3990,
      losses: 3327,
      pushes: 50,
      winRate: 54.2,
      roi: 7.7,
      avgClv: 2.15,
      status: 'Publicly settled',
    },
  ],
  stakingBasis: 'paper stakes as recorded',
}

describe('AEO-02 Item 2: launch flag contract', () => {
  it('HOMEPAGE_FRAMING defaults to A and PRE_LAUNCH remains true', () => {
    expect(HOMEPAGE_FRAMING).toBe('A')
    expect(PRE_LAUNCH).toBe(true)
  })
})

describe('AEO-02 Item 2: StoryBlock component', () => {
  describe('Variant A (default framing)', () => {
    it('renders shared heading, populated ledger figures, plans line, and Variant A CTA links', () => {
      render(<StoryBlock summary={fixtureSummary} />)

      const block = screen.getByTestId('story-block')

      // Heading as h2
      const heading = screen.getByRole('heading', { level: 2, name: /Quantitative sports betting projections, \+EV scanning, and a public graded ledger/i })
      expect(heading).toBeInTheDocument()

      // Shared body paragraph with slots filled
      expect(block).toHaveTextContent(/Across NFL, NBA, NHL and MLB, beezy publishes its model outputs and grades every flagged play automatically on a public paper ledger/i)
      expect(block).toHaveTextContent(/7,367 graded plays at \+7.70% paper stakes as recorded Return on Investment \(ROI\) from May 12, 2026 through October 28, 2026, paper-tracked, wins and losses alike\./i)

      // Plans line verbatim
      expect(block).toHaveTextContent(/Plans are \$0\/mo Explorer, \$19\/mo single-sport, \$39\/mo all-access, and \$299\/yr annual\./i)

      // Variant A closing sentence
      expect(block).toHaveTextContent(/Get started immediately on the free \$0 Explorer tier, or subscribe to a paid plan for full projections, \+EV boards, and annual Closing Line Value \(CLV\) data exports\./i)
      expect(block).not.toHaveTextContent(/beezy is currently in pre-launch/i)

      // Variant A CTA links
      const pricingLink = screen.getByRole('link', { name: /See Plans \(\$0–\$39\/mo\) →/i })
      expect(pricingLink).toHaveAttribute('href', '/pricing')

      const ledgerLink = screen.getByRole('link', { name: /Public Ledger \(7,367 graded\) →/i })
      expect(ledgerLink).toHaveAttribute('href', '/results')

      const methodologyLink = screen.getByRole('link', { name: /Methodology →/i })
      expect(methodologyLink).toHaveAttribute('href', '/models')

      // Variant B CTA not present
      expect(screen.queryByRole('link', { name: /Join the Waitlist →/i })).not.toBeInTheDocument()
    })
  })

  describe('Variant B (waitlist framing)', () => {
    it('renders Variant B closing sentence and waitlist CTA when framing="B"', () => {
      render(<StoryBlock summary={fixtureSummary} framing="B" />)

      const block = screen.getByTestId('story-block')

      // Heading and body with slots still present
      expect(screen.getByRole('heading', { level: 2, name: /Quantitative sports betting projections/i })).toBeInTheDocument()
      expect(block).toHaveTextContent(/7,367 graded plays at \+7.70%/i)

      // Variant B closing sentence
      expect(block).toHaveTextContent(/beezy is currently in pre-launch: use the free \$0 Explorer boards now and join the waitlist to lock in launch pricing when paid tiers open\./i)
      expect(block).not.toHaveTextContent(/Get started immediately on the free \$0 Explorer tier/i)

      // Variant B CTA links
      const waitlistLink = screen.getByRole('link', { name: /Join the Waitlist →/i })
      expect(waitlistLink).toHaveAttribute('href', '/signup')

      const ledgerLink = screen.getByRole('link', { name: /Public Ledger \(7,367 graded\) →/i })
      expect(ledgerLink).toHaveAttribute('href', '/results')

      const methodologyLink = screen.getByRole('link', { name: /Methodology →/i })
      expect(methodologyLink).toHaveAttribute('href', '/models')

      // Variant A CTA not present
      expect(screen.queryByRole('link', { name: /See Plans/i })).not.toBeInTheDocument()
    })
  })

  describe('null summary fallback', () => {
    it('renders cleanly without ledger figures clause when summary is null', () => {
      render(<StoryBlock summary={null} />)

      const block = screen.getByTestId('story-block')

      // Heading and intro present
      expect(screen.getByRole('heading', { level: 2, name: /Quantitative sports betting projections/i })).toBeInTheDocument()
      expect(block).toHaveTextContent(/Across NFL, NBA, NHL and MLB, beezy publishes its model outputs and grades every flagged play automatically on a public paper ledger\. Plans are \$0\/mo Explorer, \$19\/mo single-sport, \$39\/mo all-access, and \$299\/yr annual\./i)

      // No figures clause
      expect(block).not.toHaveTextContent(/graded plays at/i)
      expect(block).not.toHaveTextContent(/Return on Investment/i)

      // Ledger link falls back to "Public Ledger →" without bet count
      const ledgerLink = screen.getByRole('link', { name: /^Public Ledger →$/i })
      expect(ledgerLink).toHaveAttribute('href', '/results')

      // Other links and Variant A copy still present
      expect(screen.getByRole('link', { name: /See Plans \(\$0–\$39\/mo\) →/i })).toHaveAttribute('href', '/pricing')
      expect(screen.getByRole('link', { name: /Methodology →/i })).toHaveAttribute('href', '/models')
    })
  })
})

describe('AEO-02 Item 2: visible banner reconciliation', () => {
  it('PricingSummary does not render the pre-launch footnote', () => {
    render(<PricingSummary />)
    expect(screen.queryByText(/Pre-launch: paid plans open after the 200-bet gate\. Prices lock at launch\./i)).not.toBeInTheDocument()
  })

  it('PricingSection renders "Cancel anytime in 1 click." header text even when PRE_LAUNCH is true', () => {
    render(<PricingSection />)
    expect(screen.getByText('Cancel anytime in 1 click.')).toBeInTheDocument()
    expect(screen.queryByText(/Pre-launch: join the waitlist\. Prices lock at launch\./i)).not.toBeInTheDocument()
  })

  it('app/pricing metadata description drops the pre-launch sentence but keeps plan prices', () => {
    expect(pricingMetadata.description).toBe(
      'beezy.fyi pricing: Single-Sport Pass $19/mo, All-Access Pro $39/mo, All-Access Pro Annual $299/yr.'
    )
    expect(pricingMetadata.description).not.toMatch(/Pre-launch: join the waitlist/i)
  })
})
