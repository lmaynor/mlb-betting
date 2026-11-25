"""Build and verify the C1 NHL primary/secondary assist split table from banked shots CSVs."""
from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from mlb_core import storage
from mod04c.nhl_assist_split import SPLIT_COLUMNS, split_from_shots, verify_split
from nhl.config import SKATER_BOX_MASTER
from nhl.data.settings import shots_key

DEFAULT_SEASONS = list(range(2019, 2027))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build NHL primary/secondary assist split from shots CSVs.")
    ap.add_argument(
        "--seasons",
        default=",".join(str(s) for s in DEFAULT_SEASONS),
        help="Comma-separated season start years (default: 2019..2026)",
    )
    ap.add_argument("--out-key", default="NHL/assist_split/assist_split.csv")
    ap.add_argument("--verify-key", default="NHL/assist_split/verification.json")
    args = ap.parse_args(argv)

    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
    parts = []
    loaded_seasons = []
    for syear in seasons:
        key = shots_key(syear)
        if not storage.exists(key):
            continue
        shots = storage.read_csv(key)
        sp = split_from_shots(shots)
        parts.append(sp)
        loaded_seasons.append(syear)

    if not parts:
        print(json.dumps({"error": "No shots CSVs found", "seasons_checked": seasons}))
        return 1

    split_df = pd.concat(parts, ignore_index=True)
    split_df = (
        split_df.groupby(["game_id", "player_id"], as_index=False)[
            ["primary_assists", "secondary_assists", "assists_split_total"]
        ]
        .sum()[SPLIT_COLUMNS]
        .sort_values(["game_id", "player_id"])
        .reset_index(drop=True)
    )

    master_df = storage.read_csv(SKATER_BOX_MASTER)
    if "season_year" in master_df.columns and loaded_seasons:
        master_sub = master_df[master_df["season_year"].isin(loaded_seasons)].copy()
    else:
        master_sub = master_df

    verification = verify_split(split_df, master_sub)
    verification["seasons_loaded"] = loaded_seasons
    verification["out_key"] = args.out_key

    storage.write_csv(split_df, args.out_key)
    storage.write_bytes(json.dumps(verification, indent=2).encode("utf-8"), args.verify_key)
    print(json.dumps(verification, indent=2))
    # Success = exact on the master population. Orphan split rows (PBP-credited
    # assists with no master boxscore row) are a documented data property, not
    # a build failure; they are counted in the verification report.
    return 0 if verification["exact_match_on_master_population"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
