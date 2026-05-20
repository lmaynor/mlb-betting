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

  After getting events from either source, the extractors slice out
  the markets each system needs:

    extract_hr_props(events)      → {player_name: {odds, away_team, home_team, ...}}
    extract_nrfi_odds(events)     → {event_id: {nrfi_odds, yrfi_odds, ...}}
    extract_1i_3way_odds(events)  → {event_id: {away_odds, home_odds, draw_odds, ...}}
    extract_f5_odds(events)       → {event_id: {f5_over_odds, f5_under_odds, line, ...}}
    extract_f5_ml_odds(events)    → {event_id: {home_odds, away_odds, ...}}
    extract_k_odds(events)        → {pitcher_name: {over_odds, under_odds, line, ...}}
    extract_outs_odds(events)     → {pitcher_name: {over_odds, under_odds, line, ...}}

  All extractors read DraftKings only and also capture fairOdds +
  openBookOdds for future analysis without changing the primary contract.

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

SGO_API_KEY  = os.environ.get("SGO_API_KEY", "")
SGO_API_BASE = "https://api.sportsgameodds.com"

# Amateur tier: 10 req/min. We pace at 7 sec between calls to leave headroom.
SGO_REQUEST_INTERVAL_SEC = 7.0

# Onshore US books we accept. Offshore (bovada, unibet, williamhill, etc.) excluded.
ONSHORE_BOOKS = {
    "draftkings", "fanduel", "caesars", "betmgm", "espnbet", "thescore", "pointsbet",
}

# SGO key -> canonical name stored in DB.
# ESPN Bet rebranded to theScore Bet in 2025; SGO may use either key.
BOOK_CANONICAL: dict[str, str] = {
    "draftkings": "draftkings",
    "fanduel":    "fanduel",
    "caesars":    "caesars",
    "betmgm":     "betmgm",
    "espnbet":    "thescore",
    "thescore":   "thescore",
    "pointsbet":  "pointsbet",
}

# Slate windowing is done in Eastern Time.
_ET = ZoneInfo("America/New_York")

# Market ID constants — confirmed against live CLE-LAA payload (2026-05-13)
_HR_YN_PREFIX     = "batting_homeRuns-"
_HR_OU_PREFIX     = "batting_homeRuns-"
_K_PREFIX         = "pitching_strikeouts-"
_OUTS_PREFIX      = "pitching_outs-"
_BATTER_HITS_PREFIX = "batting_hits-"
_BATTER_TB_PREFIX   = "batting_totalBases-"
_BATTER_K_PREFIX    = "batting_strikeouts-"
_PITCHER_ER_PREFIX  = "pitching_earnedRuns-"
_NRFI_OVER_ID     = "points-all-1i-ou-over"
_NRFI_UNDER_ID    = "points-all-1i-ou-under"
_1I_3WAY_AWAY_ID  = "points-away-1i-ml3way-away"   # away scores, home doesn't
_1I_3WAY_HOME_ID  = "points-home-1i-ml3way-home"   # home scores, away doesn't
_1I_3WAY_DRAW_ID  = "points-all-1i-ml3way-draw"    # neither scores (= NRFI)
_F5_OVER_ID       = "points-all-1ix5-ou-over"
_F5_UNDER_ID      = "points-all-1ix5-ou-under"
_F5_ML_HOME_ID    = "points-home-1ix5-ml-home"
_F5_ML_AWAY_ID    = "points-away-1ix5-ml-away"


# ── Helpers ───────────────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """NFD + ASCII fold + lower + strip."""
    if not isinstance(name, str):
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return n.encode("ascii", "ignore").decode().lower().strip()


def et_day_window(run_date: str) -> tuple[str, str]:
    """Return (startsAfter, startsBefore) as ISO strings with ET offset."""
    y, m, d = (int(x) for x in run_date.split("-"))
    start = datetime(y, m, d, 0, 0, 0, tzinfo=_ET)
    end   = datetime(y, m, d, 23, 59, 59, tzinfo=_ET)
    return start.isoformat(), end.isoformat()


def _parse_odds_int(raw) -> Optional[int]:
    if raw is None:
        return None
    try:
        return int(str(raw).replace("\u2212", "-").strip())
    except (ValueError, TypeError):
        return None


