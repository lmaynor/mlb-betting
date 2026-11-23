import '@testing-library/jest-dom'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'fs'
import { join } from 'path'
import { GamesHeader, formatLinesAge } from '@/components/picks/games-header'
import type { LedgerSummary } from '@/lib/ledger-summary'
import { buildSitemapXml, sitemapEntries } from '@/lib/sitemap'

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

describe('AEO-02 Item 5: /picks/games sitemap entry', () => {
  it("sitemapEntries() contains '/picks/games' without a lastmod", () => {
    const entries = sitemapEntries()
    const gamePicks = entries.find(e => e.path === '/picks/games')
    expect(gamePicks).toBeDefined()
    expect(gamePicks!.path).toBe('/picks/games')
    expect(gamePicks!.lastmod).toBeUndefined()
  })

  it('buildSitemapXml() includes the canonical /picks/games loc without a lastmod tag', () => {
    const xml = buildSitemapXml()
    expect(xml).toContain('<loc>https://beezy.fyi/picks/games</loc>')
    const picksGamesBlock = xml.split('<url>').find(u => u.includes('https://beezy.fyi/picks/games'))
    expect(picksGamesBlock).toBeDefined()
    expect(picksGamesBlock).not.toContain('<lastmod>')
  })
})

describe('AEO-02 Item 5: /picks/games metadata & robots', () => {
  it('page source no longer contains index: false', () => {
    const pageSource = readFileSync(join(__dirname, '../app/picks/games/page.tsx'), 'utf8')
    expect(pageSource).not.toContain('index: false')
    expect(pageSource).not.toContain('follow: false')
    expect(pageSource).not.toContain('robots:')
  })
})

describe('AEO-02 Item 5: GamesHeader component', () => {
  it('renders heading, date line, publish time, Unvalidated explainer, and ledger line from fixture summary', () => {
    render(<GamesHeader summary={fixtureSummary} date="2026-11-23" />)

    const header = screen.getByTestId('games-header')

    // Heading: h2 by default, contains long date
    const heading = screen.getByRole('heading', { level: 2 })
    expect(heading).toHaveTextContent(/Daily Model Game Projections — November 23, 2026/i)

    // Date line: short date, 11:00 AM CT publish time, no updated-at fragment
    expect(header).toHaveTextContent(/Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT/i)
    expect(header).not.toHaveTextContent(/Lines updated:/i)

    // Explainer paragraph: mentions active slates and Unvalidated disclosure
    expect(header).toHaveTextContent(/This board compares beezy's quantitative game probabilities against current sportsbook prices across today's active slates \(NBA and NHL\), listing matchups where the model price sits above the market/i)
    expect(header).toHaveTextContent(/Rows tagged "Unvalidated" come from leagues or models running live for research and preview that are not yet settled in beezy's public results ledger—see Methodology \(\/models\) for each sport's current validation status/i)

    // Link to /models
    const modelsLink = screen.getByRole('link', { name: /\/models/i })
    expect(modelsLink).toHaveAttribute('href', '/models')

    // Ledger record line: verbatim from summary
    const ledgerLine = screen.getByTestId('games-header-ledger-record')
    expect(ledgerLine).toHaveTextContent(
      /Public ledger record \(MLB settled systems, May 12, 2026 – October 28, 2026\): 7,367 paper-graded plays at \+7\.7% paper stakes as recorded ROI and \+2\.15% average CLV, wins and losses included \(View full ledger on \/results →\)\./i,
    )

    // Link to /results
    const resultsLink = screen.getByRole('link', { name: /\/results →/i })
    expect(resultsLink).toHaveAttribute('href', '/results')

    // Reading the table explainer
    expect(header).toHaveTextContent(
      /Reading the table: Model Side is the side beezy prices above the market; Model Prob is beezy's estimated win probability; Gap is the difference between model probability and implied market probability; Best Price is the strongest available sportsbook odds scanned\./i,
    )
  })

  it('omits the ledger line for a null summary while rendering descriptive copy', () => {
    render(<GamesHeader summary={null} date="2026-11-23" />)

    const header = screen.getByTestId('games-header')

    // Heading and date line still present
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/Daily Model Game Projections — November 23, 2026/i)
    expect(header).toHaveTextContent(/Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT/i)

    // Explainer and reading the table still present
    expect(header).toHaveTextContent(/This board compares beezy's quantitative game probabilities/i)
    expect(header).toHaveTextContent(/Rows tagged "Unvalidated"/i)
    expect(header).toHaveTextContent(/Reading the table:/i)

    // Ledger record line is omitted
    expect(screen.queryByTestId('games-header-ledger-record')).toBeNull()
    expect(header).not.toHaveTextContent(/Public ledger record/i)
  })

  it('omits the avg CLV clause when avgClv is null', () => {
    const summaryNoClv: LedgerSummary = {
      ...fixtureSummary,
      avgClv: null,
      clvCount: 0,
    }
    render(<GamesHeader summary={summaryNoClv} date="2026-11-23" />)

    const ledgerLine = screen.getByTestId('games-header-ledger-record')
    expect(ledgerLine).toHaveTextContent(/7,367 paper-graded plays at \+7\.7% paper stakes as recorded ROI, wins and losses included/i)
    expect(ledgerLine).not.toHaveTextContent(/average CLV/i)
  })

  it('renders lines updated fragment when linesAgeMinutes is a non-null number (< 60m and >= 60m)', () => {
    // Under 60 minutes: {n}m ago
    const { rerender } = render(<GamesHeader summary={fixtureSummary} date="2026-11-23" linesAgeMinutes={22} />)
    const header = screen.getByTestId('games-header')
    expect(header).toHaveTextContent('Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT · Lines updated: 22m ago')

    // 60 minutes or more with remainder: {h}h {m}m ago
    rerender(<GamesHeader summary={fixtureSummary} date="2026-11-23" linesAgeMinutes={75} />)
    expect(header).toHaveTextContent('Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT · Lines updated: 1h 15m ago')

    // 60 minutes or more with 0 remainder: {h}h ago
    rerender(<GamesHeader summary={fixtureSummary} date="2026-11-23" linesAgeMinutes={60} />)
    expect(header).toHaveTextContent('Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT · Lines updated: 1h ago')

    // 120 minutes: 2h ago
    rerender(<GamesHeader summary={fixtureSummary} date="2026-11-23" linesAgeMinutes={120} />)
    expect(header).toHaveTextContent('Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT · Lines updated: 2h ago')
  })

  it('keeps the lines updated fragment dropped when linesAgeMinutes is null', () => {
    render(<GamesHeader summary={fixtureSummary} date="2026-11-23" linesAgeMinutes={null} />)
    const header = screen.getByTestId('games-header')
    expect(header).toHaveTextContent('Date: Nov 23, 2026 · Daily card publishes: 11:00 AM CT')
    expect(header).not.toHaveTextContent(/Lines updated:/i)
  })
})

describe('formatLinesAge helper', () => {
  it.each([
    [0, '0m ago'],
    [1, '1m ago'],
    [5, '5m ago'],
    [22, '22m ago'],
    [59, '59m ago'],
    [60, '1h ago'],
    [61, '1h 1m ago'],
    [75, '1h 15m ago'],
    [119, '1h 59m ago'],
    [120, '2h ago'],
    [125, '2h 5m ago'],
    [180, '3h ago'],
  ])('formats %d minutes as "%s"', (mins, expected) => {
    expect(formatLinesAge(mins)).toBe(expected)
  })
})
