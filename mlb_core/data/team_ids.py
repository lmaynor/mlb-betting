"""
mlb_core.data.team_ids — canonical MLB Stats API team-id <-> abbreviation map.

Single source of truth: previously hardcoded independently in
mlb_core/data/auxiliary_features.py, mlb_core/data/id_resolver.py, and
mlb_core/data/lineups.py (id_resolver.py's own comment even said it inlined
a duplicate "to avoid importing that heavier module just for a constant").
Consolidated 2026-09-04 into this dependency-free module so all three (and
any future caller) can import the same constant instead of maintaining
their own copy that can silently drift on a team relocation/expansion.

No heavy imports here on purpose -- this module exists specifically so a
caller doesn't have to pull in auxiliary_features.py's full Savant/FanGraphs
dependency chain just for a 30-entry dict.
"""
from __future__ import annotations

TEAM_ID_TO_ABBREV: dict[int, str] = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC", 119: "LAD",
    120: "WSH", 121: "NYM", 133: "OAK", 134: "PIT", 135: "SD", 136: "SEA",
    137: "SF", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}
ABBREV_TO_TEAM_ID: dict[str, int] = {v: k for k, v in TEAM_ID_TO_ABBREV.items()}
MLB_TEAM_IDS: list[int] = list(TEAM_ID_TO_ABBREV.keys())
