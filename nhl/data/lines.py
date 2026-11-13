"""NHL even-strength lines derivation module.

Derives each NHL team's current even-strength line combinations (forward trios/pairs
and defense pairs) from banked shift charts (nhl/data/shiftcharts_bank.py, sink
NHL/raw/shiftcharts/<game_id>.json.gz) over each team's trailing games, and publishes
a versioned JSON artifact the odds board annotates rows from.

EVEN-STRENGTH ONLY: power-play units are out of scope (do not emit them).

Algorithm:
    1. game_pair_seconds(chart, pbp): parses shifts and roster, builds 5v5 timeline
       mask ((eff_home == 5) & (eff_away == 5)), computes pairwise shared 5v5 seconds
       matrix for each team's active skaters, returning per-team player metadata and
       pair seconds with pid_a < pid_b.
    2. derive_lines(team_abbr, pair_totals, players): greedy forward trios from seed
       pairs (>= 900s) + maximizing third member (>= 600s sum), followed by leftover
       forward pairs (>= 1800s), and defense pairs (>= 1200s), capped at MAX_F_LINES (4)
       and MAX_D_LINES (3).
    3. build_artifact(window=10, as_of=None, game_ids=None): resolves trailing games,
       accumulates pair totals and player metadata across games in ascending date order,
       derives line combinations, and constructs versioned JSON artifact and QA stats.
    4. publish(artifact): writes latest and dated JSON artifacts to GCS via mlb_core.storage.
    5. main(argv=None): CLI interface with --window, --as-of, --game-ids, --out, and --no-write.

Usage:
    PYTHONPATH=. python -m nhl.data.lines --window 10
    PYTHONPATH=. python -m nhl.data.lines --window 10 --as-of 2024-11-20
    PYTHONPATH=. python -m nhl.data.lines --game-ids 2024020001,2024020002 --out data/lines.json --no-write
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import logging
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from ev.props import norm_name
from mlb_core import storage
from nhl.data.settings import GAMES_MASTER, raw_pbp_key, raw_shiftchart_key
from nhl.data.shiftcharts_bank import trailing_window_ids
from nhl.data.toi_splits import anchor_to_pbp, roster_frame, shifts_frame, timeline

log = logging.getLogger("nhl.data.lines")

# ---------------------------------------------------------------------------------------------------------------------
# Module Constants & Thresholds
# ---------------------------------------------------------------------------------------------------------------------
SEED_PAIR_MIN_S = 900          # Min shared 5v5 seconds for a forward seed pair
THIRD_MIN_S = 600              # Min shared 5v5 seconds sum with seed pair to accept third forward
LEFTOVER_PAIR_MIN_S = 1800     # Min shared 5v5 seconds for a 2-man leftover forward line
DEF_PAIR_MIN_S = 1200          # Min shared 5v5 seconds for a defense pair
MAX_F_LINES = 4                # Max forward lines per team
MAX_D_LINES = 3                # Max defense lines per team

LINES_LATEST_KEY = "NHL/lines/lines_latest.json"
LINES_DATED_KEY = "NHL/lines/lines_{date}.json"


# ---------------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------------
def _extract_name_part(val: object) -> str:
    """Extract string value from a name field (handles dict with 'default' key or plain string)."""
    if isinstance(val, dict):
        return str(val.get("default") or "")
    if isinstance(val, str):
        return val
    return ""


def _player_display_name(r: dict) -> str:
    """Extract player display name from rosterSpot dict."""
    first = _extract_name_part(r.get("firstName")).strip()
    last = _extract_name_part(r.get("lastName")).strip()
    if first and last:
        return f"{first} {last}"
    if first or last:
        return first or last
    name_val = r.get("name")
    if isinstance(name_val, dict):
        return str(name_val.get("default") or "").strip()
    if isinstance(name_val, str):
        return name_val.strip()
    return ""


# ---------------------------------------------------------------------------------------------------------------------
# 1. Per-game Pairwise 5v5 Seconds
# ---------------------------------------------------------------------------------------------------------------------
def game_pair_seconds(chart: dict | None, pbp: dict | None) -> dict[str, dict] | None:
    """Compute pairwise shared 5v5 seconds for each team's active skaters in a single game.

    Guards mirror toi_splits.build_game: returns None when chart has no data rows,
    pbp has no rosterSpots, home/away team IDs are missing/invalid, or shifts are empty.

    Returns:
        {team_abbr: {"players": {player_id: {"name": str, "pos": str}},
                     "pairs": {(pid_a, pid_b): seconds}}} with pid_a < pid_b,
        or None if game data is invalid/missing.
    """
    if not chart or not (chart.get("data") or []):
        return None
    if not pbp or not (pbp.get("rosterSpots") or []):
        return None

    home_info = pbp.get("homeTeam") or {}
    away_info = pbp.get("awayTeam") or {}
    home_id_raw = home_info.get("id")
    away_id_raw = away_info.get("id")
    if home_id_raw is None or away_id_raw is None:
        return None
    try:
        home_id, away_id = int(home_id_raw), int(away_id_raw)
        if home_id < 0 or away_id < 0:
            return None
    except (TypeError, ValueError):
        return None

    sh = shifts_frame(chart)
    ro = roster_frame(pbp)
    if sh.empty or ro.empty:
        return None

    gtype = int(pbp.get("gameType") or 2)
    if gtype == 2:
        sh = sh[sh["period"] <= 4]
    if sh.empty:
        return None

    tl = anchor_to_pbp(timeline(sh, ro, home_id, away_id), pbp)
    n = tl.get("n", 0)
    if n <= 0:
        return None

    mask = (tl["eff_home"] == 5) & (tl["eff_away"] == 5)

    team_abbrs: dict[int, str] = {}
    if home_info.get("abbrev"):
        team_abbrs[home_id] = str(home_info["abbrev"])
    if away_info.get("abbrev"):
        team_abbrs[away_id] = str(away_info["abbrev"])

    # Fallback to shift chart team abbreviation if missing in PBP
    for tid in (home_id, away_id):
        if tid not in team_abbrs:
            sh_teams = sh[sh["team_id"] == tid]["team"].dropna()
            if not sh_teams.empty:
                team_abbrs[tid] = str(sh_teams.iloc[0])
            else:
                team_abbrs[tid] = str(tid)

    sh_pids = set(sh["player_id"])
    roster_spots = pbp.get("rosterSpots") or []

    out: dict[str, dict] = {}
    for tid in (home_id, away_id):
        abbr = team_abbrs[tid]
        team_players_meta: dict[int, dict] = {}
        for r in roster_spots:
            try:
                p_tid = int(r.get("teamId", -1))
                pid = int(r.get("playerId", -1))
            except (TypeError, ValueError):
                continue
            if p_tid != tid or pid < 0:
                continue
            pos = str(r.get("positionCode") or "")
            if pos == "G":
                continue
            if pid not in sh_pids:
                continue
            team_players_meta[pid] = {
                "name": _player_display_name(r),
                "pos": pos,
            }

        team_skater_ids = sorted(team_players_meta.keys())
        if not team_skater_ids:
            out[abbr] = {"players": {}, "pairs": {}}
            continue

        cov_list = []
        for pid in team_skater_ids:
            p_sh = sh[sh["player_id"] == pid]
            cov = np.zeros(n, dtype=bool)
            for a, b in zip(p_sh["start"], p_sh["end"]):
                cov[max(0, min(int(a), n)):max(0, min(int(b), n))] = True
            cov_list.append(cov & mask)

        M = np.stack(cov_list, axis=0).astype(np.int32)
        S = M @ M.T

        pairs: dict[tuple[int, int], int] = {}
        for i in range(len(team_skater_ids)):
            for j in range(i + 1, len(team_skater_ids)):
                s = int(S[i, j])
                if s > 0:
                    pairs[(team_skater_ids[i], team_skater_ids[j])] = s

        out[abbr] = {
            "players": team_players_meta,
            "pairs": pairs,
        }

    return out


# ---------------------------------------------------------------------------------------------------------------------
# 2. Line Derivation (Single Team)
# ---------------------------------------------------------------------------------------------------------------------
def derive_lines(
    team_abbr: str | dict = "",
    pair_totals: dict | None = None,
    players: dict | None = None,
) -> list[dict]:
    """Derive even-strength forward lines (trios or pairs) and defense pairs for one team.

    Accepts both derive_lines(team_abbr, pair_totals, players) and
    derive_lines(pair_totals, players).

    Args:
        team_abbr: Team abbreviation string (e.g. 'AAA') or pair_totals dict when team_abbr is omitted.
        pair_totals: Mapping of (pid_a, pid_b) with pid_a < pid_b to total shared 5v5 seconds.
        players: Mapping of pid to player metadata dict containing name, pos, and optionally player_norm.

    Returns:
        List of line dicts:
        {"line_id": str, "kind": "F"|"D", "players": [...], "pair_seconds": {"<pid_a>|<pid_b>": seconds}}
    """
    if isinstance(team_abbr, dict):
        actual_pair_totals = team_abbr
        actual_players = pair_totals or {}
        actual_team_abbr = ""
    else:
        actual_team_abbr = str(team_abbr or "")
        actual_pair_totals = pair_totals or {}
        actual_players = players or {}

    # Normalize pair totals and ensure (min_id, max_id) keys
    norm_pair_totals: dict[tuple[int, int], int] = {}
    for (pa, pb), sec in actual_pair_totals.items():
        k = (min(int(pa), int(pb)), max(int(pa), int(pb)))
        norm_pair_totals[k] = norm_pair_totals.get(k, 0) + int(sec)

    def shared(p1: int, p2: int) -> int:
        return norm_pair_totals.get((min(p1, p2), max(p1, p2)), 0)

    # Separate forwards and defenders
    forwards: set[int] = set()
    defenders: set[int] = set()
    for pid, meta in actual_players.items():
        p_int = int(pid)
        pos = str(meta.get("pos") or "").upper()
        if pos in {"C", "L", "R"}:
            forwards.add(p_int)
        elif pos == "D":
            defenders.add(p_int)

    # Candidate forward pairs sorted desc by seconds, tie-broken by pid
    f_pairs: list[tuple[int, int, int]] = []
    for (pa, pb), sec in norm_pair_totals.items():
        if pa in forwards and pb in forwards:
            f_pairs.append((sec, pa, pb))
    f_pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

    used_forwards: set[int] = set()
    f_lines: list[list[int]] = []

    # Greedy trios
    for sec, p1, p2 in f_pairs:
        if len(f_lines) >= MAX_F_LINES:
            break
        if sec < SEED_PAIR_MIN_S:
            break
        if p1 in used_forwards or p2 in used_forwards:
            continue

        best_c = None
        best_score = -1
        for c in sorted(forwards):
            if c in used_forwards or c == p1 or c == p2:
                continue
            score = shared(p1, c) + shared(p2, c)
            if score > best_score:
                best_score = score
                best_c = c

        if best_c is not None and best_score >= THIRD_MIN_S:
            used_forwards.add(p1)
            used_forwards.add(p2)
            used_forwards.add(best_c)
            f_lines.append([p1, p2, best_c])

    # Leftover forward pairs
    for sec, p1, p2 in f_pairs:
        if len(f_lines) >= MAX_F_LINES:
            break
        if sec < LEFTOVER_PAIR_MIN_S:
            break
        if p1 in used_forwards or p2 in used_forwards:
            continue
        used_forwards.add(p1)
        used_forwards.add(p2)
        f_lines.append([p1, p2])

    # Candidate defense pairs sorted desc by seconds
    d_pairs: list[tuple[int, int, int]] = []
    for (pa, pb), sec in norm_pair_totals.items():
        if pa in defenders and pb in defenders:
            d_pairs.append((sec, pa, pb))
    d_pairs.sort(key=lambda x: (-x[0], x[1], x[2]))

    used_defenders: set[int] = set()
    d_lines: list[list[int]] = []
    for sec, p1, p2 in d_pairs:
        if len(d_lines) >= MAX_D_LINES:
            break
        if sec < DEF_PAIR_MIN_S:
            break
        if p1 in used_defenders or p2 in used_defenders:
            continue
        used_defenders.add(p1)
        used_defenders.add(p2)
        d_lines.append([p1, p2])

    def format_line(line_id: str, kind: str, pids: list[int]) -> dict:
        sorted_pids = sorted(pids)
        player_list = []
        for pid in sorted_pids:
            meta = actual_players.get(pid) or actual_players.get(str(pid)) or {}
            pname = str(meta.get("name") or "")
            pnorm = meta.get("player_norm") or norm_name(pname)
            pos = str(meta.get("pos") or "")
            player_list.append({
                "player_id": int(pid),
                "name": pname,
                "player_norm": pnorm,
                "pos": pos,
            })
        pair_seconds = {}
        for i in range(len(sorted_pids)):
            for j in range(i + 1, len(sorted_pids)):
                pa, pb = sorted_pids[i], sorted_pids[j]
                pair_seconds[f"{pa}|{pb}"] = int(norm_pair_totals.get((pa, pb), 0))
        return {
            "line_id": line_id,
            "kind": kind,
            "players": player_list,
            "pair_seconds": pair_seconds,
        }

    out: list[dict] = []
    f_prefix = f"{actual_team_abbr}-F" if actual_team_abbr else "F"
    for idx, pids in enumerate(f_lines, 1):
        out.append(format_line(f"{f_prefix}{idx}", "F", pids))

    d_prefix = f"{actual_team_abbr}-D" if actual_team_abbr else "D"
    for idx, pids in enumerate(d_lines, 1):
        out.append(format_line(f"{d_prefix}{idx}", "D", pids))

    return out


# ---------------------------------------------------------------------------------------------------------------------
# 3. Artifact Builder
# ---------------------------------------------------------------------------------------------------------------------
def build_artifact(
    window: int = 10,
    as_of: str | None = None,
    game_ids: Sequence[int] | str | None = None,
) -> tuple[dict, dict]:
    """Build the NHL even-strength lines JSON artifact and execution statistics.

    Args:
        window: Number of trailing games per team to process (default 10).
        as_of: Cutoff date YYYY-MM-DD (inclusive).
        game_ids: Explicit sequence or comma-separated string of game IDs. If omitted,
                  resolved via trailing_window_ids over the games master.

    Returns:
        (artifact dict, stats dict)
    """
    gids: list[int] | None = None
    if isinstance(game_ids, str):
        gids = [int(x) for x in game_ids.split(",") if x.strip()]
    elif game_ids is not None:
        gids = [int(x) for x in game_ids]

    games_df = None
    try:
        games_df = storage.read_csv(GAMES_MASTER, low_memory=False)
    except Exception as exc:
        log.info("could not load games master (%s)", exc)

    if gids is None:
        if games_df is not None and not games_df.empty:
            resolved_ids = trailing_window_ids(games_df, window=window, as_of=as_of)
        else:
            resolved_ids = []
    else:
        resolved_ids = gids

    date_map: dict[int, str] = {}
    if games_df is not None and not games_df.empty and "game_id" in games_df.columns:
        for _, row in games_df.iterrows():
            try:
                gid = int(row["game_id"])
                d = str(row.get("date") or "")
                if d and d != "nan":
                    date_map[gid] = d
            except (ValueError, TypeError):
                continue

    # Sort games in ascending date order using games master date
    sorted_game_ids = sorted(resolved_ids, key=lambda gid: (date_map.get(gid, ""), gid))

    stats = {
        "games_requested": len(sorted_game_ids),
        "games_used": 0,
        "no_chart": 0,
        "no_pbp": 0,
        "teams_covered": 0,
        "lines_total": 0,
    }

    team_pair_totals: dict[str, dict[tuple[int, int], int]] = {}
    team_players: dict[str, dict[int, dict]] = {}
    team_games_count: dict[str, int] = {}
    processed_dates: list[str] = []

    for gid in sorted_game_ids:
        chart_key = raw_shiftchart_key(gid)
        chart_bytes = None
        try:
            chart_bytes = storage.read_bytes(chart_key)
        except Exception as exc:
            log.info("chart %s not in storage (%s)", gid, exc)
        if not chart_bytes:
            stats["no_chart"] += 1
            continue
        try:
            chart = json.loads(gzip.decompress(chart_bytes))
        except Exception as exc:
            log.warning("failed to decompress/parse chart %s (%s)", gid, exc)
            stats["no_chart"] += 1
            continue

        pbp_key = raw_pbp_key(gid)
        pbp_bytes = None
        try:
            pbp_bytes = storage.read_bytes(pbp_key)
        except Exception as exc:
            log.info("pbp %s not in storage (%s)", gid, exc)
        if not pbp_bytes:
            stats["no_pbp"] += 1
            continue
        try:
            pbp = json.loads(gzip.decompress(pbp_bytes))
        except Exception as exc:
            log.warning("failed to decompress/parse pbp %s (%s)", gid, exc)
            stats["no_pbp"] += 1
            continue

        game_pairs = game_pair_seconds(chart, pbp)
        if not game_pairs:
            continue

        stats["games_used"] += 1
        gdate = date_map.get(gid) or str(pbp.get("gameDate") or "")
        if gdate:
            processed_dates.append(gdate)

        for team_abbr, tdata in game_pairs.items():
            team_games_count[team_abbr] = team_games_count.get(team_abbr, 0) + 1
            pt = team_pair_totals.setdefault(team_abbr, {})
            for pair, sec in tdata.get("pairs", {}).items():
                pt[pair] = pt.get(pair, 0) + sec

            pl = team_players.setdefault(team_abbr, {})
            for pid, pmeta in tdata.get("players", {}).items():
                pname = pmeta.get("name", "")
                pl[pid] = {
                    "name": pname,
                    "pos": pmeta.get("pos", ""),
                    "player_norm": norm_name(pname),
                }

    artifact_as_of = max(processed_dates) if processed_dates else (str(as_of) if as_of else "")

    teams: dict[str, dict] = {}
    player_index: dict[str, list[dict]] = {}

    for team_abbr in sorted(team_games_count.keys()):
        pair_totals = team_pair_totals.get(team_abbr, {})
        players = team_players.get(team_abbr, {})
        lines = derive_lines(team_abbr, pair_totals, players)
        teams[team_abbr] = {
            "n_games": team_games_count[team_abbr],
            "lines": lines,
        }
        for line in lines:
            lid = line["line_id"]
            for p in line["players"]:
                pnorm = p["player_norm"]
                entry = {
                    "team": team_abbr,
                    "line_id": lid,
                    "player_id": p["player_id"],
                    "name": p["name"],
                }
                player_index.setdefault(pnorm, []).append(entry)

    stats["teams_covered"] = len(teams)
    stats["lines_total"] = sum(len(t["lines"]) for t in teams.values())

    artifact = {
        "version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "as_of": artifact_as_of,
        "window_games_per_team": window,
        "n_games": stats["games_used"],
        "teams": teams,
        "player_index": player_index,
    }
    return artifact, stats


# ---------------------------------------------------------------------------------------------------------------------
# 4. Publish
# ---------------------------------------------------------------------------------------------------------------------
def publish(artifact: dict) -> list[str]:
    """Publish the lines artifact to GCS (latest and dated keys)."""
    raw = json.dumps(artifact, indent=2).encode("utf-8")
    as_of = artifact.get("as_of") or "unknown"
    dated_key = LINES_DATED_KEY.format(date=as_of)
    keys_written = [LINES_LATEST_KEY, dated_key]
    for k in keys_written:
        storage.write_bytes(raw, k)
    return keys_written


# ---------------------------------------------------------------------------------------------------------------------
# 5. CLI
# ---------------------------------------------------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    """CLI to derive and publish NHL line combinations."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("--window", type=int, default=10, help="Trailing games per team window (default 10)")
    ap.add_argument("--as-of", dest="as_of", help="Cut-off date (YYYY-MM-DD)")
    ap.add_argument("--game-ids", help="Comma-separated list of game IDs")
    ap.add_argument("--out", help="Local file path to write artifact JSON")
    ap.add_argument("--no-write", action="store_true", help="Skip publishing to GCS storage")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    gids = [int(x) for x in args.game_ids.split(",") if x.strip()] if args.game_ids else None
    artifact, stats = build_artifact(window=args.window, as_of=args.as_of, game_ids=gids)

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(artifact, indent=2))
        log.info("wrote artifact to %s", args.out)

    if not args.no_write:
        keys = publish(artifact)
        log.info("published artifact to %s", keys)

    log.info("stats: %s", stats)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
