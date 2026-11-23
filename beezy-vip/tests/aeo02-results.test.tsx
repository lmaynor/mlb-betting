import '@testing-library/jest-dom'
import { render, screen, fireEvent } from '@testing-library/react'
import { LedgerSummaryBlock } from '@/app/results/ledger-summary-block'
import { ResultsClient } from '@/app/results/results-client'
import ResultsPage from '@/app/results/page'
import { getLedgerSummary, buildLedgerSummary, type LedgerSummary } from '@/lib/ledger-summary'
import { apiGetStats, apiGetAnalyticsDeepDive, apiGetCLVData, apiGetPicks } from '@/lib/betting-api'
import type { Bet } from '@/lib/types'

let mockParams = new URLSearchParams()
const mockReplace = jest.fn()

jest.mock('next/navigation', () => ({
  useSearchParams: () => mockParams,
  useRouter: () => ({ push: jest.fn(), replace: mockReplace }),
  usePathname: () => '/results',
}))

jest.mock('@/lib/betting-api', () => ({
  apiGetStats: jest.fn(),
  apiGetAnalyticsDeepDive: jest.fn(),
  apiGetCLVData: jest.fn(),
  apiGetPicks: jest.fn().mockResolvedValue([]),
}))

const mockApiGetStats = apiGetStats as jest.MockedFunction<typeof apiGetStats>
const mockApiGetAnalyticsDeepDive = apiGetAnalyticsDeepDive as jest.MockedFunction<typeof apiGetAnalyticsDeepDive>
const mockApiGetCLVData = apiGetCLVData as jest.MockedFunction<typeof apiGetCLVData>

describe('AEO-02 Item 1: getLedgerSummary aggregation math', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('aggregates totals, W/L/P sums, CLV mean, and date-range min/max correctly', async () => {
    mockApiGetStats.mockResolvedValueOnce({
      overall: {
        total_bets: '7367',
        win_rate: '54.2',
        roi: '7.70',
        avg_edge: '0.045',
      },
      bySystem: [
        { system: 'NRFI', total_bets: 2000, wins: 1100, losses: 880, pushes: 20, win_rate: 55.5, roi: 8.5, total_pnl: 1700, avg_edge: 0.05 },
        { system: 'F5', total_bets: 2500, wins: 1350, losses: 1130, pushes: 20, win_rate: 54.4, roi: 7.2, total_pnl: 1800, avg_edge: 0.04 },
        { system: 'K', total_bets: 2867, wins: 1540, losses: 1317, pushes: 10, win_rate: 53.9, roi: 7.5, total_pnl: 2150, avg_edge: 0.04 },
      ],
    })

    mockApiGetAnalyticsDeepDive.mockResolvedValueOnce({
      edge_buckets: [],
      book_leaderboard: [],
      system: 'EV',
      bankroll_curve: [
        { date: '2026-05-12', ev_flat_pnl: 10, ev_kelly_pnl: 15, models_pnl: 20, ev_flat_cum: 10, ev_kelly_cum: 15, models_cum: 20 },
        { date: '2026-08-01', ev_flat_pnl: 5, ev_kelly_pnl: 8, models_pnl: 12, ev_flat_cum: 15, ev_kelly_cum: 23, models_cum: 32 },
        { date: '2026-10-28', ev_flat_pnl: 0, ev_kelly_pnl: 0, models_pnl: 0, ev_flat_cum: 15, ev_kelly_cum: 23, models_cum: 32 },
      ],
      ev_headline: {
        logged: 100, graded: 90, wins: 50, losses: 40, pushes: 0, voids: 0, pending: 10,
        first_date: '2026-05-15',
        last_date: '2026-10-25',
        flat_stake_unit: 100, flat_roi_pct: 7.7, flat_pnl_dollars: 770,
        bankroll: 1000, kelly_mult: 0.5, kelly_cap: 0.05,
        kelly_roi_pct: 8.2, kelly_pnl_dollars: 820, kelly_ending_bankroll: 1820,
        unique_props_graded: 80, voids_excluded_from_roi: true,
      },
    })

    // CLV rows with mixed numeric and string values, and unparseable ones
    mockApiGetCLVData.mockResolvedValueOnce([
      { clv_pct: 1.5 } as any,
      { clv_pct: '2.5' } as any,
      { clv_pct: null } as any,
      { clv_pct: 'invalid' } as any,
      { clv_pct: 2.0 } as any,
    ])

    const summary = await getLedgerSummary()

    expect(summary).not.toBeNull()
    expect(summary!.totalGraded).toBe(7367)
    expect(summary!.winRate).toBe(54.2)
    expect(summary!.roi).toBe(7.70)
    expect(summary!.wins).toBe(3990)
    expect(summary!.losses).toBe(3327)
    expect(summary!.pushes).toBe(50)
    // CLV mean of [1.5, 2.5, 2.0] = 6.0 / 3 = 2.0
    expect(summary!.avgClv).toBe(2.0)
    expect(summary!.clvCount).toBe(3)
    // Date min: earliest of '2026-05-15' and '2026-05-12' = '2026-05-12'
    expect(summary!.startDate).toBe('2026-05-12')
    // Date max: latest of '2026-10-25' and '2026-10-28' = '2026-10-28'
    expect(summary!.endDate).toBe('2026-10-28')
    expect(summary!.sports).toHaveLength(1)
    expect(summary!.sports[0].sport).toBe('MLB')
    expect(summary!.sports[0].dateRange).toBe('May 12, 2026 \u2013 October 28, 2026')
    expect(summary!.sports[0].status).toBe('Publicly settled')
    expect(summary!.stakingBasis).toBe('paper stakes as recorded')
    expect(summary!.gradedLeagues).toEqual(['MLB'])
    expect(summary!.unsettledLeagues).toEqual(['NFL', 'NBA', 'NHL'])
  })

  it('returns null if apiGetStats fails', async () => {
    mockApiGetStats.mockRejectedValueOnce(new Error('Stats service unavailable'))
    mockApiGetAnalyticsDeepDive.mockResolvedValueOnce({} as any)
    mockApiGetCLVData.mockResolvedValueOnce([])

    const summary = await getLedgerSummary()
    expect(summary).toBeNull()
  })

  it('handles deep dive and CLV failures gracefully without throwing', async () => {
    mockApiGetStats.mockResolvedValueOnce({
      overall: { total_bets: '100', win_rate: '50.0', roi: '-2.5', avg_edge: '0.01' },
      bySystem: [{ system: 'K', total_bets: 100, wins: 50, losses: 50, pushes: 0, win_rate: 50.0, roi: -2.5, total_pnl: -250, avg_edge: 0.01 }],
    })
    mockApiGetAnalyticsDeepDive.mockRejectedValueOnce(new Error('Deep dive unavailable'))
    mockApiGetCLVData.mockRejectedValueOnce(new Error('CLV unavailable'))

    const summary = await getLedgerSummary()
    expect(summary).not.toBeNull()
    expect(summary!.totalGraded).toBe(100)
    expect(summary!.winRate).toBe(50.0)
    expect(summary!.roi).toBe(-2.5)
    expect(summary!.startDate).toBeNull()
    expect(summary!.endDate).toBeNull()
    expect(summary!.avgClv).toBeNull()
    expect(summary!.clvCount).toBe(0)
    expect(summary!.sports[0].dateRange).toBe('')
  })
})

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

