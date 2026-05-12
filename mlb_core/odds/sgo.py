"""
mlb_core.odds.sgo — SportsGameOdds API client and market extractors.

Why this exists:
  Replaces The Odds API for all MLB odds. One /v2/events call returns the
  full slate with every market (HR / NRFI / F5 / K) and every bookmaker —
  billed as N objects (one per game), regardless of market count.

Two entry points:

  1. fetch_mlb_slate() — live API call. Returns list of event dicts.
     Cost: N objects (N = today's slate size). Used by snapshot_odds.py.

  2. load_snapshot(gcs_key) — reads a saved snapshot JSON from GCS.
     Cost: 0 objects. Used by every runner (HR, NRFI, F5, K) at predict time.

  After getting events from either source, the four extractors slice out
  the markets each system needs:

    extract_hr_props(events)  → {player_name: {odds, away_team, home_team, event_id, ...}}
    extract_nrfi_odds(events) → {event_id: {nrfi_odds, yrfi_odds, ...}}
    extract_f5_odds(events)   → {event_id: {f5_over_odds, f5_under_odds, line, ...}}
    extract_k_odds(events)    → {pitcher_name: {odds, line, ...}}

  All extractors read DraftKings only, prefer yn-yes for binary props,
  and also capture fairOdds + openBookOdds for future analysis without
  changing the primary contract.

API docs: https://sportsgameodds.com/docs/
"""
import json
import logging
import os
import time
import unicodedata
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────

SGO_API_KEY  = os.environ.get("SGO_API_KEY", "beceb4f0a660a1204cbc735b9a3082f2")
SGO_API_BASE = "https://api.sportsgameodds.com"

# Amateur tier: 10 req/min. We pace at 7 sec between calls to leave headroom.
SGO_REQUEST_INTERVAL_SEC = 7.0

# Bookmaker we read for predictions. SGO returns many; we use DK only because
# HR Pro v6 was trained against DK lines.
PRIMARY_BOOKMAKER = "draftkings"

# Slate windowing is done in Eastern Time. MLB schedules are reported in ET
# and a "today" in ET maps cleanly to the local game day. UTC would
# misclassify late West Coast games into "tomorrow".
_ET = ZoneInfo("America/New_York")

# Market families. Prefixes are confirmed against a real CLE-LAA event payload
# (2026-05-12) and match what the SGO docs publish.
_HR_YN_PREFIX     = "batting_homeRuns-"           # ...-PLAYER_MLB-game-yn-yes/no
_HR_OU_PREFIX     = "batting_homeRuns-"           # ...-PLAYER_MLB-game-ou-over/under
_K_PREFIX         = "pitching_strikeouts-"        # ...-PITCHER_MLB-game-ou-over/under
_NRFI_OVER_ID     = "points-all-1i-ou-over"
_NRFI_UNDER_ID    = "points-all-1i-ou-under"
_F5_OVER_ID       = "points-all-1ix5-ou-over"
_F5_UNDER_ID      = "points-all-1ix5-ou-under"
_F5_ML_HOME_ID    = "points-home-1ix5-ml-home"    # F5 moneyline, home side
_F5_ML_AWAY_ID    = "points-away-1ix5-ml-away"    # F5 moneyline, away side


