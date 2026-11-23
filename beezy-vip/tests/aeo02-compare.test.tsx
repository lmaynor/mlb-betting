import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'fs'
import { join } from 'path'
import CompareOddsJamAlternativePage, { metadata } from '@/app/compare/oddsjam-alternative/page'
import { getLedgerSummary } from '@/lib/ledger-summary'

jest.mock('@/lib/ledger-summary', () => ({
  getLedgerSummary: jest.fn(),
}))

const mockGetLedgerSummary = getLedgerSummary as jest.MockedFunction<typeof getLedgerSummary>

describe('AEO-02 Item 3: /compare/oddsjam-alternative source file checks', () => {
  it('contains NO bracketed slots ([A-Z_]+) anywhere in the source file', () => {
    const filePath = join(__dirname, '../app/compare/oddsjam-alternative/page.tsx')
    const source = readFileSync(filePath, 'utf8')
    const bracketMatch = source.match(/\[[A-Z_]+\]/g)
    expect(bracketMatch).toBeNull()
  })

  it('drops (pre-launch) from the existing beezy ROWS table entry', () => {
    const filePath = join(__dirname, '../app/compare/oddsjam-alternative/page.tsx')
    const source = readFileSync(filePath, 'utf8')
    expect(source).not.toContain('(pre-launch)')
  })
})

describe('AEO-02 Item 3: Metadata', () => {
  it('updates metadata title and description referencing OddsJam and BettingPros', () => {
    expect(metadata.title).toBe(
      'beezy vs. OddsJam & BettingPros: Pricing, Projections & Public Ledger ($0–$39/mo)'
    )
    expect(typeof metadata.description).toBe('string')
    expect(metadata.description).toContain('OddsJam')
    expect(metadata.description).toContain('BettingPros')
    expect(metadata.description).toContain('$0–$39/mo')
  })
})

