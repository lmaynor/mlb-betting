"""Court frame, hex grid, zone labels and shot-type groups shared by the builder and the read path.

Canonical frame (feet): x is lateral, -25..25 (the source's own sign is kept; the charts do not label left/right by player
handedness), y is the distance from the baseline, 0..94. The hoop is at (0, 5.25). Every source adapter must emit this
frame; the half court the charts draw is y in [0, HALF_Y].
"""
from __future__ import annotations

import math
import re

HOOP = (0.0, 5.25)
HALF_Y = 40.0          # shots farther than this from the baseline are heaves/backcourt: never charted
HEX_R = 1.5            # hex circumradius in feet (pointy-top); ~330 cells cover the half court
SQRT3 = math.sqrt(3.0)

# the 14 classic zones: BASIC_ZONE x area. 'Backcourt' is dropped.
ZONE_LABELS = {
    "Restricted Area|C": "Restricted area",
    "In The Paint (Non-RA)|C": "Paint (non-RA)",
    "In The Paint (Non-RA)|L": "Paint left",
    "In The Paint (Non-RA)|R": "Paint right",
    "Mid-Range|L": "Mid-range left",
    "Mid-Range|LC": "Mid-range left-centre",
    "Mid-Range|C": "Mid-range centre",
    "Mid-Range|RC": "Mid-range right-centre",
    "Mid-Range|R": "Mid-range right",
    "Left Corner 3|L": "Left corner 3",
    "Right Corner 3|R": "Right corner 3",
    "Above the Break 3|LC": "Above the break 3 left",
    "Above the Break 3|C": "Above the break 3 centre",
    "Above the Break 3|RC": "Above the break 3 right",
}
ZONE_KEYS = list(ZONE_LABELS)


def hex_of(x: float, y: float, r: float = HEX_R) -> tuple[int, int]:
    """Axial (q, r) of the pointy-top hex of circumradius `r` containing the point (x, y)."""
    qf = (SQRT3 / 3.0 * x - 1.0 / 3.0 * y) / r
    rf = (2.0 / 3.0 * y) / r
    return _round_axial(qf, rf)


def _round_axial(qf: float, rf: float) -> tuple[int, int]:
    sf = -qf - rf
    q, rr, s = round(qf), round(rf), round(sf)
    dq, dr, ds = abs(q - qf), abs(rr - rf), abs(s - sf)
    if dq > dr and dq > ds:
        q = -rr - s
    elif dr > ds:
        rr = -q - s
    return int(q), int(rr)


def hex_center(q: int, r: int, size: float = HEX_R) -> tuple[float, float]:
    return size * SQRT3 * (q + r / 2.0), size * 1.5 * r


def shot_group(action: str | None) -> str:
    """ESPN/NBA free-text action type -> one of 8 groups (order matters: the first match wins)."""
    a = (action or "").lower()
    if "dunk" in a:
        return "Dunk"
    if "hook" in a:
        return "Hook"
    if "tip" in a:
        return "Tip-in"
    if "layup" in a or "finger roll" in a:
        return "Layup"
    if "step back" in a:
        return "Step-back"
    if "pull" in a:
        return "Pull-up"
    if "fadeaway" in a or "turnaround" in a:
        return "Fadeaway"
    if "floating" in a or "floater" in a:
        return "Floater"
    return "Jump shot"


SHOT_GROUPS = ["Jump shot", "Pull-up", "Step-back", "Fadeaway", "Floater", "Layup", "Dunk", "Hook", "Tip-in"]

_SEASON = re.compile(r"^(\d{4})-(\d{2})$")


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"


DIST_BINS = 35                 # 1-ft bins from the hoop: 0..34 (anything farther is folded into the last bin)


def dist_bin(x, y):
    """Feet from the hoop -> bin index 0..DIST_BINS-1 (works on scalars and numpy arrays)."""
    import numpy as np
    d = np.hypot(np.asarray(x, dtype=float) - HOOP[0], np.asarray(y, dtype=float) - HOOP[1])
    return np.minimum(np.floor(d), DIST_BINS - 1).astype(int)


FRAME_COLS = ["season", "player_id", "name", "name_norm", "team", "opp", "home", "game_id", "date", "x", "y", "made", "three",
              "group", "zone", "hex_q", "hex_r", "period", "assisted", "ast_id", "ast_norm"]
