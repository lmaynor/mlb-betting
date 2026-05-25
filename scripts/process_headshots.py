#!/usr/bin/env python3
"""
process_headshots.py — download MLB player headshots and remove background.

Run this after today's picks are generated so the cheat-sheet card shows
clean cut-out headshots on a transparent background.

Usage:
    python scripts/process_headshots.py                     # today's picks only
    python scripts/process_headshots.py Aaron Judge "Mitch Keller"  # specific players
    python scripts/process_headshots.py --all               # all players in player_map.json

Requirements:
    pip install "rembg[cpu]" pillow requests
    (downloads a ~170 MB u2net ONNX model on first run — no PyTorch/CUDA needed)

Outputs:
    beezy-vip/public/headshots/{first_last}.png  (transparent background PNG)
"""

import sys
import json
import os
import io
import urllib.request
from pathlib import Path

REPO_ROOT   = Path(__file__).resolve().parent.parent
PLAYER_MAP  = REPO_ROOT / "beezy-vip" / "public" / "headshots" / "player_map.json"
OUT_DIR     = REPO_ROOT / "beezy-vip" / "public" / "headshots"
HEADSHOT_URL = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{id}/headshot/67/current"

BETTING_API  = os.getenv("BETTING_API_URL", "https://mlb-betting-628109313129.us-central1.run.app")
BETTING_KEY  = os.getenv("BETTING_API_KEY", "")


def slug(name: str) -> str:
    import re
    # Must match playerSlug() in page.tsx: hyphen→_ BEFORE stripping non-word chars
    s = name.lower().replace(" ", "_").replace("-", "_")
    s = re.sub(r"[^\wÀ-ɏ]", "", s)  # strips dots, apostrophes, etc. but keeps accents
    return s


def fetch_today_players() -> list[str]:
    """Pull today's picks from the Cloud Run API and return player names."""
    import urllib.request, json
    url = f"{BETTING_API}/api/public/picks/today"
    req = urllib.request.Request(url, headers={"X-API-Key": BETTING_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        picks = data.get("picks", [])
        names = [p["player"] for p in picks if p.get("player")]
        print(f"  Found {len(picks)} picks today → {len(names)} with player names")
        return names
    except Exception as e:
        print(f"  ⚠️  Could not fetch today's picks: {e}")
        return []


def remove_background(img_bytes: bytes) -> bytes:
    """Remove background using rembg (lightweight ONNX, no PyTorch needed)."""
    try:
        from rembg import remove as rembg_remove
        return rembg_remove(img_bytes)
    except ImportError:
        raise RuntimeError('Install: pip install "rembg[cpu]"')


def _process_by_key(key: str, mlbam_id: int) -> bool:
    out_path = OUT_DIR / f"{key}.png"
    if out_path.exists():
        return True
    url = HEADSHOT_URL.format(id=mlbam_id)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            img_bytes = r.read()
        print(f"  ⬇️  {key} ({len(img_bytes):,}B)", end="", flush=True)
        result_bytes = remove_background(img_bytes)
        out_path.write_bytes(result_bytes)
        print(f" → saved ({len(result_bytes):,}B)")
        return True
    except Exception as e:
        print(f"\n  ❌ {key}: {e}")
        return False


def process_player(name: str, player_map: dict) -> bool:
    key = slug(name)
    mlbam_id = player_map.get(key)
    if not mlbam_id:
        print(f"  ⚠️  {name!r} not in player_map (key={key!r})")
        return False

    out_path = OUT_DIR / f"{key}.png"
    if out_path.exists():
        print(f"  ✅ {name} — already processed, skipping")
        return True

    # Fetch headshot
    url = HEADSHOT_URL.format(id=mlbam_id)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            img_bytes = r.read()
        print(f"  ⬇️  {name} — fetched {len(img_bytes):,} bytes", end="", flush=True)
    except Exception as e:
        print(f"  ❌ {name} — fetch failed: {e}")
        return False

    # Remove background
    try:
        result_bytes = remove_background(img_bytes)
        out_path.write_bytes(result_bytes)
        print(f" → saved {out_path.name} ({len(result_bytes):,} bytes)")
        return True
    except Exception as e:
        print(f"\n  ❌ {name} — background removal failed: {e}")
        return False


def refresh_player_map() -> dict:
    """Merge active 2026 MLB roster into player_map.json and return updated map."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    season = __import__('datetime').date.today().year
    url = f"https://statsapi.mlb.com/api/v1/sports/1/players?season={season}"
    try:
        with urllib.request.urlopen(url, timeout=15, context=ctx) as r:
            people = json.loads(r.read()).get("people", [])
        print(f"  MLB API returned {len(people)} active players for {season}")
    except Exception as e:
        print(f"  ⚠️  Could not fetch active roster: {e}")
        return {}

    with open(PLAYER_MAP) as f:
        existing = json.load(f)

    added = 0
    for p in people:
        key = slug(p["fullName"])
        if key not in existing:
            existing[key] = p["id"]
            added += 1

    existing = dict(sorted(existing.items()))
    with open(PLAYER_MAP, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    print(f"  Added {added} new players → {len(existing)} total in player_map.json")
    return existing


def main():
    if "--refresh-map" in sys.argv or "--sync" in sys.argv:
        print("Refreshing player_map.json from MLB active roster...")
        player_map = refresh_player_map()
        if not player_map:
            sys.exit(1)
        print()
    else:
        with open(PLAYER_MAP) as f:
            player_map = json.load(f)

    print(f"Player map: {len(player_map)} entries")
    print(f"Output dir: {OUT_DIR}")
    print()

    if "--all" in sys.argv:
        # Process directly by key to avoid slug round-trip errors
        missing_keys = [(k, v) for k, v in player_map.items()
                        if not (OUT_DIR / f"{k}.png").exists()]
        print(f"Processing {len(missing_keys)} players without existing headshots...")
        print()
        ok = sum(1 for k, v in missing_keys if _process_by_key(k, v))
        print(f"\nDone — {ok}/{len(missing_keys)} processed successfully")
        print(f"Commit: git add beezy-vip/public/headshots/ && git push")
        return
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        names = [a for a in sys.argv[1:] if not a.startswith("--")]
        print(f"Processing {len(names)} specified player(s)...")
    else:
        print("Fetching today's picks to determine which players to process...")
        names = fetch_today_players()
        if not names:
            print("  No players found for today. Pass player names as args or use --all.")
            sys.exit(0)

    print()
    ok = 0
    for name in names:
        if process_player(name, player_map):
            ok += 1

    print()
    print(f"Done — {ok}/{len(names)} processed successfully")
    print(f"Commit: git add beezy-vip/public/headshots/ && git push")


if __name__ == "__main__":
    main()