def _best_book_odds_int(odd_entry: dict) -> tuple[Optional[int], Optional[str]]:
    """Best American odds across onshore US books.

    Returns (odds_int, book_name) or (None, None) if nothing available.
    Best = highest American odds value (most favorable to the bettor).
    """
    by_book = odd_entry.get("byBookmaker") or {}
    best_odds, best_book = None, None
    for book, info in by_book.items():
        if book not in ONSHORE_BOOKS:
            continue
        if not info.get("available"):
            continue
        val = _parse_odds_int(info.get("odds"))
        if val is None:
            continue
        if best_odds is None or val > best_odds:
            best_odds, best_book = val, BOOK_CANONICAL.get(book, book)
    return best_odds, best_book


def _dk_odds_int(odd_entry: dict) -> Optional[int]:
    """Compat alias -- returns best onshore odds without book name."""
    odds, _ = _best_book_odds_int(odd_entry)
    return odds


def _dk_line_float(odd_entry: dict) -> Optional[float]:
    """Over/under line from first available onshore book (lines are identical across books)."""
    by_book = odd_entry.get("byBookmaker") or {}
    for book in ONSHORE_BOOKS:
        info = by_book.get(book) or {}
        if not info.get("available"):
            continue
        raw = info.get("overUnder")
        if raw is None:
            continue
        try:
            return float(raw)
        except (ValueError, TypeError):
            continue
    return None


def _event_teams(event: dict) -> tuple[str, str]:
    """Return (away_name, home_name) using medium names."""
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
        body = self._get("/v2/account/usage")
        return body.get("data", body)

    def fetch_mlb_slate(self, run_date: str | None = None,
                         odds_available: bool = True,
                         limit: int = 50) -> list[dict]:
        params: dict = {
            "leagueID":      "MLB",
            "oddsAvailable": "true" if odds_available else "false",
            "limit":         str(limit),
        }
        if run_date:
            starts_after, starts_before = et_day_window(run_date)
            params["startsAfter"]  = starts_after
            params["startsBefore"] = starts_before
        # Let exceptions propagate — callers (snapshot_odds.py) catch them
        # and post Discord alerts. Swallowing here made errors invisible.
        body = self._get("/v2/events", params)
        return body.get("data") or []


# ── Snapshot I/O ──────────────────────────────────────────────────────────

def load_snapshot(gcs_key: str) -> list[dict]:
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


def check_snapshot_freshness(gcs_key: str, max_age_hours: float = 4.0) -> tuple[bool, str]:
    """Return (is_fresh, reason). Runners should refuse to score if not fresh.

    A stale snapshot causes all systems to price against yesterday's lines,
    generating fictitious edge. Checking before scoring is cheap insurance.

    Args:
        gcs_key:       GCS path to the snapshot, e.g. 'Odds/sgo/latest.json'.
        max_age_hours: Max acceptable age. Default 4h covers the gap between
                       the 10:55 AM and 4:55 PM ET scheduled snapshot runs.

    Returns:
        (True, "ok: snapshot is X.Xh old")   — fresh enough, proceed
        (False, "<reason>")                   — stale or missing, abort run
    """
    from mlb_core.storage import stat
    from datetime import datetime, timezone

    info = stat(gcs_key)
    if info is None:
        return False, f"snapshot missing: {gcs_key}"
    mtime = info["mtime_utc"]
    if mtime.tzinfo is None:
        mtime = mtime.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - mtime).total_seconds() / 3600
    if age_hours > max_age_hours:
        return False, (
            f"snapshot is {age_hours:.1f}h old (max {max_age_hours}h) — "
            f"Cloud Scheduler may have missed a snapshot run. "
            f"Refusing to score against stale lines: {gcs_key}"
        )
    return True, f"snapshot is {age_hours:.1f}h old — ok"


# ── Extractors ────────────────────────────────────────────────────────────

