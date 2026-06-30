"""
mlb_core.data.id_resolver -- map (date, teams) -> game_pk and (name, team) ->
MLBAM player_id via the MLB Stats API.

Shared bridge for normalizing odds into the odds_history store (BettingPros
P0.3 + SGO P0.2). HTTP fetches are separated from pure index-building so the
mapping logic is testable without network.

Caching: schedule per date, player index per season -- both module-level, so a
single normalization run makes ~1 call per date + ~1 call per season.

Caveats:
- Doubleheaders: a (date, away, home) maps to >1 game_pk. resolve_game_pk
  returns the FIRST (game 1) and increments _ambiguous_doubleheaders; BettingPros
  rows carry no game number, so game-2 props join to game 1. Small fraction;
  surfaced via doubleheader_count().
- Player team is matched on CURRENT team (both BettingPros and the players
  endpoint use current team), so traded players still resolve.
"""

from __future__ import annotations

import unicodedata

import requests

# Stable MLB Stats API team id <-> 3-letter abbrev (mirrors
# mlb_core.data.auxiliary_features._TEAM_ID_TO_ABBREV; inlined to avoid importing
# that heavier module just for a constant).
_TEAM_ID_TO_ABBREV = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC", 119: "LAD",
    120: "WSH", 121: "NYM", 133: "OAK", 134: "PIT", 135: "SD", 136: "SEA",
    137: "SF", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}"
_PLAYERS_URL = "https://statsapi.mlb.com/api/v1/sports/1/players?season={season}"
_BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"

_session = requests.Session()
_schedule_cache: dict = {}   # date -> {(away_abbr, home_abbr): [game_pk, ...]}
_player_cache: dict = {}     # season -> (name->{ids}, (name,abbr)->id)
_roster_cache: dict = {}     # game_pk -> {norm_name: mlbam_id} (boxscore fallback)
_ambiguous_doubleheaders = 0


def _norm(s: str) -> str:
    """NFD + ASCII fold + lower + strip -- matches game_result / auxiliary_features."""
    if not s:
        return ""
    n = unicodedata.normalize("NFD", s)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


# --- game_pk ----------------------------------------------------------------

def _build_game_index(schedule_json: dict) -> dict:
    """Pure: schedule JSON -> {(away_abbr, home_abbr): [game_pk, ...]} (in order)."""
    index: dict = {}
    for d in schedule_json.get("dates", []):
        for g in d.get("games", []):
            try:
                away = _TEAM_ID_TO_ABBREV.get(g["teams"]["away"]["team"]["id"])
                home = _TEAM_ID_TO_ABBREV.get(g["teams"]["home"]["team"]["id"])
                pk = g["gamePk"]
            except (KeyError, TypeError):
                continue
            if not away or not home:
                continue
            index.setdefault((away, home), []).append(pk)
    return index


def _fetch_schedule(date: str) -> dict:
    r = _session.get(_SCHEDULE_URL.format(date=date), timeout=30)
    r.raise_for_status()
    return r.json()


def game_pks_for_date(date: str) -> dict:
    if date not in _schedule_cache:
        try:
            _schedule_cache[date] = _build_game_index(_fetch_schedule(date))
        except Exception:  # noqa: BLE001 -- treat a failed/empty date as no games
            _schedule_cache[date] = {}
    return _schedule_cache[date]


def resolve_game_pk(date: str, away: str, home: str):
    """game_pk for (date, away, home), or None. First game on a doubleheader."""
    global _ambiguous_doubleheaders
    pks = game_pks_for_date(date).get((away, home))
    if not pks:
        return None
    if len(pks) > 1:
        _ambiguous_doubleheaders += 1
    return pks[0]


def doubleheader_count() -> int:
    return _ambiguous_doubleheaders


# --- player_id --------------------------------------------------------------

def _build_player_index(players_json: dict) -> tuple:
    """Pure: players JSON -> (name->{ids}, (name,abbr)->id)."""
    name_to_ids: dict = {}
    name_team_to_id: dict = {}
    for p in players_json.get("people", []):
        pid = p.get("id")
        nm = _norm(p.get("fullName", ""))
        if not pid or not nm:
            continue
        name_to_ids.setdefault(nm, set()).add(pid)
        abbr = _TEAM_ID_TO_ABBREV.get((p.get("currentTeam") or {}).get("id"))
        if abbr:
            name_team_to_id[(nm, abbr)] = pid
    return name_to_ids, name_team_to_id


def _fetch_players(season: str) -> dict:
    r = _session.get(_PLAYERS_URL.format(season=season), timeout=60)
    r.raise_for_status()
    return r.json()


def season_player_index(season: str) -> tuple:
    if season not in _player_cache:
        try:
            _player_cache[season] = _build_player_index(_fetch_players(season))
        except Exception:  # noqa: BLE001
            _player_cache[season] = ({}, {})
    return _player_cache[season]


def _build_roster(boxscore_json: dict) -> dict:
    """Pure: boxscore JSON -> {norm_name: mlbam_id} for both teams' players."""
    out: dict = {}
    teams = (boxscore_json or {}).get("teams", {}) or {}
    for side in ("home", "away"):
        for p in (teams.get(side, {}) or {}).get("players", {}).values():
            person = p.get("person") or {}
            pid, nm = person.get("id"), _norm(person.get("fullName", ""))
            if pid and nm:
                out[nm] = pid
    return out


def _fetch_boxscore(game_pk) -> dict:
    r = _session.get(_BOXSCORE_URL.format(pk=game_pk), timeout=30)
    r.raise_for_status()
    return r.json()


def game_roster_index(game_pk) -> dict:
    """{norm_name: mlbam_id} for everyone in a game's boxscore (cached)."""
    if game_pk not in _roster_cache:
        try:
            _roster_cache[game_pk] = _build_roster(_fetch_boxscore(game_pk))
        except Exception:  # noqa: BLE001
            _roster_cache[game_pk] = {}
    return _roster_cache[game_pk]


def resolve_player_id(name: str, team: str, date: str, game_pk=None):
    """MLBAM id for a player, or None. Order: season (name,team) -> unique season
    name -> game boxscore roster (when game_pk given). The boxscore fallback is
    game-scoped (~50 players) so it disambiguates and guarantees the player was in
    that game -- this closes the ~10-22% name->MLBAM gap the season index leaves."""
    season = (date or "")[:4]
    nm = _norm(name)
    if season:
        name_to_ids, name_team_to_id = season_player_index(season)
        if (nm, team) in name_team_to_id:
            return name_team_to_id[(nm, team)]
        ids = name_to_ids.get(nm)
        if ids and len(ids) == 1:
            return next(iter(ids))
    if game_pk:
        rid = game_roster_index(game_pk).get(nm)
        if rid:
            return rid
    return None  # missing or ambiguous
