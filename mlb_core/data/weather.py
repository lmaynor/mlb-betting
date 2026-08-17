"""
Shared weather pull (Open-Meteo) for all systems.
"""
import os
import time
import random
import requests
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from mlb_core.odds.dk_scraper import TEAM_NAME_TO_ABBREV

_session = requests.Session()

WEATHER_VARS = "temperature_2m,windspeed_10m,winddirection_10m,precipitation,weathercode"

# (lat, lon, roof_type, utc_offset)
# roof_type: "open" | "dome" | "retractable"
STADIUMS: dict = {
    "ARI": (33.4453, -112.0667, "retractable", -7),
    "ATL": (33.8908,  -84.4678, "open",        -4),
    "BAL": (39.2838,  -76.6218, "open",        -4),
    "BOS": (42.3467,  -71.0972, "open",        -4),
    "CHC": (41.9484,  -87.6553, "open",        -5),
    "CWS": (41.8299,  -87.6338, "open",        -5),
    "CIN": (39.0979,  -84.5082, "open",        -4),
    "CLE": (41.4962,  -81.6852, "open",        -4),
    "COL": (39.7559, -104.9942, "open",        -6),
    "DET": (42.3390,  -83.0485, "open",        -4),
    "HOU": (29.7573,  -95.3555, "retractable", -5),
    "KC":  (39.0517,  -94.4803, "open",        -5),
    "LAA": (33.8003, -117.8827, "open",        -7),
    "LAD": (34.0739, -118.2400, "open",        -7),
    "MIA": (25.7781,  -80.2197, "retractable", -4),
    "MIL": (43.0280,  -87.9712, "retractable", -5),
    "MIN": (44.9817,  -93.2778, "open",        -5),
    "NYM": (40.7571,  -73.8458, "open",        -4),
    "NYY": (40.8296,  -73.9262, "open",        -4),
    "OAK": (37.7516, -122.2005, "open",        -7),
    "PHI": (39.9061,  -75.1665, "open",        -4),
    "PIT": (40.4469,  -80.0057, "open",        -4),
    "SD":  (32.7076, -117.1570, "open",        -7),
    "SF":  (37.7786, -122.3893, "open",        -7),
    "SEA": (47.5914, -122.3325, "retractable", -7),
    "STL": (38.6226,  -90.1928, "open",        -5),
    "TB":  (27.7683,  -82.6534, "dome",        -4),
    "TEX": (32.7512,  -97.0832, "retractable", -5),
    "TOR": (43.6414,  -79.3894, "retractable", -4),
    "WSH": (38.8730,  -77.0074, "open",        -4),
}

# Modern team-code aliases for 2026+ Statcast data.
# 2025+ Statcast uses "AZ" instead of "ARI" and "ATH" instead of "OAK".
# Backfill the modern keys so lookups in either era resolve to the same stadium.
STADIUMS["AZ"]  = STADIUMS["ARI"]
STADIUMS["ATH"] = STADIUMS["OAK"]

WIND_OUT_PARKS = {"CHC", "COL", "TEX", "LAD"}
WIND_IN_PARKS  = {"CHC", "COL", "SF", "BOS"}

from mlb_core.data.lineups import _get_games_for_date  # noqa: F401


def _fetch_weather(lat, lon, date_str, hour_utc, is_forecast=False) -> dict | None:
    base = ("https://api.open-meteo.com/v1/forecast" if is_forecast
            else "https://archive-api.open-meteo.com/v1/archive")
    params = {"latitude": lat, "longitude": lon, "hourly": WEATHER_VARS, "timezone": "UTC"}
    if is_forecast:
        params["forecast_days"] = 2
    else:
        params["start_date"] = date_str
        params["end_date"] = date_str

    for attempt in range(4):
        try:
            r = _session.get(base, params=params, timeout=30)
            r.raise_for_status()
            hourly = r.json().get("hourly", {})
            times = hourly.get("time", [])
            target = f"{date_str}T{hour_utc:02d}:00"
            if target in times:
                idx = times.index(target)
            elif times:
                idx = min(range(len(times)), key=lambda i: abs(int(times[i][11:13]) - hour_utc))
            else:
                idx = 0
            dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
            deg = hourly["winddirection_10m"][idx]
            return {
                "temperature_f":    round(hourly["temperature_2m"][idx] * 9 / 5 + 32, 1),
                "wind_speed_mph":   round(hourly["windspeed_10m"][idx] * 0.621371, 1),
                "wind_dir_degrees": deg,
                "wind_direction":   dirs[round(deg / 45) % 8] if deg is not None else None,
                "precipitation_in": round(hourly["precipitation"][idx] * 0.0393701, 3),
                "weather_code":     hourly.get("weathercode", [None])[idx],
            }
        except Exception:
            time.sleep((2 ** attempt) + random.uniform(0.5, 1.0))
    return None


