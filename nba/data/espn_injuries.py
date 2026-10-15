"""NBA injury report from ESPN's public site API (no auth, no persistence).

    GET https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries

Payload shape (observed 2026-10-14, HTTP 200, ~1.5 MB, 29 teams / 134 entries, preseason):
    {"timestamp": "...Z", "status": "success", "season": {...},
     "injuries": [ {"id": "<ESPN TEAM id>", "displayName": "Atlanta Hawks",
                    "injuries": [ {"id": "<injury id, NOT the player id>", "status": "Out" | "Day-To-Day",
                                   "date": "2026-09-21T19:50Z", "shortComment": "...", "longComment": "...",
                                   "type": {"description": "out", "abbreviation": "O"},
                                   "details": {"type": "Knee", "location": "Leg", "side": "Right",
                                               "returnDate": "2027-07-01", "fantasyStatus": {...}},
                                   "athlete": {"displayName": "...", "links": [{"href": ".../player/_/id/<ESPN id>/<slug>"}],
                                               "headshot": {"href": ".../players/full/<ESPN id>.png"},
                                               "team": {"abbreviation": "ATL", ...}, "position": {...}} } ] } ]}
The ESPN PLAYER id lives only in `athlete.links[].href` (`/id/<n>/`) and the headshot URL; it is the same id space as the
crosswalk NBA/players_xwalk.csv (nba/data/espn_players.py). Teams with no injuries are simply not listed, so "team missing
from the payload" means HEALTHY only when the fetch itself succeeded -- `absent_by_team` encodes that rule.

FAIL CLOSED: nothing here raises into a scoring run. A network error, a non-200, a non-JSON body, a payload without the
`injuries` list, or ANY entry that cannot be parsed returns what it could parse with `frame.attrs["ok"] = False` (an
empty frame for the first four). Callers must treat ok=False as "availability unknown" (NaN features / abstain), never as
"nobody is hurt".

Status classes (normalised from ESPN's free text): Out, Doubtful, Questionable, Day-To-Day, Probable, Other.
Only `Out` is a hard absence; Doubtful / Questionable / Day-To-Day are uncertain and stay opt-in via `statuses`.
"""
from __future__ import annotations

import logging
import re

import pandas as pd

from nba.teams import TEAMS, team_code

log = logging.getLogger(__name__)

ESPN_INJURIES_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-data/1.0)"}
COLUMNS = ["team_abbrev", "espn_player_id", "player", "status", "detail", "updated", "raw_status"]
STATUSES = ("Out", "Doubtful", "Questionable", "Day-To-Day", "Probable", "Other")
_ID_RE = re.compile(r"/id/(\d+)")
_HEADSHOT_RE = re.compile(r"/players/full/(\d+)\.")


def classify_status(raw) -> str:
    """ESPN free-text status -> one of STATUSES."""
    s = str(raw or "").strip().lower()
    if s.startswith("out") or s in ("o", "suspension", "suspended"):
        return "Out"
    if s.startswith("doubtful"):
        return "Doubtful"
    if s.startswith("questionable"):
        return "Questionable"
    if s.startswith("day") or "day-to-day" in s:
        return "Day-To-Day"
    if s.startswith("probable"):
        return "Probable"
    return "Other"


def _player_id(athlete: dict):
    for ln in athlete.get("links") or []:
        m = _ID_RE.search(str((ln or {}).get("href") or ""))
        if m:
            return int(m.group(1))
    m = _HEADSHOT_RE.search(str(((athlete.get("headshot") or {}).get("href")) or ""))
    return int(m.group(1)) if m else None


def _detail(entry: dict) -> str:
    d = entry.get("details") or {}
    bits = [str(d.get("type") or "").strip(), str(d.get("side") or "").strip()]
    txt = " ".join(b for b in bits if b)
    if d.get("returnDate"):
        txt = (txt + " " if txt else "") + f"(return {d['returnDate']})"
    return txt or str(entry.get("shortComment") or "")[:160]


def _empty(ok: bool, error: str | None = None, **attrs) -> pd.DataFrame:
    df = pd.DataFrame(columns=COLUMNS)
    df.attrs.update({"ok": ok, "error": error, "n_skipped": 0, **attrs})
    return df


