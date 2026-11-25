"""MOD-04e E0(iv): game-day inactives ingest and banking.

Source strategy (recorded honestly):
  Primary historical/mirror source is nflverse ``rosters_weekly`` (``game_status`` /
  ``status``) combined with ``injuries`` (``report_status``) read from the GCS NFL
  mirror via ``nfl.data.read.load``. This is a post-hoc official record rather than
  a live Sunday 90-minute pre-kickoff feed; when a live 90-min feed (e.g. ESPN /
  NFL official inactive wire) is wired, it plugs directly into the exact same
  ``InactiveRecord`` schema and ``bank_inactives`` / ``rescore_ready`` interface.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from mlb_core import storage
from nfl.data import read as nfl_read

INACTIVE_STATUSES = frozenset({
    "INA", "INACTIVE", "OUT", "DOUBTFUL", "IR", "RES", "PUP", "NFI", "SUS", "DNP",
    "INJURED RESERVE", "RESERVE/INJURED", "SCRATCH", "SCRATCHED",
})

_GAME_SW_RE = re.compile(r"^(\d{4})_(\d{1,2})_")


@dataclass(frozen=True)
class InactiveRecord:
    player: str
    team: str
    game: str
    status: str
    source: str
    retrieved_at_utc: str
    season: Optional[int] = None
    week: Optional[int] = None
    player_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_iso(ts: Any = None) -> str:
    if ts is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = pd.to_datetime(ts, utc=True, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.to_pydatetime().astimezone(timezone.utc).isoformat()


def _parse_sw_from_game(game: str) -> tuple[Optional[int], Optional[int]]:
    m = _GAME_SW_RE.match(str(game or ""))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def fetch_inactives(
    season: int,
    week: int,
    source: str = "nflverse",
    *,
    rosters_weekly: Optional[pd.DataFrame] = None,
    injuries: Optional[pd.DataFrame] = None,
    schedules: Optional[pd.DataFrame] = None,
    retrieved_at_utc: Optional[str] = None,
) -> list[InactiveRecord]:
    """Fetch official game-day inactives for (season, week) as InactiveRecord objects."""
    s_int, w_int = int(season), int(week)
    default_ts = _utc_iso(retrieved_at_utc)

    if rosters_weekly is None and source == "nflverse":
        rosters_weekly = nfl_read.load("rosters_weekly", seasons=[s_int], strict=False)
    if injuries is None and source == "nflverse":
        injuries = nfl_read.load("injuries", seasons=[s_int], strict=False)
    if schedules is None and source == "nflverse":
        schedules = nfl_read.load("schedules")

    # Map (season, week, team) -> game_id
    game_map: dict[tuple[int, int, str], str] = {}
    if schedules is not None and not schedules.empty:
        sub_s = schedules[
            (pd.to_numeric(schedules["season"], errors="coerce") == s_int)
            & (pd.to_numeric(schedules["week"], errors="coerce") == w_int)
        ]
        for r in sub_s.to_dict("records"):
            gid = str(r.get("game_id") or "")
            if not gid:
                continue
            for col in ("home_team", "away_team"):
                tm = str(r.get(col) or "").strip().upper()
                if tm:
                    game_map[(s_int, w_int, tm)] = gid

    records: dict[tuple[str, str], InactiveRecord] = {}

    if rosters_weekly is not None and not rosters_weekly.empty:
        rw = rosters_weekly.copy()
        if "season" in rw.columns:
            rw = rw[pd.to_numeric(rw["season"], errors="coerce") == s_int]
        if "week" in rw.columns:
            rw = rw[pd.to_numeric(rw["week"], errors="coerce") == w_int]
        for r in rw.to_dict("records"):
            gs = str(r.get("game_status") or "").strip().upper()
            st = str(r.get("status") or "").strip().upper()
            chosen = gs if gs in INACTIVE_STATUSES else (st if st in INACTIVE_STATUSES else "")
            if not chosen:
                continue
            tm = str(r.get("team") or "").strip().upper()
            player = str(r.get("full_name") or r.get("player_name") or r.get("gsis_id") or "").strip()
            pid = str(r.get("gsis_id") or "").strip() or None
            game = str(r.get("game_id") or game_map.get((s_int, w_int, tm), f"{s_int}_{w_int:02d}_{tm}"))
            ts = _utc_iso(r.get("retrieved_at_utc") or r.get("date_modified") or default_ts)
            if not player:
                continue
            records[(pid or player.lower(), tm)] = InactiveRecord(
                player=player,
                team=tm,
                game=game,
                status=chosen,
                source=f"{source}:rosters_weekly",
                retrieved_at_utc=ts,
                season=s_int,
                week=w_int,
                player_id=pid,
            )

    if injuries is not None and not injuries.empty:
        inj = injuries.copy()
        if "season" in inj.columns:
            inj = inj[pd.to_numeric(inj["season"], errors="coerce") == s_int]
        if "week" in inj.columns:
            inj = inj[pd.to_numeric(inj["week"], errors="coerce") == w_int]
        for r in inj.to_dict("records"):
            rst = str(r.get("report_status") or r.get("status") or "").strip().upper()
            if rst not in INACTIVE_STATUSES:
                continue
            tm = str(r.get("team") or "").strip().upper()
            player = str(r.get("full_name") or r.get("player_name") or r.get("gsis_id") or "").strip()
            pid = str(r.get("gsis_id") or "").strip() or None
            game = str(r.get("game_id") or game_map.get((s_int, w_int, tm), f"{s_int}_{w_int:02d}_{tm}"))
            ts = _utc_iso(r.get("retrieved_at_utc") or r.get("date_modified") or default_ts)
            if not player:
                continue
            key = (pid or player.lower(), tm)
            if key not in records:
                records[key] = InactiveRecord(
                    player=player,
                    team=tm,
                    game=game,
                    status=rst,
                    source=f"{source}:injuries",
                    retrieved_at_utc=ts,
                    season=s_int,
                    week=w_int,
                    player_id=pid,
                )

    return sorted(records.values(), key=lambda x: (x.team, x.player))


def _partition_key(season: int, week: int, ext: str) -> str:
    return f"NFL/mod04e/inactives/season={int(season)}/week={int(week):02d}/inactives.{ext}"


def _write_partition(df: pd.DataFrame, season: int, week: int, root: Optional[str | Path]) -> tuple[str, str]:
    pq_rel = _partition_key(season, week, "parquet")
    js_rel = _partition_key(season, week, "json")
    js_bytes = json.dumps(df.to_dict("records"), indent=2).encode("utf-8")

    if root is None or str(root).startswith("gs://"):
        storage.write_parquet(df, pq_rel)
        storage.write_bytes(js_bytes, js_rel)
        return pq_rel, js_rel

    base = Path(root)
    pq_path = base / pq_rel
    js_path = base / js_rel
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(pq_path, index=False)
    js_path.write_bytes(js_bytes)
    return str(pq_path), str(js_path)


def bank_inactives(
    records: Iterable[InactiveRecord | Mapping[str, Any]],
    root: Optional[str | Path] = None,
    *,
    season: Optional[int] = None,
    week: Optional[int] = None,
) -> dict[tuple[int, int], tuple[str, str]]:
    """Write timestamped InactiveRecord partitions (parquet + JSON) under NFL/mod04e/inactives/season=/week=/."""
    rows: list[dict[str, Any]] = []
    for rec in records:
        d = rec.to_dict() if isinstance(rec, InactiveRecord) else dict(rec)
        s_val = d.get("season") if d.get("season") is not None else season
        w_val = d.get("week") if d.get("week") is not None else week
        if s_val is None or w_val is None:
            ps, pw = _parse_sw_from_game(str(d.get("game") or ""))
            s_val = s_val if s_val is not None else ps
            w_val = w_val if w_val is not None else pw
        if s_val is None or w_val is None:
            raise ValueError(f"Cannot determine (season, week) for inactive record {d!r}")
        d["season"] = int(s_val)
        d["week"] = int(w_val)
        rows.append(d)

    if not rows:
        if season is not None and week is not None:
            empty_df = pd.DataFrame(columns=["player", "team", "game", "status", "source", "retrieved_at_utc", "season", "week", "player_id"])
            return {(int(season), int(week)): _write_partition(empty_df, int(season), int(week), root)}
        return {}

    df_all = pd.DataFrame(rows)
    written: dict[tuple[int, int], tuple[str, str]] = {}
    for (s_i, w_i), sub in df_all.groupby(["season", "week"], sort=True):
        written[(int(s_i), int(w_i))] = _write_partition(sub.reset_index(drop=True), int(s_i), int(w_i), root)
    return written


def load_inactives(season: int, week: int, root: Optional[str | Path] = None) -> list[InactiveRecord]:
    """Load banked InactiveRecord objects for (season, week)."""
    pq_rel = _partition_key(season, week, "parquet")
    if root is None or str(root).startswith("gs://"):
        df = storage.read_parquet(pq_rel)
    else:
        df = pd.read_parquet(Path(root) / pq_rel)
    out: list[InactiveRecord] = []
    for r in df.to_dict("records"):
        out.append(InactiveRecord(
            player=str(r["player"]),
            team=str(r["team"]),
            game=str(r["game"]),
            status=str(r["status"]),
            source=str(r["source"]),
            retrieved_at_utc=str(r["retrieved_at_utc"]),
            season=int(r["season"]) if pd.notna(r.get("season")) else None,
            week=int(r["week"]) if pd.notna(r.get("week")) else None,
            player_id=str(r["player_id"]) if r.get("player_id") is not None and pd.notna(r.get("player_id")) else None,
        ))
    return out


def rescore_ready(
    inactives: Iterable[InactiveRecord | Mapping[str, Any]],
    kickoff_utc: Any,
    lead_minutes: float = 90.0,
) -> list[InactiveRecord]:
    """Return records known at or before (kickoff_utc - lead_minutes)."""
    ko = pd.Timestamp(kickoff_utc)
    if ko.tzinfo is None:
        ko = ko.tz_localize("UTC")
    else:
        ko = ko.tz_convert("UTC")
    cutoff = ko - timedelta(minutes=float(lead_minutes))

    ready: list[InactiveRecord] = []
    for rec in inactives:
        obj = rec if isinstance(rec, InactiveRecord) else InactiveRecord(
            player=str(rec["player"]),
            team=str(rec["team"]),
            game=str(rec["game"]),
            status=str(rec["status"]),
            source=str(rec["source"]),
            retrieved_at_utc=str(rec["retrieved_at_utc"]),
            season=rec.get("season"),
            week=rec.get("week"),
            player_id=rec.get("player_id"),
        )
        ts = pd.to_datetime(obj.retrieved_at_utc, utc=True, errors="coerce")
        if pd.notna(ts) and ts <= cutoff:
            ready.append(obj)
    return ready
