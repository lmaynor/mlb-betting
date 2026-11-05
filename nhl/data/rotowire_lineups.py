"""RotoWire NHL lineups -> confirmed starting goalies (and PP units / injuries) for today or tomorrow.

Access is by RotoWire's agreement (the owner phoned and got a yes; get it in writing -- their terms, Article VI, bar
automated access without written consent). Rules this module keeps so that stays defensible:
  * one request per call, no retries, no crawling past the one page;
  * an identifiable User-Agent;
  * nothing from the page is stored: only the derived team -> goalie names are used in memory by the game model.

Only a goalie RotoWire marks "Confirmed" is returned by `confirmed_goalies`; "Expected" is a guess and is ignored.
Fail-open: any error returns {} and the game model falls back to its inferred starter.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from nhl.config import NAME_TO_ABBREV, team_abbrev

logger = logging.getLogger(__name__)

URL = "https://www.rotowire.com/hockey/nhl-lineups.php"
USER_AGENT = "beezy.fyi-lineups/1.0 (approved by RotoWire; one request per run)"
TIMEOUT_S = 15

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


if __name__ == "__main__":
    import sys

    for team, v in sorted(goalies_by_team(fetch_games("--tomorrow" in sys.argv)).items()):
        print(f"{team}  {v['goalie']}  [{v['status']}]")
