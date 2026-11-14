"""Build beezy-vip/lib/ncaab-logos.json: NCAAB team code -> ESPN team id, for the (league, abbrev) logo mapping
(lib/sport-assets.ts `anyTeamLogoUrl`). ESPN serves college logos at /i/teamlogos/ncaa/500/{id}.png keyed by its
own numeric id, so the frontend map needs code -> id for every code vocabulary the boards can emit:

  - BettingPros codes (the ncaab board's anchor vocabulary) -- via NCAAB_BP_TO_KALSHI in ev.sports.ncaa_teams
  - Kalshi codes (the census itself) -- NCAAB_KALSHI_CODES / NCAAB_KALSHI_NAMES in ev.sports.ncaa_teams
  - BestBook codes named by the board -- ev.ncaab_board.ALIASES, plus CODE_OVERRIDES below

The join is Kalshi school name -> ESPN team, matched on normalised names (ESPN `location`, `shortDisplayName`,
`name`, and displayName minus its mascot). A school joins ONLY when exactly one ESPN team matches: an ambiguous
or unmatched school is left OUT of the map (the resolver fails closed to no logo) and printed in the report.
NAME_OVERRIDES covers the schools ESPN names differently from Kalshi; every entry was verified by hand against
the ESPN record (2026-11-13). BP codes with no crosswalk entry (46 of 345: schools with no Kalshi market that
week, D2/D3 opponents) carry no name evidence anywhere in the repo, so they stay unmapped on purpose.

ESPN's site API refuses non-browser user agents (403), so the fetch sends a normal browser UA.

Run:  python scripts/build_ncaab_logo_map.py            (writes beezy-vip/lib/ncaab-logos.json)
      python scripts/build_ncaab_logo_map.py --check    (report only, no write)
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ev.ncaab_board import ALIASES as BOARD_ALIASES  # BestBook/Kalshi code -> BettingPros code
from ev.sports.ncaa_teams import NCAAB_BP_TO_KALSHI, NCAAB_KALSHI_NAMES

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams?limit=500"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
OUT = Path(__file__).resolve().parent.parent / "beezy-vip" / "lib" / "ncaab-logos.json"

# Kalshi code -> ESPN team id, for schools whose ESPN name defeats the normalised join. Verified 2026-11-13
# against the ESPN records (id / abbreviation / displayName):
NAME_OVERRIDES = {
    "ALBY": 399,     # University at Albany -> UAlbany Great Danes
    "APP": 2026,     # Appalachian St. -> App State Mountaineers
    "CCSU": 2115,    # Central Connecticut St. -> Central Connecticut Blue Devils
    "LIU": 112358,   # LIU -> Long Island University Sharks
    "MIA": 2390,     # Miami (FL) -> Miami Hurricanes (ESPN location "Miami"; Miami (OH) is a different record)
    "NICH": 2447,    # Nicholls St. -> Nicholls Colonels
    "SEA": 2547,     # Seattle -> Seattle U Redhawks
    "SELA": 2545,    # Southeastern Louisiana -> SE Louisiana Lions
    "SOU": 2582,     # Southern University -> Southern Jaguars
    "ULM": 2433,     # Louisiana-Monroe -> UL Monroe Warhawks
    "UTM": 2630,     # Tennessee-Martin -> UT Martin Skyhawks
}

# Codes for schools outside the Kalshi census entirely, so no crosswalk/name route exists. Evidence:
# ev/ncaab_board.py's docstring pairs BettingPros 'CSF' / BestBook 'CSUF' as one school; ESPN's only Fullerton
# entry is 2239 (its own abbreviation is CSUF) and High Point is HP's joined id (2272).
CODE_OVERRIDES = {
    "CSF": 2239,     # Cal State Fullerton (BettingPros code)
    "CSUF": 2239,    # Cal State Fullerton (BestBook code; also ESPN's abbreviation)
    "HPU": 2272,     # High Point (BestBook code; Kalshi/BP code HP joins to the same id)
}


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ").replace(".", "").replace("-", " ")
    s = s.replace("'", "").replace("(", " ").replace(")", " ")
    s = re.sub(r"\bstate\b", "st", s)
    return re.sub(r"\s+", " ", s).strip()


def kalshi_variants(name: str) -> set[str]:
    n = norm(name)
    out = {n}
    for prefix in ("university at ", "university of "):
        if n.startswith(prefix):
            out.add(n[len(prefix):])
    return out


def main() -> int:
    req = urllib.request.Request(ESPN_TEAMS_URL, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    espn = [t["team"] for t in payload["sports"][0]["leagues"][0]["teams"]]

    by_name: dict[str, list[dict]] = {}
    for t in espn:
        cands = set()
        for key in ("location", "shortDisplayName", "name", "displayName"):
            if t.get(key):
                cands.add(norm(t[key]))
        nick, dn = norm(t.get("nickname")), norm(t.get("displayName"))
        if nick and dn.endswith(" " + nick):
            cands.add(dn[: -len(nick) - 1])
        for c in cands:
            by_name.setdefault(c, []).append(t)

    kalshi_to_id: dict[str, int] = {}
    unmatched, ambiguous = [], []
    for code, name in sorted(NCAAB_KALSHI_NAMES.items()):
        hits, seen = [], set()
        for v in kalshi_variants(name):
            for h in by_name.get(v, []):
                if h["id"] not in seen:
                    seen.add(h["id"])
                    hits.append(h)
        if len(hits) == 1:
            kalshi_to_id[code] = int(hits[0]["id"])
        elif len(hits) > 1:
            ambiguous.append((code, name, [(h["id"], h["displayName"]) for h in hits]))
        else:
            unmatched.append((code, name))

    for code, espn_id in NAME_OVERRIDES.items():
        if code in kalshi_to_id:
            raise SystemExit(f"NAME_OVERRIDES[{code}] is stale: the join now resolves it to {kalshi_to_id[code]}")
        if code not in NCAAB_KALSHI_NAMES:
            raise SystemExit(f"NAME_OVERRIDES[{code}] is not a census code")
        kalshi_to_id[code] = espn_id
        unmatched = [u for u in unmatched if u[0] != code]

    out: dict[str, int] = dict(kalshi_to_id)
    bp_mapped = 0
    for bp, kalshi in sorted(NCAAB_BP_TO_KALSHI.items()):
        if kalshi in kalshi_to_id:
            out[bp] = kalshi_to_id[kalshi]
            bp_mapped += 1
    for alias, bp in BOARD_ALIASES.items():
        if bp in out:
            out[alias] = out[bp]
    for code, espn_id in CODE_OVERRIDES.items():
        out[code] = espn_id

    print(f"ESPN teams: {len(espn)}")
    print(f"Kalshi census schools resolved: {len(kalshi_to_id)}/{len(NCAAB_KALSHI_NAMES)} "
          f"(join {len(kalshi_to_id) - len(NAME_OVERRIDES)} + overrides {len(NAME_OVERRIDES)})")
    print(f"BettingPros codes resolved via crosswalk: {bp_mapped}/{len(NCAAB_BP_TO_KALSHI)}")
    print(f"Map entries: {len(out)}")
    if unmatched:
        print(f"UNMATCHED (left unmapped, fail closed): {unmatched}")
    if ambiguous:
        print(f"AMBIGUOUS (left unmapped, fail closed): {ambiguous}")
        return 1
    if "--check" not in sys.argv:
        OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
        print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
