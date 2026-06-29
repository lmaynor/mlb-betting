"""Extract historical NRFI/YRFI odds from the GCS SGO snapshot archive. [CLOUD SHELL]

The user-supplied yrfi_master.csv only has prices through 2026-04-14 (and just 1
priced 2026 game), so the v18 backtest had no genuine out-of-sample window. But
the daily SGO snapshots (Odds/sgo/{date}/snapshot_HHMM.json, 4x/day) cover the
whole live season. This walks those snapshots, pulls the NRFI O/U market per game,
and emits rows in the yrfi_master.csv schema so nrfi_market.py can backtest 2026
as TRUE out-of-sample (v18 never trained on it).

Per game we take the LATEST snapshot of the day in which the game still has NRFI
prices (closest to its own first pitch == closing-ish line, and maximizes coverage
since day games drop out of late snapshots). Reuses the production SGO parsers
(_best_book_odds_int, _event_teams, etc.) so prices match what the runner saw.

Run in Cloud Shell (needs GCS):

    cd ~/mlb-betting
    export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
    PYTHONPATH=. python3 -m mlb.analysis.extract_2026_nrfi_odds \
        --year 2026 --master yrfi_master.csv --out yrfi_master_2026.csv

Then:
    PYTHONPATH=. python3 -m mlb.analysis.nrfi_market \
        --odds yrfi_master_2026.csv --preds nrfi_preds.csv --year 2026
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

import pandas as pd

from mlb_core.odds.sgo import (
    _NRFI_OVER_ID,
    _NRFI_UNDER_ID,
    _best_book_odds_int,
    _event_teams,
    _parse_odds_int,
    _safe_int,
    load_snapshot,
    ONSHORE_BOOKS,
)
from mlb_core.odds.utils import american_to_implied_prob, implied_to_american
from mlb_core.storage import list_keys
from mlb.analysis.nrfi_market import norm_team

_SNAP_PREFIX = "Odds/sgo/"


def _consensus_american(odd_entry: dict):
    """Vig-inclusive consensus American odds = median implied prob across onshore
    books, converted back to American. Returns None if no onshore book quotes it.

    Median is taken in implied-probability space (American odds are discontinuous
    at +/-100, so a raw median of the integers is wrong).
    """
    by_book = odd_entry.get("byBookmaker") or {}
    probs = []
    for book, info in by_book.items():
        if book not in ONSHORE_BOOKS or not info.get("available"):
            continue
        val = _parse_odds_int(info.get("odds"))
        if val is not None:
            probs.append(american_to_implied_prob(val))
    if not probs:
        return None
    return implied_to_american(statistics.median(probs))


def _row_from_event(event: dict) -> dict | None:
    """Build one yrfi_master-schema row from an SGO event, or None if no NRFI mkt."""
    odds = event.get("odds") or {}
    over_entry = odds.get(_NRFI_OVER_ID)    # YRFI side (a run scores in 1st)
    under_entry = odds.get(_NRFI_UNDER_ID)  # NRFI side
    if not over_entry or not under_entry:
        return None

    best_yrfi, _ = _best_book_odds_int(over_entry)
    best_nrfi, _ = _best_book_odds_int(under_entry)
    cons_yrfi = _consensus_american(over_entry)
    cons_nrfi = _consensus_american(under_entry)
    if cons_yrfi is None or cons_nrfi is None:
        return None  # need a consensus pair to de-vig downstream

    away, home = _event_teams(event)
    return {
        "Away": away,
        "Home": home,
        "Open_YRFI": _safe_int(over_entry.get("openBookOdds")),
        "Open_NRFI": _safe_int(under_entry.get("openBookOdds")),
        "Best Odds_YRFI": best_yrfi,
        "Best Odds_NRFI": best_nrfi,
        "Consensus_YRFI": cons_yrfi,
        "Consensus_NRFI": cons_nrfi,
        "_event_id": event.get("eventID"),
    }


def extract_year(year: int) -> pd.DataFrame:
    """Walk every snapshot folder for `year` and return one row per game."""
    keys = [k for k in list_keys(_SNAP_PREFIX)
            if k.endswith(".json") and "/snapshot_" in k]
    # Group snapshot keys by date folder, restricted to the target year.
    by_date: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        # Odds/sgo/2026-06-01/snapshot_2330.json
        parts = k.split("/")
        if len(parts) < 4:
            continue
        date = parts[2]
        if date.startswith(str(year)):
            by_date[date].append(k)
    if not by_date:
        raise RuntimeError(f"no {year} snapshots found under {_SNAP_PREFIX}")

    print(f"{len(by_date)} {year} snapshot days "
          f"({min(by_date)} -> {max(by_date)})")

    rows = []
    for date in sorted(by_date):
        # latest-available price per event across the day's snapshots
        # (iterate snapshots oldest->newest, overwrite so newest wins).
        per_event: dict[str, dict] = {}
        for snap_key in sorted(by_date[date]):  # snapshot_1555 < _1900 < _2155 < _2330
            for event in load_snapshot(snap_key):
                row = _row_from_event(event)
                if row is not None:
                    per_event[row["_event_id"]] = row
        for row in per_event.values():
            row = dict(row)
            row["Date"] = date
            rows.append(row)
        if per_event:
            print(f"  {date}: {len(per_event)} games priced")

    df = pd.DataFrame(rows).drop(columns=["_event_id"])
    print(f"extracted {len(df)} {year} games with NRFI consensus prices")
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract NRFI odds from SGO snapshots")
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--master", default="yrfi_master.csv",
                    help="existing odds master to concatenate onto (optional)")
    ap.add_argument("--out", default="yrfi_master_2026.csv")
    args = ap.parse_args()

    new = extract_year(args.year)

    if args.master:
        try:
            master = pd.read_csv(args.master)
            combined = pd.concat([master, new], ignore_index=True)
            print(f"concatenated onto {len(master)} master rows -> {len(combined)}")
        except FileNotFoundError:
            print(f"master {args.master} not found -- writing extracted rows only")
            combined = new
    else:
        combined = new

    # Dedup on the normalized game_key (date_away@home); keep the snapshot-derived
    # row (last) over any pre-existing stub for the same game.
    gk = (pd.to_datetime(combined["Date"]).dt.strftime("%Y-%m-%d")
          + "_" + combined["Away"].map(norm_team)
          + "@" + combined["Home"].map(norm_team))
    combined = combined.loc[~gk.duplicated(keep="last")].copy()

    # Coverage check: flag any team name norm_team didn't recognize.
    unmapped = sorted({t for t in pd.concat([new["Away"], new["Home"]])
                       if norm_team(t) == str(t).strip().upper()
                       and len(str(t)) > 3})
    if unmapped:
        print(f"WARNING: unmapped team names (add to _TEAM_CANON): {unmapped}")

    combined.to_csv(args.out, index=False)
    print(f"wrote {len(combined)} rows -> {args.out}")


if __name__ == "__main__":
    main()
