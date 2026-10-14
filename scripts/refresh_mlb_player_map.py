#!/usr/bin/env python3
"""refresh_mlb_player_map.py -- merge the active MLB roster into beezy-vip/public/headshots/player_map.json.

Replaces the map-refresh half of the old scripts/process_headshots.py. The other half (downloading each headshot and removing
its background with rembg) is gone: MLB's CDN serves the transparent `headshot/silo` variant directly, so the site hotlinks it
(beezy-vip/lib/sport-assets.ts) and only needs this name -> MLBAM id map.

  python scripts/refresh_mlb_player_map.py               # merge this season's active players, write the map
  python scripts/refresh_mlb_player_map.py --dry-run     # show what would be added, write nothing
  python scripts/refresh_mlb_player_map.py --check-photos   # also flag NEW ids whose silo is the generic placeholder

Never drops an entry (IL / minors / retired players can still appear in picks). A duplicate name keeps the id already in the
map (two Max Muncys: first one wins) -- the map has no team keys, a pre-existing limitation.
TLS certificates ARE verified (the old script disabled verification for this call).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PLAYER_MAP = ROOT / "beezy-vip" / "public" / "headshots" / "player_map.json"
ROSTER_URL = "https://statsapi.mlb.com/api/v1/sports/1/players?season={season}"
SILO = ("https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:silo:current.png/"
        "w_213,q_auto:best/v1/people/{id}/headshot/silo/current")
UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-assets/1.0)"}


def slug(name: str) -> str:
    """Same keys the frontend looks up (playerSlug): lower, spaces and hyphens -> '_', strip everything but
    letters/digits/_ but KEEP accents ("randy_vasquez" with the accent stays "randy_v..." as written)."""
    s = (name or "").lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"[^\wÀ-ɏ]", "", s)


def fetch_roster(season: int, get=requests.get) -> list:
    r = get(ROSTER_URL.format(season=season), headers=UA, timeout=30)
    r.raise_for_status()
    return r.json().get("people", [])


def merge(existing: dict, people: list) -> tuple[dict, dict]:
    """-> (merged map sorted by key, {slug: id} of additions). Never removes or overwrites an existing key."""
    added = {}
    merged = dict(existing)
    for p in people:
        key, pid = slug(p.get("fullName", "")), p.get("id")
        if key and pid is not None and key not in merged:
            merged[key] = int(pid)
            added[key] = int(pid)
    return dict(sorted(merged.items())), added


def generic_placeholder_hash(get=requests.get) -> str:
    return hashlib.sha256(get(SILO.format(id=1), headers=UA, timeout=30).content).hexdigest()


def photo_missing(ids: dict, get=requests.get) -> list:
    """Slugs whose silo image is the generic placeholder (MLB returns 200 + a silhouette for ids with no photo)."""
    generic = generic_placeholder_hash(get)
    out = []
    for key, pid in ids.items():
        h = hashlib.sha256(get(SILO.format(id=pid), headers=UA, timeout=30).content).hexdigest()
        if h == generic:
            out.append(key)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check-photos", action="store_true")
    ap.add_argument("--season", type=int, default=date.today().year)
    a = ap.parse_args(argv)
    people = fetch_roster(a.season)
    print(f"MLB API returned {len(people)} active players for {a.season}")
    existing = json.loads(PLAYER_MAP.read_text())
    merged, added = merge(existing, people)
    print(f"{len(added)} new -> {len(merged)} total")
    for k, v in list(added.items())[:20]:
        print(f"  + {k} = {v}")
    if a.check_photos and added:
        missing = photo_missing(added)
        print(f"{len(missing)} of {len(added)} new ids have NO photo yet (generic placeholder): {missing[:20]}")
    if not a.dry_run and added:
        PLAYER_MAP.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {PLAYER_MAP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