def parse_injuries(payload) -> pd.DataFrame:
    """Pure: ESPN injuries payload -> normalised frame [team_abbrev, espn_player_id, player, status, detail, updated,
    raw_status]. Never raises. `frame.attrs`: ok (False when the payload is unusable or any entry was skipped), error,
    n_skipped, timestamp."""
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get("injuries"), list):
            log.warning("espn injuries: payload has no 'injuries' list (type %s)", type(payload).__name__)
            return _empty(False, "no_injuries_list")
        rows, skipped = [], 0
        for team in payload["injuries"]:
            tname = (team or {}).get("displayName") if isinstance(team, dict) else None
            entries = (team or {}).get("injuries") if isinstance(team, dict) else None
            if not isinstance(entries, list):
                skipped += 1
                continue
            for e in entries:
                try:
                    a = e["athlete"]
                    pid = _player_id(a)
                    code = team_code((a.get("team") or {}).get("abbreviation")) or team_code(tname)
                    name = a.get("displayName") or " ".join(
                        x for x in (a.get("firstName"), a.get("lastName")) if x)
                    if pid is None or not code or not name:
                        raise ValueError("missing id/team/name")
                    rows.append({"team_abbrev": code, "espn_player_id": pid, "player": name,
                                 "status": classify_status(e.get("status")), "detail": _detail(e),
                                 "updated": e.get("date"), "raw_status": e.get("status")})
                except Exception as exc:                                                 # noqa: BLE001
                    skipped += 1
                    log.warning("espn injuries: entry skipped (%r)", exc)
        df = pd.DataFrame(rows, columns=COLUMNS)
        df.attrs.update({"ok": skipped == 0, "error": None if skipped == 0 else f"{skipped}_entries_skipped",
                         "n_skipped": skipped, "timestamp": payload.get("timestamp")})
        return df
    except Exception as exc:                                                             # noqa: BLE001
        log.warning("espn injuries: parse failed: %r", exc)
        return _empty(False, f"parse_error:{type(exc).__name__}")


def fetch_injuries(session=None, timeout: float = 20.0) -> pd.DataFrame:
    """One GET + parse. Never raises (see module docstring: ok=False means availability UNKNOWN)."""
    try:
        import requests
        s = session or requests
        r = s.get(ESPN_INJURIES_URL, headers=_UA, timeout=timeout)
        r.raise_for_status()
        return parse_injuries(r.json())
    except Exception as exc:                                                             # noqa: BLE001
        log.warning("espn injuries fetch failed: %r", exc)
        return _empty(False, f"fetch_error:{type(exc).__name__}")


def attach_ids(frame: pd.DataFrame, xwalk: pd.DataFrame | None = None, bridge=None) -> pd.DataFrame:
    """Adds `in_xwalk` (ESPN id present in NBA/players_xwalk.csv) and `sb_player_id` (nba.props.identity.Bridge; None when
    unbridged/ambiguous) where those are supplied. Returns a copy; attrs preserved."""
    out = frame.copy()
    out.attrs.update(frame.attrs)
    if xwalk is not None and len(out):
        out["in_xwalk"] = out["espn_player_id"].isin(set(xwalk["espn_id"].astype(int)))
    if bridge is not None and len(out):
        out["sb_player_id"] = [bridge.sb_id(i)[0] for i in out["espn_player_id"]]
    return out


def absent_ids(team, statuses=("Out",), frame: pd.DataFrame | None = None) -> set:
    """ESPN player ids of `team` (any vocabulary) whose status is in `statuses`. Fetches when `frame` is None. An unusable
    report (frame.attrs['ok'] False) returns an EMPTY set -- check `frame.attrs` / use absent_by_team when unknown must
    not be confused with healthy."""
    frame = fetch_injuries() if frame is None else frame
    code = team_code(team) if team else None
    if not code or not len(frame):
        return set()
    sel = frame[(frame["team_abbrev"] == code) & frame["status"].isin(set(statuses))]
    return {int(i) for i in sel["espn_player_id"]}


def absent_by_team(frame: pd.DataFrame, statuses=("Out",)) -> dict:
    """{tricode: set of ESPN ids} for ALL 30 teams when the report is usable (teams absent from the payload are healthy),
    else {} (unknown: a consumer keyed on this mapping then emits NaN, not zeros)."""
    if not frame.attrs.get("ok", False):
        return {}
    out = {c: set() for c, *_ in TEAMS}
    sel = frame[frame["status"].isin(set(statuses))]
    for code, pid in zip(sel["team_abbrev"], sel["espn_player_id"]):
        if code in out:
            out[code].add(int(pid))
    return out