def _pull_weather_date(date_str: str, is_forecast: bool = False) -> pd.DataFrame:
    games = _get_games_for_date(date_str)
    if not games:
        return pd.DataFrame()
    rows = []
    for game in games:
        abbrev = TEAM_NAME_TO_ABBREV.get(game["home_team_name"])
        if not abbrev or abbrev not in STADIUMS:
            continue
        lat, lon, roof, tz = STADIUMS[abbrev]
        try:
            game_dt = datetime.strptime(game["game_time_utc"], "%Y-%m-%dT%H:%M:%SZ")
            hour_utc = game_dt.hour
        except Exception:
            hour_utc = 23
        row = {
            "game_pk":       game["game_pk"],
            "game_date":     date_str,
            "home_team":     abbrev,
            "away_team":     TEAM_NAME_TO_ABBREV.get(game["away_team_name"], ""),
            "game_time_utc": game.get("game_time_utc", ""),
            "roof":          roof,
            # Matches fetch_live_weather_for_slate's convention exactly (a
            # retractable roof can be open) -- fixed 2026-08-17, this was
            # int(roof=="open") here vs "1 if roof in (open,retractable)"
            # live, a train/serve skew for every retractable-roof stadium
            # (~23% of parks). See docs/audits/
            # 2026-08-16_cloud_efficiency_and_profitability_review.md
            # finding A13.
            "is_outdoor":    int(roof in ("open", "retractable")),
        }
        if roof == "dome":
            row.update({
                "temperature_f": None, "wind_speed_mph": None,
                "wind_dir_degrees": None, "wind_direction": None,
                "precipitation_in": None, "weather_code": None,
                "wind_out": 0, "wind_in": 0, "is_cold": 0, "is_hot": 0, "high_wind": 0,
            })
        else:
            wx = _fetch_weather(lat, lon, date_str, hour_utc, is_forecast)
            if wx:
                spd = wx["wind_speed_mph"]
                row.update({
                    **wx,
                    "wind_out": int(abbrev in WIND_OUT_PARKS and spd > 10),
                    "wind_in":  int(abbrev in WIND_IN_PARKS and spd > 10),
                    "is_cold":  int(wx["temperature_f"] < 50),
                    "is_hot":   int(wx["temperature_f"] > 85),
                    "high_wind": int(spd > 15),
                })
            else:
                row.update({k: None for k in [
                    "temperature_f", "wind_speed_mph", "wind_dir_degrees",
                    "wind_direction", "precipitation_in", "weather_code",
                    "wind_out", "wind_in", "is_cold", "is_hot", "high_wind",
                ]})
        rows.append(row)
    return pd.DataFrame(rows)


def weather_historical(cache_dir: Path, master_path: Path,
                       season_start: int = 2019,
                       season_start_month: int = 3,
                       season_end_month: int = 11):
    from mlb_core.data.statcast import _build_date_list
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        end_date=date.today() - timedelta(days=1),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    cached = {f.replace(".csv", "") for f in os.listdir(cache_dir) if f.endswith(".csv")}
    to_pull = [d for d in all_dates if d.strftime("%Y-%m-%d") not in cached]
    print(f"Weather historical: {len(to_pull)} days to fetch")
    consecutive_failures = 0
    it = tqdm(to_pull, desc="Weather", unit="day") if HAS_TQDM else to_pull
    for i, d in enumerate(it):
        date_str = d.strftime("%Y-%m-%d")
        cache_file = cache_dir / f"{date_str}.csv"
        day_df = _pull_weather_date(date_str, is_forecast=False)
        if day_df.empty:
            pd.DataFrame().to_csv(cache_file, index=False)
            consecutive_failures += 1
            if consecutive_failures >= 10:
                print("10 consecutive failures - stopping")
                break
            continue
        consecutive_failures = 0
        day_df.to_csv(cache_file, index=False)
        if i % 50 == 0:
            print(f"  {date_str}: {len(day_df)} games")
        time.sleep(random.uniform(0.5, 1.0))
    weather_rebuild_master(cache_dir, master_path,
                           season_start=season_start,
                           season_start_month=season_start_month,
                           season_end_month=season_end_month)


def weather_nightly(cache_dir: Path, master_path: Path, **kwargs):
    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Weather nightly: {yesterday}")
    day_df = _pull_weather_date(yesterday, is_forecast=False)
    if day_df.empty:
        print("  No weather data for yesterday")
        return
    (Path(cache_dir) / f"{yesterday}.csv").write_text(day_df.to_csv(index=False))
    weather_rebuild_master(cache_dir, master_path, **kwargs)