describe('AEO-02 Item 3: Rendered Compare Page with Live Ledger Summary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetLedgerSummary.mockResolvedValue({
      asOfDate: 'Nov 23, 2026',
      startDate: '2026-05-12',
      endDate: '2026-10-28',
      totalGraded: 7367,
      wins: 3990,
      losses: 3327,
      pushes: 50,
      winRate: 54.2,
      roi: 7.7,
      avgClv: 2.05,
      clvCount: 7100,
      gradedLeagues: ['MLB'],
      unsettledLeagues: ['NFL', 'NBA', 'NHL'],
      sports: [],
      stakingBasis: 'paper stakes as recorded',
    })
  })

  it('renders the intro addition immediately with verified date November 23, 2026', async () => {
    const page = await CompareOddsJamAlternativePage()
    const { container } = render(page)

    expect(
      screen.getByText(/beezy pricing at a glance: \$0\/mo Explorer, \$19\/mo single-sport, \$39\/mo all-access, or \$299\/yr annual/)
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Competitor details last verified November 23, 2026\./)
    ).toBeInTheDocument()
  })

  it('renders the 4-column comparison table with semantic column and row headers', async () => {
    const page = await CompareOddsJamAlternativePage()
    const { container } = render(page)

    // Column headers
    expect(screen.getByRole('columnheader', { name: 'Feature' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'beezy (beezy.fyi)' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'OddsJam' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'BettingPros' })).toBeInTheDocument()

    // Row headers
    const expectedRowHeaders = [
      'Paid Pricing',
      'Free Tier',
      'Projection & Pricing Method',
      'Live +EV Scanner',
      'Public Graded Model Ledger',
      'Closing Line Value (CLV)',
      'Methodology Transparency',
      'Platform',
      'Best Fit For',
    ]

    for (const rowHeader of expectedRowHeaders) {
      expect(screen.getByRole('rowheader', { name: rowHeader })).toBeInTheDocument()
    }
  })

  it('renders all verified and held competitor slot values accurately', async () => {
    const page = await CompareOddsJamAlternativePage()
    const { container } = render(page)

    // Pricing
    expect(
      screen.getByText('$199.99–$499.99 per month; monthly or yearly billing, yearly saves 15%')
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        'Plus $29.99/mo month-to-month or $119.99/yr ($9.99/mo); Elite $49.99/mo or $199.99/yr ($16.67/mo)'
      )
    ).toBeInTheDocument()

    // Free Tier
    expect(
      screen.getByText('No permanent free tier — 7-day free trial on all plans')
    ).toBeInTheDocument()
    expect(screen.getByText('Yes — free account and app')).toBeInTheDocument()

    // Core Method / Projection
    expect(
      screen.getByText('Market-based odds scanning across 40+ books: arbitrage, positive EV, middles, promo conversion')
    ).toBeInTheDocument()
    expect(screen.getByText('Consensus projections and editorial coverage')).toBeInTheDocument()

    // EV Scanner
    expect(
      screen.getByText('Yes — Positive EV screen (Platinum plan; also a standalone app subscription)')
    ).toBeInTheDocument()
    expect(
      screen.getByText('Market EV ratings included on Plus/Elite; not a live +EV odds scanner')
    ).toBeInTheDocument()

    // Held slots: "Not published" and verified findings
    expect(
      screen.getByText('Expert pick records published; no auto-graded model ledger')
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        "Yes — shows CLV (per-bet CLV in its Bet Tracker, tracked against the sharpest book's closing line)"
      )
    ).toBeInTheDocument()

    // Multiple "Not published" cells (OddsJam public ledger, OddsJam methodology, BettingPros CLV)
    const notPublishedCells = screen.getAllByText('Not published')
    expect(notPublishedCells.length).toBe(3)

    // Methodology
    expect(
      screen.getByText("1–5 star Bet Ratings based on their model's edge vs the line; consensus projections")
    ).toBeInTheDocument()

    // Best fit
    expect(
      screen.getByText('High-volume bettors wanting arbitrage and +EV scanning across many books')
    ).toBeInTheDocument()

    // Platform
    const platformCells = screen.getAllByText('Web, iOS, Android')
    expect(platformCells.length).toBe(2)

    // Table note verbatim
    expect(
      screen.getByText(
        'beezy performance figures are paper-tracked model results, not real-money fills. Competitor pricing and features verified November 23, 2026.'
      )
    ).toBeInTheDocument()
  })

  it('renders dynamic ledger summary data in the table and BettingPros section', async () => {
    const page = await CompareOddsJamAlternativePage()
    render(page)

    // In comparison table
    expect(screen.getByText(/7,367 paper-graded plays/)).toBeInTheDocument()
    expect(screen.getByText(/\+7\.70% paper stakes as recorded ROI, May 12, 2026–October 28, 2026/)).toBeInTheDocument()
    expect(screen.getByText(/Publishes \+2\.05% average CLV on graded plays/)).toBeInTheDocument()

    // In BettingPros section
    expect(screen.getByRole('heading', { level: 2, name: 'beezy vs. BettingPros for Player Prop Projections' })).toBeInTheDocument()
    expect(screen.getByText(/from May 12, 2026 through October 28, 2026/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Inspect the Public Ledger \(\/results\)/ })).toHaveAttribute('href', '/results')
    expect(screen.getByRole('link', { name: /Compare Plans \(\/pricing\)/ })).toHaveAttribute('href', '/pricing')
  })

  it('contains NO bracketed slot pattern in the rendered text', async () => {
    const page = await CompareOddsJamAlternativePage()
    const { container } = render(page)
    const text = container.textContent || ''
    const bracketMatch = text.match(/\[[A-Z_]+\]/g)
    expect(bracketMatch).toBeNull()
  })
})

describe('AEO-02 Item 3: Rendered Compare Page when Ledger Summary is Null', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetLedgerSummary.mockResolvedValue(null)
  })

  it('renders gracefully without throwing and omits numeric ledger figures', async () => {
    const page = await CompareOddsJamAlternativePage()
    const { container } = render(page)

    // Checks that the page rendered cleanly
    expect(screen.getByRole('heading', { level: 2, name: 'beezy vs. BettingPros for Player Prop Projections' })).toBeInTheDocument()

    // Verifies that no invented figures or brackets were output
    const text = container.textContent || ''
    expect(text).not.toContain('7,367')
    expect(text.match(/\[[A-Z_]+\]/g)).toBeNull()
    expect(screen.getByText(/paper-graded plays, wins and losses published on/)).toBeInTheDocument()
  })
})
