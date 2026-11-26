"""ESPN scoreboard game-state poller and fail-closed tip-off gate.

Fetches public ESPN site-API scoreboard payloads (one date per request) using stdlib
urllib, indexes games by repo game_key (`{sport}:{YYYY-MM-DD}:{AWAY}@{HOME}` in ET
with UTC-date and team-alias keys), and enforces a pre-game tip-off buffer before any
live RFQ quote is placed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

log = logging.getLogger("kalshi_exec.espn_tipoff")

SITE_API_BASE = "https://site.api.espn.com"
ESPN_HEADERS = {
    "User-Agent": "Beezy Research (beezy.fyi; research@beezy.fyi)",
    "Accept": "application/json",
}
LEAGUE_SPORT_PATH: dict[str, tuple[str, str]] = {
    "nfl": ("football", "nfl"),
    "nba": ("basketball", "nba"),
    "nhl": ("hockey", "nhl"),
    "mlb": ("baseball", "mlb"),
}
TEAM_ALIASES: dict[str, str] = {"WAS": "WSH"}

_ET_TZ = ZoneInfo("America/New_York")


def _team_variants(code: str) -> tuple[str, ...]:
    """Return canonical team code plus any bidirectional alias forms."""
    variants = [code]
    if code in TEAM_ALIASES:
        variants.append(TEAM_ALIASES[code])
    for src, dst in TEAM_ALIASES.items():
        if dst == code and src not in variants:
            variants.append(src)
    return tuple(variants)


@dataclass(frozen=True)
class GameState:
    game_key: str
    event_id: str
    start_ts: float
    state: str
    detail: str
    home: str
    away: str


def parse_scoreboard(payload: dict, sport: str) -> dict[str, GameState]:
    """Parse an ESPN scoreboard JSON payload into a dict keyed by game_key (plus aliases)."""
    out: dict[str, GameState] = {}
    sport_l = str(sport).lower()
    for event in (payload or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        raw_date = event.get("date")
        if not raw_date:
            continue
        try:
            dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        start_ts = dt.timestamp()

        comps = event.get("competitions") or []
        if not comps or not isinstance(comps[0], dict):
            continue
        home = ""
        away = ""
        for comp in comps[0].get("competitors") or []:
            if not isinstance(comp, dict):
                continue
            ha = str(comp.get("homeAway") or "").lower()
            team = comp.get("team") or {}
            abbr = str(team.get("abbreviation") or "").strip().upper()
            if ha == "home":
                home = abbr
            elif ha == "away":
                away = abbr
        if not (home and away):
            continue

        status_type = (event.get("status") or {}).get("type") or {}
        raw_state = status_type.get("state")
        # Fail safe: preserve raw lowercased state (unknown != "pre" blocks in TipoffGate).
        state = str(raw_state).strip().lower() if raw_state is not None else ""
        detail = str(status_type.get("name") or "")
        event_id = str(event.get("id") or "")

        et_date = dt.astimezone(_ET_TZ).date().isoformat()
        utc_date = dt.astimezone(timezone.utc).date().isoformat()
        primary_key = f"{sport_l}:{et_date}:{away}@{home}"
        gs = GameState(
            game_key=primary_key,
            event_id=event_id,
            start_ts=start_ts,
            state=state,
            detail=detail,
            home=home,
            away=away,
        )

        dates = (et_date,) if utc_date == et_date else (et_date, utc_date)
        away_vars = _team_variants(away)
        home_vars = _team_variants(home)
        for d in dates:
            for a in away_vars:
                for h in home_vars:
                    out[f"{sport_l}:{d}:{a}@{h}"] = gs
    return out


@dataclass
class GameStateSnapshot:
    games: dict[str, GameState]
    fetched_at: float
    source: str = "espn"

    def age_sec(self, now: float) -> float:
        return now - self.fetched_at


def _default_fetcher(url: str) -> dict:
    req = urllib.request.Request(url, headers=ESPN_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_scoreboard(
    fetcher: Callable[[str], dict] | None,
    base_url: str,
    sport: str,
    date_str: str,
) -> dict:
    """Fetch one ESPN scoreboard page for a single YYYYMMDD date."""
    league_key = str(sport).lower()
    if league_key not in LEAGUE_SPORT_PATH:
        raise ValueError(f"unsupported ESPN sport/league {sport!r}")
    espn_sport, espn_league = LEAGUE_SPORT_PATH[league_key]
    base = (base_url or SITE_API_BASE).rstrip("/")
    clean_date = str(date_str).replace("-", "")
    url = f"{base}/apis/site/v2/sports/{espn_sport}/{espn_league}/scoreboard?dates={clean_date}"
    fn = _default_fetcher if fetcher is None else fetcher
    return fn(url)


class EspnGameStatePoller:
    """Background daemon poller maintaining the latest ESPN GameStateSnapshot."""

    def __init__(
        self,
        fetcher: Callable[[str], dict] | None = None,
        base_url: str = SITE_API_BASE,
        leagues: Iterable[str] = ("nfl",),
        poll_interval_sec: float = 60.0,
        clock: Callable[[], float] = time.time,
    ):
        self._fetcher = fetcher
        self._base_url = base_url
        self._leagues = tuple(leagues)
        self._poll_interval_sec = float(poll_interval_sec)
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: GameStateSnapshot | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self, now: float | None = None) -> GameStateSnapshot:
        """Fetch ET today and ET tomorrow scoreboards for all configured leagues."""
        now = self._clock() if now is None else float(now)
        try:
            et_today = datetime.fromtimestamp(now, _ET_TZ).date().strftime("%Y%m%d")
            et_tomorrow = datetime.fromtimestamp(now + 86400.0, _ET_TZ).date().strftime("%Y%m%d")
            merged: dict[str, GameState] = {}
            for league in self._leagues:
                for date_str in (et_today, et_tomorrow):
                    payload = fetch_scoreboard(self._fetcher, self._base_url, league, date_str)
                    merged.update(parse_scoreboard(payload, league))
            snap = GameStateSnapshot(games=merged, fetched_at=now, source="espn")
            with self._lock:
                self._snapshot = snap
            return snap
        except Exception as exc:  # noqa: BLE001 -- fail closed via stale/empty previous snapshot
            log.warning("ESPN scoreboard poll failed: %r", exc)
            with self._lock:
                if self._snapshot is not None:
                    return self._snapshot
                return GameStateSnapshot(games={}, fetched_at=0.0, source="espn")

    def snapshot(self) -> GameStateSnapshot | None:
        with self._lock:
            return self._snapshot

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            if self._stop_event.wait(self._poll_interval_sec):
                break

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="espn-tipoff-poller", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            t = self._thread
            self._thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)


class TipoffGate:
    """Fail-closed pre-game tip-off gate over GameStateSnapshot."""

    def __init__(self, buffer_sec: float = 600.0, max_snapshot_age_sec: float = 180.0):
        self.buffer_sec = float(buffer_sec)
        self.max_snapshot_age_sec = float(max_snapshot_age_sec)

    def check(
        self,
        game_keys: Iterable[str],
        now: float,
        snapshot: GameStateSnapshot | None,
    ) -> tuple[bool, str]:
        if snapshot is None or snapshot.age_sec(now) > self.max_snapshot_age_sec:
            return False, "GATE_SNAPSHOT_STALE"
        keys = tuple(game_keys)
        if not keys:
            return False, "GATE_UNKNOWN_GAME"
        # HARD BLOCKER (Luc, Nov 26, 2026): any leg whose game is IN PLAY blocks the
        # whole combo under its own reason, checked before the unknown-game scan so
        # the class stays countable even alongside an unresolvable leg. Pregame
        # probabilities cannot price live games; this is structural, not a taper input.
        for key in keys:
            gs = snapshot.games.get(key)
            if gs is not None and gs.state == "in":
                return False, "BLOCKED_LIVE_GAME"
        for key in keys:
            if key not in snapshot.games:
                return False, "GATE_UNKNOWN_GAME"
        for key in keys:
            gs = snapshot.games[key]
            if gs.state != "pre":
                return False, "GATE_STARTED"
        for key in keys:
            gs = snapshot.games[key]
            if gs.start_ts - now <= self.buffer_sec:
                return False, "GATE_INSIDE_BUFFER"
        return True, ""
