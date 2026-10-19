#!/usr/bin/env python3
"""build_sport_assets.py -- NHL/NBA team logos, headshot id maps, and the shared team table for beezy-vip.

  python scripts/build_sport_assets.py --logos      # download + SANITIZE team logo SVGs into public/logos/{nhl,nba}/
  python scripts/build_sport_assets.py --players    # rebuild public/headshots/{nhl,nba}_player_map.json (rosters)
  python scripts/build_sport_assets.py --nfl-players  # rebuild public/headshots/nfl_player_map.json (nflverse rosters)
  python scripts/build_sport_assets.py --teams      # rebuild lib/sport-teams.json from the Python team tables
  python scripts/build_sport_assets.py --all

Design (decided 2026-10-14, verified live):
  * Team logos are COPIED into the repo (~125 small SVGs incl. light + dark variants, rarely change) -- one less third party on every page load.
  * Player headshots are HOTLINKED from the official CDNs, which serve transparent PNG cutouts, so no rembg step:
      NHL  https://assets.nhle.com/mugs/nhl/latest/{playerId}.png                        (336x336, ~100KB, Cloudflare)
      NBA  https://a.espncdn.com/combiner/i?img=/i/headshots/nba/players/full/{espnId}.png&w=200&h=146   (~31KB)
      NFL  https://static.www.nfl.com/image/upload/f_png,q_auto,w_200/league/{token}                (~8KB, nfl.com CDN)
    The player maps here only translate a name (+ team) into the id those URLs need. MLB keeps its existing
    public/headshots/player_map.json pipeline untouched. NFL's "id" is the nfl.com image token carried by the nflverse
    rosters' `headshot_url` (97% of players; the ESPN id exists for only ~70%), so it is a string, not an integer.
  * lib/sport-teams.json is GENERATED from nhl.config.TEAMS / nba.teams.TEAMS (the single source of truth) and
    tests/test_sport_assets.py fails if the committed copy drifts from it.

Downloaded files are untrusted input: every SVG is rejected unless it is a plain vector image (no <script>, event
handlers, javascript: URLs, <foreignObject>, or external references) and under a size cap. The original bytes are
written unmodified -- we validate, we do not rewrite. A manifest records source URL + sha256 per file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "beezy-vip" / "public"
LIB = ROOT / "beezy-vip" / "lib"
UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-assets/1.0)"}
MAX_SVG_BYTES = 200_000

NHL_LOGO = "https://assets.nhle.com/logos/nhl/svg/{abbr}_{variant}.svg"
# L = for light backgrounds, D = for dark backgrounds (verified 200 for all 30 teams). The UI is dark, so D is the default;
# several teams (Spurs: black wordmark) are effectively invisible on the dark UI in their L form.
NBA_LOGO = "https://cdn.nba.com/logos/nba/{team_id}/global/{variant}/logo.svg"

_BAD_SVG = [
    re.compile(rb"<\s*script", re.I), re.compile(rb"\son[a-z]+\s*=", re.I), re.compile(rb"javascript\s*:", re.I),
    re.compile(rb"<\s*foreignObject", re.I), re.compile(rb"<\s*iframe", re.I), re.compile(rb"<\s*embed", re.I),
    re.compile(rb"""(?:xlink:)?href\s*=\s*["']\s*(?:https?:)?//""", re.I),      # external reference
    re.compile(rb"<!ENTITY", re.I),                                              # XML entity tricks
]


def validate_svg(data: bytes):
    """-> (ok, reason). Plain vector images only."""
    if len(data) > MAX_SVG_BYTES:
        return False, f"too large ({len(data)} bytes)"
    head = data[:400].lstrip()
    if b"<svg" not in data[:2000].lower() or not head.startswith((b"<", b"\xef\xbb\xbf<")):
        return False, "not an SVG document"
    for pat in _BAD_SVG:
        if pat.search(data):
            return False, f"forbidden construct {pat.pattern!r}"
    return True, "ok"


def _get(url: str) -> bytes:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.content