def extract_hr_props(events: list[dict]) -> dict:
    """Extract anytime-HR props for every player on every event.

    Returns {player_name: {odds, away_team, home_team, event_id, line,
                            fair_odds, open_odds, source_oddid}}
    Reading priority: yn-yes → ou-over@0.5 fallback.
    """
    out: dict = {}
    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}

        per_player_yn: dict = {}
        per_player_ou: dict = {}

        for odd_id, entry in odds.items():
            if not odd_id.startswith(_HR_YN_PREFIX):
                continue
            if entry.get("statID") != "batting_homeRuns":
                continue
            player_id = entry.get("playerID") or entry.get("statEntityID")
            if not player_id:
                continue
            side     = entry.get("sideID")
            bet_type = entry.get("betTypeID")
            if bet_type == "yn" and side == "yes":
                per_player_yn[player_id] = (odd_id, entry)
            elif bet_type == "ou" and side == "over":
                line = _dk_line_float(entry)
                fair_line = entry.get("fairOverUnder")
                if line == 0.5 or (line is None and str(fair_line) == "0.5"):
                    per_player_ou[player_id] = (odd_id, entry)

        for player_id, (yn_oid, yn_entry) in per_player_yn.items():
            row = _hr_row_from_entry(event, player_id, yn_oid, yn_entry,
                                      away, home, event_id)
            if row:
                out[row.pop("_player_name")] = row

        for player_id, (ou_oid, ou_entry) in per_player_ou.items():
            if player_id in per_player_yn:
                continue
            row = _hr_row_from_entry(event, player_id, ou_oid, ou_entry,
                                      away, home, event_id)
            if row:
                out[row.pop("_player_name")] = row

    logger.info(f"SGO extract_hr_props: {len(out)} players with DK prices "
                f"across {len(events)} events")
    return out


def _hr_row_from_entry(event: dict, player_id: str, odd_id: str,
                       entry: dict, away: str, home: str,
                       event_id: str) -> Optional[dict]:
    dk_odds, _book = _best_book_odds_int(entry)
    if dk_odds is None:
        return None
    name = _player_name(event, player_id)
    if not name:
        return None
    return {
        "_player_name": name,
        "odds":         dk_odds,
        "bookmaker":    _book,
        "away_team":    away,
        "home_team":    home,
        "event_id":     event_id,
        "line":         _dk_line_float(entry) or 0.5,
        "fair_odds":    _safe_int(entry.get("fairOdds")),
        "open_odds":    _safe_int(entry.get("openBookOdds")),
        "source_oddid": odd_id,
    }


def extract_nrfi_odds(events: list[dict]) -> dict:
    """Extract NRFI/YRFI O/U odds (under 0.5 = NRFI, over 0.5 = YRFI).

    Returns {event_id: {away_team, home_team, commence_time,
                         nrfi_odds, yrfi_odds, point, bookmaker,
                         fair_nrfi, fair_yrfi, open_nrfi, open_yrfi}}
    """
    out: dict = {}
    for event in events:
        odds        = event.get("odds") or {}
        over_entry  = odds.get(_NRFI_OVER_ID)
        under_entry = odds.get(_NRFI_UNDER_ID)
        if not over_entry or not under_entry:
            continue
        yrfi, _yrfi_book = _best_book_odds_int(over_entry)
        nrfi, _nrfi_book = _best_book_odds_int(under_entry)
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
            "nrfi_book":     _nrfi_book,
            "yrfi_book":     _yrfi_book,
            "bookmaker":     _nrfi_book,
            "fair_nrfi":     _safe_int(under_entry.get("fairOdds")),
            "fair_yrfi":     _safe_int(over_entry.get("fairOdds")),
            "open_nrfi":     _safe_int(under_entry.get("openBookOdds")),
            "open_yrfi":     _safe_int(over_entry.get("openBookOdds")),
        }
    logger.info(f"SGO extract_nrfi_odds: {len(out)} events with onshore prices")
    return out


