"""RotoWire NHL lineups -> confirmed starting goalies (and PP units / injuries) for today or tomorrow.

Access is by RotoWire's agreement (the owner phoned and got a yes; get it in writing -- their terms, Article VI, bar
automated access without written consent). Rules this module keeps so that stays defensible:
  * one request per refresh, no retries, no crawling past the one page; the game model reads a small derived cache
    (`CACHE_KEY`, names only) so its 30-minute runs never touch the site -- only `refresh_cache` (the nhl-lineups-refresh job) does;
  * an identifiable User-Agent;
  * nothing from the page is stored except that cache: team -> confirmed goalie name, the day and the fetch time.

Only a goalie RotoWire marks "Confirmed" is returned by `confirmed_goalies`; "Expected" is a guess and is ignored.
Fail-open: any error returns {} and the game model falls back to its inferred starter.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from mlb_core import storage

from nhl.config import NAME_TO_ABBREV, team_abbrev

logger = logging.getLogger(__name__)

URL = "https://www.rotowire.com/hockey/nhl-lineups.php"
USER_AGENT = "beezy.fyi-lineups/1.0 (approved by RotoWire; one request per run)"
TIMEOUT_S = 15
CACHE_KEY = "EVSweep/nhl_games/confirmed_goalies.json"
ARCHIVE_PREFIX = "NHL/lineups/rotowire"  # one file per ET day, overwritten each firing: the last pre-game state
CACHE_MAX_AGE_MIN = 120  # a cache older than this is ignored (the refresh job runs every 30 min)

TEAM_RE = re.compile(r"^(.+?) \((\d+-\d+-\d+)\)$")  # "Flyers (3-7-3)"
RECORD_RE = re.compile(r"^\(\d+-\d+-\d+\)$")  # the live page writes the record as its own text node
PP_RE = re.compile(r"^POWER PLAY #(\d)$")
POSITIONS = {"C", "LW", "RW", "LD", "RD", "D", "G"}
STATUS_RE = re.compile(r"^(IR(-LT|-NR)?|OUT|DTD)$")
GOALIE_STATUS = {"Confirmed", "Expected", "Unconfirmed", "Probable", "Likely"}

_ET = ZoneInfo("America/New_York")
# "Sabres" / "Red Wings" -> abbrev: every word-suffix of the full name ("buffalo sabres" -> "sabres").
_NICK_TO_ABBREV: Dict[str, str] = {}
for _full, _ab in NAME_TO_ABBREV.items():
    _w = _full.split()
    for _i in range(1, len(_w)):
        _NICK_TO_ABBREV.setdefault(" ".join(_w[_i:]), _ab)


class _Text(HTMLParser):
    """Stripped text nodes in document order, skipping <script>/<style> (what BeautifulSoup.stripped_strings gives)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.out.append(data.strip())


def html_to_lines(html: str) -> List[str]:
    p = _Text()
    p.feed(html)
    return p.out


def team_code(label: str) -> Optional[str]:
    """'Sabres' / 'Buffalo Sabres' / 'BUF' -> BUF (NHL-API abbrev) or None."""
    code = team_abbrev(label)
    if code:
        return code
    return _NICK_TO_ABBREV.get((label or "").strip().lower())


