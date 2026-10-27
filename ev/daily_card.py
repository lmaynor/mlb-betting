"""
ev.daily_card -- Unified source of truth for the Daily Card across Discord, Web (/cheat-sheet), and X.

Official Card Gate (Tier 1):
  - Projections (NFL / NBA / NHL):
      confluence is True (both model and market agree on the side)
      n_books >= 4 (thick reference consensus)
      0.030 <= market_ev <= 0.100 (sweet-spot EV; cuts out adverse selection traps > 10%)
      model_edge >= 0.015 (at least +1.5% model probability edge)
      beezy_score >= 68
  - MLB Validated Systems:
      validated is True (or validated system)
      0.030 <= edge <= 0.100
      beezy_score >= 68
  - Sink A Pure Market Dislocations (fallback eligible when < 2 confluence plays exist):
      n_books >= 5
      disp <= 0.035
      0.040 <= ev <= 0.095
      book not offshore

EV Outlier Gate (Tier 2):
  - n_books >= 3
  - disp <= 0.06 (if present)
  - 0.025 <= market_ev <= 0.150
  - not an official card play

Dynamic Card Builder:
  - 1 to 5 plays max
  - Filter: mins_to_start >= 30
  - Deduplicate: max 1 play per player (highest beezy score), max 2 plays per game
  - Dynamic size: NEVER pad with weak plays to force 5. If only 2 qualify, return 2.
"""
from __future__ import annotations

from datetime import datetime
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from mlb_core.alerts.beezy_score import beezy_score
from mlb_core import storage

log = logging.getLogger("ev.daily_card")

_CT = ZoneInfo("America/Chicago")

OFFSHORE_BOOKS = {
    "bovada", "betfair", "pinnacle", "betonline", "betonlineag",
    "mybookie", "bookmakereu", "heritagesports", "lowvig",
    "betanysports", "sportsbetting", "betus", "matchbook",
}

VALIDATED_MLB_SYSTEMS = {
    "HR", "NRFI", "K", "F5", "GAME", "BATTER_HITS", "BATTER_TB", "SB", "OUTS",
}


def is_offshore_book(book: str | None) -> bool:
    if not book:
        return False
    return str(book).strip().lower() in OFFSHORE_BOOKS


def parse_ev(r: dict[str, Any]) -> float:
    for k in ("market_ev", "ev", "edge"):
        v = r.get(k)
        if v is not None:
            try:
                val = float(v)
                return val / 100.0 if abs(val) >= 2.0 else val
            except (ValueError, TypeError):
                continue
    return 0.0


def parse_model_edge(r: dict[str, Any]) -> float:
    for k in ("model_edge", "edge"):
        v = r.get(k)
        if v is not None:
            try:
                val = float(v)
                return val / 100.0 if abs(val) >= 2.0 else val
            except (ValueError, TypeError):
                continue
    return 0.0


def parse_beezy_score(r: dict[str, Any]) -> int:
    for k in ("beezy_score", "score"):
        v = r.get(k)
        if v is not None:
            try:
                return int(v)
            except (ValueError, TypeError):
                continue
    ev = parse_ev(r)
    n_books = r.get("n_books")
    p_fair = r.get("p_fair")
    if ev and n_books:
        try:
            return beezy_score(ev, int(n_books), float(p_fair) if p_fair is not None else None)
        except Exception:
            pass
    return 0


def is_confluence_play(r: dict[str, Any]) -> bool:
    """NFL / NBA / NHL projection row meeting strict Tier 1 Official Play criteria."""
    if not bool(r.get("confluence")):
        return False
    try:
        n_books = int(r.get("n_books") or 0)
    except (ValueError, TypeError):
        return False
    if n_books < 4:
        return False

    ev = parse_ev(r)
    if not (0.030 <= ev <= 0.100):
        return False

    model_edge = parse_model_edge(r)
    if model_edge < 0.015:
        return False

    score = parse_beezy_score(r)
    if score < 68:
        return False

    return True


def is_mlb_validated_play(r: dict[str, Any]) -> bool:
    """MLB validated system play meeting strict Tier 1 Official Play criteria."""
    sport = str(r.get("sport") or "").lower()
    system = str(r.get("system") or "").upper()
    is_mlb = sport == "mlb" or bool(r.get("bet_type")) or system in VALIDATED_MLB_SYSTEMS
    if not is_mlb:
        return False

    val = r.get("validated")
    if val is False:
        return False
    # If not explicitly validated and not in validated MLB systems, reject
    if val is None and system not in VALIDATED_MLB_SYSTEMS:
        return False

    edge = parse_ev(r)
    if not (0.030 <= edge <= 0.100):
        return False

    score = parse_beezy_score(r)
    if score < 68:
        return False

    return True


def is_dislocation_fallback_play(r: dict[str, Any]) -> bool:
    """Pure market dislocation fallback when < 2 confluence plays exist on the slate."""
    try:
        n_books = int(r.get("n_books") or 0)
    except (ValueError, TypeError):
        return False
    if n_books < 5:
        return False

    disp = r.get("disp")
    if disp is not None:
        try:
            if float(disp) > 0.035:
                return False
        except (ValueError, TypeError):
            return False

    ev = parse_ev(r)
    if not (0.040 <= ev <= 0.095):
        return False

    book = r.get("book") or r.get("best_over_book") or r.get("best_under_book")
    if is_offshore_book(book):
        return False

    return True


def is_official_card_play(r: dict[str, Any], allow_dislocation: bool = False) -> bool:
    """
    Tier 1 gate: checks whether a row qualifies as an Official Card Play.
    - NFL / NBA / NHL Projections: confluence=True, n_books>=4, 3%-10% EV, model_edge>=1.5%, score>=68.
    - MLB Bets: validated=True, 3%-10% edge, score>=68.
    - Pure Market Dislocations: only when allow_dislocation=True (slate has < 2 confluence plays).
    """
    if is_confluence_play(r):
        return True
    if is_mlb_validated_play(r):
        return True
    if allow_dislocation and is_dislocation_fallback_play(r):
        return True
    return False


