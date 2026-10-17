/**
 * Name -> player id / headshot lookups for NHL, NBA and MLB (SERVER components only: imports the player maps, which would
 * bloat any client bundle that pulled this in). Client code should use headshotUrlById() from ./sport-assets with a
 * resolved id.
 *
 * NHL/NBA maps are written by scripts/build_sport_assets.py --players, the NFL map by --nfl-players (values are nfl.com image
 * tokens, strings). Keys: `slug__team` for every player (team =
 * lowercase abbreviation) and a bare `slug` ONLY when the name is unique league-wide -- so a duplicate name with no team hint
 * resolves to NOTHING rather than to the wrong person's face. Rosters change: re-run the script.
 *
 * MLB uses the older public/headshots/player_map.json (bare slug -> MLBAM id, refreshed by
 * scripts/refresh_mlb_player_map.py). It has no team keys, so a duplicate name (two Max Muncys) keeps whichever id the
 * map holds -- a known, pre-existing limitation. MLB lookups try the accented slug first ("randy_vasquez" would miss
 * "randy_vasquez" keys written with the accent), then the two ASCII forms, exactly as the pages did before.
 */
import nhlMap from '@/public/headshots/nhl_player_map.json'
import nbaMap from '@/public/headshots/nba_player_map.json'
import mlbMap from '@/public/headshots/player_map.json'
import nflMap from '@/public/headshots/nfl_player_map.json'
import { headshotUrlById, playerSlug, teamCode, type AssetSport, type HeadshotSport } from './sport-assets'

const MAPS: Record<HeadshotSport, Record<string, number | string>> = {
  nfl: nflMap as Record<string, string>,
  nhl: nhlMap as Record<string, number>,
  nba: nbaMap as Record<string, number>,
  mlb: mlbMap as Record<string, number>,
}

/** accent-preserving slug (hyphen -> underscore, strip everything but letters/digits/_): what the MLB map keys use. */
function slugAccented(name: string): string {
  return name.toLowerCase().replace(/\s+/g, '_').replace(/-/g, '_').replace(/[^\p{L}\p{N}_]/gu, '')
}

/** the pages' old ASCII fallback: NFD, strip marks, hyphens DROPPED (not underscored). Kept so nothing that matched before stops matching. */
function slugAsciiLegacy(name: string): string {
  return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
}

export function playerIdByName(sport: HeadshotSport, name: string | null | undefined, team?: string | null): number | string | null {
  if (!name) return null
  const map = MAPS[sport]
  if (sport === 'mlb') {
    for (const k of [slugAccented(name), slugAsciiLegacy(name), playerSlug(name)]) {
      if (k && map[k]) return map[k]
    }
    return null
  }
  const slug = playerSlug(name)
  if (!slug) return null
  // NFL rows carry the ev/ team code already (HOU, JAC, LAR); NHL / NBA spell teams many ways, so normalise those
  const code = sport === 'nfl' ? (team ?? '').trim().toUpperCase() || null : teamCode(sport as AssetSport, team)
  return (code ? map[`${slug}__${code.toLowerCase()}`] : undefined) ?? map[slug] ?? null
}

export function headshotUrlByName(sport: HeadshotSport, name: string | null | undefined, team?: string | null): string | null {
  return headshotUrlById(sport, playerIdByName(sport, name, team))
}
