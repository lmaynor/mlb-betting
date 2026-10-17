"""
ev.rosters -- the CURRENT active roster per sport: {player_norm: team code}, for the NFL / NHL / NBA picks boards.

Why: ev.daily_projections identified a player's team from his LAST game-log row. After a trade, a retirement or a release
that is wrong: Anze Kopitar (retired) stayed in the NHL rotation rows because his last row is a Kings game, and every
offseason NBA mover whose team changed was dropped from the lines as `ambiguous_team` (the book quotes him on his NEW
team's game, the log still says the old one). A roster layer fixes both without touching the projection engine.

Sources (all public, no credentials):
  nhl  the official NHL web API  /roster/{team}/current  (nhl.data.client.NhlClient, 429-aware; failed teams get one retry)
  nba  the daily `nba-espn-players` job's NBA/players_xwalk.csv when it is FRESH (rows re-stamped by the latest run are
       the teams' current players; the master also keeps retired/released rows, so stale rows are ignored), else ESPN's
       roster API live (nba.data.espn_players.fetch_all)
  nfl  the nflverse rosters mirror NFL/nflverse/rosters/rosters_{season}.parquet: the newest-week row per gsis_id with
       status ACT or INA (CUT / RES injured-reserve / DEV practice squad / RET / EXE / TRD / TRT are not on the board)

Cached at Rosters/{sport}.json {fetched_at, source, partial, teams, players, ambiguous}. refresh() is cheap to call every
run: it re-fetches only when the cache is older than TTL_HOURS (30 min when the last pull was partial), and a FAILED
refresh falls back to the last good copy for MAX_STALE_DAYS (flagged `stale`), then to None -- callers treat None as
"no roster layer" and keep the old last-game-log behaviour, with a logged warning. Nothing here ever raises.

Names are folded with mlb_core.data.cockpit_form.norm_name, the SAME function that keys the game-log index, so a roster
key is a game-log key. A name carried by two different teams (e.g. two active "Marcus Harris") goes to `ambiguous`, never
to a guessed team.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from mlb_core.data.cockpit_form import norm_name

log = logging.getLogger("ev.rosters")

SPORTS = ("nfl", "nhl", "nba")
CACHE_KEY = "Rosters/{sport}.json"
TTL_HOURS = 6.0
PARTIAL_TTL_HOURS = 0.5
MAX_STALE_DAYS = 7.0
MIN_TEAMS = {"nfl": 28, "nhl": 28, "nba": 28}          # fewer covered teams than this = a failed pull, not a roster
NFL_ACTIVE_STATUS = ("ACT", "INA")                     # INA = inactive for the game, still on the 53
XWALK_FRESH_HOURS = 36.0
XWALK_STAMP_SLACK = timedelta(hours=1)                 # rows stamped within this of the run's newest stamp = that run


@dataclass
class Roster:
    sport: str
    fetched_at: str
    source: str
    players: dict = field(default_factory=dict)          # player_norm -> canonical team code
    ambiguous: dict = field(default_factory=dict)        # player_norm -> [team codes]
    teams: list = field(default_factory=list)            # canonical team codes with at least one player
    partial: bool = False                                # some team(s) failed to load: absence proves nothing
    failed: list = field(default_factory=list)
    stale: bool = False                                  # served from an old cache after a failed refresh

    def lookup(self, player_norm: str, log_team: str | None = None) -> tuple:
        """-> ('on', team) | ('off', None) | ('unknown', None).

        on       the roster names his team (an ambiguous name resolves only when his game-log team is one of the
                 candidates)
        off      a COMPLETE roster does not carry him at all: retired / released / not in the league
        unknown  cannot tell (partial pull, or an ambiguous name that the game log does not disambiguate): callers keep
                 the last-game-log behaviour"""
        team = self.players.get(player_norm)
        if team is not None:
            return "on", team
        amb = self.ambiguous.get(player_norm)
        if amb:
            return ("on", log_team) if log_team in amb else ("unknown", None)
        return ("unknown", None) if self.partial else ("off", None)

    def to_json(self) -> dict:
        return {"sport": self.sport, "fetched_at": self.fetched_at, "source": self.source, "partial": self.partial,
                "failed": self.failed, "teams": self.teams, "players": self.players, "ambiguous": self.ambiguous}

    @classmethod
    def from_json(cls, obj: dict) -> "Roster":
        return cls(sport=obj["sport"], fetched_at=obj["fetched_at"], source=obj.get("source", "cache"),
                   players=dict(obj.get("players") or {}), ambiguous=dict(obj.get("ambiguous") or {}),
                   teams=list(obj.get("teams") or []), partial=bool(obj.get("partial")),
                   failed=list(obj.get("failed") or []))

    def age_hours(self, now: datetime) -> float | None:
        try:
            return (now - datetime.fromisoformat(self.fetched_at)).total_seconds() / 3600.0
        except (TypeError, ValueError):
            return None

    def summary(self) -> dict:
        return {"source": self.source, "players": len(self.players), "ambiguous": len(self.ambiguous),
                "teams": len(self.teams), "partial": self.partial, "stale": self.stale, "fetched_at": self.fetched_at}


# ----------------------------------------------------------------------------------------------------- construction
def build_roster(sport: str, rows: list, failed: list | None, source: str, now: datetime) -> Roster:
    """[{name, team}] (team in any spelling the sport's canon_team accepts) -> Roster. Rows with an unknown team or an
    empty name are skipped."""
    from ev.daily_projections import canon_team
    seen: dict = {}
    for r in rows:
        pn = norm_name(r.get("name"))
        team = canon_team(sport, r.get("team"))
        if not pn or not team:
            continue
        seen.setdefault(pn, set()).add(team)
    players = {pn: next(iter(ts)) for pn, ts in seen.items() if len(ts) == 1}
    ambiguous = {pn: sorted(ts) for pn, ts in seen.items() if len(ts) > 1}
    teams = sorted({t for ts in seen.values() for t in ts})
    failed = sorted(set(failed or []))
    return Roster(sport=sport, fetched_at=now.isoformat(), source=source, players=players, ambiguous=ambiguous,
                  teams=teams, partial=bool(failed) or len(teams) < MIN_TEAMS[sport], failed=failed)


# --------------------------------------------------------------------------------------------------------- fetchers
def fetch_nhl(client=None, retry_pause: float = 5.0) -> dict:
    """-> {'rows': [{name, team}], 'failed': [abbrev], 'source': 'nhl-api'}. One retry pass for failed teams (a 429
    window usually clears)."""
    import time

    from nhl.config import TEAMS
    from nhl.data.client import NhlClient
    from nhl.data.players import flatten_roster
    client = client or NhlClient()

    def pull(abbrevs):
        rows, bad = [], []
        for a in abbrevs:
            try:
                r = client.roster(a)
            except Exception as exc:                                                    # noqa: BLE001
                log.warning("NHL roster %s failed: %r", a, exc)
                bad.append(a)
                continue
            fl = flatten_roster(a, r) if r else []
            if not fl:
                bad.append(a)
                continue
            rows.extend({"name": p["full_name"], "team": a} for p in fl)
        return rows, bad

    rows, failed = pull([a for a, _, _ in TEAMS])
    if failed and retry_pause is not None:
        time.sleep(retry_pause)
        more, failed = pull(failed)
        rows.extend(more)
    return {"rows": rows, "failed": failed, "source": "nhl-api"}


def nba_rows_from_xwalk(df, now: datetime) -> list | None:
    """The NBA crosswalk master -> [{name, team}] for the rows the LATEST daily run stamped, or None when that run is
    older than XWALK_FRESH_HOURS / covers fewer than MIN_TEAMS teams. The master accumulates (a released or retired player
    keeps his last row, unstamped since), so un-restamped rows are exactly the ones that must NOT count as active."""
    import pandas as pd
    if df is None or len(df) == 0 or "updated_at" not in df.columns:
        return None
    stamp = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")
    top = stamp.max()
    if pd.isna(top) or (now - top.to_pydatetime()).total_seconds() / 3600.0 > XWALK_FRESH_HOURS:
        return None
    cur = df[stamp >= top - XWALK_STAMP_SLACK]
    if cur["team"].nunique() < MIN_TEAMS["nba"]:
        return None
    return [{"name": n, "team": t} for n, t in zip(cur["full_name"], cur["team"])]


def fetch_nba(now: datetime | None = None, session=None) -> dict:
    """-> {'rows', 'failed', 'source'}: the fresh daily ESPN crosswalk when there is one, else ESPN live."""
    from mlb_core import storage
    from nba.data import espn_players
    now = now or datetime.now(timezone.utc)
    try:
        if storage.exists(espn_players.PLAYERS_XWALK):
            rows = nba_rows_from_xwalk(storage.read_csv(espn_players.PLAYERS_XWALK), now)
            if rows:
                return {"rows": rows, "failed": [], "source": "espn-xwalk"}
    except Exception as exc:                                                            # noqa: BLE001
        log.info("NBA xwalk unusable (%s); fetching ESPN rosters live", exc)
    res = espn_players.fetch_all(session)
    return {"rows": [{"name": r["full_name"], "team": r["team"]} for r in res["rows"]], "failed": res["failed"],
            "source": "espn-live"}


def nfl_rows_from_frame(df, statuses=NFL_ACTIVE_STATUS) -> list:
    """nflverse rosters frame -> [{name, team}] of ACTIVE players: the newest-week row per player must itself be ACT/INA
    (a player cut in week 3 whose last row says CUT is not carried forward from his earlier ACT row)."""
    from nfl.data.keys import ev_code
    if df is None or len(df) == 0:
        return []
    d = df.copy()
    d["_pid"] = d["gsis_id"] if "gsis_id" in d.columns else None
    d["_pid"] = d["_pid"].where(d["_pid"].notna(), d["full_name"].astype(str) + "|" + d["team"].astype(str))
    if "week" in d.columns:
        d = d.sort_values("week", kind="stable")
    last = d.drop_duplicates("_pid", keep="last")
    act = last[last["status"].astype(str).str.upper().isin(statuses)]
    out = []
    for name, team in zip(act["full_name"], act["team"]):
        code = ev_code(team)
        if code and isinstance(name, str) and name.strip():
            out.append({"name": name, "team": code})
    return out


def fetch_nfl(now: datetime | None = None) -> dict:
    from nfl.data import read as nread
    now = now or datetime.now(timezone.utc)
    have = [s for s in nread.mirrored_seasons("rosters") if s <= now.year]
    if not have:
        raise FileNotFoundError("no nflverse rosters season mirrored")
    df = nread.load("rosters", seasons=[max(have)])
    return {"rows": nfl_rows_from_frame(df), "failed": [], "source": f"nflverse-rosters-{max(have)}"}


# ----------------------------------------------------------------------------------------------------------- cache
def read_cache(sport: str) -> Roster | None:
    from mlb_core import storage
    try:
        return Roster.from_json(json.loads(storage.read_bytes(CACHE_KEY.format(sport=sport))))
    except Exception:                                                                   # noqa: BLE001
        return None


def write_cache(roster: Roster) -> bool:
    from mlb_core import storage
    try:
        storage.write_bytes(json.dumps(roster.to_json()).encode(), CACHE_KEY.format(sport=roster.sport))
        return True
    except Exception as exc:                                                            # noqa: BLE001
        log.warning("roster cache write failed for %s: %s", roster.sport, exc)
        return False


def _fresh(r: Roster, now: datetime) -> bool:
    age = r.age_hours(now)
    return age is not None and age < (PARTIAL_TTL_HOURS if r.partial else TTL_HOURS)


def load(sport: str, now: datetime | None = None, fetch=None, refresh: bool = True, write: bool = True) -> Roster | None:
    """The sport's current roster, or None when none can be had (callers then keep the game-log team).

    fetch(now) -> {'rows', 'failed', 'source'} is injectable (tests / local runs); the default is the sport's source.
    refresh=False only reads the cache (the web request path); write=False never writes it (dry runs). Never raises."""
    now = now or datetime.now(timezone.utc)
    cached = read_cache(sport)
    if cached is not None and _fresh(cached, now):
        return cached
    if refresh:
        try:
            res = (fetch or _DEFAULT_FETCH[sport])(now)
            r = build_roster(sport, res["rows"], res.get("failed"), res.get("source", "fetch"), now)
            if len(r.teams) < MIN_TEAMS[sport]:
                raise RuntimeError(f"roster pull covers {len(r.teams)} teams (< {MIN_TEAMS[sport]}); failed={r.failed}")
            if r.partial:
                log.warning("%s roster is PARTIAL (failed teams %s): absence will not exclude players", sport, r.failed)
            if write:
                write_cache(r)
            return r
        except Exception as exc:                                                        # noqa: BLE001
            log.warning("%s roster refresh failed: %s", sport, exc)
    if cached is not None:
        age = cached.age_hours(now)
        if age is not None and age <= MAX_STALE_DAYS * 24.0:
            cached.stale = True
            log.warning("%s roster: serving the cached copy from %s (%.1f h old)", sport, cached.fetched_at, age)
            return cached
    log.warning("%s roster unavailable: falling back to the last game-log team", sport)
    return None


_DEFAULT_FETCH = {"nhl": lambda now: fetch_nhl(), "nba": lambda now: fetch_nba(now), "nfl": lambda now: fetch_nfl(now)}
