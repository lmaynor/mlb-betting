/**
 * Header-level anonymous traffic identification.
 *
 * This classifies traffic by what the caller DECLARES in its request headers (the reciprocity rule:
 * agents that identify themselves get identified; agents that do not are labeled unknown, never
 * unmasked). It reads only descriptive headers -- user-agent, client hints, Vercel geo, and the
 * first Accept-Language tag. It never reads or derives identity: no IP, no cookies, no fingerprint
 * signals, no cross-visit state. Pure function over a Headers-like getter; no I/O.
 */

export type AnonTrafficKind = 'human' | 'declared_bot' | 'unknown_automation'
export type AnonDeviceClass = 'mobile' | 'desktop' | 'tablet' | 'bot' | 'unknown'

export interface HeaderGetter {
  get(name: string): string | null
}

export interface AnonTrafficClassification {
  kind: AnonTrafficKind
  /** Declared bot name when the UA names a known bot (or a generic bot/spider/crawler label). */
  bot: string | null
  uaFamily: string
  deviceClass: AnonDeviceClass
  country: string | null
  region: string | null
  /** First Accept-Language tag only (e.g. "en-US"); the rest of the list is never kept. */
  language: string | null
}

function header(headers: HeaderGetter, name: string): string | null {
  try {
    const value = headers.get(name)
    if (typeof value !== 'string') return null
    const trimmed = value.trim()
    return trimmed === '' ? null : trimmed
  } catch {
    return null
  }
}

// Declared bots, matched on the token the operator itself publishes in its user-agent.
// Order matters only where one token contains another (Applebot-Extended before Applebot).
const DECLARED_BOTS: ReadonlyArray<{ token: string; name: string }> = [
  { token: 'googlebot', name: 'Googlebot' },
  { token: 'bingbot', name: 'Bingbot' },
  { token: 'gptbot', name: 'GPTBot' },
  { token: 'oai-searchbot', name: 'OAI-SearchBot' },
  { token: 'chatgpt-user', name: 'ChatGPT-User' },
  { token: 'claudebot', name: 'ClaudeBot' },
  { token: 'claude-user', name: 'Claude-User' },
  { token: 'perplexitybot', name: 'PerplexityBot' },
  { token: 'perplexity-user', name: 'Perplexity-User' },
  { token: 'ccbot', name: 'CCBot' },
  { token: 'meta-externalagent', name: 'Meta-ExternalAgent' },
  { token: 'applebot-extended', name: 'Applebot-Extended' },
  { token: 'applebot', name: 'Applebot' },
]

// Generic self-declared automation tokens: the caller says it is a bot/spider/crawler without
// naming a known operator. It is labeled generically, never resolved to an operator.
const GENERIC_BOT_TOKENS: ReadonlyArray<{ token: string; name: string }> = [
  { token: 'spider', name: 'generic_spider' },
  { token: 'crawler', name: 'generic_crawler' },
  { token: 'crawl', name: 'generic_crawler' },
  { token: 'bot', name: 'generic_bot' },
]

// Library / default automation user-agents. These do not declare a bot identity, so they are
// unknown_automation -- NOT named bots.
const AUTOMATION_FAMILIES: ReadonlyArray<{ token: string; family: string }> = [
  { token: 'python-requests', family: 'python-requests' },
  { token: 'python-urllib', family: 'Python-urllib' },
  { token: 'aiohttp', family: 'aiohttp' },
  { token: 'curl', family: 'curl' },
  { token: 'wget', family: 'Wget' },
  { token: 'axios', family: 'axios' },
  { token: 'go-http-client', family: 'Go-http-client' },
  { token: 'okhttp', family: 'okhttp' },
  { token: 'node-fetch', family: 'node-fetch' },
  { token: 'undici', family: 'undici' },
  { token: 'libwww-perl', family: 'libwww-perl' },
  { token: 'postmanruntime', family: 'PostmanRuntime' },
  { token: 'insomnia', family: 'Insomnia' },
  { token: 'java/', family: 'Java' },
]