# ----------------------------------------------------------------------------- logos
def build_logos() -> dict:
    sys.path.insert(0, str(ROOT))
    from nba.teams import NBA_ID, TEAMS as NBA_TEAMS
    from nhl.config import TEAMS as NHL_TEAMS
    jobs = []
    for abbr, _n, _k in NHL_TEAMS:
        for variant, suffix in (("light", ""), ("dark", "-dark")):
            jobs.append(("nhl", f"{abbr.lower()}{suffix}.svg", NHL_LOGO.format(abbr=abbr, variant=variant)))
    for code, *_ in NBA_TEAMS:
        for variant, suffix in (("L", ""), ("D", "-dark")):
            jobs.append(("nba", f"{code.lower()}{suffix}.svg", NBA_LOGO.format(team_id=NBA_ID[code], variant=variant)))
    manifests: dict = {"nhl": {}, "nba": {}}
    rejected, failed = [], []
    for sport, fname, url in jobs:
        out = PUBLIC / "logos" / sport / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = _get(url)
        except Exception as exc:                                                         # noqa: BLE001
            failed.append((url, repr(exc)))
            continue
        ok, why = validate_svg(data)
        if not ok:
            rejected.append((url, why))
            continue
        out.write_bytes(data)
        manifests[sport][fname] = {"source": url, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        time.sleep(0.1)
    for sport, m in manifests.items():
        (PUBLIC / "logos" / sport / "manifest.json").write_text(json.dumps(
            {"fetched": datetime.now(timezone.utc).date().isoformat(), "files": m}, indent=1, sort_keys=True) + "\n")
    return {"written": sum(len(m) for m in manifests.values()), "rejected": rejected, "failed": failed}


# ----------------------------------------------------------------------------- player maps
def _slug(name: str) -> str:
    """Same algorithm as playerSlug() in the frontend (accent-folded, lower, '_' separated)."""
    import unicodedata
    s = unicodedata.normalize("NFD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("-", " ").replace(".", "")
    return re.sub(r"[^a-z0-9_]", "", re.sub(r"\s+", "_", s.strip()))


def _to_map(rows, cast=int) -> dict:
    """rows: (full_name, team_code, id). Emits `slug__team` for every player and a bare `slug` ONLY when that name is
    unique league-wide: a duplicate name ("Jack Hughes") with no team must resolve to nothing, never to the wrong face."""
    by_slug: dict = {}
    out: dict = {}
    for name, team, pid in rows:
        s = _slug(name)
        if not s:
            continue
        out[f"{s}__{team.lower()}"] = cast(pid)
        by_slug.setdefault(s, set()).add(cast(pid))
    for name, team, pid in rows:
        s = _slug(name)
        if s and len(by_slug[s]) == 1:
            out[s] = cast(pid)
    return dict(sorted(out.items()))


def build_player_maps() -> dict:
    sys.path.insert(0, str(ROOT))
    from nba.data import espn_players
    from nhl.config import TEAMS as NHL_TEAMS
    from nhl.data import players as nhl_players
    from nhl.data.client import NhlClient

    nhl_rows, failed = [], []
    client = NhlClient(delay=0.6)
    for attempt in range(2):
        todo = [a for a, _, _ in NHL_TEAMS if a not in {r[1] for r in nhl_rows}]
        if not todo:
            break
        if attempt:
            time.sleep(20)                                   # NHL API rate-limits per IP; let the window clear
        for a in todo:
            try:
                roster = client.roster(a)
            except Exception as exc:                                                     # noqa: BLE001
                failed.append((a, repr(exc)))
                continue
            for p in nhl_players.flatten_roster(a, roster or {}):
                nhl_rows.append((p["full_name"], a, p["player_id"]))
    covered = {r[1] for r in nhl_rows}
    missing = [a for a, _, _ in NHL_TEAMS if a not in covered]
    if missing:
        # A partial map would hand out the wrong namesake's face; refuse rather than write it.
        raise SystemExit(f"NHL rosters missing for {missing}; not writing a partial map. Re-run later.")

    espn = espn_players.fetch_all()
    if espn["failed"]:
        raise SystemExit(f"ESPN rosters missing for {espn['failed']}; not writing a partial map. Re-run later.")
    nba_rows = [(r["full_name"], r["team"], r["espn_id"]) for r in espn["rows"]]

    res = {}
    for sport, rows in (("nhl", nhl_rows), ("nba", nba_rows)):
        m = _to_map(rows)
        (PUBLIC / "headshots" / f"{sport}_player_map.json").write_text(json.dumps(m, separators=(",", ":")) + "\n")
        res[sport] = {"players": len(rows), "keys": len(m)}
    return res


NFL_TOKEN = re.compile(r"static\.www\.nfl\.com/image/(?:upload|private)/[^/]+/league/([A-Za-z0-9]{10,40})(?:[/?.]|$)")
NFL_POSITIONS = {"QB", "RB", "FB", "WR", "TE"}              # the positions the projection board has props for


def nfl_rows(roster) -> list:
    """nflverse roster frame -> (full_name, ev team code, nfl.com image token) for offensive skill players on a roster."""
    sys.path.insert(0, str(ROOT))
    from nfl.data.keys import ev_code
    rows = []
    for r in roster.to_dict("records"):
        m = NFL_TOKEN.search(str(r.get("headshot_url") or ""))
        team = ev_code(r.get("team"))
        if m and team and str(r.get("position")) in NFL_POSITIONS and r.get("full_name"):
            rows.append((str(r["full_name"]), team, m.group(1)))
    return rows


def build_nfl_player_map() -> dict:
    """public/headshots/nfl_player_map.json from the current-season nflverse roster mirror (GCS read; needs GCS_BUCKET)."""
    sys.path.insert(0, str(ROOT))
    from nfl.data.read import load
    season = datetime.now(timezone.utc).year
    roster = None
    for s in (season, season - 1):                            # before the new season's file exists, use last season's
        try:
            roster = load("rosters", seasons=[s])
        except FileNotFoundError:
            continue
        if len(roster):
            break
    if roster is None or not len(roster):
        raise SystemExit("no nflverse roster mirror found; not writing an empty map")
    rows = nfl_rows(roster)
    if len(rows) < 500:
        raise SystemExit(f"only {len(rows)} NFL skill players with a headshot; refusing to write a partial map")
    m = _to_map(rows, cast=str)
    (PUBLIC / "headshots" / "nfl_player_map.json").write_text(json.dumps(m, separators=(",", ":")) + "\n")
    return {"players": len(rows), "keys": len(m)}


# ----------------------------------------------------------------------------- shared team table
def team_table() -> dict:
    sys.path.insert(0, str(ROOT))
    from nba import teams as nba
    from nhl import config as nhl
    return {
        "nhl": [{"abbr": a, "name": n, "alias": sorted({k})} for a, n, k in nhl.TEAMS],
        "nba": [{"abbr": c, "name": n, "alias": sorted({e} | {k for k, v in nba._EXTRA_ALIASES.items() if v == c})}
                for c, n, _i, _e, e in nba.TEAMS],
    }


def build_teams() -> dict:
    t = team_table()
    (LIB / "sport-teams.json").write_text(json.dumps(t, indent=1, sort_keys=False) + "\n")
    return {k: len(v) for k, v in t.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logos", action="store_true")
    ap.add_argument("--players", action="store_true")
    ap.add_argument("--nfl-players", action="store_true")
    ap.add_argument("--teams", action="store_true")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not (a.logos or a.players or a.nfl_players or a.teams or a.all):
        ap.error("choose --logos, --players, --nfl-players, --teams or --all")
    if a.all or a.teams:
        print("teams:", build_teams())
    if a.all or a.logos:
        print("logos:", build_logos())
    if a.all or a.players:
        print("players:", build_player_maps())
    if a.all or a.nfl_players:
        print("nfl players:", build_nfl_player_map())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
