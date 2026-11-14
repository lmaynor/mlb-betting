"""ev.decisions_prep.n5_capture -- deterministic input capture for the N5
post-settlement correction sentinel (Argon Nov 13, 2026 sequencing ruling
3(c), clock 1: "Begin logging decision-time inputs paired with realized
ground truth immediately in deterministic Python").

For every settled row this capture pairs:
  * the settled ledger row itself (the value settlement consumed),
  * the CURRENT boxscore value for the same player/game/stat from the
    banked masters, captured at capture time,
  * the delta between them -- a nonzero delta is exactly the realized
    ground truth the sentinel exists to catch (a stat revised after
    settlement), and a zero delta is the negative the shadow scores
    against,
  * postgame prose items for the game where a banked prose source exists
    (currently none for NHL; the field is carried empty with a note
    rather than silently dropped).

Settled sources implemented:
  * NHL paper track (GCS NHL/paper_track/settled/{day}.jsonl) -- the
    first-pass dataset; its rows carry the settled `actual` directly.
  * The tracker bets table (mlb_core.tracking.bet_tracker shape) via
    --tracker-db: rows carry result/profit but NOT the settled actual, so
    those records pair the boxscore value with settled_actual=None and
    cannot produce a delta until the prose/fixture leg exists. The
    production ledger is Cloud SQL (MLB_DB_URL/DB_URL, see
    ev.model_picks._db_path); the local EV_Alerts mirrors are dev stubs.

Cadence (documented, NOT scheduled by this prep): run nightly after the
settlement jobs, inputs refreshed from the GCS prefixes above, output
appended under DecisionsPrep/n5_capture/. Each run re-snapshots the
masters, so consecutive runs produce the +24h/+48h boxscore diffs the
ruling names: run N's boxscore_current is compared against run N-1's for
the same settled row by diff_captures().

Usage:
  python -m ev.decisions_prep.n5_capture \
      --settled-dir /path/to/settled_jsonl_dir \
      --games-master games_master.csv --skater-master skater.csv \
      --goalie-master goalie.csv --out capture.jsonl
No API calls are made anywhere in this module.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

CAPTURE_SOURCE_NHL_PAPER = "nhl_paper_track"
CAPTURE_SOURCE_TRACKER = "bet_tracker"

# unmatched_reason values (join audit, 2026-11-13): a record whose settled
# row could not be paired with a current boxscore value says WHY, so the
# unmatched residue is self-describing instead of a bare count. The two
# failure modes are distinct: the game itself did not resolve against the
# games master, or the game resolved but the player has no boxscore row
# for it (did-not-dress: settled pre-game, absent from the master).
UNMATCHED_GAME_UNRESOLVED = "game_unresolved"
UNMATCHED_PLAYER_ABSENT = "player_absent_from_boxscore"

SKATER_STATS = ("goals", "assists", "points", "sog")
GOALIE_STATS = ("saves",)


def _norm_name(name: Any) -> str:
    return re.sub(r"[^a-z]", "", str(name or "").lower())


def parse_game_key(game_key: str) -> Optional[dict]:
    """'nhl:2026-11-02:COL@CHI' -> {sport, date, away, home} or None."""
    parts = str(game_key or "").split(":")
    if len(parts) != 3 or "@" not in parts[2]:
        return None
    away, home = parts[2].split("@", 1)
    return {"sport": parts[0], "date": parts[1], "away": away, "home": home}


def game_id_for(game_key: str, games_index: Mapping[tuple, int]) -> Optional[int]:
    """Deterministic (date, away_abbr, home_abbr) -> game_id via games master."""
    parsed = parse_game_key(game_key)
    if not parsed:
        return None
    return games_index.get((parsed["date"], parsed["away"], parsed["home"]))


def build_games_index(games_rows: Iterable[Mapping[str, Any]]) -> dict:
    return {
        (str(r["date"]), str(r["away_abbr"]), str(r["home_abbr"])): int(r["game_id"])
        for r in games_rows
    }


def build_boxscore_index(
    skater_rows: Iterable[Mapping[str, Any]],
    goalie_rows: Iterable[Mapping[str, Any]],
) -> dict:
    """(game_id, player_id) -> {stat: current value} from both masters."""
    index: dict[tuple, dict] = {}
    for rows, stats in ((skater_rows, SKATER_STATS), (goalie_rows, GOALIE_STATS)):
        for r in rows:
            key = (int(r["game_id"]), int(r["player_id"]))
            entry = index.setdefault(key, {})
            for s in stats:
                if r.get(s) is not None:
                    entry[s] = float(r[s])
    return index


def build_capture_record(
    settled: Mapping[str, Any],
    *,
    source: str,
    game_id: Optional[int],
    boxscore_current: Optional[float],
    boxscore_source: Optional[str],
    captured_ts: str,
    prose_items: Sequence[Mapping[str, Any]] = (),
    prose_note: Optional[str] = None,
    unmatched_reason: Optional[str] = None,
) -> dict:
    """One capture record: settled row + realized ground truth pairing.

    ``settled_actual`` is the value settlement consumed (None for tracker
    rows, which do not store it). ``delta`` is boxscore_current -
    settled_actual when both exist; correction_detected is delta != 0.
    ``unmatched_reason`` is None when a boxscore value was paired, else
    one of UNMATCHED_GAME_UNRESOLVED / UNMATCHED_PLAYER_ABSENT.
    """
    settled_actual = settled.get("actual")
    delta = None
    if settled_actual is not None and boxscore_current is not None:
        delta = float(boxscore_current) - float(settled_actual)
    return {
        "capture_id": (
            f"{source}:{settled.get('game_key') or settled.get('game_date')}:"
            f"{settled.get('player_id') or _norm_name(settled.get('player'))}:"
            f"{settled.get('stat') or settled.get('bet_type')}"
        ),
        "source": source,
        "game_key": settled.get("game_key"),
        "game_id": game_id,
        "game_date": settled.get("game_date")
        or (parse_game_key(settled.get("game_key") or "") or {}).get("date"),
        "player_id": settled.get("player_id"),
        "player": settled.get("player"),
        "stat": settled.get("stat") or settled.get("bet_type"),
        "line": settled.get("line") if settled.get("line") is not None else settled.get("point"),
        "settled_actual": settled_actual,
        "settled_result": settled.get("result"),
        "settled_ts": settled.get("settled_ts") or settled.get("settled_at"),
        "void": bool(settled.get("void", False)),
        "boxscore_current": boxscore_current,
        "boxscore_source": boxscore_source,
        "boxscore_captured_ts": captured_ts,
        "delta": delta,
        "correction_detected": (delta is not None and delta != 0),
        "unmatched_reason": unmatched_reason,
        "prose_items": list(prose_items),
        "prose_note": prose_note,
    }


def diff_captures(
    earlier: Sequence[Mapping[str, Any]], later: Sequence[Mapping[str, Any]]
) -> list[dict]:
    """Boxscore diffs between two capture runs for the same settled rows.

    This is the ruling's 'first-final vs +24h/+48h boxscore diff': each
    entry names a settled row whose current boxscore value moved between
    the two snapshots, with both values and both capture timestamps.
    """
    before = {r["capture_id"]: r for r in earlier}
    diffs = []
    for rec in later:
        prev = before.get(rec["capture_id"])
        if prev is None:
            continue
        a, b = prev.get("boxscore_current"), rec.get("boxscore_current")
        if a is not None and b is not None and float(a) != float(b):
            diffs.append(
                {
                    "capture_id": rec["capture_id"],
                    "stat": rec["stat"],
                    "earlier_value": a,
                    "later_value": b,
                    "earlier_ts": prev.get("boxscore_captured_ts"),
                    "later_ts": rec.get("boxscore_captured_ts"),
                }
            )
    return diffs


# --------------------------------------------------------------------------
# settled-source adapters
# --------------------------------------------------------------------------

def read_nhl_paper_settled(settled_dir: Path) -> list[dict]:
    """All rows from NHL/paper_track/settled/*.jsonl in a local directory."""
    rows: list[dict] = []
    day_file = re.compile(r"^\d{4}-\d{2}-\d{2}\.jsonl$")
    for path in sorted(Path(settled_dir).glob("*.jsonl")):
        if not day_file.match(path.name):
            continue  # never ingest a capture output or stray file as settled input
        for line in path.read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def read_tracker_settled(db_path: str | Path) -> list[dict]:
    """Settled rows from a bet_tracker sqlite DB (result recorded).

    Tracker rows do not store the settled actual; records built from them
    carry settled_actual=None (see module docstring).
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT game_date, player, bet_type, result, settled_at, notes "
            "FROM bets WHERE result IS NOT NULL AND result != ''"
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------

def run_nhl_paper_capture(
    settled_dir: Path,
    games_master_csv: Path,
    skater_master_csv: Path,
    goalie_master_csv: Path,
    out_path: Path,
    captured_ts: Optional[str] = None,
) -> dict:
    """First-pass capture over the NHL paper settled ledger. Returns counts."""
    import pandas as pd

    captured_ts = captured_ts or datetime.now(timezone.utc).isoformat()
    settled = read_nhl_paper_settled(settled_dir)
    games_index = build_games_index(pd.read_csv(games_master_csv).to_dict("records"))
    box_index = build_boxscore_index(
        pd.read_csv(skater_master_csv).to_dict("records"),
        pd.read_csv(goalie_master_csv).to_dict("records"),
    )
    records, unmatched = [], 0
    for row in settled:
        gid = game_id_for(row.get("game_key", ""), games_index)
        current = None
        if gid is not None and row.get("player_id") is not None:
            current = box_index.get((gid, int(row["player_id"])), {}).get(row["stat"])
        if current is None:
            unmatched += 1
            unmatched_reason = (
                UNMATCHED_GAME_UNRESOLVED if gid is None else UNMATCHED_PLAYER_ABSENT
            )
        else:
            unmatched_reason = None
        records.append(
            build_capture_record(
                row,
                source=CAPTURE_SOURCE_NHL_PAPER,
                game_id=gid,
                boxscore_current=current,
                boxscore_source="NHL/skater_boxscores_master.csv+NHL/goalie_boxscores_master.csv",
                captured_ts=captured_ts,
                prose_note="no banked NHL postgame prose source; field reserved",
                unmatched_reason=unmatched_reason,
            )
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return {
        "settled_rows": len(settled),
        "records": len(records),
        "unmatched_boxscore": unmatched,
        "unmatched_game_unresolved": sum(
            1 for r in records if r["unmatched_reason"] == UNMATCHED_GAME_UNRESOLVED
        ),
        "unmatched_player_absent": sum(
            1 for r in records if r["unmatched_reason"] == UNMATCHED_PLAYER_ABSENT
        ),
        "corrections_detected": sum(1 for r in records if r["correction_detected"]),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="N5 deterministic input capture (NHL paper leg)")
    ap.add_argument("--settled-dir", required=True)
    ap.add_argument("--games-master", required=True)
    ap.add_argument("--skater-master", required=True)
    ap.add_argument("--goalie-master", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    counts = run_nhl_paper_capture(
        Path(args.settled_dir),
        Path(args.games_master),
        Path(args.skater_master),
        Path(args.goalie_master),
        Path(args.out),
    )
    print(json.dumps(counts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