describe('AEO-02 Item 1: LedgerSummaryBlock component', () => {
  it('renders nothing when summary is null', () => {
    const { container } = render(<LedgerSummaryBlock summary={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders headline figures, verbatim copy, table, and methodology link', () => {
    render(<LedgerSummaryBlock summary={fixtureSummary} />)

    const block = screen.getByTestId('ledger-summary-block')

    // Heading and subtitle
    expect(screen.getByRole('heading', { level: 2, name: /Public Paper-Tracked Ledger/i })).toBeInTheDocument()
    expect(block).toHaveTextContent(/As of Nov 23, 2026 · Paper record \(paper stakes as recorded\)/i)

    // Headline paragraph
    expect(block).toHaveTextContent(/From May 12, 2026 through October 28, 2026, beezy has published and graded 7,367 paper plays across MLB/i)
    expect(block).toHaveTextContent(/recording 3,990–3,327–50 \(54.2% win rate\), \+7.70% paper stakes as recorded Return on Investment \(ROI\), and \+2.15% average Closing Line Value \(CLV\)\./i)

    // Verification copy verbatim
    expect(block).toHaveTextContent(/How this record is verified: Every flagged play is timestamped and published before game time, then graded automatically against final results—wins and losses alike, with losing systems included and no picks edited or deleted after the fact\./i)

    // Semantic table
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^Sport$/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /ROI \(paper stakes as recorded, paper\)/i })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^Avg CLV$/ })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: /^Status$/ })).toBeInTheDocument()

    // MLB row and All Publicly Settled row
    expect(screen.getByRole('cell', { name: 'MLB' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'All Publicly Settled' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Paper-tracked' })).toBeInTheDocument()

    // Scope and honesty note with unsettled sports list and CLV source
    expect(block).toHaveTextContent(/Leagues still in preview or research \(NFL, NBA and NHL\) are excluded from public totals until promoted\./i)
    expect(block).toHaveTextContent(/Closing Line Value compares each play's published price against the closing price, averaged over the 7,259 graded plays with a recorded closing line;/i)

    // /models methodology link line verbatim
    expect(block).toHaveTextContent(/Read full walk-forward methodology, out-of-sample validation, and per-system results on/i)
    const link = screen.getByRole('link', { name: /\/models →/i })
    expect(link).toHaveAttribute('href', '/models')
  })

  it('omits CLV clause when avgClv is null and displays dash in table', () => {
    const summaryNoClv: LedgerSummary = {
      ...fixtureSummary,
      avgClv: null,
      clvCount: 0,
      sports: [
        {
          ...fixtureSummary.sports[0],
          avgClv: null,
        },
      ],
    }

    render(<LedgerSummaryBlock summary={summaryNoClv} />)

    // Headline paragraph should NOT have CLV clause
    expect(screen.queryByText(/average Closing Line Value/i)).not.toBeInTheDocument()
    expect(screen.getByText(/Return on Investment \(ROI\)\./i)).toBeInTheDocument()

    // Scope note should fall back to "the closing price" without average count
    expect(screen.getByText(/Closing Line Value compares each play's published price against the closing price; beezy tracks CLV/i)).toBeInTheDocument()

    // Table should render dashes for Avg CLV
    const dashCells = screen.getAllByRole('cell', { name: '—' })
    expect(dashCells.length).toBeGreaterThanOrEqual(2) // sport row + total row
  })
})

const testBet: Bet = {
  id: 1, system: 'K', game_date: '2026-10-16', game_pk: 100, bet_type: 'K_OVER_5.5', player: 'Gerrit Cole',
  away_team: 'NYY', home_team: 'BOS', odds: 110, stake: 25, model_prob: 0.58, market_prob: 0.5, edge: 0.08,
  kelly_pct: 0.02, kelly_triggered: true, result: 'win', profit: 27.5, paper: true, book: 'draftkings', notes: null,
  created_at: '2026-10-16T16:00:00Z',
}

describe('AEO-02 Item 1: ResultsClient empty-filter fallback', () => {
  it('does not render LedgerSummaryBlock inside ResultsClient', () => {
    mockParams = new URLSearchParams()
    render(
      <ResultsClient
        initialPicks={[testBet]}
        initialStats={[]}
        summary={fixtureSummary}
      />
    )
    expect(screen.queryByTestId('ledger-summary-block')).not.toBeInTheDocument()
  })

  it('renders Neon empty-filter fallback copy with reset button when filtered bets are empty', () => {
    mockParams = new URLSearchParams()
    render(
      <ResultsClient
        initialPicks={[testBet]}
        initialStats={[]}
        summary={fixtureSummary}
      />
    )

    // Select a filter that has no matching rows (e.g. filter by system "NRFI")
    const nrfiBtn = screen.getByRole('button', { name: /NRFI/i })
    fireEvent.click(nrfiBtn)

    // Fallback copy should appear
    expect(screen.getByText(/No graded plays match the active filter \(NRFI\)\. Showing 0 filtered rows out of 7,367 total graded plays\./i)).toBeInTheDocument()

    // Reset button should be visible
    const resetBtn = screen.getByRole('button', { name: /Reset filters to All Time/i })
    expect(resetBtn).toBeInTheDocument()

    // Clicking reset button restores rows
    fireEvent.click(resetBtn)
    expect(screen.queryAllByText(/Gerrit Cole/i).length).toBeGreaterThan(0)
  })
})

describe('AEO-02 Item 1: ResultsPage server component SSR prerender', () => {
  it('renders LedgerSummaryBlock in the server component outside Suspense', async () => {
    mockApiGetStats.mockResolvedValue({
      overall: { total_bets: '7367', win_rate: '54.2', roi: '7.70', avg_edge: '0.045' },
      bySystem: [{ system: 'NRFI', total_bets: 7367, wins: 3990, losses: 3327, pushes: 50, win_rate: 54.2, roi: 7.7, total_pnl: 1000, avg_edge: 0.05 }],
    })
    mockApiGetAnalyticsDeepDive.mockResolvedValue({
      bankroll_curve: [{ date: '2026-05-12' }],
      ev_headline: { first_date: '2026-05-12', last_date: '2026-10-28' },
    } as any)
    mockApiGetCLVData.mockResolvedValue([{ clv_pct: 2.15 }] as any)

    const pageElement = await ResultsPage()
    render(pageElement)

    expect(screen.getByTestId('ledger-summary-block')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: /Public Paper-Tracked Ledger/i })).toBeInTheDocument()
  })
})
