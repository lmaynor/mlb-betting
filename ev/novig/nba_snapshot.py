"""
ev.novig.nba_snapshot -- the NBA snapshot the Novig executor grades against, built from the NBA
pipeline's own served boards. The NBA twin of ev.novig.nhl_snapshot: NBA has no sweep-published
consensus (the NFL route), so the executor's NBA route is the equivalent the pipeline already serves,
republished every 30 min by daily-projections:

  * EVSweep/nba_props/projections_latest.json -- one row per (player, stat) with the SERVED fair:
    p_over_blend, the market-anchored blend. For the two wired stats that blend is the officials'
    trained signal (ev.model_picks.PROPS_SIGNAL_ALLOWLIST: threes + rebounds, the only NBA stats with
    measured market-blend information). No new model is built or consulted here; rows without a line
    or a blend are not candidates.
  * EVSweep/nba_props/odds_board_latest.json -- the same publication's per-book strips
    ({book, over, under} per player/stat/line, ParlayAPI + BettingPros panel). The strips supply the
    reference-book accounting the executor's admission filters need: leave-Novig-out book count and
    dispersion, computed exactly like ev.novig.exec.leave_out computes them for NFL.

 candidates_nba mirrors ev.novig.exec.candidates filter-for-filter (pre-start buffer, leave-out
n_books >= cfg.n_min_books, leave-out disp <= cfg.disp_max, exact leg join) with ONE deliberate
difference, inherited from the NHL route: the point's p_over stays the served blend instead of being
replaced by the leave-out median. The blend is the fair this pipeline prices officials off, and it
already contains the market; the leave-out median still governs admission (thickness + dispersion),
so Novig's own price can never admit a candidate by itself. Everything downstream (gates, shrink,
sizing, risk, paper mirror) is the unchanged NFL path.

Stats NOT wired, on purpose (fail closed, the NHL goals/assists/points precedent): points and pra are
served display-only, assists was removed from the signal allowlist 2026-11-10 (Argon props D3) and is
banned as a fair input in the 2026-11-12 Novig ruling, and pr / pa / ra / steals / blocks were never
measured. The snapshot only ever emits threes + rebounds points.

PLAYER IDENTITY is plain norm_name on both sides (projections rows and Novig legs), exactly like the
NFL join. Points whose strip is missing or too thin fail closed (no candidate), never guessed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from ev.novig.exec import leave_out
from ev.props import DFS_BOOKS
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

log = logging.getLogger("ev.novig.nba_snapshot")
PROJECTIONS_KEY = "EVSweep/nba_props/projections_latest.json"
BOARD_KEY = "EVSweep/nba_props/odds_board_latest.json"
STATS = ("threes", "rebounds")              # the enabled Novig NBA stats (allowlist order)


def read_latest(read=None):
    """-> (projections, board) parsed JSON dicts, or (None, None). No age refusal here (the driver decides)."""
    if read is None:
        from mlb_core import storage
        read = storage.read_bytes
    try:
        proj = json.loads(read(PROJECTIONS_KEY))
        board = json.loads(read(BOARD_KEY))
        if not isinstance(proj.get("rows"), list) or not isinstance(board.get("rows"), list):
            return None, None
        return proj, board
    except Exception:                                                                # noqa: BLE001
        log.exception("NBA served boards unavailable")
        return None, None


def snapshot_ts(proj: dict) -> datetime:
    return datetime.fromisoformat(str(proj["ts"]).replace("Z", "+00:00"))


def snapshot_age_min(proj: dict, now: datetime) -> float:
    return (now - snapshot_ts(proj)).total_seconds() / 60.0


def _strip_fairs(board: dict) -> dict:
    """{(game_key, player_norm, stat, point): {book: de-vigged fair P(over)}} from the board's strips.
    Two-sided real books only (DFS apps carry the number, not a price -- same exclusion as
    ev.props.build_consensus)."""
    out: dict = {}
    for r in board.get("rows", []):
        if r.get("stat") not in STATS or r.get("line") is None:
            continue
        fairs: dict = {}
        for b in r.get("books") or []:
            book, over, under = b.get("book"), b.get("over"), b.get("under")
            if not book or book in DFS_BOOKS or over is None or under is None:
                continue
            fair_over, _ = devig_two_way(american_to_implied_prob(over), american_to_implied_prob(under),
                                         "proportional")
            fairs[book] = float(fair_over)
        if fairs:
            out[(r["game_key"], r["player_norm"], r["stat"], float(r["line"]))] = fairs
    return out


def build_snapshot(proj: dict, board: dict) -> dict:
    """Served boards -> {'ts', 'cycle', 'events': [...]} in the NFL consensus.json shape. Each event's
    points carry the served blend as p_over and the strip's per-book fairs as book_fair (the admission
    reference). One point per projections row: the pipeline serves one line per (player, stat)."""
    strips = _strip_fairs(board)
    events: dict = {}
    for r in proj.get("rows", []):
        if r.get("stat") not in STATS or not r.get("has_line") or r.get("line") is None:
            continue
        if r.get("p_over_blend") is None or not r.get("commence"):
            continue
        ev = events.setdefault(r["game_key"], {"game_key": r["game_key"], "commence": r["commence"],
                                               "event_id": None, "points": []})
        key = (r["game_key"], r["player_norm"], r["stat"], float(r["line"]))
        ev["points"].append({"player_norm": r["player_norm"], "stat": r["stat"], "point": float(r["line"]),
                             "p_over": float(r["p_over_blend"]), "n_books": int(r.get("n_books") or 0),
                             "disp": 0.0, "books": sorted(strips.get(key, {})),
                             "team_hints": [r["team"]] if r.get("team") else [],
                             "book_fair": strips.get(key, {})})
    return {"ts": proj["ts"], "cycle": None, "events": list(events.values())}


def candidates_nba(snap_events: list, legs: dict, cfg, now: datetime) -> list:
    """exec.candidates' filters, NBA fair: (leg, point, mins) for points whose leave-Novig-out strip is
    thick and tight enough and that have a Novig leg at the same line, not near tip. The returned
    point's p_over is the served blend; its n_books / disp are the leave-out strip's (the same values
    exec.candidates would hand the gates)."""
    out = []
    for ev in snap_events:
        start = datetime.fromisoformat(str(ev["commence"]).replace("Z", "+00:00"))
        mins = (start - now).total_seconds() / 60.0
        if mins < cfg.close_buffer_min:
            continue
        for p in ev["points"]:
            lo = leave_out(p)
            if lo is None or lo["n_books"] < cfg.n_min_books or lo["disp"] > cfg.disp_max:
                continue
            leg = legs.get((ev["game_key"], p["stat"], p["player_norm"], p["point"]))
            if leg is not None:
                out.append((leg, dict(p, n_books=lo["n_books"], disp=lo["disp"]), mins))
    return out