def extract_1i_3way_odds(events: list[dict]) -> dict:
    """Extract first-inning 3-way moneyline odds for every event.

    The three legs and their market IDs (confirmed 2026-05-13):
      away scores, home doesn't  → points-away-1i-ml3way-away
      home scores, away doesn't  → points-home-1i-ml3way-home
      neither scores (draw)      → points-all-1i-ml3way-draw

    Returns:
        {
          event_id: {
            "away_team":     str,
            "home_team":     str,
            "commence_time": str,
            "away_odds":     int,   # DK American — away scores, home doesn't
            "home_odds":     int,   # DK American — home scores, away doesn't
            "draw_odds":     int,   # DK American — neither scores
            "bookmaker":     "draftkings",
            "fair_away":     int,
            "fair_home":     int,
            "fair_draw":     int,
          },
          ...
        }

    All three legs must have DK prices for an event to be included.
    Events with any leg missing are silently dropped.
    """
    out: dict = {}
    for event in events:
        odds        = event.get("odds") or {}
        away_entry  = odds.get(_1I_3WAY_AWAY_ID)
        home_entry  = odds.get(_1I_3WAY_HOME_ID)
        draw_entry  = odds.get(_1I_3WAY_DRAW_ID)
        if not away_entry or not home_entry or not draw_entry:
            continue
        away_odds, _away_book = _best_book_odds_int(away_entry)
        home_odds, _home_book = _best_book_odds_int(home_entry)
        draw_odds, _draw_book = _best_book_odds_int(draw_entry)
        if away_odds is None or home_odds is None or draw_odds is None:
            continue
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        commence   = (event.get("status") or {}).get("startsAt", "")
        out[event_id] = {
            "away_team":     away,
            "home_team":     home,
            "commence_time": commence,
            "away_odds":     away_odds,
            "home_odds":     home_odds,
            "draw_odds":     draw_odds,
            "bookmaker":     _away_book,
            "fair_away":     _safe_int(away_entry.get("fairOdds")),
            "fair_home":     _safe_int(home_entry.get("fairOdds")),
            "fair_draw":     _safe_int(draw_entry.get("fairOdds")),
        }
    logger.info(f"SGO extract_1i_3way_odds: {len(out)} events with onshore prices")
    return out


def extract_f5_odds(events: list[dict]) -> dict:
    """Extract first-5-innings O/U odds.

    Returns {event_id: {away_team, home_team, commence_time,
                         over_odds, under_odds, line, bookmaker,
                         fair_over, fair_under, open_over, open_under}}
    """
    out: dict = {}
    for event in events:
        odds        = event.get("odds") or {}
        over_entry  = odds.get(_F5_OVER_ID)
        under_entry = odds.get(_F5_UNDER_ID)
        if not over_entry or not under_entry:
            continue
        over_odds, _over_book   = _best_book_odds_int(over_entry)
        under_odds, _under_book = _best_book_odds_int(under_entry)
        if over_odds is None or under_odds is None:
            continue
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        commence   = (event.get("status") or {}).get("startsAt", "")
        out[event_id] = {
            "away_team":     away,
            "home_team":     home,
            "commence_time": commence,
            "over_odds":     over_odds,
            "under_odds":    under_odds,
            "line":          _dk_line_float(over_entry),
            "bookmaker":     _over_book,
            "fair_over":     _safe_int(over_entry.get("fairOdds")),
            "fair_under":    _safe_int(under_entry.get("fairOdds")),
            "open_over":     _safe_int(over_entry.get("openBookOdds")),
            "open_under":    _safe_int(under_entry.get("openBookOdds")),
        }
    logger.info(f"SGO extract_f5_odds: {len(out)} events with onshore prices")
    return out


def extract_f5_ml_odds(events: list[dict]) -> dict:
    """Extract first-5-innings two-way moneyline odds.

    Returns {event_id: {away_team, home_team, commence_time,
                         home_odds, away_odds, bookmaker,
                         fair_home, fair_away, open_home, open_away}}
    """
    out: dict = {}
    for event in events:
        odds       = event.get("odds") or {}
        home_entry = odds.get(_F5_ML_HOME_ID)
        away_entry = odds.get(_F5_ML_AWAY_ID)
        if not home_entry or not away_entry:
            continue
        home_odds, _home_book = _best_book_odds_int(home_entry)
        away_odds, _away_book = _best_book_odds_int(away_entry)
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
            "bookmaker":     _home_book or _away_book,
            "fair_home":     _safe_int(home_entry.get("fairOdds")),
            "fair_away":     _safe_int(away_entry.get("fairOdds")),
            "open_home":     _safe_int(home_entry.get("openBookOdds")),
            "open_away":     _safe_int(away_entry.get("openBookOdds")),
        }
    logger.info(f"SGO extract_f5_ml_odds: {len(out)} events with onshore prices")
    return out


