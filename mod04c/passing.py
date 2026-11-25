"""C4 v2 data build: NBA.com Stats Passing API banker and skip-condition gate."""
from __future__ import annotations

import re

import pandas as pd
import requests

from mlb_core import storage

STATS_PASSING_URL = "https://stats.nba.com/stats/leaguedashptstats"
USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"
REFERER = "https://www.nba.com/stats"

REQUIRED_COLUMNS = [
    "player_id",
    "player_name",
    "team_id",
    "gp",
    "min",
    "passes_made",
    "passes_received",
    "assists",
    "secondary_assists",
    "potential_assists",
    "assist_pts_created",
    "assist_to_pass_pct",
]

_EXPLICIT_RENAMES = {
    "PLAYER_ID": "player_id",
    "PLAYER_NAME": "player_name",
    "TEAM_ID": "team_id",
    "TEAM_ABBREVIATION": "team_abbreviation",
    "GP": "gp",
    "W": "w",
    "L": "l",
    "MIN": "min",
    "PASSES_MADE": "passes_made",
    "PASSES_RECEIVED": "passes_received",
    "AST": "assists",
    "SECONDARY_AST": "secondary_assists",
    "POTENTIAL_AST": "potential_assists",
    "AST_PTS_CREATED": "assist_pts_created",
    "AST_POINTS_CREATED": "assist_pts_created",
    "AST_ADJ": "ast_adj",
    "AST_TO_PASS_PCT": "assist_to_pass_pct",
    "AST_TO_PASS_PCT_ADJ": "assist_to_pass_pct_adj",
}


def _to_snake(name: str) -> str:
    if name in _EXPLICIT_RENAMES:
        return _EXPLICIT_RENAMES[name]
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name).strip())
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()


def fetch_passing(
    season: str = "2025-26",
    season_type: str = "Regular Season",
    per_mode: str = "PerGame",
    *,
    timeout: float = 20.0,
) -> pd.DataFrame:
    """Fetch player passing tracking stats from stats.nba.com/stats/leaguedashptstats."""
    params = {
        "PtMeasureType": "Passing",
        "PlayerOrTeam": "Player",
        "LeagueID": "00",
        "Season": season,
        "SeasonType": season_type,
        "PerMode": per_mode,
    }
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": REFERER,
        "Accept": "application/json, text/plain, */*",
    }
    try:
        resp = requests.get(STATS_PASSING_URL, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"NBA Stats Passing API request failed: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(f"NBA Stats Passing API returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"NBA Stats Passing API returned invalid JSON: {exc}") from exc

    result_sets = payload.get("resultSets")
    if isinstance(result_sets, list) and result_sets:
        rs = result_sets[0]
    elif isinstance(payload.get("resultSet"), dict):
        rs = payload["resultSet"]
    else:
        raise RuntimeError("NBA Stats Passing API payload missing resultSets[0]")

    headers_raw = rs.get("headers") or []
    rows_raw = rs.get("rowSet") or []
    if not headers_raw or not rows_raw:
        raise RuntimeError("NBA Stats Passing API returned empty resultSet (no rows)")

    df = pd.DataFrame(rows_raw, columns=[_to_snake(h) for h in headers_raw])
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise RuntimeError(f"NBA Stats Passing API response missing expected columns: {missing}")
    return df


def bank_passing(df: pd.DataFrame, bank_date: str) -> str:
    """Persist NBA passing DataFrame to `NBA/passing/passing_{bank_date}.csv`."""
    if df is None or df.empty:
        raise ValueError("Cannot bank an empty passing DataFrame")
    key = f"NBA/passing/passing_{bank_date}.csv"
    storage.write_csv(df, key)
    return key


def evaluate_skip_condition(holdout_slope: float, delta_ll_vs_market: float) -> bool:
    """Gate-time evaluation for C4 passing v2 signal.

    Returns True iff `0.85 <= holdout_slope <= 1.15` AND `delta_ll_vs_market <= -0.0030`.
    """
    return bool(0.85 <= float(holdout_slope) <= 1.15 and float(delta_ll_vs_market) <= -0.0030)
