#!/usr/bin/env python3
"""scripts/canonical_translate.py -- BNK-12 Canonical Odds Translation CLI.

Subcommands:
  an:             Translate Action Network raw banks
  bp:             Translate BettingPros long props and games banks
  oddsaccum:      Translate OddsAccum raw JSON snapshots
  kalshi-futures: Translate Kalshi futures snapshot parquets

Spec 5.4 Rules:
  - AN football: translate by event date from Nov 15 2026 captures forward ONLY.
    Refuse earlier requested dates for nfl/ncaaf with a clear message.
  - Fail-closed validation; published via ingest.publish().
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import sys
from typing import Iterator, List, Optional
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mlb_core import storage
from mlb_core.odds.canonical.ingest import publish
from mlb_core.odds.canonical.resolve import (
    MasterIndex,
    build_generic_master_index,
    build_nba_master_index,
)
from mlb_core.odds.canonical.writers import (
    write_an,
    write_bp,
    write_kalshi_futures,
    write_oddsaccum,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("canonical_translate")


def date_range(start_date: str, end_date: str) -> Iterator[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    cur = start
    while cur <= end:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def get_dates(args: argparse.Namespace) -> List[str]:
    if getattr(args, "date", None):
        return [args.date]
    if getattr(args, "start", None) and getattr(args, "end", None):
        return list(date_range(args.start, args.end))
    raise ValueError("Must specify either --date or both --start and --end")


def tier_dates(dates: List[str], args: argparse.Namespace, source: str, scope: str) -> List[str]:
    """Two-tier watermark discipline (RUL-20261119 D3 amendment).

    Tier 1 (default): active-season partitions from 2026-09-01 forward,
    translated immediately; earlier dates are refused (they are Tier 2's job).
    Tier 2: historical partitions, translated incrementally — any partition
    already in the watermark is skipped, so re-running a range translates
    only what the slow backfill has newly landed. Zero re-translation.
    --force bypasses the watermark skip (the publish fingerprint still
    prevents duplicate writes of identical inputs)."""
    from mlb_core.odds.canonical.ingest import TIER1_FLOOR, watermarked
    tier = getattr(args, "tier", 1)
    out: List[str] = []
    for dt in dates:
        if tier == 1 and dt < TIER1_FLOOR:
            logger.error("Tier 1 refuses %s: before the %s active-season floor; "
                         "historical partitions translate under --tier 2.", dt, TIER1_FLOOR)
            continue
        if tier == 2 and not getattr(args, "force", False) and watermarked(source, scope, dt, root=args.root):
            logger.info("Tier 2 skip %s %s %s: already watermarked (zero re-translation).", source, scope, dt)
            continue
        out.append(dt)
    return out


def get_master(league: str, master_root: Optional[str] = None) -> MasterIndex:
    lg = league.lower()
    if lg == "nba":
        try:
            return build_nba_master_index()
        except Exception as exc:
            logger.warning("Could not build NBA master from bucket: %s; using generic empty master", exc)
            return build_generic_master_index(lg)
    return build_generic_master_index(lg)


# ===========================================================================
# 1. Action Network Subcommand
# ===========================================================================

def cmd_an(args: argparse.Namespace) -> int:
    league = args.league.lower()
    dates = tier_dates(get_dates(args), args, "actionnetwork", league)
    master = get_master(league, args.master_root)

    for dt in dates:
        # Spec 5.4: AN football replication exclusion
        if league in ("nfl", "ncaaf") and dt < "2026-11-15":
            logger.error(
                "Refusing to translate AN %s date %s prior to 2026-11-15: "
                "replicated partitions are excluded per spec 5.4.",
                league, dt,
            )
            continue

        prefix = f"Odds/actionnetwork_{league}/"
        keys = storage.list_keys(prefix)
        matching_keys = [k for k in keys if k.endswith(f"/{dt}.parquet")]

        if not matching_keys:
            logger.info("No raw parquets found for AN %s on %s under %s", league, dt, prefix)
            continue

        frames: List[pd.DataFrame] = []
        for k in matching_keys:
            try:
                df = storage.read_parquet(k)
                frames.append(df)
            except Exception as exc:
                logger.warning("Failed reading %s: %s", k, exc)

        if not frames:
            continue

        combined_frame = pd.concat(frames, ignore_index=True)
        c_df, q_df, report = write_an(
            combined_frame,
            league=league,
            master=master,
            requested_date=dt,
            source_key=f"Odds/actionnetwork_{league}/*/{dt}.parquet",
        )

        res = publish("actionnetwork", league, dt, c_df, q_df, report, root=args.root, force=args.force)
        print(f"AN [{league}] {dt}: {res.get('status')} (Canonical: {len(c_df)}, Quarantine: {len(q_df)})")
        print(json.dumps(report, indent=2, default=str))

    return 0


# ===========================================================================
# 2. BettingPros Subcommand
# ===========================================================================

def cmd_bp(args: argparse.Namespace) -> int:
    sport = args.sport.lower()
    dates = tier_dates(get_dates(args), args, "bettingpros", sport)
    master = get_master(sport, args.master_root)

    for dt in dates:
        # Check props and games prefixes
        prefixes = [
            f"Odds/bettingpros_{sport}/",
            f"Odds/bettingpros_{sport}_games/",
        ]
        frames: List[pd.DataFrame] = []
        for pfx in prefixes:
            keys = storage.list_keys(pfx)
            for k in keys:
                if k.endswith(f"/{dt}.parquet"):
                    try:
                        frames.append(storage.read_parquet(k))
                    except Exception as exc:
                        logger.warning("Failed reading %s: %s", k, exc)

        if not frames:
            logger.info("No BP %s rows found for date %s", sport, dt)
            continue

        combined_frame = pd.concat(frames, ignore_index=True)
        c_df, q_df, report = write_bp(
            combined_frame,
            sport=sport,
            master=master,
            source_key=f"Odds/bettingpros_{sport}/{dt}",
        )

        res = publish("bettingpros", sport, dt, c_df, q_df, report, root=args.root, force=args.force)
        print(f"BP [{sport}] {dt}: {res.get('status')} (Canonical: {len(c_df)}, Quarantine: {len(q_df)})")
        print(json.dumps(report, indent=2, default=str))

    return 0


# ===========================================================================
# 3. OddsAccum Subcommand
# ===========================================================================

def cmd_oddsaccum(args: argparse.Namespace) -> int:
    sport_key = args.sport.lower()
    dates = tier_dates(get_dates(args), args, "oddsaccum", sport_key)
    master = get_master(sport_key, args.master_root)

    for dt in dates:
        prefix = f"OddsAccum/{sport_key}/raw/{dt}/"
        keys = storage.list_keys(prefix)
        json_keys = [k for k in keys if k.endswith(".json")]

        if not json_keys:
            logger.info("No raw JSON snapshots found for OddsAccum %s on %s under %s", sport_key, dt, prefix)
            continue

        for jk in sorted(json_keys):
            try:
                raw_bytes = storage.read_bytes(jk)
                payload = json.loads(raw_bytes.decode("utf-8"))
            except Exception as exc:
                logger.warning("Failed reading raw JSON %s: %s", jk, exc)
                continue

            stem = Path(jk).stem
            parts = stem.split("_")
            hhmm = parts[-1] if len(parts) > 1 and parts[-1].isdigit() else "0000"
            captured_ts = f"{dt}T{hhmm[:2]}:{hhmm[2:]}:00Z" if len(hhmm) == 4 else f"{dt}T00:00:00Z"

            c_df, q_df, report = write_oddsaccum(
                payload,
                sport_key=sport_key,
                captured_ts=captured_ts,
                master=master,
                source_key=jk,
            )
            report["hhmm"] = hhmm

            res = publish("oddsaccum", sport_key, dt, c_df, q_df, report, root=args.root, force=args.force, hhmm=hhmm)
            print(f"OddsAccum [{sport_key}] {dt} {hhmm}: {res.get('status')} (Canonical: {len(c_df)}, Quarantine: {len(q_df)})")

    return 0


# ===========================================================================
# 4. Kalshi Futures Subcommand
# ===========================================================================

def cmd_kalshi_futures(args: argparse.Namespace) -> int:
    dates = tier_dates(get_dates(args), args, "kalshi", "futures")

    for dt in dates:
        key = f"Futures/kalshi/dt={dt}/snapshot.parquet"
        if not storage.exists(key):
            logger.info("No Kalshi futures snapshot found at %s", key)
            continue

        try:
            df = storage.read_parquet(key)
        except Exception as exc:
            logger.warning("Failed reading %s: %s", key, exc)
            continue

        c_df, q_df, report = write_kalshi_futures(df, dt, source_key=key)
        res = publish("kalshi", "futures", dt, c_df, q_df, report, root=args.root, force=args.force)
        print(f"Kalshi Futures {dt}: {res.get('status')} (Canonical: {len(c_df)}, Quarantine: {len(q_df)})")
        print(json.dumps(report, indent=2, default=str))

    return 0


# ===========================================================================
# Main Parser
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="BNK-12 Canonical Odds Translation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common options helper
    def add_common_opts(p: argparse.ArgumentParser):
        p.add_argument("--date", help="Single date (YYYY-MM-DD)")
        p.add_argument("--start", help="Start date (YYYY-MM-DD)")
        p.add_argument("--end", help="End date (YYYY-MM-DD)")
        p.add_argument("--root", default="", help="Root prefix for storage")
        p.add_argument("--force", action="store_true", help="Force republishing even if fingerprint matches")
        p.add_argument("--tier", type=int, choices=(1, 2), default=1,
                       help="1 = active-season partitions from 2026-09-01 forward, translated immediately; "
                            "2 = historical partitions by watermark (already-translated partitions are skipped)")
        p.add_argument("--master-root", default=None, help="Root path for outcome masters")

    # AN
    p_an = subparsers.add_parser("an", help="Translate Action Network odds")
    add_common_opts(p_an)
    p_an.add_argument("--league", required=True, help="League slug (e.g. nba, nfl, nhl, mlb)")
    p_an.set_defaults(func=cmd_an)

    # BP
    p_bp = subparsers.add_parser("bp", help="Translate BettingPros odds")
    add_common_opts(p_bp)
    p_bp.add_argument("--sport", required=True, help="Sport slug (e.g. nba, nfl, nhl)")
    p_bp.set_defaults(func=cmd_bp)

    # OddsAccum
    p_oa = subparsers.add_parser("oddsaccum", help="Translate OddsAccum raw JSON snapshots")
    add_common_opts(p_oa)
    p_oa.add_argument("--sport", required=True, help="Odds API sport key (e.g. basketball_nba, icehockey_nhl)")
    p_oa.set_defaults(func=cmd_oddsaccum)

    # Kalshi Futures
    p_kf = subparsers.add_parser("kalshi-futures", help="Translate Kalshi futures snapshot parquets")
    add_common_opts(p_kf)
    p_kf.set_defaults(func=cmd_kalshi_futures)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