def extract_k_odds(events: list[dict]) -> dict:
    """Extract pitcher strikeout O/U odds.

    Returns {pitcher_name: {over_odds, under_odds, line,
                              away_team, home_team, event_id, bookmaker,
                              fair_over, fair_under, open_over, open_under}}
    """
    out: dict = {}
    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}

        over_map:  dict = {}
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
            _, under_entry = under_pair
            over_odds, _over_book   = _best_book_odds_int(over_entry)
            under_odds, _under_book = _best_book_odds_int(under_entry)
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
                "bookmaker":  _over_book,
                "fair_over":  _safe_int(over_entry.get("fairOdds")),
                "fair_under": _safe_int(under_entry.get("fairOdds")),
                "open_over":  _safe_int(over_entry.get("openBookOdds")),
                "open_under": _safe_int(under_entry.get("openBookOdds")),
            }
    logger.info(f"SGO extract_k_odds: {len(out)} pitchers with onshore prices")
    return out


def extract_outs_odds(events: list[dict]) -> dict:
    """Extract pitcher outs recorded O/U odds.

    Market ID pattern: pitching_outs-{PITCHER_MLB}-game-ou-over/under
    Confirmed in live payload (2026-05-13).

    Returns:
        {
          pitcher_name: {
            "over_odds":  int,
            "under_odds": int,
            "line":       float,   # e.g. 14.5 outs = ~4.8 IP
            "away_team":  str,
            "home_team":  str,
            "event_id":   str,
            "bookmaker":  "draftkings",
            "fair_over":  int,
            "fair_under": int,
          },
          ...
        }

    Same structure as extract_k_odds — just a different stat prefix.
    """
    out: dict = {}
    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}

        over_map:  dict = {}
        under_map: dict = {}
        for odd_id, entry in odds.items():
            if not odd_id.startswith(_OUTS_PREFIX):
                continue
            if entry.get("statID") != "pitching_outs":
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
            _, under_entry = under_pair
            over_odds, _over_book   = _best_book_odds_int(over_entry)
            under_odds, _under_book = _best_book_odds_int(under_entry)
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
                "bookmaker":  _over_book,
                "fair_over":  _safe_int(over_entry.get("fairOdds")),
                "fair_under": _safe_int(under_entry.get("fairOdds")),
            }
    logger.info(f"SGO extract_outs_odds: {len(out)} pitchers with onshore prices")
    return out


def _safe_int(raw) -> Optional[int]:
    """Parse American odds string to int. None on failure."""
    if raw is None:
        return None
    try:
        return int(str(raw).replace("\u2212", "-").strip())
    except (ValueError, TypeError):
        return None


def _extract_player_ou_props(events: list, prefix: str, stat_id: str,
                              log_label: str) -> dict:
    """Generic player O/U extractor -- shared by all four new batter/pitcher markets.

    Mirrors extract_k_odds() pattern exactly: two-pass over/under per playerID,
    zip into output dict keyed by display name from event players block.
    Uses _best_book_odds_int() for multi-book best-odds selection.
    """
    out: dict = {}
    for event in events:
        away, home = _event_teams(event)
        event_id   = event.get("eventID")
        odds       = event.get("odds") or {}
        over_map:  dict = {}
        under_map: dict = {}
        for odd_id, entry in odds.items():
            if not odd_id.startswith(prefix):
                continue
            if entry.get("statID") != stat_id:
                continue
            if entry.get("betTypeID") != "ou":
                continue
            player_id = entry.get("playerID") or entry.get("statEntityID")
            if not player_id:
                continue
            side = entry.get("sideID")
            if side == "over":
                over_map[player_id] = entry
            elif side == "under":
                under_map[player_id] = entry

        for player_id, over_entry in over_map.items():
            under_entry = under_map.get(player_id)
            if not under_entry:
                continue
            over_odds,  over_book  = _best_book_odds_int(over_entry)
            under_odds, under_book = _best_book_odds_int(under_entry)
            if over_odds is None or under_odds is None:
                continue
            name = _player_name(event, player_id)
            if not name:
                continue
            book = over_book or under_book
            out[name] = {
                "over_odds":  over_odds,
                "under_odds": under_odds,
                "line":       _dk_line_float(over_entry),
                "away_team":  away,
                "home_team":  home,
                "event_id":   event_id,
                "bookmaker":  book,
                "fair_over":  _safe_int(over_entry.get("fairOdds")),
                "fair_under": _safe_int(under_entry.get("fairOdds")),
                "open_over":  _safe_int(over_entry.get("openBookOdds")),
                "open_under": _safe_int(under_entry.get("openBookOdds")),
            }

    logger.info(f"SGO {log_label}: {len(out)} players with onshore prices")
    return out


