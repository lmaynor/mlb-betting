export const SITE_TIME_ZONE = 'America/Chicago'

export function siteDateKey(date = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: SITE_TIME_ZONE,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(date)

  const get = (type: string) => parts.find(p => p.type === type)?.value ?? ''
  return `${get('year')}-${get('month')}-${get('day')}`
}

export function addDaysToDateKey(dateKey: string, days: number): string {
  const [year, month, day] = dateKey.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day + days, 12))
  return date.toISOString().slice(0, 10)
}

export function formatDateKey(dateKey: string, options: Intl.DateTimeFormatOptions): string {
  const [year, month, day] = dateKey.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day, 12))
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', ...options }).format(date)
}

export function formatCentralDate(date = new Date(), options: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat('en-US', { timeZone: SITE_TIME_ZONE, ...options }).format(date)
}
