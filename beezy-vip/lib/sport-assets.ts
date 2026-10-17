/**
 * NHL / NBA team logos + player headshots (client-safe: no JSON maps imported here).
 *
 *  - Team logos are SVGs COPIED into public/logos/{sport}/ by scripts/build_sport_assets.py (validated on download).
 *  - Player headshots are HOTLINKED from the official CDNs (verified 2026-10-14 to serve transparent cutouts, so no
 *    background-removal step):
 *      NHL  assets.nhle.com/mugs/nhl/latest/{playerId}.png          336x336, ~100KB
 *      NBA  a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{espnId}.png&w=200&h=146   ~31KB, ~17h cache
 *    These are undocumented URLs and could change without notice -- every <img> must tolerate a 404 (see
 *    components/ui/sport-assets.tsx, which hides a broken image instead of showing the broken-image icon).
 *  - NBA ids here are ESPN's, NOT NBA personIds (the official NBA JSON endpoints are blocked from our infra).
 *  - NFL: the nfl.com image CDN, keyed by the image TOKEN the nflverse rosters carry (a short string, not a number; the ESPN id
 *    exists for only ~70% of players, the token for 97%). Verified 2026-10-16: 200, transparent cutout, ~8KB at w_200.
 *      NFL  static.www.nfl.com/image/upload/f_png,q_auto,w_200/league/{token}
 *
 *  - MLB headshots use MLB's own `silo` variant, the transparent cutout (the older `headshot/67` URL is a JPEG WITH a
 *    background, which is why a local rembg pipeline used to exist). Verified 2026-10-14: real photos for all 1,272 mapped
 *    players, median 16KB, CORS `*`:
 *      MLB  img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:silo:current.png/w_213,q_auto:best/v1/people/{mlbamId}/headshot/silo/current
 *
 * Name -> id lookups need the ~40KB player maps, so they live in lib/sport-assets.server.ts (server components only).
 * Rows that already carry a resolved `player_id` (the +EV props feed does) can use headshotUrlById() directly.
 * MLB keeps its own pipeline (public/headshots/player_map.json, /logos/{abbr}.png) -- unchanged.
 *
 * Team vocabulary: lib/sport-teams.json is GENERATED from nhl.config.TEAMS / nba.teams.TEAMS
 * (tests/test_sport_assets.py fails if it drifts).
 */
import teams from './sport-teams.json'

export type AssetSport = 'nhl' | 'nba'
/** Headshots also cover MLB and NFL (teams/logos do not: MLB keeps its own flat /logos/{abbr}.png assets, NFL has none). */
export type HeadshotSport = AssetSport | 'mlb' | 'nfl'

/** Sports with a headshot source. A sport added to lib/sports.ts gets headshots by being added here. */
export const HEADSHOT_SPORTS: readonly string[] = ['mlb', 'nfl', 'nhl', 'nba']
export const hasHeadshots = (sport: string): sport is HeadshotSport => HEADSHOT_SPORTS.includes(sport)

interface TeamRow { abbr: string; name: string; alias: string[] }
const TABLE = teams as unknown as Record<AssetSport, TeamRow[]>

/** accent-fold, lowercase, drop periods/hyphens, collapse spaces: "L.A. Clippers" -> "la clippers". */
export function fold(s: string | null | undefined): string {
  return (s ?? '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/\./g, '')
    .replace(/-/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const BY_KEY: Record<AssetSport, Map<string, string>> = { nhl: new Map(), nba: new Map() }
for (const sport of ['nhl', 'nba'] as AssetSport[]) {
  for (const t of TABLE[sport]) {
    BY_KEY[sport].set(fold(t.abbr), t.abbr)
    BY_KEY[sport].set(fold(t.name), t.abbr)
    for (const a of t.alias) BY_KEY[sport].set(fold(a), t.abbr)
  }
}

/**
 * Any of an abbreviation ('MTL'), an alias (Kalshi 'LA', ESPN 'GS'), or a full name ('Montr\u00e9al Canadiens',
 * 'L.A. Clippers') -> the canonical abbreviation, or null. Trailing-nickname match ("Canadiens") is attempted last
 * and only when exactly one team ends with it, so an ambiguous nickname never picks a team.
 */
export function teamCode(sport: AssetSport, input: string | null | undefined): string | null {
  const f = fold(input)
  if (!f) return null
  const hit = BY_KEY[sport].get(f)
  if (hit) return hit
  const tails = TABLE[sport].filter(t => fold(t.name).endsWith(' ' + f))
  return tails.length === 1 ? tails[0].abbr : null
}

/** Repo-relative logo path for a team, or null when the team is unknown. Both leagues ship light + dark variants; the UI is dark so dark is the default. */
export function teamLogoUrl(sport: AssetSport, team: string | null | undefined, opts: { dark?: boolean } = {}): string | null {
  const code = teamCode(sport, team)
  if (!code) return null
  const base = code.toLowerCase()
  const dark = opts.dark ?? true
  return dark ? `/logos/${sport}/${base}-dark.svg` : `/logos/${sport}/${base}.svg`
}

/** nfl.com image tokens are short lowercase-alphanumeric strings; anything else (a path, a URL) is rejected, never interpolated. */
const NFL_TOKEN = /^[a-z0-9]{10,40}$/i

/** Headshot URL from a league player id (NHL playerId / ESPN athlete id / NFL image token), or null for a missing/invalid id. */
export function headshotUrlById(sport: HeadshotSport, id: number | string | null | undefined): string | null {
  if (sport === 'nfl') return typeof id === 'string' && NFL_TOKEN.test(id) ? `https://static.www.nfl.com/image/upload/f_png,q_auto,w_200/league/${id}` : null
  const n = typeof id === 'string' ? Number(id) : id
  if (n == null || !Number.isInteger(n) || n <= 0) return null
  if (sport === 'nhl') return `https://assets.nhle.com/mugs/nhl/latest/${n}.png`
  if (sport === 'mlb') {
    return `https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:silo:current.png/w_213,q_auto:best/v1/people/${n}/headshot/silo/current`
  }
  return `https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/${n}.png&w=200&h=146`
}

/** Same slug algorithm as scripts/build_sport_assets.py::_slug (accent-fold, lowercase, '_' separated). */
export function playerSlug(name: string | null | undefined): string {
  return fold(name).replace(/ /g, '_').replace(/[^a-z0-9_]/g, '')
}
