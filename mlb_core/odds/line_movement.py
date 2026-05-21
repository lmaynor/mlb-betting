"""
mlb_core/odds/line_movement.py -- E10: line movement signal.

Provides utilities to compute how much odds moved between the morning
snapshot (15:55 UTC) and the evening snapshot (21:55 UTC / bet time).

Line movement interpretation:
  line_move_pct > 0: closing implied prob HIGHER than morning implied prob
                     = book shortened odds on this side = sharp/public money
                     AGAINST our bet side. Negative signal.
  line_move_pct < 0: closing implied prob LOWER than morning implied prob
                     = book lengthened odds on this side = money going OTHER
                     way. Could be value signal (we're on the less-bet side).
  line_move_pct near 0: stable line, no new information.

Usage in runners:
    from mlb_core.odds.line_movement import load_morning_odds, get_line_move

    morning = load_morning_odds(run_date)  # loads 15:55 UTC snapshot
    line_move = get_line_move(morning, event_id, odd_id, current_odds)

The morning_odds value is passed to log_bet() and stored in the DB.
line_move_pct is computed at capture_closing_lines time.

GCS snapshot path pattern:
    Odds/sgo/{YYYY-MM-DD}/snapshot_{HHMM}.json
Morning snapshot: snapshot_1555.json (15:55 UTC = 10:55 ET)
Evening snapshot: snapshot_2155.json (21:55 UTC = 16:55 ET)
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Morning snapshot is written at 15:55 UTC by mlb-snapshot-morning
MORNING_SNAPSHOT_HHMM = "1555"
EVENING_SNAPSHOT_HHMM = "2155"


def _snapshot_key(run_date: str, hhmm: str) -> str:
    return f"Odds/sgo/{run_date}/snapshot_{hhmm}.json"


def load_morning_odds(run_date: str) -> dict[str, int]:
    """Load the morning snapshot and return a flat odds lookup dict.

    Returns:
        Dict mapping odd_id -> best_onshore_odds_int for all markets
        in the morning snapshot. Empty dict if snapshot not found.

    The odd_id is the SGO market identifier, e.g.:
        "pitching_strikeouts-675096-game-ou-over"
        "points-all-1i-ou-over"
        "batting_homeRuns-123456-game-yn-yes"
    """
    from mlb_core.storage import exists, read_bytes
    from mlb_core.odds.sgo import _best_book_odds_int

    key = _snapshot_key(run_date, MORNING_SNAPSHOT_HHMM)
    if not exists(key):
        logger.debug(f"line_movement: morning snapshot not found: {key}")
        return {}

    try:
        raw = read_bytes(key)
        events = json.loads(raw)
    except Exception as e:
        logger.warning(f"line_movement: failed to load morning snapshot: {e}")
        return {}

    odds_map: dict[str, int] = {}
    for event in events:
        for odd_id, odd_data in (event.get("odds") or {}).items():
            try:
                best_odds, _ = _best_book_odds_int(odd_data)
                if best_odds is not None:
                    odds_map[odd_id] = best_odds
            except Exception:
                continue

    logger.debug(f"line_movement: loaded {len(odds_map)} morning markets for {run_date}")
    return odds_map


def get_line_move(morning_odds_map: dict[str, int],
                  odd_id: str,
                  current_odds: int) -> int | None:
    """Return morning_odds for this odd_id, or None if not in morning snapshot.

    Callers store the raw morning_odds integer in the DB.
    line_move_pct is computed later at capture_closing_lines time.

    Args:
        morning_odds_map: output of load_morning_odds()
        odd_id:           SGO odd_id for this market
        current_odds:     current (evening) American odds integer

    Returns:
        morning_odds integer if the market existed in the morning snapshot,
        else None (line was not available in the morning -- e.g. DK K props
        for evening games are not posted until ~2-3pm ET).
    """
    return morning_odds_map.get(odd_id)


def compute_line_move_pct(morning_odds: int, closing_odds: int) -> float | None:
    """Compute line_move_pct from American odds integers.

    line_move_pct = (closing_implied - morning_implied) / morning_implied * 100

    Positive = odds shortened (more money on this side since morning).
    Negative = odds lengthened (less money on this side since morning).

    Uses raw implied prob (no devig) for both sides -- we want the raw
    market movement signal, not the fair value shift.
    """
    from mlb_core.odds.utils import american_to_implied_prob

    m = american_to_implied_prob(morning_odds)
    c = american_to_implied_prob(closing_odds)

    if m is None or c is None or m <= 0:
        return None

    return round((c - m) / m * 100, 4)


def enrich_runner_results(results: list[dict],
                          morning_odds_map: dict[str, int],
                          odd_id_key: str = "odd_id") -> list[dict]:
    """Add morning_odds to each result row in-place.

    Runners build a results list and call this before log_bet to populate
    morning_odds from the morning snapshot.

    Args:
        results:          list of result dicts from runner prediction loop
        morning_odds_map: output of load_morning_odds()
        odd_id_key:       key in result dict containing the SGO odd_id

    Returns:
        Same list with morning_odds added to each dict (None if not found).
    """
    for row in results:
        odd_id = row.get(odd_id_key)
        row["morning_odds"] = get_line_move(morning_odds_map, odd_id, row.get("odds")) \
            if odd_id else None
    return results
