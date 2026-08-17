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


# --- GCS-backed persistence for the two module-level caches above (finding
# B3.1) ------------------------------------------------------------------
#
# Both caches used to be PURE in-process dicts with zero persistence --
# rebuilt from the MLB Stats API on every cold start, and the Cloud Run
# service very plausibly scales to zero between the ~8 daily /snapshot-odds
# calls. _fetch_players(season) in particular downloads every active MLB
# player just to resolve prop names, on every cold start rather than once
# per season. /snapshot-odds has only a 180s scheduler deadline, so this
# plausibly contributed to occasional deadline-exceeded failures -- i.e. a
# cloud-cost bug that's also a "missed betting window" bug.
#
# Persists to a small GCS JSON object per date/season, with a same-day TTL:
# a cache entry is only trusted if it was fetched on TODAY's calendar date.
# This is intentionally simple rather than "trust past-date schedules
# forever" (which would also be technically correct, since a finalized
# past date's schedule doesn't change) -- one fetch/day per date/season is
# already the entire cost win this finding is about, and a uniform same-day
# rule is much easier to reason about than a permanent-for-the-past
# special case that a future maintainer has to remember not to break.
# Best-effort throughout: any read/write failure here just falls back to
# the live HTTP fetch this module already had -- this is purely an
# optimization layer, never a new failure mode.
def _today_str() -> str:
    from datetime import date as _date
    return _date.today().isoformat()


def _gcs_schedule_key(sched_date: str) -> str:
    return f"IdResolver/schedule/{sched_date}.json"


def _gcs_player_key(season: str) -> str:
    return f"IdResolver/players/{season}.json"


def _load_gcs_cache(key: str):
    """Return the cached JSON-safe payload if present and fetched today,
    else None (a cache miss -- caller falls back to a live fetch)."""
    from mlb_core.storage import exists, read_bytes
    import json as _json
    if not exists(key):
        return None
    try:
        payload = _json.loads(read_bytes(key))
        if payload.get("fetched_on") != _today_str():
            return None
        return payload.get("data")
    except Exception:  # noqa: BLE001 -- corrupt/unreadable entry = cache miss, not an error
        return None


def _save_gcs_cache(key: str, data) -> None:
    from mlb_core.storage import write_bytes
    import json as _json
    try:
        write_bytes(_json.dumps({"fetched_on": _today_str(), "data": data}).encode(), key)
    except Exception:  # noqa: BLE001 -- best-effort; in-memory cache still works this process
        pass


def _schedule_index_to_json(index: dict) -> dict:
    """{(away,home): [pk,...]} -> {"AWAY|HOME": [pk,...]} (JSON object keys
    must be strings; tuples aren't allowed)."""
    return {f"{away}|{home}": pks for (away, home), pks in index.items()}


def _schedule_index_from_json(obj: dict) -> dict:
    out = {}
    for k, pks in obj.items():
        away, _, home = k.partition("|")
        out[(away, home)] = pks
    return out


def _player_index_to_json(name_to_ids: dict, name_team_to_id: dict) -> dict:
    return {
        "name_to_ids": {name: sorted(ids) for name, ids in name_to_ids.items()},
        "name_team_to_id": {f"{name}|{abbr}": pid for (name, abbr), pid in name_team_to_id.items()},
    }


def _player_index_from_json(obj: dict) -> tuple:
    name_to_ids = {name: set(ids) for name, ids in obj.get("name_to_ids", {}).items()}
    name_team_to_id = {}
    for k, pid in obj.get("name_team_to_id", {}).items():
        name, _, abbr = k.partition("|")
        name_team_to_id[(name, abbr)] = pid
    return name_to_ids, name_team_to_id


_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _norm(s: str) -> str:
    """NFD + ASCII fold + lower, drop . and ', strip a trailing generational
    suffix (Jr/Sr/II..). Symmetric on both index + query sides, so
    'Lourdes Gurriel' == 'Lourdes Gurriel Jr.' and 'T.J.' == 'TJ'."""
    if not s:
        return ""
    n = unicodedata.normalize("NFD", s)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    n = n.encode("ascii", "ignore").decode().lower().replace(".", "").replace("'", "")
    toks = n.split()
    if len(toks) > 1 and toks[-1] in _NAME_SUFFIXES:
        toks = toks[:-1]
    return " ".join(toks).strip()


def is_player_name(name: str) -> bool:
    """False for non-player junk that some books put in the prop description:
    unrendered templates ({...}) and matchup strings ('Away @ Home')."""
    n = (name or "").strip()
    return bool(n) and "{" not in n and "}" not in n and " @ " not in n


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
        cached = _load_gcs_cache(_gcs_schedule_key(date))
        if cached is not None:
            _schedule_cache[date] = _schedule_index_from_json(cached)
        else:
            try:
                index = _build_game_index(_fetch_schedule(date))
            except Exception:  # noqa: BLE001 -- treat a failed/empty date as no games
                index = {}
            _schedule_cache[date] = index
            _save_gcs_cache(_gcs_schedule_key(date), _schedule_index_to_json(index))
    return _schedule_cache[date]


# Odds-feed team abbreviations that differ from the MLB schedule's
# (_TEAM_ID_TO_ABBREV). Most notably the Athletics: BettingPros uses "ATH"
# (the 2025 rebrand) while the schedule keys on id 133 -> "OAK". Applied to the
# query side of resolve_game_pk so feed abbrevs match the schedule index.
_ABBR_ALIASES = {"ATH": "OAK", "OAK": "OAK", "AZ": "ARI", "CHW": "CWS",
                 "WSN": "WSH", "SDP": "SD", "SFG": "SF", "TBR": "TB", "KCR": "KC"}


def _canon_abbr(a: str) -> str:
    return _ABBR_ALIASES.get(a, a)


def resolve_game_pk(date: str, away: str, home: str):
    """game_pk for (date, away, home), or None. First game on a doubleheader.
    Feed abbrevs are canonicalized (ATH->OAK, etc.) to match the schedule index."""
    global _ambiguous_doubleheaders
    pks = game_pks_for_date(date).get((_canon_abbr(away), _canon_abbr(home)))
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
        cached = _load_gcs_cache(_gcs_player_key(season))
        if cached is not None:
            _player_cache[season] = _player_index_from_json(cached)
        else:
            try:
                name_to_ids, name_team_to_id = _build_player_index(_fetch_players(season))
            except Exception:  # noqa: BLE001
                name_to_ids, name_team_to_id = {}, {}
            _player_cache[season] = (name_to_ids, name_team_to_id)
            _save_gcs_cache(_gcs_player_key(season),
                           _player_index_to_json(name_to_ids, name_team_to_id))
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
