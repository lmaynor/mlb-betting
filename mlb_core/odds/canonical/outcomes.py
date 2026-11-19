"""mlb_core.odds.canonical.outcomes -- canonical outcome identity (RUL-20261119 D1 amendment).

D1 as amended by the Nov 19, 2026 close-out ruling: outcome identity is a
DETERMINISTIC COMPOSITE NATURAL-KEY HASH. Never auto-increment, never a
database sequence: the same natural key always produces the same outcome_id,
on any machine, from raw alone, forever.

Natural keys:
- Prop outcome:  (league, event_id, player_id, market, period, line, side)
  PERIOD IS MANDATORY. Full-game props carry period="full_game"; period
  markets (1Q/1H/...) carry their period. A key without a period component
  does not exist in this layer.
- Future outcome: (venue, market, outcome, season)
  SEASON IS MANDATORY (the season the future settles in, as a starting-year
  string, derived from the partition date by the league's season-start month).

Venue outcome mapping is FAIL-CLOSED: a venue-specific outcome identifier
resolves only through (a) an explicit row in the venue_outcome_map registry
artifact, or (b) the documented identity rule for venues whose bankers emit
canonical (market, outcome) pairs by contract (Kalshi, via the BNK-14
snapshotter). Anything else returns None, and callers quarantine the row
with reason code "unmapped_outcome" (see read.unmapped_quarantine).
The map ships intentionally sparse: it grows by reviewed additions, never
by auto-registration from payloads.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from mlb_core.odds.canonical.registry import get_registry_dir

# Venues whose bankers emit canonical (market, outcome) pairs by contract.
# Kalshi: the BNK-14 snapshotter's output contract (ev/kalshi_futures_snapshot.py)
# banks venue/market/outcome already in canonical form, so its outcome keys
# map by identity. Every other venue must appear in venue_outcome_map.csv.
IDENTITY_VENUES = frozenset({"kalshi"})

UNMAPPED_OUTCOME = "unmapped_outcome"

_SEASON_START_MONTH: Dict[str, int] = {
    "mlb": 3, "nba": 10, "nhl": 10, "nfl": 9, "ncaaf": 8, "ncaab": 11, "wnba": 5,
}


def season_for(league: str, on_date: str) -> str:
    """Season starting-year string for a league on an ISO date (BNK-13's rule)."""
    y, m, _ = (int(x) for x in str(on_date)[:10].split("-"))
    start = _SEASON_START_MONTH.get(str(league).lower(), 1)
    return str(y if m >= start else y - 1)


def _hash_key(parts: Tuple[Any, ...]) -> str:
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return "oc_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]


def _period_of(row: Dict[str, Any]) -> str:
    """The row's period: an explicit period field/extras entry, else full_game.
    The component is ALWAYS present in the key (mandatory per D1 amendment)."""
    period = row.get("period")
    if period:
        return str(period)
    extras = row.get("source_extras")
    if isinstance(extras, str) and extras:
        try:
            period = json.loads(extras).get("period")
        except Exception:
            period = None
    elif isinstance(extras, dict):
        period = extras.get("period")
    return str(period) if period else "full_game"


def _line_of(row: Dict[str, Any]) -> str:
    line = row.get("line")
    return "" if line is None or (isinstance(line, float) and pd.isna(line)) else f"{float(line):g}"


def prop_natural_key(row: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        row.get("league"),
        row.get("event_id"),
        row.get("player_id"),
        row.get("market"),
        _period_of(row),
        _line_of(row),
        row.get("side"),
    )


def future_natural_key(row: Dict[str, Any], season: Optional[str] = None) -> Tuple[Any, ...]:
    if season is None:
        date = str(row.get("game_date_et") or row.get("dt") or "")[:10]
        season = season_for(str(row.get("league") or ""), date) if date else ""
    outcome = row.get("outcome")
    if not outcome:
        extras = row.get("source_extras")
        if isinstance(extras, str) and extras:
            try:
                outcome = json.loads(extras).get("outcome")
            except Exception:
                outcome = None
        elif isinstance(extras, dict):
            outcome = extras.get("outcome")
    return (row.get("book_id") or row.get("venue"), row.get("market"), outcome, season)


def outcome_id(natural_key: Tuple[Any, ...]) -> str:
    """Deterministic hash id for a natural key. Never auto-increment."""
    return _hash_key(natural_key)


def load_venue_outcome_map(registry_dir: Path | str | None = None) -> pd.DataFrame:
    path = Path(get_registry_dir(registry_dir)) / "venue_outcome_map.csv"
    if not path.exists():
        return pd.DataFrame(columns=["venue", "market_key", "outcome_key", "league",
                                     "canonical_market", "period", "season_scope",
                                     "canonical_outcome", "evidence"])
    return pd.read_csv(path, dtype=str).fillna("")


def resolve_venue_outcome(
    venue: str,
    market_key: str,
    outcome_key: str,
    league: str = "",
    season: str = "",
    registry_dir: Path | str | None = None,
) -> Optional[Tuple[Any, ...]]:
    """Map a venue outcome identifier to its canonical FUTURE natural key.

    Fail-closed: returns None (caller quarantines as unmapped_outcome) unless
    an explicit registry row matches, or the venue is an identity venue.
    """
    m = load_venue_outcome_map(registry_dir)
    if len(m):
        hit = m[(m["venue"] == venue) & (m["market_key"] == market_key) & (m["outcome_key"] == outcome_key)]
        if len(hit):
            r = hit.iloc[0]
            return (venue, r["canonical_market"], r["canonical_outcome"],
                    season or (r["season_scope"] if r["season_scope"] != "from_partition_date" else ""))
    if venue in IDENTITY_VENUES:
        return (venue, market_key, outcome_key, season)
    return None


def build_outcomes(quotes: pd.DataFrame) -> pd.DataFrame:
    """One row per distinct outcome natural key in a canonical quotes frame:
    outcome_id, key components, first/last observed, quote count. Prop rows
    (player_id present or a prop market class) key as props; binary_contract
    rows key as futures."""
    if quotes is None or not len(quotes):
        return pd.DataFrame(columns=["outcome_id", "kind", "natural_key", "league", "event_id",
                                     "player_id", "market", "period", "line", "side", "venue",
                                     "outcome", "season", "first_observed", "last_observed", "n_quotes"])
    rows: Dict[str, Dict[str, Any]] = {}
    for rec in quotes.to_dict("records"):
        is_future = rec.get("market_class") == "binary_contract"
        if is_future:
            key = future_natural_key(rec)
            kind = "future"
        else:
            key = prop_natural_key(rec)
            kind = "prop"
        oid = _hash_key(key)
        obs = rec.get("observed_at")
        if oid not in rows:
            base = {
                "outcome_id": oid, "kind": kind, "natural_key": "\x1f".join(str(k) for k in key),
                "league": rec.get("league"), "event_id": rec.get("event_id"),
                "player_id": rec.get("player_id"), "market": rec.get("market"),
                "period": _period_of(rec) if kind == "prop" else "season",
                "line": rec.get("line"), "side": rec.get("side"),
                "venue": key[0] if kind == "future" else None,
                "outcome": key[2] if kind == "future" else None,
                "season": key[3] if kind == "future" else None,
                "first_observed": obs, "last_observed": obs, "n_quotes": 0,
            }
            rows[oid] = base
        rows[oid]["n_quotes"] += 1
        if obs is not None:
            if rows[oid]["first_observed"] is None or str(obs) < str(rows[oid]["first_observed"]):
                rows[oid]["first_observed"] = obs
            if rows[oid]["last_observed"] is None or str(obs) > str(rows[oid]["last_observed"]):
                rows[oid]["last_observed"] = obs
    return pd.DataFrame(list(rows.values()))
