"""N6 deterministic input capture (Argon ruling 3(c), clock 1).

Ruling: ~/workspace/beezy/profitability/argon-decisions-api-expansion-2026-11-13.md
section 2 (N6) and section 3(c). Clock 1 starts NOW, before PR #242/#243
merge: log decision-time inputs paired with realized ground truth in
deterministic Python -- for N6 that is (expected-lines state available
before the game, realized night-of shift-chart 5v5 trios). Clock 2 (live
gemini-decision-1 shadow scoring) does NOT start here; this module makes
NO API calls and NO Decisions calls.

For each banked team-game this module emits one record:

    {team, game_id, game_date, decision_context, realized_5v5_trios,
     realized_pp_units, capture_ts}

* ``decision_context`` is the expected-lines state available BEFORE the
  game: the team's realized lines from its previous banked game in the
  same season (the trailing-artifact state a pre-game process could have
  known; ``source == "prior_banked_team_game_realized"``), or nulls with
  ``source == "none_first_banked_appearance"`` for a team's first banked
  game of a season. When a dated ``nhl/data/lines.py`` artifact exists
  with ``as_of`` strictly before the game date, the team's lines from the
  latest such artifact are attached as ``lines_artifact`` (never an
  artifact dated on/after the game -- that would be hindsight leakage).
* ``realized_5v5_trios`` are derived from the banked shift chart with the
  repo's own machinery: the ``nhl.data.toi_splits`` frames + anchored
  ``timeline`` (the same inputs ``nhl.data.lines.game_pair_seconds``
  builds on) for pairwise 5v5 seconds, plus exact trio co-occurrence
  seconds computed from per-player 5v5 coverage on the same timeline. A trio is
  emitted only when its exact shared 5v5 time is >= ``TRIO_MIN_S``
  seconds (120 s) in the single game.
* ``realized_pp_units`` come from ``nhl.data.toi_splits.build_game``
  (``pp_unit_guess`` 1/2 per skater). Units are ``None`` for a team when
  the game carried no usable power-play time for it.

Determinism / idempotence: games are processed in (date, game_id) order
and teams in abbreviation order; re-running over the same banked inputs
reproduces the same records except for ``capture_ts`` (a single run-level
timestamp; pin it with ``--capture-ts`` for a byte-identical rerun). The
CLI overwrites its output, so a rerun is idempotent.

Usage:
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data \\
      python -m ev.decisions_prep.n6_capture \\
        --out gs://concrete-crow-445205-m4-mlb-data/DecisionsPrep/n6_capture/capture_2026-11-13.jsonl
    python -m ev.decisions_prep.n6_capture --out /tmp/capture.jsonl --game-ids 2026020309
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Iterable, Mapping, Optional, Sequence

import numpy as np

log = logging.getLogger("ev.decisions_prep.n6_capture")

# Single-game trio verification thresholds (the 10-game aggregate
# thresholds in nhl.data.lines -- 900 s seed / 600 s third -- cannot apply
# to one game; these are the single-game analogues, stated in the N6
# fixture README).
SEED_PAIR_MIN_S = 120   # min shared 5v5 seconds for a forward seed pair
TRIO_MIN_S = 120        # min EXACT shared 5v5 seconds for an emitted trio
MAX_F_LINES = 4

FORWARD_POS = {"C", "L", "R"}

# ---------------------------------------------------------------------------
# Name / roster helpers (same extraction rules as nhl.data.lines)
# ---------------------------------------------------------------------------


def _name_part(val: object) -> str:
    if isinstance(val, dict):
        return str(val.get("default") or "")
    if isinstance(val, str):
        return val
    return ""


def player_display_name(roster_spot: Mapping) -> str:
    first = _name_part(roster_spot.get("firstName")).strip()
    last = _name_part(roster_spot.get("lastName")).strip()
    if first and last:
        return f"{first} {last}"
    if first or last:
        return first or last
    name_val = roster_spot.get("name")
    if isinstance(name_val, dict):
        return str(name_val.get("default") or "").strip()
    if isinstance(name_val, str):
        return name_val.strip()
    return ""


def roster_name_map(pbp: Mapping) -> dict[int, str]:
    out: dict[int, str] = {}
    for spot in (pbp or {}).get("rosterSpots") or []:
        try:
            pid = int(spot.get("playerId", -1))
        except (TypeError, ValueError):
            continue
        if pid >= 0:
            out[pid] = player_display_name(spot)
    return out


# ---------------------------------------------------------------------------
# Realized 5v5 trios (single game)
# ---------------------------------------------------------------------------


def _team_coverage(chart: dict, pbp: dict) -> Optional[dict]:
    """Per-team 5v5 coverage vectors + player metadata for one game.

    Single pass over the same machinery nhl.data.lines.game_pair_seconds
    uses: shifts/roster frames and the per-second timeline from
    nhl.data.toi_splits (anchored to the play-by-play), the 5v5 mask
    (eff_home == 5) & (eff_away == 5), pairwise shared seconds from the
    coverage matrix product -- identical definitions, computed once so
    the exact trio seconds below reuse the same coverage. Returns
    {team_abbr: {"players": {pid: {"name","pos"}}, "pairs": {(a,b): s},
                 "coverage": {pid: np.ndarray[bool]}}} or None.
    """
    from nhl.data.toi_splits import anchor_to_pbp, roster_frame, shifts_frame, timeline

    if not chart or not (chart.get("data") or []):
        return None
    if not pbp or not (pbp.get("rosterSpots") or []):
        return None
    home_info = pbp.get("homeTeam") or {}
    away_info = pbp.get("awayTeam") or {}
    try:
        home_id, away_id = int(home_info.get("id")), int(away_info.get("id"))
    except (TypeError, ValueError):
        return None
    if home_id < 0 or away_id < 0:
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
    n = int(tl.get("n", 0))
    if n <= 0:
        return None
    mask = (tl["eff_home"] == 5) & (tl["eff_away"] == 5)
    team_abbrs: dict[int, str] = {}
    for tid, info in ((home_id, home_info), (away_id, away_info)):
        if info.get("abbrev"):
            team_abbrs[tid] = str(info["abbrev"])
        else:
            sh_teams = sh[sh["team_id"] == tid]["team"].dropna()
            team_abbrs[tid] = str(sh_teams.iloc[0]) if not sh_teams.empty else str(tid)
    sh_pids = set(int(p) for p in sh["player_id"])
    out: dict[str, dict] = {}
    for tid in (home_id, away_id):
        players: dict[int, dict] = {}
        for spot in pbp.get("rosterSpots") or []:
            try:
                p_tid, pid = int(spot.get("teamId", -1)), int(spot.get("playerId", -1))
            except (TypeError, ValueError):
                continue
            pos = str(spot.get("positionCode") or "")
            if p_tid != tid or pid < 0 or pos == "G" or pid not in sh_pids:
                continue
            players[pid] = {"name": player_display_name(spot), "pos": pos}
        skater_ids = sorted(players)
        coverage: dict[int, np.ndarray] = {}
        for pid in skater_ids:
            p_sh = sh[sh["player_id"] == pid]
            cov = np.zeros(n, dtype=bool)
            for a, b in zip(p_sh["start"], p_sh["end"]):
                cov[max(0, min(int(a), n)):max(0, min(int(b), n))] = True
            coverage[pid] = cov & mask
        pairs: dict[tuple[int, int], int] = {}
        if skater_ids:
            mat = np.stack([coverage[pid] for pid in skater_ids]).astype(np.int32)
            shared = mat @ mat.T
            for i in range(len(skater_ids)):
                for j in range(i + 1, len(skater_ids)):
                    sec = int(shared[i, j])
                    if sec > 0:
                        pairs[(skater_ids[i], skater_ids[j])] = sec
        out[team_abbrs[tid]] = {"players": players, "pairs": pairs, "coverage": coverage}
    return out


def derive_single_game_trios(team_data: Mapping) -> list[dict]:
    """Greedy forward trios for ONE game from pair seconds + exact trio time.

    Seed pairs are tried in descending shared-5v5-seconds order; the third
    member maximizes the trio's EXACT shared 5v5 seconds (coverage AND).
    A trio is kept only at >= TRIO_MIN_S exact seconds. Trios are returned
    ranked by exact trio seconds (rank 1 = most 5v5 time together).
    """
    players: Mapping[int, dict] = team_data.get("players", {})  # type: ignore[assignment]
    pairs: Mapping[tuple[int, int], int] = team_data.get("pairs", {})  # type: ignore[assignment]
    coverage: Mapping[int, np.ndarray] = team_data.get("coverage", {})  # type: ignore[assignment]
    forwards = sorted(
        pid for pid, meta in players.items()
        if str(meta.get("pos") or "").upper() in FORWARD_POS and pid in coverage
    )
    if len(forwards) < 3:
        return []

    def trio_seconds(a: int, b: int, c: int) -> int:
        return int((coverage[a] & coverage[b] & coverage[c]).sum())

    seed_pairs = sorted(
        ((sec, a, b) for (a, b), sec in pairs.items() if a in forwards and b in forwards),
        key=lambda x: (-x[0], x[1], x[2]),
    )
    used: set[int] = set()
    trios: list[dict] = []
    for sec, p1, p2 in seed_pairs:
        if len(trios) >= MAX_F_LINES:
            break
        if sec < SEED_PAIR_MIN_S:
            break
        if p1 in used or p2 in used:
            continue
        best_c, best_ts = None, -1
        for c in forwards:
            if c in used or c in (p1, p2):
                continue
            ts = trio_seconds(p1, p2, c)
            if ts > best_ts or (ts == best_ts and best_c is not None and c < best_c):
                best_c, best_ts = c, ts
        if best_c is None or best_ts < TRIO_MIN_S:
            continue
        used.update((p1, p2, best_c))
        member_ids = sorted((p1, p2, best_c))
        trios.append({
            "player_ids": member_ids,
            "players": sorted(str(players[pid].get("name") or "") for pid in member_ids),
            "trio_seconds": int(best_ts),
            "pair_seconds": {
                f"{a}|{b}": int(pairs.get((a, b), 0))
                for i, a in enumerate(member_ids) for b in member_ids[i + 1:]
            },
        })
    trios.sort(key=lambda t: (-t["trio_seconds"], t["player_ids"]))
    for idx, trio in enumerate(trios, 1):
        trio["rank"] = idx
    return trios


def realized_trios_for_game(chart: dict, pbp: dict) -> dict[str, list[dict]]:
    """{team_abbr: [trio, ...]} realized 5v5 forward trios for one game."""
    cov = _team_coverage(chart, pbp)
    if not cov:
        return {}
    return {abbr: derive_single_game_trios(tdata) for abbr, tdata in cov.items()}


# ---------------------------------------------------------------------------
# Realized PP units (single game)
# ---------------------------------------------------------------------------


def realized_pp_units_for_game(game_id: int, chart: dict, pbp: dict) -> dict[str, Optional[dict]]:
    """{team_abbr: {"pp1": [names], "pp2": [names], "team_pp_s": int} | None}.

    From nhl.data.toi_splits.build_game pp_unit_guess (share of the team's
    power-play seconds). None when the team had no power-play time or the
    splits could not be built.
    """
    from nhl.data.toi_splits import build_game

    try:
        df, qa = build_game(int(game_id), chart, pbp)
    except Exception as exc:  # noqa: BLE001 -- one bad game must not sink the run
        log.warning("pp units game %s failed (%s)", game_id, exc)
        return {}
    if df is None or getattr(df, "empty", True) or qa.get("status") != "ok":
        return {}
    names = roster_name_map(pbp)
    out: dict[str, Optional[dict]] = {}
    for team, sub in df.groupby("team"):
        team_pp_s = int(sub["team_pp_s"].iloc[0]) if len(sub) else 0
        if team_pp_s <= 0:
            out[str(team)] = None
            continue
        pp1 = sorted(names.get(int(pid), str(int(pid))) for pid in sub.loc[sub["pp_unit_guess"] == 1, "player_id"])
        pp2 = sorted(names.get(int(pid), str(int(pid))) for pid in sub.loc[sub["pp_unit_guess"] == 2, "player_id"])
        out[str(team)] = {"pp1": pp1, "pp2": pp2, "team_pp_s": team_pp_s}
    return out


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------


def _artifact_lines_for(artifacts: Sequence[Mapping], team: str, game_date: str) -> Optional[dict]:
    """Latest lines artifact with as_of strictly before game_date, one team."""
    best: Optional[Mapping] = None
    for art in artifacts:
        as_of = str(art.get("as_of") or "")
        if as_of and as_of < str(game_date) and team in (art.get("teams") or {}):
            if best is None or as_of > str(best.get("as_of") or ""):
                best = art
    if best is None:
        return None
    lines = []
    for line in (best.get("teams", {}).get(team, {}) or {}).get("lines", []):
        lines.append({
            "line_id": line.get("line_id"),
            "kind": line.get("kind"),
            "players": sorted(str(p.get("name") or "") for p in line.get("players", [])),
        })
    return {"as_of": str(best.get("as_of")), "lines": lines}


def build_records(
    games: Sequence[Mapping],
    *,
    chart_loader: Callable[[int], Optional[dict]],
    pbp_loader: Callable[[int], Optional[dict]],
    lines_artifacts: Sequence[Mapping] = (),
    capture_ts: Optional[str] = None,
) -> list[dict]:
    """Assemble capture records. ``games`` rows carry game_id, date,
    season_year, home_abbr, away_abbr (games-master shape). Loaders return
    parsed JSON dicts or None. Pure given the loaders: no I/O of its own.
    """
    capture_ts = capture_ts or datetime.now(timezone.utc).isoformat()
    ordered = sorted(games, key=lambda g: (str(g.get("date") or ""), int(g.get("game_id", 0))))
    last_by_team_season: dict[tuple[str, object], dict] = {}
    records: list[dict] = []
    for game in ordered:
        gid = int(game["game_id"])
        gdate = str(game.get("date") or "")
        season = game.get("season_year")
        chart = chart_loader(gid)
        pbp = pbp_loader(gid)
        if not chart or not pbp:
            log.info("game %s skipped (chart=%s pbp=%s)", gid, bool(chart), bool(pbp))
            continue
        trios_by_team = realized_trios_for_game(chart, pbp)
        pp_by_team = realized_pp_units_for_game(gid, chart, pbp)
        for team in sorted(trios_by_team.keys()):
            prior = last_by_team_season.get((team, season))
            if prior is not None:
                context = {
                    "source": "prior_banked_team_game_realized",
                    "prior_game_id": prior["game_id"],
                    "prior_game_date": prior["game_date"],
                    "expected_5v5_trios": prior["realized_5v5_trios"],
                    "expected_pp_units": prior["realized_pp_units"],
                }
            else:
                context = {
                    "source": "none_first_banked_appearance",
                    "prior_game_id": None,
                    "prior_game_date": None,
                    "expected_5v5_trios": None,
                    "expected_pp_units": None,
                }
            context["lines_artifact"] = _artifact_lines_for(lines_artifacts, team, gdate)
            record = {
                "team": team,
                "game_id": gid,
                "game_date": gdate,
                "season_year": season,
                "decision_context": context,
                "realized_5v5_trios": trios_by_team.get(team, []),
                "realized_pp_units": pp_by_team.get(team),
                "capture_ts": capture_ts,
            }
            records.append(record)
            last_by_team_season[(team, season)] = record
    records.sort(key=lambda r: (r["game_date"], r["game_id"], r["team"]))
    return records


# ---------------------------------------------------------------------------
# Default (GCS-backed) loaders + CLI
# ---------------------------------------------------------------------------


def _storage_json_gz(key: str) -> Optional[dict]:
    from mlb_core import storage

    try:
        raw = storage.read_bytes(key)
    except Exception as exc:  # noqa: BLE001
        log.info("storage read %s failed (%s)", key, exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(gzip.decompress(raw))
    except Exception as exc:  # noqa: BLE001
        log.warning("storage object %s unreadable (%s)", key, exc)
        return None


def default_games(game_ids: Optional[Iterable[int]] = None) -> list[dict]:
    """Games-master rows for the banked shift charts (or an explicit list)."""
    from mlb_core import storage
    from nhl.data.settings import GAMES_MASTER, raw_shiftchart_key

    df = storage.read_csv(GAMES_MASTER, low_memory=False)
    by_id = {int(r["game_id"]): r for _, r in df.iterrows()}
    if game_ids is not None:
        wanted = [int(g) for g in game_ids]
    else:
        keys = storage.list_keys("NHL/raw/shiftcharts/")
        wanted = []
        for key in keys:
            tail = key.rsplit("/", 1)[-1].removesuffix(".json.gz")
            if tail.isdigit():
                wanted.append(int(tail))
        _ = raw_shiftchart_key  # key scheme documented in nhl.data.settings
    games = []
    for gid in sorted(wanted):
        row = by_id.get(gid)
        if row is None:
            log.warning("game %s has a banked chart but no games-master row; skipped", gid)
            continue
        games.append({
            "game_id": gid,
            "date": str(row.get("date") or ""),
            "season_year": int(row["season_year"]) if str(row.get("season_year", "")).strip() not in ("", "nan") else None,
            "home_abbr": str(row.get("home_abbr") or ""),
            "away_abbr": str(row.get("away_abbr") or ""),
        })
    return games


def default_lines_artifacts() -> list[dict]:
    from mlb_core import storage

    artifacts: list[dict] = []
    try:
        keys = storage.list_keys("NHL/lines/")
    except Exception as exc:  # noqa: BLE001
        log.info("lines artifact listing failed (%s)", exc)
        return artifacts
    for key in keys:
        if not key.endswith(".json") or key.endswith("lines_latest.json"):
            continue
        try:
            artifacts.append(json.loads(storage.read_bytes(key)))
        except Exception as exc:  # noqa: BLE001
            log.info("lines artifact %s unreadable (%s)", key, exc)
    return artifacts


def write_jsonl(records: Sequence[Mapping], out: str) -> None:
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records).encode("utf-8")
    if out.startswith("gs://"):
        from mlb_core import storage

        key = out[len("gs://"):].split("/", 1)[1]  # strip bucket; env selects it
        storage.write_bytes(body, key)
    else:
        from pathlib import Path

        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="output JSONL path (local path or gs:// URL)")
    ap.add_argument("--game-ids", help="comma-separated game IDs (default: every banked shift chart)")
    ap.add_argument("--capture-ts", help="pin the run timestamp (default: now, UTC ISO-8601)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from nhl.data.settings import raw_pbp_key, raw_shiftchart_key

    game_ids = [int(x) for x in args.game_ids.split(",") if x.strip()] if args.game_ids else None
    games = default_games(game_ids)
    records = build_records(
        games,
        chart_loader=lambda gid: _storage_json_gz(raw_shiftchart_key(gid)),
        pbp_loader=lambda gid: _storage_json_gz(raw_pbp_key(gid)),
        lines_artifacts=default_lines_artifacts(),
        capture_ts=args.capture_ts,
    )
    write_jsonl(records, args.out)
    summary = {
        "games": len(games),
        "records": len(records),
        "teams": len({r["team"] for r in records}),
        "with_expected": sum(1 for r in records if r["decision_context"]["expected_5v5_trios"] is not None),
        "out": args.out,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