def is_ev_outlier(r: dict[str, Any]) -> bool:
    """
    Tier 2 gate: +EV Outlier for scanner and discord signals.
    - n_books >= 3
    - disp <= 0.06 (if disp present)
    - 0.025 <= market_ev <= 0.150
    """
    try:
        n_books = int(r.get("n_books") or 0)
    except (ValueError, TypeError):
        return False
    if n_books < 3:
        return False

    disp = r.get("disp")
    if disp is not None:
        try:
            if float(disp) > 0.06:
                return False
        except (ValueError, TypeError):
            pass

    ev = parse_ev(r)
    if not (0.025 <= ev <= 0.150):
        return False

    if is_official_card_play(r, allow_dislocation=False):
        return False

    return True


def build_daily_card(rows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    """
    Build 1 to 5 dynamic locked card plays for today's slate.
    - Filters rows with >= 30 minutes to start
    - Checks for confluence plays; if < 2 exist, permits pure market dislocations
    - Deduplicates: max 1 play per player, max 2 plays per game
    - Sorts: confluence first, then beezy_score desc, n_books desc, EV desc
    - Takes top min(5, len(candidates)). NEVER pads with weak filler.
    """
    candidates = []
    for r in rows:
        mins = None
        for k in ("mins_to_start", "mins_to_kick"):
            v = r.get(k)
            if v is not None:
                try:
                    mins = float(v)
                    break
                except (ValueError, TypeError):
                    continue
        if mins is not None and mins < 30:
            continue
        candidates.append(r)

    confluence_plays = [r for r in candidates if is_official_card_play(r, allow_dislocation=False)]
    allow_dislocation = len(confluence_plays) < 2

    qualified = [r for r in candidates if is_official_card_play(r, allow_dislocation=allow_dislocation)]
    if not qualified:
        return []

    # Deduplicate: Max 1 play per player (keep highest beezy score, tie-break higher EV)
    by_player: dict[str, dict[str, Any]] = {}
    for r in qualified:
        p = str(r.get("player") or r.get("player_norm") or r.get("who") or "").strip().lower()
        if not p:
            p = f"game_{r.get('game_key') or r.get('id') or id(r)}"
        curr = by_player.get(p)
        if curr is None:
            by_player[p] = r
        else:
            s_curr = parse_beezy_score(curr)
            s_new = parse_beezy_score(r)
            if s_new > s_curr or (s_new == s_curr and parse_ev(r) > parse_ev(curr)):
                by_player[p] = r

    player_deduped = list(by_player.values())

    def sort_key(r: dict[str, Any]):
        conf = 1 if (is_confluence_play(r) or is_mlb_validated_play(r)) else 0
        score = parse_beezy_score(r)
        try:
            n_b = int(r.get("n_books") or 0)
        except (ValueError, TypeError):
            n_b = 0
        ev = parse_ev(r)
        return (-conf, -score, -n_b, -ev)

    player_deduped.sort(key=sort_key)

    # Deduplicate: Max 2 plays per game
    game_counts: dict[str, int] = {}
    game_deduped = []
    for r in player_deduped:
        g = str(r.get("game_key") or r.get("game") or "").strip().lower()
        if not g:
            game_deduped.append(r)
            continue
        c = game_counts.get(g, 0)
        if c < 2:
            game_counts[g] = c + 1
            game_deduped.append(r)

    # Dynamic 1..5 plays. If 2 qualify, returns 2. NEVER pads.
    return game_deduped[:5]


def central_today_str() -> str:
    return datetime.now(_CT).strftime("%Y-%m-%d")


def locked_card_key(ct_day: str) -> str:
    return f"DailyCard/{ct_day}/card_locked.json"


def latest_card_key() -> str:
    return "DailyCard/card_latest.json"


def save_locked_card(card: list[dict[str, Any]], ct_day: str | None = None, force: bool = False) -> tuple[str, bool]:
    """
    Saves the locked Daily Card snapshot to GCS/disk.
    Returns (key, was_written). If locked card already exists and not force, does not overwrite.
    """
    day = ct_day or central_today_str()
    key = locked_card_key(day)
    if storage.exists(key) and not force:
        log.info("Locked card already exists at %s; skipping overwrite", key)
        return key, False

    payload = {
        "date": day,
        "locked_at": datetime.now(_CT).isoformat(),
        "count": len(card),
        "plays": card,
    }
    raw = json.dumps(payload, indent=2).encode("utf-8")
    storage.write_bytes(raw, key)
    storage.write_bytes(raw, latest_card_key())
    log.info("Saved locked card with %d plays to %s", len(card), key)
    return key, True


def load_locked_card(ct_day: str | None = None) -> dict[str, Any] | None:
    """
    Loads the locked Daily Card snapshot.
    Checks DailyCard/{ct_day}/card_locked.json first, falls back to DailyCard/card_latest.json.
    """
    day = ct_day or central_today_str()
    key = locked_card_key(day)
    if storage.exists(key):
        try:
            return json.loads(storage.read_bytes(key).decode("utf-8"))
        except Exception as e:
            log.warning("Failed parsing locked card at %s: %r", key, e)

    lat_key = latest_card_key()
    if storage.exists(lat_key):
        try:
            data = json.loads(storage.read_bytes(lat_key).decode("utf-8"))
            if not ct_day or data.get("date") == day:
                return data
        except Exception as e:
            log.warning("Failed parsing latest card at %s: %r", lat_key, e)

    return None