def extract_batter_hits_odds(events: list) -> dict:
    """Extract batter hits O/U odds. Returns {player_name: {over_odds, under_odds, line, ...}}"""
    return _extract_player_ou_props(events, _BATTER_HITS_PREFIX, "batting_hits",
                                    "extract_batter_hits_odds")


def extract_batter_tb_odds(events: list) -> dict:
    """Extract batter total bases O/U odds."""
    return _extract_player_ou_props(events, _BATTER_TB_PREFIX, "batting_totalBases",
                                    "extract_batter_tb_odds")


def extract_batter_k_odds(events: list) -> dict:
    """Extract batter strikeouts O/U odds."""
    return _extract_player_ou_props(events, _BATTER_K_PREFIX, "batting_strikeouts",
                                    "extract_batter_k_odds")


def extract_pitcher_er_odds(events: list) -> dict:
    """Extract pitcher earned runs O/U odds."""
    return _extract_player_ou_props(events, _PITCHER_ER_PREFIX, "pitching_earnedRuns",
                                    "extract_pitcher_er_odds")


# F1H and GAME innings-window two-way ML extractors.
# F3 and F7 intentionally omitted -- SGO serves them as 3-way markets
# (draw/not-draw) incompatible with the F5 home/away proxy model.
# See CONTEXT.md §8.
_F1H_ML_HOME_ID  = "points-home-1h-ml-home"
_F1H_ML_AWAY_ID  = "points-away-1h-ml-away"
_GAME_ML_HOME_ID = "points-home-game-ml-home"
_GAME_ML_AWAY_ID = "points-away-game-ml-away"


def _extract_two_sided_innings_ml(events: list, home_id: str,
                                   away_id: str, log_label: str) -> dict:
    """Shared helper for two-sided innings-window ML extraction.

    Mirrors extract_f5_ml_odds() -- uses _best_book_odds_int for
    multi-book best-odds selection.
    """
    out: dict = {}
    for event in events:
        odds       = event.get("odds") or {}
        home_entry = odds.get(home_id)
        away_entry = odds.get(away_id)
        if not home_entry or not away_entry:
            continue
        home_odds, home_book = _best_book_odds_int(home_entry)
        away_odds, away_book = _best_book_odds_int(away_entry)
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
            "bookmaker":     home_book or away_book,
            "fair_home":     _safe_int(home_entry.get("fairOdds")),
            "fair_away":     _safe_int(away_entry.get("fairOdds")),
            "open_home":     _safe_int(home_entry.get("openBookOdds")),
            "open_away":     _safe_int(away_entry.get("openBookOdds")),
        }
    logger.info(f"SGO {log_label}: {len(out)} events with onshore prices")
    return out


def extract_f1h_ml_odds(events: list) -> dict:
    """Extract first-half (innings 1-4) two-way moneyline odds."""
    return _extract_two_sided_innings_ml(
        events, _F1H_ML_HOME_ID, _F1H_ML_AWAY_ID, "extract_f1h_ml_odds")


def extract_game_ml_odds(events: list) -> dict:
    """Extract full-game two-way moneyline odds."""
    return _extract_two_sided_innings_ml(
        events, _GAME_ML_HOME_ID, _GAME_ML_AWAY_ID, "extract_game_ml_odds")