function browserFamily(uaLower: string): string {
  if (uaLower.includes('edg/') || uaLower.includes('edge/')) return 'Edge'
  if (uaLower.includes('opr/') || uaLower.includes('opera')) return 'Opera'
  if (uaLower.includes('samsungbrowser/')) return 'Samsung Internet'
  if (uaLower.includes('crios/') || uaLower.includes('chrome/') || uaLower.includes('chromium/')) return 'Chrome'
  if (uaLower.includes('fxios/') || uaLower.includes('firefox/')) return 'Firefox'
  if (uaLower.includes('safari/')) return 'Safari'
  if (uaLower.includes('mozilla/')) return 'Mozilla'
  return 'Other'
}

function deviceClass(uaLower: string, kind: AnonTrafficKind, mobileHint: string | null): AnonDeviceClass {
  if (kind === 'declared_bot') return 'bot'
  if (/ipad|tablet|kindle|silk\/|playbook/.test(uaLower)) return 'tablet'
  if (/android/.test(uaLower) && !/mobile/.test(uaLower)) return 'tablet'
  if (mobileHint === '?1') return 'mobile'
  if (/iphone|ipod|android|mobile|windows phone|blackberry|opera mini|iemobile/.test(uaLower)) return 'mobile'
  if (mobileHint === '?0') return 'desktop'
  if (/windows nt|macintosh|mac os x|x11|cros|linux x86_64|ubuntu/.test(uaLower)) return 'desktop'
  return 'unknown'
}

function geoValue(raw: string | null): string | null {
  if (!raw) return null
  const value = raw.trim()
  if (value === '' || value.length > 64) return null
  return /^[a-z]{2}$/i.test(value) ? value.toUpperCase() : value
}

function primaryLanguage(raw: string | null): string | null {
  if (!raw) return null
  const first = raw.split(',')[0]?.split(';')[0]?.trim() ?? ''
  if (first === '' || first === '*') return null
  if (!/^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/.test(first)) return null
  const [language, ...rest] = first.split('-')
  return [language.toLowerCase(), ...rest.map(part => (part.length === 2 ? part.toUpperCase() : part))].join('-')
}

export function classifyAnonTraffic(headers: HeaderGetter): AnonTrafficClassification {
  const ua = header(headers, 'user-agent')
  const uaLower = (ua ?? '').toLowerCase()
  const country = geoValue(header(headers, 'x-vercel-ip-country'))
  const region = geoValue(header(headers, 'x-vercel-ip-country-region'))
  const language = primaryLanguage(header(headers, 'accept-language'))
  const mobileHint = header(headers, 'sec-ch-ua-mobile')

  let kind: AnonTrafficKind
  let bot: string | null = null
  let uaFamily: string

  const declared = ua ? DECLARED_BOTS.find(rule => uaLower.includes(rule.token)) : undefined
  const generic = !declared && ua ? GENERIC_BOT_TOKENS.find(rule => uaLower.includes(rule.token)) : undefined
  const automation = !declared && !generic && ua ? AUTOMATION_FAMILIES.find(rule => uaLower.includes(rule.token)) : undefined

  if (declared) {
    kind = 'declared_bot'
    bot = declared.name
    uaFamily = declared.name
  } else if (generic) {
    kind = 'declared_bot'
    bot = generic.name
    uaFamily = 'Bot'
  } else if (automation) {
    kind = 'unknown_automation'
    uaFamily = automation.family
  } else if (!ua) {
    // No user-agent at all: something automated that did not identify itself.
    kind = 'unknown_automation'
    uaFamily = 'unknown'
  } else {
    kind = 'human'
    uaFamily = browserFamily(uaLower)
  }

  return {
    kind,
    bot,
    uaFamily,
    deviceClass: deviceClass(uaLower, kind, mobileHint),
    country,
    region,
    language,
  }
}