def parse_lineups(lines: List[str]) -> List[dict]:
    """Page text lines -> games. Each block starts at a goalie name followed by a status word (Confirmed / Expected)."""
    merged: List[str] = []
    for ln in lines:
        if RECORD_RE.match(ln) and merged:
            merged[-1] = f"{merged[-1]} {ln}"
        else:
            merged.append(ln)
    lines = merged
    games: List[dict] = []
    game = None
    section = pos = pp = None
    prev_was_header = False

    for i, line in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        header = TEAM_RE.match(line)
        if header:
            if not prev_was_header:  # first team of a new game
                game = {"teams": [], "blocks": []}
                games.append(game)
            game["teams"].append(header.group(1))
        prev_was_header = bool(header)
        if game is None:
            continue

        if line in GOALIE_STATUS and i > 0:
            idx = len(game["blocks"])
            team = game["teams"][idx] if idx < len(game["teams"]) else None
            game["blocks"].append({"team": team, "goalie": lines[i - 1], "status": line,
                                   "lineup": [], "injuries": []})
            section, pos, pp = "lineup", None, None
            continue
        if line == "LINE":  # the odds line ends a game
            section = None
            continue
        if header or nxt in GOALIE_STATUS or not game["blocks"] or section is None:
            continue

        blk = game["blocks"][-1]
        m = PP_RE.match(line)
        if m:
            pp = int(m.group(1))
        elif line == "INJURIES":
            section, pos = "injuries", None
        elif line in POSITIONS:
            pos = line
        elif section == "lineup" and pos:
            blk["lineup"].append({"pp": pp, "pos": pos, "name": line})
            pos = None
        elif section == "injuries" and pos:
            blk["injuries"].append({"pos": pos, "name": line, "status": None})
            pos = None
        elif section == "injuries" and STATUS_RE.match(line) and blk["injuries"]:
            blk["injuries"][-1]["status"] = line
    return games


def fetch_games(tomorrow: bool = False) -> List[dict]:
    resp = requests.get(URL + ("?date=tomorrow" if tomorrow else ""), headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT_S)
    resp.raise_for_status()
    return parse_lineups(html_to_lines(resp.text))


def goalies_by_team(games: List[dict]) -> Dict[str, dict]:
    """{abbrev: {goalie, status, confirmed}} for every block whose team resolves."""
    out: Dict[str, dict] = {}
    for g in games:
        for blk in g["blocks"]:
            code = team_code(blk.get("team") or "")
            if code:
                out[code] = {"goalie": blk["goalie"], "status": blk["status"], "confirmed": blk["status"] == "Confirmed"}
    return out


def lineups_by_team(games: List[dict]) -> Dict[str, dict]:
    """{abbrev: {"pp": {"1": [names], "2": [names]}, "injuries": [{"name", "status"}]}} (names as RotoWire writes them)."""
    out: Dict[str, dict] = {}
    for g in games:
        for blk in g["blocks"]:
            code = team_code(blk.get("team") or "")
            if not code:
                continue
            pp: Dict[str, List[str]] = {"1": [], "2": []}
            for p in blk["lineup"]:
                if p["pp"] in (1, 2) and p["pos"] != "G":
                    pp[str(p["pp"])].append(p["name"])
            out[code] = {"pp": pp, "injuries": [{"name": i["name"], "status": i["status"]} for i in blk["injuries"]]}
    return out


def _norm(name: str) -> List[str]:
    x = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", x.replace("-", " ").replace(".", ". ").replace(".", "")).split()


def _same_player(board: List[str], page: List[str]) -> bool:
    """Full-name equal, or the page abbreviates the first name ('C. Giroux') to an initial that matches."""
    if not board or not page or board[-1] != page[-1]:
        return False
    return board == page or (len(page[0]) == 1 and board[0][:1] == page[0])


def player_context(lineups: Dict[str, dict], team: Optional[str], player: str) -> dict:
    """{"pp_unit": 1|2|None, "lineup_status": 'OUT'|'IR'|'DTD'|None} for a board player; None when not found or ambiguous.
    Display context only: nothing prices off it (no history to validate a power-play multiplier against)."""
    out = {"pp_unit": None, "lineup_status": None}
    t = (lineups or {}).get(team or "")
    if not t:
        return out
    want = _norm(player)
    hits = {u for u in ("1", "2") for n in t["pp"].get(u, []) if _same_player(want, _norm(n))}
    if len(hits) == 1:
        out["pp_unit"] = int(next(iter(hits)))
    inj = {i["status"] for i in t["injuries"] if _same_player(want, _norm(i["name"]))}
    if len(inj) == 1:
        out["lineup_status"] = next(iter(inj))
    return out


