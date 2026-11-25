"""MOD-04e E0(v): pre-game NFL stadium kickoff weather banking.

Reuses ``nfl.data.weather.kickoff_weather`` and ``nfl.data.weather.is_closed`` to
persist one forecast record per scheduled game under
``NFL/mod04e/weather/season=/week=/``. Indoor / closed-roof games are banked with
``indoor=True`` and ``wind_mph=0.0``. ``wind_flag(record)`` flags sustained wind
>= 15 mph (the named NFL prop failure mode).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core import storage
from nfl.data import weather as nfl_weather

WIND_FLAG_MPH = 15.0
_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class WeatherRecord:
    game_id: str
    kickoff_utc: str
    retrieved_at_utc: str
    temp_f: Optional[float]
    wind_mph: Optional[float]
    precip: Optional[float]
    indoor: bool
    season: Optional[int] = None
    week: Optional[int] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    source: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_kickoff_utc(row: Mapping[str, Any]) -> Optional[datetime]:
    ko = row.get("kickoff_utc") or row.get("commence_time")
    if ko is not None and str(ko).strip() and str(ko).lower() != "nan":
        ts = pd.to_datetime(ko, utc=True, errors="coerce")
        if pd.notna(ts):
            return ts.to_pydatetime().astimezone(timezone.utc)
    gd = row.get("gameday") or row.get("game_date")
    if gd is None or str(gd).lower() == "nan" or not str(gd).strip():
        return None
    gt = str(row.get("gametime") or "13:00").strip()
    if not gt or gt.lower() == "nan":
        gt = "13:00"
    try:
        naive = datetime.fromisoformat(f"{str(gd)[:10]}T{gt[:5]}:00")
        return naive.replace(tzinfo=_ET).astimezone(timezone.utc)
    except ValueError:
        return None


def _finite_or_none(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def wind_flag(record: WeatherRecord | Mapping[str, Any]) -> bool:
    """True when the game is outdoors and sustained wind >= 15 mph."""
    indoor = record.indoor if isinstance(record, WeatherRecord) else bool(record.get("indoor", False))
    if indoor:
        return False
    w = record.wind_mph if isinstance(record, WeatherRecord) else record.get("wind_mph")
    wf = _finite_or_none(w)
    return wf is not None and wf >= WIND_FLAG_MPH


def _partition_key(season: int, week: int, ext: str) -> str:
    return f"NFL/mod04e/weather/season={int(season)}/week={int(week):02d}/weather.{ext}"


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


def bank_game_forecasts(
    schedule_rows: pd.DataFrame | Iterable[Mapping[str, Any]],
    http_get: Optional[Callable] = None,
    *,
    root: Optional[str | Path] = None,
    now: Optional[datetime] = None,
) -> list[WeatherRecord]:
    """Fetch and bank pre-game kickoff weather for each game in schedule_rows."""
    if isinstance(schedule_rows, pd.DataFrame):
        rows = schedule_rows.to_dict("records")
    else:
        rows = [dict(r) for r in schedule_rows]

    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    retrieved_iso = ref_now.isoformat()

    records: list[WeatherRecord] = []
    for r in rows:
        gid = str(r.get("game_id") or "")
        if not gid:
            continue
        home = r.get("home_team")
        away = r.get("away_team")
        roof = r.get("roof")
        ko = _parse_kickoff_utc(r)
        ko_iso = ko.isoformat() if ko is not None else ""

        closed = nfl_weather.is_closed(home, schedule_roof=roof)
        wx = nfl_weather.kickoff_weather(
            home,
            ko,
            schedule_wind=r.get("wind"),
            schedule_temp=r.get("temp"),
            schedule_roof=roof,
            fetch=True,
            http_get=http_get,
            now=ref_now,
        )
        indoor = bool(closed or wx.get("dome"))
        wind_val = 0.0 if indoor else _finite_or_none(wx.get("wind_mph"))
        temp_val = _finite_or_none(wx.get("temp_f"))
        precip_val = 0.0 if indoor else _finite_or_none(wx.get("precip_mm"))

        szn = int(r["season"]) if r.get("season") is not None and pd.notna(r.get("season")) else None
        wk = int(r["week"]) if r.get("week") is not None and pd.notna(r.get("week")) else None

        records.append(WeatherRecord(
            game_id=gid,
            kickoff_utc=ko_iso,
            retrieved_at_utc=retrieved_iso,
            temp_f=temp_val,
            wind_mph=wind_val,
            precip=precip_val,
            indoor=indoor,
            season=szn,
            week=wk,
            home_team=str(home) if home is not None else None,
            away_team=str(away) if away is not None else None,
            source=str(wx.get("source") or "none"),
        ))

    if records:
        df = pd.DataFrame([rec.to_dict() for rec in records])
        if df["season"].notna().all() and df["week"].notna().all():
            for (s_i, w_i), sub in df.groupby(["season", "week"], sort=True):
                _write_partition(sub.reset_index(drop=True), int(s_i), int(w_i), root)
    return records


def load_game_forecasts(season: int, week: int, root: Optional[str | Path] = None) -> list[WeatherRecord]:
    """Load banked WeatherRecord objects for (season, week)."""
    pq_rel = _partition_key(season, week, "parquet")
    if root is None or str(root).startswith("gs://"):
        df = storage.read_parquet(pq_rel)
    else:
        df = pd.read_parquet(Path(root) / pq_rel)
    out: list[WeatherRecord] = []
    for r in df.to_dict("records"):
        out.append(WeatherRecord(
            game_id=str(r["game_id"]),
            kickoff_utc=str(r["kickoff_utc"]),
            retrieved_at_utc=str(r["retrieved_at_utc"]),
            temp_f=_finite_or_none(r.get("temp_f")),
            wind_mph=_finite_or_none(r.get("wind_mph")),
            precip=_finite_or_none(r.get("precip")),
            indoor=bool(r["indoor"]),
            season=int(r["season"]) if pd.notna(r.get("season")) else None,
            week=int(r["week"]) if pd.notna(r.get("week")) else None,
            home_team=str(r["home_team"]) if r.get("home_team") is not None else None,
            away_team=str(r["away_team"]) if r.get("away_team") is not None else None,
            source=str(r.get("source") or "none"),
        ))
    return out
