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
    pip install backgroundremover pillow requests
    (backgroundremover downloads a ~170 MB u2net model on first run)

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
    return name.lower().replace(" ", "_").replace("'", "").replace(".", "").replace("-", "_")


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
    """Remove background using backgroundremover (u2net model)."""
    try:
        from backgroundremover.bg import remove as bg_remove
        result = bg_remove(
            img_bytes,
            model_name="u2net_human_seg",   # best for portraits
            alpha_matting=True,
            alpha_matting_foreground_threshold=240,
            alpha_matting_background_threshold=10,
            alpha_matting_erode_structure_size=10,
            alpha_matting_base_size=1000,
        )
        return result
    except ImportError:
        print("  ⚠️  backgroundremover not installed — trying rembg fallback...")
        try:
            from rembg import remove as rembg_remove
            return rembg_remove(img_bytes)
        except ImportError:
            raise RuntimeError(
                "Install backgroundremover: pip install backgroundremover\n"
                "Or alternatively: pip install rembg"
            )


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


def main():
    with open(PLAYER_MAP) as f:
        player_map = json.load(f)

    print(f"Player map: {len(player_map)} entries")
    print(f"Output dir: {OUT_DIR}")
    print()

    if "--all" in sys.argv:
        # Convert snake_case keys back to names for display
        names = [k.replace("_", " ").title() for k in player_map.keys()]
        print(f"Processing ALL {len(names)} players...")
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        names = sys.argv[1:]
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