def confirmed_goalies(date_str: str, now: Optional[datetime] = None) -> Dict[str, str]:
    """{team abbrev: goalie name} confirmed by RotoWire for `date_str` (ET day). Only today / tomorrow exist on the
    page; any other date, and any failure, returns {}."""
    try:
        today = (now or datetime.now(_ET)).astimezone(_ET).date()
        day = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        delta = (day - today).days
        if delta not in (0, 1):
            return {}
        by_team = goalies_by_team(fetch_games(tomorrow=delta == 1))
        return {t: v["goalie"] for t, v in by_team.items() if v["confirmed"]}
    except Exception as exc:  # noqa: BLE001 -- the page is decoration for the model, never a reason to fail the run
        logger.warning("rotowire lineups unavailable (%s); inferring starters", exc)
        return {}


def refresh_cache(now: Optional[datetime] = None) -> Dict[str, str]:
    """Fetch TODAY's page once and write the confirmed goalies to CACHE_KEY. Raises on a fetch/write failure so the job
    shows red; an empty page keeps the previous cache (never overwrite good data with nothing)."""
    now = (now or datetime.now(_ET)).astimezone(_ET)
    games = fetch_games(tomorrow=False)
    by_team = goalies_by_team(games)
    confirmed = {t: v["goalie"] for t, v in by_team.items() if v["confirmed"]}
    if not by_team:
        raise RuntimeError("RotoWire page parsed to zero games; cache left untouched")
    payload = {"fetched_at": now.astimezone(ZoneInfo("UTC")).isoformat(), "day": now.date().isoformat(),
               "n_teams_on_page": len(by_team), "confirmed": confirmed,
               "lineups": lineups_by_team(games)}
    body = json.dumps(payload).encode("utf-8")
    storage.write_bytes(body, CACHE_KEY)
    storage.write_bytes(body, f"{ARCHIVE_PREFIX}/{payload['day']}.json")
    logger.info("rotowire cache: %d teams on page, %d confirmed", len(by_team), len(confirmed))
    return confirmed


def cached_confirmed_goalies(date_str: str, now: Optional[datetime] = None) -> Dict[str, str]:
    """{team: goalie} from the cache for `date_str` (ET day), or {} when the cache is missing, for another day, or stale.
    Never touches the network. Fail-open."""
    try:
        now_utc = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo("UTC"))
        d = json.loads(storage.read_bytes(CACHE_KEY).decode("utf-8"))
        age_min = (now_utc - datetime.fromisoformat(d["fetched_at"])).total_seconds() / 60.0
        if d.get("day") != date_str[:10] or age_min > CACHE_MAX_AGE_MIN or age_min < -5:
            return {}
        return {str(k): str(v) for k, v in (d.get("confirmed") or {}).items()}
    except Exception as exc:  # noqa: BLE001 -- missing cache is the normal first-run state
        logger.info("no usable confirmed-goalie cache (%s); inferring starters", exc)
        return {}


def cached_lineups(date_str: str, now: Optional[datetime] = None) -> Dict[str, dict]:
    """Power-play units + injuries from the same cache (same freshness rules as cached_confirmed_goalies); {} otherwise."""
    try:
        now_utc = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo("UTC"))
        d = json.loads(storage.read_bytes(CACHE_KEY).decode("utf-8"))
        age_min = (now_utc - datetime.fromisoformat(d["fetched_at"])).total_seconds() / 60.0
        if d.get("day") != date_str[:10] or age_min > CACHE_MAX_AGE_MIN or age_min < -5:
            return {}
        return d.get("lineups") or {}
    except Exception as exc:  # noqa: BLE001
        logger.info("no usable lineup cache (%s)", exc)
        return {}


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="RotoWire NHL confirmed goalies")
    ap.add_argument("--refresh", action="store_true", help="fetch today's page once and write the GCS cache (the job)")
    ap.add_argument("--tomorrow", action="store_true", help="print tomorrow's page (no cache write)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.refresh:
        confirmed = refresh_cache()
        print(f"confirmed goalies cached: {len(confirmed)}")
        return 0
    for team, v in sorted(goalies_by_team(fetch_games(args.tomorrow)).items()):
        print(f"{team}  {v['goalie']}  [{v['status']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