# ── Helpers ───────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """NFD + ASCII fold + lower + strip. Matches HR runner's _normalize_name.

    SGO returns accented names like "Angel Martínez" / "José Ramírez".
    Lineup data and feature CSVs use varying conventions, so normalize
    both sides through this function before matching.
    """
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


def et_day_window(run_date: str) -> tuple[str, str]:
    """Return (startsAfter, startsBefore) as ISO strings with ET offset.

    A "day" is 00:00:00 ET to 23:59:59 ET on `run_date`. Returned strings
    include explicit offset so SGO interprets them in ET regardless of its
    server timezone. zoneinfo handles DST transitions correctly.

    Example:
      et_day_window("2026-05-12")
        → ("2026-05-12T00:00:00-04:00", "2026-05-12T23:59:59-04:00")
    """
    y, m, d = (int(x) for x in run_date.split("-"))
    start = datetime(y, m, d, 0, 0, 0, tzinfo=_ET)
    end   = datetime(y, m, d, 23, 59, 59, tzinfo=_ET)
    return start.isoformat(), end.isoformat()


def _dk_odds_int(odd_entry: dict) -> Optional[int]:
    """Pull DK odds from a single odd entry's byBookmaker block.

    Returns the American-odds int (e.g. -110, +840) or None if DK isn't
    populated on this market.
    """
    by_book = odd_entry.get("byBookmaker") or {}
    dk = by_book.get(PRIMARY_BOOKMAKER) or {}
    if not dk.get("available"):
        return None
    raw = dk.get("odds")
    if raw is None:
        return None
    try:
        return int(str(raw).replace("\u2212", "-").strip())
    except (ValueError, TypeError):
        return None


def _dk_line_float(odd_entry: dict) -> Optional[float]:
    """Pull DK over/under line from a single odd entry."""
    by_book = odd_entry.get("byBookmaker") or {}
    dk = by_book.get(PRIMARY_BOOKMAKER) or {}
    raw = dk.get("overUnder")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _event_teams(event: dict) -> tuple[str, str]:
    """Return (away_abbrev_or_name, home_abbrev_or_name).

    Prefers the 'medium' name ("Yankees", "Guardians") which matches how
    The Odds API returned them. Falls back to teamID or empty string.
    """
    teams = event.get("teams") or {}
    def _one(side: str) -> str:
        t = teams.get(side) or {}
        names = t.get("names") or {}
        return names.get("medium") or names.get("long") or t.get("teamID") or ""
    return _one("away"), _one("home")


def _player_name(event: dict, player_id: str) -> Optional[str]:
    """Look up a player's display name from the event's inline players block."""
    players = event.get("players") or {}
    p = players.get(player_id) or {}
    return p.get("name")


# ── Client ────────────────────────────────────────────────────────────────

class SgoClient:
    """Thin SGO API client. Stateless aside from a requests.Session."""

    def __init__(self, api_key: str = SGO_API_KEY):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "x-api-key":  api_key,
            "User-Agent": "mlb-betting/1.0",
        })
        self._last_request_at: float = 0.0

    def _throttle(self) -> None:
        """Sleep so we stay under 10 req/min."""
        gap = time.time() - self._last_request_at
        if gap < SGO_REQUEST_INTERVAL_SEC:
            time.sleep(SGO_REQUEST_INTERVAL_SEC - gap)

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._throttle()
        url = f"{SGO_API_BASE}{path}"
        r = self.session.get(url, params=params or {}, timeout=30)
        self._last_request_at = time.time()
        r.raise_for_status()
        return r.json()

    def get_usage(self) -> dict:
        """Return /v2/account/usage payload. Free (does not count against quota)."""
        body = self._get("/v2/account/usage")
        return body.get("data", body)

    def fetch_mlb_slate(self, run_date: str | None = None,
                         odds_available: bool = True,
                         limit: int = 50) -> list[dict]:
        """Fetch MLB events for one ET day.

        One call returns every game in the window with every market —
        billed as one object per event. Without `run_date`, SGO returns
        every event with posted odds (today through ~5 days out), so always
        pass run_date in production.

        Args:
            run_date:       ISO date "YYYY-MM-DD". Filters to events whose
                            start time falls within that ET calendar day.
                            If None, no date filter is applied (use only
                            for diagnostics).
            odds_available: When True (default) only returns events with
                            posted odds.
            limit:          Max events per page. MLB never exceeds ~17 games
                            per day; default 50 is a safe ceiling.

        Returns:
            List of event dicts. Empty list on API error or empty slate.
        """
        params: dict = {
            "leagueID":      "MLB",
            "oddsAvailable": "true" if odds_available else "false",
            "limit":         str(limit),
        }
        if run_date:
            starts_after, starts_before = et_day_window(run_date)
            params["startsAfter"]  = starts_after
            params["startsBefore"] = starts_before
        try:
            body = self._get("/v2/events", params)
        except Exception as e:
            logger.error(f"SGO fetch_mlb_slate failed: {e}")
            return []
        return body.get("data") or []


# ── Snapshot I/O ──────────────────────────────────────────────────────────

def load_snapshot(gcs_key: str) -> list[dict]:
    """Read a saved slate JSON from GCS (or local disk in local mode).

    Returns empty list if the key doesn't exist or fails to parse.
    Uses mlb_core.storage so it works in both modes transparently.
    """
    from mlb_core.storage import exists, read_bytes
    if not exists(gcs_key):
        logger.warning(f"SGO snapshot not found: {gcs_key}")
        return []
    try:
        raw = read_bytes(gcs_key)
        return json.loads(raw)
    except Exception as e:
        logger.error(f"SGO snapshot parse failed for {gcs_key}: {e}")
        return []


# ── Extractors ────────────────────────────────────────────────────────────

def extract_hr_props(events: list[dict]) -> dict:
    """Extract anytime-HR props for every player on every event.

    Drop-in replacement for run_hr._fetch_hr_odds. Return shape:

        {
          player_name: {
            "odds":          int,    # DK American odds for "Yes / Over 0.5"
            "away_team":     str,    # "Yankees"
            "home_team":     str,    # "Guardians"
            "event_id":      str,    # SGO eventID
            "line":          float,  # 0.5 (from the underlying market)
            "fair_odds":     int,    # SGO no-vig consensus (-105 etc.)
            "open_odds":     int,    # DK opening line (None if unavailable)
            "source_oddid":  str,    # which oddID we read
          },
          ...
        }

    Reading priority per player:
        1. ...-yn-yes (binary "Any Home Runs")
        2. ...-ou-over at line 0.5 (mathematically equivalent fallback)

    Players for whom DK posts neither market are silently dropped.
    """
    out: dict = {}

    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}

        # Index this event's HR markets by player and side
        # Two passes: prefer yn-yes, fall back to ou-over@0.5
        per_player_yn: dict = {}  # player_id -> odd entry (yn-yes)
        per_player_ou: dict = {}  # player_id -> odd entry (ou-over @ 0.5)

        for odd_id, entry in odds.items():
            if not odd_id.startswith(_HR_YN_PREFIX):
                continue
            stat_id = entry.get("statID")
            if stat_id != "batting_homeRuns":
                continue
            player_id = entry.get("playerID") or entry.get("statEntityID")
            if not player_id:
                continue
            side = entry.get("sideID")
            bet_type = entry.get("betTypeID")

            if bet_type == "yn" and side == "yes":
                per_player_yn[player_id] = (odd_id, entry)
            elif bet_type == "ou" and side == "over":
                # Only keep the 0.5 line for the OU fallback
                line = _dk_line_float(entry)
                fair_line = entry.get("fairOverUnder")
                if line == 0.5 or (line is None and str(fair_line) == "0.5"):
                    per_player_ou[player_id] = (odd_id, entry)

        # Assemble output rows for this event
        for player_id, (yn_oid, yn_entry) in per_player_yn.items():
            row = _hr_row_from_entry(event, player_id, yn_oid, yn_entry,
                                      away, home, event_id)
            if row:
                # Use player display name as the dict key
                name = row.pop("_player_name")
                out[name] = row

        # Fill in any players who only had ou-over, not yn-yes
        for player_id, (ou_oid, ou_entry) in per_player_ou.items():
            if player_id in per_player_yn:
                continue
            row = _hr_row_from_entry(event, player_id, ou_oid, ou_entry,
                                      away, home, event_id)
            if row:
                name = row.pop("_player_name")
                out[name] = row

    logger.info(f"SGO extract_hr_props: {len(out)} players with DK prices "
                f"across {len(events)} events")
    return out


def _hr_row_from_entry(event: dict, player_id: str, odd_id: str,
                       entry: dict, away: str, home: str,
                       event_id: str) -> Optional[dict]:
    """Build one HR-output row. Returns None if DK not available on this market."""
    dk_odds = _dk_odds_int(entry)
    if dk_odds is None:
        return None
    name = _player_name(event, player_id)
    if not name:
        return None
    return {
        "_player_name":  name,
        "odds":          dk_odds,
        "away_team":     away,
        "home_team":     home,
        "event_id":      event_id,
        "line":          _dk_line_float(entry) or 0.5,
        "fair_odds":     _safe_int(entry.get("fairOdds")),
        "open_odds":     _safe_int(entry.get("openBookOdds")),
        "source_oddid":  odd_id,
    }


def extract_nrfi_odds(events: list[dict]) -> dict:
    """Extract NRFI/YRFI odds for every event.

    Drop-in shape replacement for the_odds_api.fetch_nrfi_odds. Returns:

        {
          event_id: {
            "away_team":     str,
            "home_team":     str,
            "commence_time": str,    # ISO UTC from status.startsAt
            "nrfi_odds":     int,    # DK American for Under 0.5
            "yrfi_odds":     int,    # DK American for Over  0.5
            "point":         0.5,
            "bookmaker":     "draftkings",
            "fair_nrfi":     int,    # SGO no-vig under
            "fair_yrfi":     int,    # SGO no-vig over
            "open_nrfi":     int,    # opening under
            "open_yrfi":     int,    # opening over
          },
          ...
        }
    """
    out: dict = {}
    for event in events:
        odds = event.get("odds") or {}
        over_entry  = odds.get(_NRFI_OVER_ID)
        under_entry = odds.get(_NRFI_UNDER_ID)
        if not over_entry or not under_entry:
            continue

        yrfi = _dk_odds_int(over_entry)
        nrfi = _dk_odds_int(under_entry)
        if nrfi is None or yrfi is None:
            continue

        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        commence   = (event.get("status") or {}).get("startsAt", "")

        out[event_id] = {
            "away_team":     away,
            "home_team":     home,
            "commence_time": commence,
            "nrfi_odds":     nrfi,
            "yrfi_odds":     yrfi,
            "point":         0.5,
            "bookmaker":     PRIMARY_BOOKMAKER,
            "fair_nrfi":     _safe_int(under_entry.get("fairOdds")),
            "fair_yrfi":     _safe_int(over_entry.get("fairOdds")),
            "open_nrfi":     _safe_int(under_entry.get("openBookOdds")),
            "open_yrfi":     _safe_int(over_entry.get("openBookOdds")),
        }

    logger.info(f"SGO extract_nrfi_odds: {len(out)} events with DK prices")
    return out


def extract_f5_odds(events: list[dict]) -> dict:
    """Extract first-5-innings Over/Under odds for every event.

    Returns:
        {
          event_id: {
            "away_team":      str,
            "home_team":      str,
            "commence_time":  str,
            "over_odds":      int,
            "under_odds":     int,
            "line":           float,   # the DK over/under line (e.g. 4.5)
            "bookmaker":      "draftkings",
            "fair_over":      int,
            "fair_under":     int,
            "open_over":      int,
            "open_under":     int,
          },
          ...
        }
    """
    out: dict = {}
    for event in events:
        odds = event.get("odds") or {}
        over_entry  = odds.get(_F5_OVER_ID)
        under_entry = odds.get(_F5_UNDER_ID)
        if not over_entry or not under_entry:
            continue

        over_odds  = _dk_odds_int(over_entry)
        under_odds = _dk_odds_int(under_entry)
        if over_odds is None or under_odds is None:
            continue

        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        commence   = (event.get("status") or {}).get("startsAt", "")
        line       = _dk_line_float(over_entry)

        out[event_id] = {
            "away_team":     away,
            "home_team":     home,
            "commence_time": commence,
            "over_odds":     over_odds,
            "under_odds":    under_odds,
            "line":          line,
            "bookmaker":     PRIMARY_BOOKMAKER,
            "fair_over":     _safe_int(over_entry.get("fairOdds")),
            "fair_under":    _safe_int(under_entry.get("fairOdds")),
            "open_over":     _safe_int(over_entry.get("openBookOdds")),
            "open_under":    _safe_int(under_entry.get("openBookOdds")),
        }

    logger.info(f"SGO extract_f5_odds: {len(out)} events with DK prices")
    return out


def extract_f5_ml_odds(events: list[dict]) -> dict:
    """Extract first-5-innings two-way moneyline odds for every event.

    F5 Pro v5 bets the moneyline (home wins F5 vs away wins F5), not the
    totals over/under. SGO exposes this as a two-sided market with home and
    away as separate oddIDs; pull both for each event.

    Returns:
        {
          event_id: {
            "away_team":     str,
            "home_team":     str,
            "commence_time": str,
            "home_odds":     int,    # DK American for home to win F5
            "away_odds":     int,    # DK American for away to win F5
            "bookmaker":     "draftkings",
            "fair_home":     int,    # SGO no-vig home
            "fair_away":     int,    # SGO no-vig away
            "open_home":     int,    # opening home
            "open_away":     int,    # opening away
          },
          ...
        }
    """
    out: dict = {}
    for event in events:
        odds = event.get("odds") or {}
        home_entry = odds.get(_F5_ML_HOME_ID)
        away_entry = odds.get(_F5_ML_AWAY_ID)
        if not home_entry or not away_entry:
            continue

        home_odds = _dk_odds_int(home_entry)
        away_odds = _dk_odds_int(away_entry)
        if home_odds is None or away_odds is None:
            continue

        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        commence   = (event.get("status") or {}).get("startsAt", "")

        out[event_id] = {
            "away_team":     away,
            "home_team":     home,
            "commence_time": commence,
            "home_odds":     home_odds,
            "away_odds":     away_odds,
            "bookmaker":     PRIMARY_BOOKMAKER,
            "fair_home":     _safe_int(home_entry.get("fairOdds")),
            "fair_away":     _safe_int(away_entry.get("fairOdds")),
            "open_home":     _safe_int(home_entry.get("openBookOdds")),
            "open_away":     _safe_int(away_entry.get("openBookOdds")),
        }

    logger.info(f"SGO extract_f5_ml_odds: {len(out)} events with DK prices")
    return out


def extract_k_odds(events: list[dict]) -> dict:
    """Extract pitcher strikeout Over/Under odds for every pitcher.

    Returns:
        {
          pitcher_name: {
            "over_odds":     int,
            "under_odds":    int,
            "line":          float,
            "away_team":     str,
            "home_team":     str,
            "event_id":      str,
            "bookmaker":     "draftkings",
            "fair_over":     int,
            "fair_under":    int,
            "open_over":     int,
            "open_under":    int,
          },
          ...
        }
    """
    out: dict = {}
    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}

        # Two passes — collect over and under per pitcher, then zip
        over_map: dict = {}
        under_map: dict = {}
        for odd_id, entry in odds.items():
            if not odd_id.startswith(_K_PREFIX):
                continue
            if entry.get("statID") != "pitching_strikeouts":
                continue
            if entry.get("betTypeID") != "ou":
                continue
            player_id = entry.get("playerID") or entry.get("statEntityID")
            if not player_id:
                continue
            side = entry.get("sideID")
            if side == "over":
                over_map[player_id] = (odd_id, entry)
            elif side == "under":
                under_map[player_id] = (odd_id, entry)

        for player_id, (over_oid, over_entry) in over_map.items():
            under_pair = under_map.get(player_id)
            if not under_pair:
                continue
            under_oid, under_entry = under_pair

            over_odds = _dk_odds_int(over_entry)
            under_odds = _dk_odds_int(under_entry)
            if over_odds is None or under_odds is None:
                continue

            name = _player_name(event, player_id)
            if not name:
                continue

            out[name] = {
                "over_odds":  over_odds,
                "under_odds": under_odds,
                "line":       _dk_line_float(over_entry),
                "away_team":  away,
                "home_team":  home,
                "event_id":   event_id,
                "bookmaker":  PRIMARY_BOOKMAKER,
                "fair_over":  _safe_int(over_entry.get("fairOdds")),
                "fair_under": _safe_int(under_entry.get("fairOdds")),
                "open_over":  _safe_int(over_entry.get("openBookOdds")),
                "open_under": _safe_int(under_entry.get("openBookOdds")),
            }

    logger.info(f"SGO extract_k_odds: {len(out)} pitchers with DK prices")
    return out


def _safe_int(raw) -> Optional[int]:
    """Parse American odds string like "+102" / "-115" to int. None on failure."""
    if raw is None:
        return None
    try:
        return int(str(raw).replace("\u2212", "-").strip())
    except (ValueError, TypeError):
        return None
