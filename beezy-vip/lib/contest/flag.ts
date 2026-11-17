/**
 * Kill switch for the Bankroll Challenge. OFF unless CONTEST_ENABLED is set to a truthy value; the usual falsy spellings
 * (0, false, off, no, empty) all mean OFF so a flag can never be "stuck on" by a typo. Server-side only.
 */
const OFF = new Set(['', '0', 'false', 'off', 'no'])

export function contestEnabled(env: Record<string, string | undefined> = process.env): boolean {
  const raw = (env.CONTEST_ENABLED ?? '').trim().toLowerCase()
  return !OFF.has(raw)
}
