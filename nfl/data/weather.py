"""NFL stadium kickoff weather: a static stadium table plus an optional, cached Open-Meteo forecast.

nflverse `schedules` carries roof / wind / temp, but wind and temp are usually NaN until the game has been played, so an
upcoming outdoor game has no weather at all. This module fills that gap:

  * closed-roof stadiums return neutral conditions immediately (no network);
  * an outdoor game keeps the schedule's own wind / temp when they exist;
  * otherwise an outdoor game within the forecast horizon gets the Open-Meteo hourly forecast for its kickoff hour
    (https://api.open-meteo.com/v1/forecast: free, no key). 2 s timeout, cached in-process, NEVER raises -- on any
    failure the caller gets `source="none"` with None values (unknown, not zero).

Roof classes. "dome" = fixed roof (DET, LV, MIN, NO); "retractable" = ATL, DAL, ARI, HOU, IND (usually closed, not
always); "canopy" = SoFi (LAR, LAC: roofed but open-sided, treated as neutral for wind). All of them are returned neutral
by default (the spec's rule). A schedule `roof` value of "open" / "outdoors" for a retractable-roof game overrides that and
sends the game down the outdoor path. NYG / NYJ share MetLife (one entry each).

ASCII only; pure stdlib + requests (lazy, only when a forecast is actually fetched).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

log = logging.getLogger("nfl.data.weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 2.0
FORECAST_DAYS = 16                       # Open-Meteo's free horizon
CACHE_TTL_S = 3 * 3600

NEUTRAL = {"wind_mph": 0.0, "temp_f": 72.0, "precip_mm": 0.0, "precip_prob": 0.0, "gust_mph": 0.0}

# team -> (lat, lon, roof_type); nflverse team codes (JAC is canonicalised to JAX below)
STADIUM_COORDS = {
    "ARI": (33.5276, -112.2626, "retractable"), "ATL": (33.7554, -84.4010, "retractable"),
    "BAL": (39.2780, -76.6227, "open"), "BUF": (42.7738, -78.7870, "open"),
    "CAR": (35.2258, -80.8528, "open"), "CHI": (41.8623, -87.6167, "open"),
    "CIN": (39.0955, -84.5161, "open"), "CLE": (41.5061, -81.6995, "open"),
    "DAL": (32.7473, -97.0945, "retractable"), "DEN": (39.7439, -105.0201, "open"),
    "DET": (42.3400, -83.0456, "dome"), "GB": (44.5013, -88.0622, "open"),
    "HOU": (29.6847, -95.4107, "retractable"), "IND": (39.7601, -86.1639, "retractable"),
    "JAX": (30.3239, -81.6373, "open"), "KC": (39.0489, -94.4839, "open"),
    "LV": (36.0909, -115.1833, "dome"), "LAC": (33.9535, -118.3392, "canopy"),
    "LAR": (33.9535, -118.3392, "canopy"), "MIA": (25.9580, -80.2389, "open"),
    "MIN": (44.9735, -93.2575, "dome"), "NE": (42.0909, -71.2643, "open"),
    "NO": (29.9511, -90.0812, "dome"), "NYG": (40.8135, -74.0745, "open"),
    "NYJ": (40.8135, -74.0745, "open"), "PHI": (39.9008, -75.1675, "open"),
    "PIT": (40.4468, -80.0158, "open"), "SF": (37.4030, -121.9700, "open"),
    "SEA": (47.5952, -122.3316, "open"), "TB": (27.9759, -82.5033, "open"),
    "TEN": (36.1665, -86.7713, "open"), "WAS": (38.9076, -76.8645, "open"),
}
_ALIAS = {"JAC": "JAX", "LA": "LAR", "STL": "LAR", "OAK": "LV", "SD": "LAC", "WSH": "WAS"}
_OPEN_ROOF_WORDS = ("open", "outdoors", "outdoor")
_CLOSED_ROOF_WORDS = ("dome", "closed")


def canon_team(team) -> Optional[str]:
    if team is None or (isinstance(team, float) and math.isnan(team)):
        return None
    t = str(team).strip().upper()
    return _ALIAS.get(t, t) or None


def roof_type(team) -> Optional[str]:
    row = STADIUM_COORDS.get(canon_team(team))
    return row[2] if row else None


def is_closed(team, schedule_roof: Optional[str] = None) -> bool:
    """True when the game is played under a roof (neutral weather). A schedule roof value wins over the table."""
    sr = str(schedule_roof).strip().lower() if schedule_roof is not None and str(schedule_roof).lower() != "nan" else ""
    if sr in _OPEN_ROOF_WORDS:
        return False
    if sr in _CLOSED_ROOF_WORDS:
        return True
    rt = roof_type(team)
    return rt is not None and rt != "open"


def _finite(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


_CACHE: dict = {}                       # (lat, lon) -> (fetched_at, hourly dict)


def _default_get(url: str, params: dict, timeout: float):
    import requests
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_forecast(lat: float, lon: float, http_get: Optional[Callable] = None, now: Optional[float] = None) -> Optional[dict]:
    """Open-Meteo hourly forecast {iso_hour_utc: {...}} for a point, cached; None on any failure (never raises)."""
    key = (round(lat, 3), round(lon, 3))
    t = time.time() if now is None else now
    hit = _CACHE.get(key)
    if hit is not None and t - hit[0] < CACHE_TTL_S:
        return hit[1]
    params = {"latitude": lat, "longitude": lon, "timezone": "UTC", "forecast_days": FORECAST_DAYS,
              "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,precipitation_probability",
              "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "mm"}
    try:
        data = (http_get or _default_get)(OPEN_METEO_URL, params, TIMEOUT_S)
        h = data["hourly"]
        times = h["time"]
        out = {}
        for i, ts in enumerate(times):
            out[str(ts)[:13]] = {                                     # "YYYY-MM-DDTHH"
                "temp_f": _finite(h["temperature_2m"][i]), "wind_mph": _finite(h["wind_speed_10m"][i]),
                "gust_mph": _finite(h["wind_gusts_10m"][i]), "precip_mm": _finite(h["precipitation"][i]),
                "precip_prob": _finite(h["precipitation_probability"][i]),
            }
    except Exception as exc:                                            # noqa: BLE001 -- never raise into the board
        log.info("open-meteo forecast unavailable (%s: %s)", type(exc).__name__, exc)
        return None
    _CACHE[key] = (t, out)
    return out


def kickoff_weather(team, kickoff_utc: Optional[datetime], schedule_wind=None, schedule_temp=None,
                    schedule_roof: Optional[str] = None, fetch: bool = True, http_get: Optional[Callable] = None,
                    now: Optional[datetime] = None) -> dict:
    """Weather for one game at `team`'s stadium (the HOME team).

    -> {wind_mph, temp_f, precip_mm, precip_prob, gust_mph, dome, roof, source}. `source`: "dome" (neutral), "schedule"
    (schedule values kept), "open-meteo" (forecast for the kickoff hour) or "none" (outdoors, unknown: values are None)."""
    tm = canon_team(team)
    out = {"wind_mph": None, "temp_f": None, "precip_mm": None, "precip_prob": None, "gust_mph": None,
           "dome": False, "roof": roof_type(tm), "source": "none"}
    if tm is None or tm not in STADIUM_COORDS:
        return out
    if is_closed(tm, schedule_roof):
        out.update(NEUTRAL)
        out.update({"dome": True, "source": "dome"})
        return out
    w, tp = _finite(schedule_wind), _finite(schedule_temp)
    if w is not None:
        out.update({"wind_mph": w, "temp_f": tp, "source": "schedule"})
        return out
    if not fetch or kickoff_utc is None:
        if tp is not None:
            out["temp_f"] = tp
        return out
    ref = now or datetime.now(timezone.utc)
    ko = kickoff_utc if kickoff_utc.tzinfo else kickoff_utc.replace(tzinfo=timezone.utc)
    if not (timedelta(hours=-3) <= ko - ref <= timedelta(days=FORECAST_DAYS - 1)):
        return out                                                      # past or beyond the forecast horizon
    lat, lon, _ = STADIUM_COORDS[tm]
    fc = fetch_forecast(lat, lon, http_get=http_get)
    if not fc:
        return out
    hour = ko.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    row = fc.get(hour)
    if row is None or row["wind_mph"] is None:
        return out
    out.update({k: row[k] for k in ("wind_mph", "temp_f", "precip_mm", "precip_prob", "gust_mph")})
    out["source"] = "open-meteo"
    return out
