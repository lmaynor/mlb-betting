import { apiGetStats, apiGetAnalyticsDeepDive, apiGetCLVData, type StatsResponse } from '@/lib/betting-api'
import type { AnalyticsDeepDive, CLVDataPoint } from '@/lib/types'
import { asOfLabel } from '@/lib/seo'
import { formatDateKey } from '@/lib/dates'

export interface LedgerSportRow {
  sport: string            // 'MLB'
  label: string            // 'MLB'
  dateRange: string        // 'May 12, 2026 – October 28, 2026' style, from real dates
  graded: number
  wins: number
  losses: number
  pushes: number
  winRate: number | null   // percent, 1dp as the API returns
  roi: number | null       // percent, signed, 2dp as the API returns
  avgClv: number | null    // percent, signed
  status: string           // 'Publicly settled'
}

export interface LedgerSummary {
  asOfDate: string                 // lib/seo asOfLabel()
  startDate: string | null         // ISO
  endDate: string | null           // ISO
  totalGraded: number
  wins: number
  losses: number
  pushes: number
  winRate: number | null
  roi: number | null
  avgClv: number | null            // mean clv_pct over graded plays with a recorded closing line
  clvCount: number                 // how many plays that average covers
  gradedLeagues: string[]          // ['MLB']
  unsettledLeagues: string[]       // ['NFL', 'NBA', 'NHL']
  sports: LedgerSportRow[]
  stakingBasis: string             // 'paper stakes as recorded'
}

function getEarliestDate(dates: (string | null | undefined)[]): string | null {
  const valid = dates.filter((d): d is string => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d.trim()))
  if (valid.length === 0) return null
  return valid.reduce((earliest, d) => (d.trim().localeCompare(earliest.trim()) < 0 ? d.trim() : earliest.trim()), valid[0].trim())
}

function getLatestDate(dates: (string | null | undefined)[]): string | null {
  const valid = dates.filter((d): d is string => typeof d === 'string' && /^\d{4}-\d{2}-\d{2}/.test(d.trim()))
  if (valid.length === 0) return null
  return valid.reduce((latest, d) => (d.trim().localeCompare(latest.trim()) > 0 ? d.trim() : latest.trim()), valid[0].trim())
}

function formatDateRangeString(start: string | null, end: string | null): string {
  if (!start || !end) return ''
  const startStr = formatDateKey(start.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
  const endStr = formatDateKey(end.slice(0, 10), { month: 'long', day: 'numeric', year: 'numeric' })
  return `${startStr} \u2013 ${endStr}`
}

export function buildLedgerSummary({
  stats,
  deepDive,
  clvRows,
  asOf,
}: {
  stats: StatsResponse | null | undefined
  deepDive?: AnalyticsDeepDive | null
  clvRows?: (CLVDataPoint | Record<string, unknown>)[] | null
  asOf?: string
}): LedgerSummary | null {
  if (!stats || !stats.overall) {
    return null
  }

  const totalGraded = parseInt(String(stats.overall.total_bets ?? 0), 10) || 0
  const rawWinRate = parseFloat(String(stats.overall.win_rate ?? ''))
  const winRate = !isNaN(rawWinRate) && isFinite(rawWinRate) ? rawWinRate : null

  const rawRoi = parseFloat(String(stats.overall.roi ?? ''))
  const roi = !isNaN(rawRoi) && isFinite(rawRoi) ? rawRoi : null

  let wins = 0
  let losses = 0
  let pushes = 0
  if (Array.isArray(stats.bySystem)) {
    for (const s of stats.bySystem) {
      wins += parseInt(String(s.wins ?? 0), 10) || 0
      losses += parseInt(String(s.losses ?? 0), 10) || 0
      pushes += parseInt(String(s.pushes ?? 0), 10) || 0
    }
  }

  // Date range from deep dive (ev_headline and bankroll_curve)
  const curveDates = (deepDive?.bankroll_curve ?? [])
    .map(p => (p && typeof p.date === 'string' ? p.date : null))
    .filter((d): d is string => Boolean(d))
  const firstCurveDate = getEarliestDate(curveDates)
  const lastCurveDate = getLatestDate(curveDates)

  const evFirstDate = deepDive?.ev_headline?.first_date ?? null
  const evLastDate = deepDive?.ev_headline?.last_date ?? null

  const startDate = getEarliestDate([evFirstDate, firstCurveDate])
  const endDate = getLatestDate([evLastDate, lastCurveDate])

  // CLV calculation
  let clvCount = 0
  let clvSum = 0
  if (Array.isArray(clvRows)) {
    for (const row of clvRows) {
      if (row && (row as Record<string, unknown>).clv_pct != null) {
        const val = typeof (row as Record<string, unknown>).clv_pct === 'number'
          ? ((row as Record<string, unknown>).clv_pct as number)
          : parseFloat(String((row as Record<string, unknown>).clv_pct))
        if (!isNaN(val) && isFinite(val)) {
          clvCount++
          clvSum += val
        }
      }
    }
  }
  const avgClv = clvCount > 0 ? clvSum / clvCount : null

  const mlbDateRange = formatDateRangeString(startDate, endDate)

  const sports: LedgerSportRow[] = [
    {
      sport: 'MLB',
      label: 'MLB',
      dateRange: mlbDateRange,
      graded: totalGraded,
      wins,
      losses,
      pushes,
      winRate,
      roi,
      avgClv,
      status: 'Publicly settled',
    },
  ]

  return {
    asOfDate: asOf ?? asOfLabel(),
    startDate,
    endDate,
    totalGraded,
    wins,
    losses,
    pushes,
    winRate,
    roi,
    avgClv,
    clvCount,
    gradedLeagues: ['MLB'],
    unsettledLeagues: ['NFL', 'NBA', 'NHL'],
    sports,
    stakingBasis: 'paper stakes as recorded',
  }
}

export async function getLedgerSummary(): Promise<LedgerSummary | null> {
  try {
    const [statsRes, deepDiveRes, clvRes] = await Promise.allSettled([
      apiGetStats(),
      apiGetAnalyticsDeepDive('EV'),
      apiGetCLVData(365),
    ])

    if (statsRes.status !== 'fulfilled' || !statsRes.value) {
      return null
    }

    const stats = statsRes.value
    const deepDive = deepDiveRes.status === 'fulfilled' ? deepDiveRes.value : null
    const clvRows = clvRes.status === 'fulfilled' ? clvRes.value : []

    return buildLedgerSummary({ stats, deepDive, clvRows })
  } catch {
    return null
  }
}
