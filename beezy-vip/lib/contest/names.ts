/**
 * Display-name rules, a mirror of contest/names.py so the join form can say what is wrong as the player types. The server is the
 * authority (it also checks the name is not taken in this contest and counts renames); a name this accepts can still be refused.
 */
export const NAME_MIN = 2
export const NAME_MAX = 20
export const MAX_RENAMES = 3
const RESERVED = new Set(['you', 'me', 'admin', 'administrator', 'house', 'dealer', 'beezy', 'beezyfyi', 'mod', 'moderator', 'support', 'staff', 'system', 'owner', 'contest', 'bot', 'null', 'undefined'])

export const cleanName = (raw: string): string => String(raw ?? '').normalize('NFKC').split(/\s+/).filter(Boolean).join(' ')

/** The uniqueness key: case-folded, letters and digits only ("Alex", "ALEX" and "a.l.e.x" are one name). */
export const nameKey = (display: string): string => [...display.normalize('NFKC').toLowerCase()].filter(ch => /[\p{L}\p{N}]/u.test(ch)).join('')

export type NameCheck = { ok: true; name: string; key: string } | { ok: false; code: 'NAME_INVALID' | 'NAME_RESERVED'; message: string }

export function validateName(raw: string): NameCheck {
  const name = cleanName(raw)
  const bad = (message: string): NameCheck => ({ ok: false, code: 'NAME_INVALID', message })
  if (name.length < NAME_MIN || name.length > NAME_MAX) return bad(`Use ${NAME_MIN} to ${NAME_MAX} characters.`)
  if (!/^[\p{L}\p{N}\p{M}_ .'-]+$/u.test(name)) return bad("Use letters, numbers, spaces and . _ ' - only.")
  if (/[.'\-_]/.test(name[0]) || /[.'\-_]/.test(name[name.length - 1])) return bad('Start and end with a letter or number.')
  const key = nameKey(name)
  if (key.length < 2) return bad('Include at least two letters or numbers.')
  if (key.includes('http') || key.includes('www') || (name.includes('.') && /(com|net|org|io)$/.test(key))) return bad('Links are not allowed in a name.')
  if (RESERVED.has(key)) return { ok: false, code: 'NAME_RESERVED', message: 'That name is reserved.' }
  return { ok: true, name, key }
}

/** A starting point for the join form: the username, else "First L", else the email's local part as words. Always editable. */
export function suggestName(a: { username?: string | null; firstName?: string | null; lastName?: string | null; email?: string | null }): string {
  const firstLast = [a.firstName, (a.lastName ?? '').slice(0, 1)].filter(Boolean).join(' ')
  const fromEmail = (a.email ?? '').split('@')[0].replace(/[._-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  for (const cand of [a.username ?? '', firstLast, fromEmail]) {
    const v = validateName(cand)
    if (v.ok) return v.name
  }
  return 'Player'
}
