"""mlb_core.odds.canonical.registry -- Canonical Registry Loaders and Lookups.

Loads books.csv, markets.csv, teams.csv, and player_aliases/{league}.csv.
Provides fast lookups for book identities across namespaces (AN, BP, Odds API, SGO)
and market / team / alias mappings.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, Optional, Tuple
import pandas as pd

DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[3] / "data" / "canonical_registry"


def get_registry_dir(override: Path | str | None = None) -> Path:
    if override:
        return Path(override)
    return DEFAULT_REGISTRY_DIR


@functools.lru_cache(maxsize=4)
def load_books(registry_dir: Path | str | None = None) -> pd.DataFrame:
    rdir = get_registry_dir(registry_dir)
    path = rdir / "books.csv"
    if not path.exists():
        raise FileNotFoundError(f"Books registry not found at {path}")
    df = pd.read_csv(path, dtype={"an_id": "object", "bp_id": "object"})
    # Normalize booleans
    if "bettable" in df.columns:
        df["bettable"] = df["bettable"].astype(str).str.lower().isin(["true", "yes", "1"])
    if "sharp" in df.columns:
        df["sharp"] = df["sharp"].astype(str).str.lower().isin(["true", "yes", "1"])
    return df


@functools.lru_cache(maxsize=4)
def load_markets(registry_dir: Path | str | None = None) -> pd.DataFrame:
    rdir = get_registry_dir(registry_dir)
    path = rdir / "markets.csv"
    if not path.exists():
        raise FileNotFoundError(f"Markets registry not found at {path}")
    return pd.read_csv(path, dtype=str)


@functools.lru_cache(maxsize=4)
def load_teams(registry_dir: Path | str | None = None) -> pd.DataFrame:
    rdir = get_registry_dir(registry_dir)
    path = rdir / "teams.csv"
    if not path.exists():
        raise FileNotFoundError(f"Teams registry not found at {path}")
    return pd.read_csv(path, dtype=str)


def load_player_aliases(league: str, registry_dir: Path | str | None = None) -> pd.DataFrame:
    rdir = get_registry_dir(registry_dir)
    path = rdir / "player_aliases" / f"{league.lower()}.csv"
    if not path.exists():
        return pd.DataFrame(columns=[
            "league", "source", "source_player_id", "source_name_norm",
            "master_player_id", "evidence", "verified"
        ])
    return pd.read_csv(path, dtype=str)


def clear_registry_cache():
    load_books.cache_clear()
    load_markets.cache_clear()
    load_teams.cache_clear()


# ---------------------------------------------------------------------------
# Book Lookups (Fail closed: unknown -> None, never fabricated book_{id})
# ---------------------------------------------------------------------------

def book_from_an_id(an_id: int | str | None, registry_dir: Path | str | None = None) -> Optional[str]:
    """Resolve an Action Network book ID -> canonical_book_id (or None)."""
    if an_id is None or str(an_id).strip() == "":
        return None
    try:
        aid_str = str(int(float(str(an_id).strip())))
    except (ValueError, TypeError):
        aid_str = str(an_id).strip()
    df = load_books(registry_dir)
    matches = df[df["an_id"].astype(str).str.strip() == aid_str]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_book_id"])
    return None


def book_from_bp_id(bp_id: int | str | None, registry_dir: Path | str | None = None) -> Optional[str]:
    """Resolve a BettingPros book ID -> canonical_book_id (or None)."""
    if bp_id is None or str(bp_id).strip() == "":
        return None
    try:
        bid_str = str(int(float(str(bp_id).strip())))
    except (ValueError, TypeError):
        bid_str = str(bp_id).strip()
    df = load_books(registry_dir)
    matches = df[df["bp_id"].astype(str).str.strip() == bid_str]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_book_id"])
    return None


def book_from_odds_api_key(key: str | None, registry_dir: Path | str | None = None) -> Optional[str]:
    """Resolve an Odds API bookmaker key -> canonical_book_id (or None)."""
    if not key:
        return None
    k = str(key).strip().lower()
    df = load_books(registry_dir)
    matches = df[df["odds_api_key"].astype(str).str.strip().str.lower() == k]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_book_id"])
    return None


def book_from_sgo_name(name: str | None, registry_dir: Path | str | None = None) -> Optional[str]:
    """Resolve an SGO bookmaker name -> canonical_book_id (or None)."""
    if not name:
        return None
    n = str(name).strip().lower()
    df = load_books(registry_dir)
    matches = df[df["sgo_name"].astype(str).str.strip().str.lower() == n]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_book_id"])
    return None


# ---------------------------------------------------------------------------
# Team Normalization
# ---------------------------------------------------------------------------

def normalize_team(league: str, raw_abbr: str | None, registry_dir: Path | str | None = None) -> str:
    """Normalize raw team abbreviation to canonical abbreviation.

    If no mapping is found, returns raw_abbr stripped and uppercase.
    """
    if not raw_abbr:
        return ""
    token = str(raw_abbr).strip().upper()
    df = load_teams(registry_dir)
    l_df = df[df["league"].astype(str).str.lower() == league.lower()]
    matches = l_df[l_df["alias"].astype(str).str.strip().str.upper() == token]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_abbr"])
    return token


# ---------------------------------------------------------------------------
# Market Lookups
# ---------------------------------------------------------------------------

def market_from_an(slug_or_type: str | None, registry_dir: Path | str | None = None) -> Optional[Tuple[str, str]]:
    """Map Action Network market slug or type -> (canonical_market, market_class)."""
    if not slug_or_type:
        return None
    s = str(slug_or_type).strip()

    # Milestone pattern: {stat}_milestone_{n}plus
    if "_milestone_" in s:
        stat = s.split("_milestone_")[0]
        return f"{stat}_milestone", "milestone"

    df = load_markets(registry_dir)
    matches = df[df["an_slug_or_type"].astype(str).str.strip() == s]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_market"]), str(matches.iloc[0]["market_class"])

    # Fallback to direct canonical match
    direct = df[df["canonical_market"].astype(str).str.strip() == s]
    if not direct.empty:
        return str(direct.iloc[0]["canonical_market"]), str(direct.iloc[0]["market_class"])

    return None


def market_from_bp(label_or_slug: str | None, registry_dir: Path | str | None = None) -> Optional[Tuple[str, str]]:
    """Map BettingPros stat label or slug -> (canonical_market, market_class)."""
    if not label_or_slug:
        return None
    raw = str(label_or_slug).strip().lower()

    # Direct synonyms for BP short stat codes
    bp_synonyms = {
        "pts": "points",
        "reb": "rebounds",
        "ast": "assists",
        "sog": "shots_on_goal",
        "shots": "shots_on_goal",
        "points-assists-rebounds": "pra",
        "points-rebounds": "pr",
        "points-assists": "pa",
        "rebounds-assists": "ra",
        "puck-line": "spread",
        "pass_yds": "passing_yards",
        "pass_tds": "passing_touchdowns",
        "rush_yds": "rushing_yards",
        "rec_yds": "receiving_yards",
        "rush_rec_yds": "rushing_receiving_yards",
        "pass_att": "passing_attempts",
        "pass_cmp": "passing_completions",
        "pass_int": "interceptions",
        "rush_att": "rushing_attempts",
        "anytime_td": "anytime_scorer",
    }
    target = bp_synonyms.get(raw, raw)

    df = load_markets(registry_dir)
    matches = df[df["bp_label"].astype(str).str.strip().str.lower() == target]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_market"]), str(matches.iloc[0]["market_class"])

    direct = df[df["canonical_market"].astype(str).str.strip().str.lower() == target]
    if not direct.empty:
        return str(direct.iloc[0]["canonical_market"]), str(direct.iloc[0]["market_class"])

    return None


def market_from_odds_api(odds_api_key: str | None, registry_dir: Path | str | None = None) -> Optional[Tuple[str, str]]:
    """Map Odds API key -> (canonical_market, market_class)."""
    if not odds_api_key:
        return None
    k = str(odds_api_key).strip().lower()

    # Alternate rungs
    if k.endswith("_alternate"):
        base_k = k[:-len("_alternate")]
        df = load_markets(registry_dir)
        matches = df[df["odds_api_key"].astype(str).str.strip().str.lower() == base_k]
        if not matches.empty:
            return str(matches.iloc[0]["canonical_market"]), "alternate"
        if base_k.startswith("player_"):
            return base_k[len("player_"):], "alternate"
        return base_k, "alternate"

    df = load_markets(registry_dir)
    matches = df[df["odds_api_key"].astype(str).str.strip().str.lower() == k]
    if not matches.empty:
        return str(matches.iloc[0]["canonical_market"]), str(matches.iloc[0]["market_class"])

    # Generic player_ prefix fallback
    if k.startswith("player_"):
        stat = k[len("player_"):]
        direct = df[df["canonical_market"].astype(str).str.strip().str.lower() == stat]
        if not direct.empty:
            return str(direct.iloc[0]["canonical_market"]), str(direct.iloc[0]["market_class"])
        return stat, "main"

    return None