def weather_live(cache_dir: Path = None, master_path: Path = None) -> pd.DataFrame:
    """Pull today forecast at game time. Not cached."""
    today_str = date.today().strftime("%Y-%m-%d")
    print(f"Live weather: {today_str}")
    day_df = _pull_weather_date(today_str, is_forecast=True)
    if not day_df.empty:
        outdoor = day_df[day_df["is_outdoor"] == 1]
        if not outdoor.empty:
            cols = ["home_team", "away_team", "temperature_f", "wind_speed_mph", "wind_direction"]
            print(outdoor[[c for c in cols if c in outdoor.columns]].to_string(index=False))
    return day_df


def weather_rebuild_master(cache_dir: Path, master_path: Path,
                            season_start: int = 2019,
                            season_start_month: int = 3,
                            season_end_month: int = 11):
    from mlb_core.data.statcast import _build_date_list
    all_dates = _build_date_list(
        datetime(season_start, 3, 20).date(),
        season_start_month=season_start_month,
        season_end_month=season_end_month,
    )
    frames = []
    for d in all_dates:
        f = Path(cache_dir) / f"{d.strftime('%Y-%m-%d')}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f)
            if not df.empty:
                frames.append(df)
        except Exception:
            pass
    if not frames:
        print("  No weather cache files found")
        return
    master = pd.concat(frames, ignore_index=True).drop_duplicates("game_pk")
    master.to_csv(master_path, index=False)
    print(f"  Weather: {len(master):,} games saved")


def weather_nightly_gcs(gcs_bucket: str, gcs_master_key: str, **kwargs):
    """Fetch yesterday's weather, append to GCS master. No local cache needed.

    Mirrors statcast_nightly_gcs(): pulls one day from Open-Meteo archive via
    the existing _pull_weather_date() helper, then concat+dedupe against the
    GCS master CSV and write back via mlb_core.storage (twin-aware, with a
    local-disk fallback when no GCS bucket is configured).
    """
    from mlb_core import storage as _st  # twin-aware master IO

    yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"Weather nightly (GCS): {yesterday}")

    new_df = _pull_weather_date(yesterday, is_forecast=False)
    if new_df.empty:
        print("  No weather data for yesterday")
        return
    print(f"  Fetched {len(new_df):,} games")

    if _st.exists(gcs_master_key):
        existing = _st.read_csv(gcs_master_key, low_memory=False)
        print(f"  Existing master: {len(existing):,} rows")
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        print("  No existing master - creating new")
        combined = new_df

    combined = combined.drop_duplicates(subset=["game_pk"], keep="last")

    _st.write_csv(combined, gcs_master_key)
    print(f"  Master updated: {len(combined):,} rows | through {yesterday}")

def fetch_live_weather_for_slate(sched: "pd.DataFrame") -> dict:
    """Fetch live Open-Meteo forecasts for today's slate, keyed by game_pk.

    Shared utility used by run_hr.py and run_f5.py so retry logic and
    roof handling live in one place. Uses _fetch_weather which already has
    4-attempt exponential backoff with jitter.

    Args:
        sched: DataFrame with columns game_pk, home_team, game_time_utc.

    Returns:
        Dict[game_pk -> weather_dict] with keys:
            temperature_f, wind_speed_mph, wind_dir_degrees, is_outdoor, roof.
        Missing games are silently omitted (XGBoost handles NaN features).
    """
    import pandas as _pd
    if sched.empty:
        return {}

    from datetime import date as _date

    today_str = _date.today().isoformat()
    out: dict = {}

    for _, g in sched.iterrows():
        home = g.get("home_team", "")
        stadium = STADIUMS.get(home)
        if stadium is None:
            continue
        lat, lon, roof, _tz = stadium

        if roof == "dome":
            out[g["game_pk"]] = {
                "temperature_f":    72.0,
                "wind_speed_mph":   0.0,
                "wind_dir_degrees": 0.0,
                "is_outdoor":       0,
                "roof":             roof,
            }
            continue

        # Parse game start hour in UTC for _fetch_weather's hour picker
        try:
            game_utc = _pd.to_datetime(g.get("game_time_utc", ""))
            hour_utc = game_utc.hour if not _pd.isna(game_utc) else 22
        except Exception:
            hour_utc = 22

        wx = _fetch_weather(lat, lon, today_str, hour_utc, is_forecast=True)
        if wx is None:
            continue

        spd = wx["wind_speed_mph"]
        out[g["game_pk"]] = {
            "temperature_f":    wx["temperature_f"],
            "wind_speed_mph":   spd,
            "wind_dir_degrees": wx["wind_dir_degrees"],
            # retractable roofs: default open; a full fix requires a live
            # roof-status query from the MLB schedule API (TODO).
            "is_outdoor":  1 if roof in ("open", "retractable") else 0,
            "is_cold":     int(wx["temperature_f"] < 50),
            "is_hot":      int(wx["temperature_f"] > 85),
            "high_wind":   int(spd > 15),
            "wind_out":    int(home in WIND_OUT_PARKS and spd > 10),
            "wind_in":     int(home in WIND_IN_PARKS  and spd > 10),
            "roof":        roof,
        }

    return out
