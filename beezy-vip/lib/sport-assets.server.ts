/**
 * Name -> headshot lookups for NHL/NBA (SERVER components only: imports the ~40KB player maps, which would bloat any
 * client bundle that pulled this in). Client code should use headshotUrlById() from ./sport-assets with a resolved id.
 *
 * Maps are written by scripts/build_sport_assets.py --players. Keys: `slug__team` for every player (team = lowercase
 * abbreviation) and a bare `slug` ONLY when the name is unique league-wide -- so a duplicate name with no team hint
 * resolves to NOTHING rather than to the wrong person's face. Rosters change (trades, call-ups): re-run the script.
 */
import nhlMap from '@/public/headshots/nhl_player_map.json'
import nbaMap from '@/public/headshots/nba_player_map.json'
import { headshotUrlById, playerSlug, teamCode, type AssetSport } from './sport-assets'

const MAPS: Record<AssetSport, Record<string, number>> = {
  nhl: nhlMap as Record<string, number>,
  nba: nbaMap as Record<string, number>,
}

export function headshotUrlByName(sport: AssetSport, name: string | null | undefined, team?: string | null): string | null {
  const slug = playerSlug(name)
  if (!slug) return null
  const map = MAPS[sport]
  const code = teamCode(sport, team)
  const id = (code ? map[`${slug}__${code.toLowerCase()}`] : undefined) ?? map[slug]
  return headshotUrlById(sport, id)
}
