"""soccer.props -- Phase 2 player props: PRE-WIRED, GATED OFF.

The interface Phase 2 will use is fixed now so Phase 1 code never has to be reshaped for it:
  * odds come from ParlayAPI's soccer bucket (the `soccer_other` prop rows seen live 2026-11-03: player_goals,
    player_goals_assists, player_shots_on_target, plus league buckets as coverage is probed),
  * the availability / minutes spine is API-Football Pro ($19/mo; fixtures, confirmed lineups ~1h pre-kick,
    injuries, per-player match stats). NOTE: API-Football has no real xG product -- it is the spine, not the
    model input. Player rates come from Understat / FBref per-90 aggregates.
  * identity resolves through the same Reep bridge as clubs (player bridge lands with Phase 2).

Nothing in this module prices a prop today: PHASE2_ENABLED is False and build_prop_projections raises until
the Phase 1 CLV gate (soccer.game.backtest vs Pinnacle close + forward CLV) clears and Luc approves the
API-Football spend. Do not flip the flag as part of any other change.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("soccer.props")

PHASE2_ENABLED = False

# ParlayAPI soccer prop market key -> beezy stat key. player_shots_on_goal is deliberately NOT mapped: books use
# it for both "shots" and "shots on target" in the wild, and guessing which is exactly the mislabel class this
# repo refuses to ship. It joins the map only after the probe identifies each book's definition.
PROP_MARKET_MAP = {
    "player_goals": "anytime_goal",
    "player_goals_assists": "goals_assists",
    "player_shots": "shots",
    "player_shots_on_target": "sot",
}

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"


def api_football_get(path: str, params: dict | None = None) -> dict:
    """Phase 2 spine client (API-Football Pro). The key comes from the API_FOOTBALL_KEY environment variable at
    call time; it is never hardcoded and never logged. Raises when the key is absent -- Phase 2 does not run
    on a free-tier key (100 requests/day cannot carry a slate)."""
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        raise RuntimeError("API_FOOTBALL_KEY is not set; Phase 2 (soccer props) has no data spine")
    import requests
    resp = requests.get(f"{API_FOOTBALL_BASE}/{path.lstrip('/')}", params=params or {},
                        headers={"x-apisports-key": key}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_prop_projections(fixtures: list[dict], player_rates: dict | None = None) -> list[dict]:
    """Phase 2 entry point (anytime_goal / goals_assists / shots / sot per player per fixture, from per-90
    rates x expected minutes off the API-Football lineup spine). GATED OFF: raises until PHASE2_ENABLED."""
    if not PHASE2_ENABLED:
        raise RuntimeError(
            "soccer player props are Phase 2 and gated OFF until the Phase 1 CLV gate clears "
            "(see soccer.game.backtest); refusing to price props")
    raise NotImplementedError("Phase 2 projection builder lands with the API-Football spine")
