#!/usr/bin/env python3
"""Bank MOD-04 Cluster (d) shadow predictions from local slate/feature data.

CLI:
    python scripts/mod04d_bank_shadow.py --date YYYY-MM-DD --family {nba_points,nba_threes,nhl_goals,nhl_points} --out-dir DIR

If no real local slate/feature data is available, exits with code 2 and a clear message
(never fabricates rows).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mod04d import nba_points, nba_threes, nhl_scoring  # noqa: E402
from mod04d.features import build_pregame_features  # noqa: E402
from mod04d.shadow import FAMILY_ENGINE_MAP, build_shadow_rows, write_shadow_file  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Bank MOD-04 Cluster (d) shadow predictions")
    ap.add_argument("--date", required=True, help="Target game date (YYYY-MM-DD)")
    ap.add_argument("--family", required=True, choices=tuple(FAMILY_ENGINE_MAP), help="Prop family")
    ap.add_argument("--out-dir", required=True, help="Output directory for shadow JSON")
    ap.add_argument("--slate-path", default=None, help="Optional local parquet/csv pregame slate features")
    ap.add_argument("--boxscores-path", default=None, help="Optional local parquet/csv historical boxscores")
    ap.add_argument("--model-dir", default=None, help="Optional local saved NHL shadow bundle dir")
    ap.add_argument("--cutoff-utc", default=None, help="Pre-game snapshot cutoff ISO timestamp")
    ap.add_argument("--blend-c", type=float, default=0.0, help="U4 OOF-fit blend coefficient c in [0,1]")
    return ap.parse_args(argv)


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported table format: {path}")


def _discover_local_slate(date: str, family: str, slate_path: str | None) -> Path | None:
    if slate_path:
        p = Path(slate_path)
        return p if p.exists() else None
    candidates = [
        REPO_ROOT / "data" / "mod04d" / f"{family}_slate_{date}.parquet",
        REPO_ROOT / "data" / "mod04d" / f"{family}_slate_{date}.csv",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    slate_file = _discover_local_slate(args.date, args.family, args.slate_path)
    box_file = Path(args.boxscores_path) if args.boxscores_path else None

    if slate_file is None and (box_file is None or not box_file.exists()):
        print(
            f"ERROR: No local slate or history data found for family={args.family} date={args.date}. "
            "Supply --slate-path (or --boxscores-path); refusing to fabricate shadow rows.",
            file=sys.stderr,
        )
        return 2

    cutoff = args.cutoff_utc or f"{args.date}T23:00:00Z"
    if slate_file is not None:
        slate_df = _read_table(slate_file)
    else:
        assert box_file is not None
        box_df = _read_table(box_file)
        slate_df = build_pregame_features(args.date, cutoff, box_df)

    if len(slate_df) == 0:
        print(
            f"ERROR: Local slate for family={args.family} date={args.date} is empty; exiting.",
            file=sys.stderr,
        )
        return 2

    market_probs = slate_df["p_market"].tolist() if "p_market" in slate_df.columns else None
    lines = slate_df["line"].tolist() if "line" in slate_df.columns else None
    feat_df = slate_df.drop(columns=[c for c in ("line", "odds", "p_market") if c in slate_df.columns])

    if args.family.startswith("nhl_"):
        if not args.model_dir or not Path(args.model_dir).exists():
            print(
                f"ERROR: Local --model-dir required for {args.family} when banking from local slate.",
                file=sys.stderr,
            )
            return 2
        mdir = Path(args.model_dir)
        model = nhl_scoring.load_bundle(lambda k: (mdir / k).read_bytes(), prefix="")
    elif box_file is not None and box_file.exists():
        box_df = _read_table(box_file)
        if args.family == "nba_points":
            model = nba_points.fit(box_df)
        else:
            model = nba_threes.fit_threes(box_df)
    else:
        print(
            f"ERROR: Local --boxscores-path required to fit shadow {args.family} model from local data.",
            file=sys.stderr,
        )
        return 2

    rows = build_shadow_rows(
        family=args.family,
        slate_features=feat_df,
        model=model,
        market_probs=market_probs,
        lines=lines,
        c=args.blend_c,
        date=args.date,
    )
    out_file = Path(args.out_dir) / f"{args.family}_{args.date}_shadow.json"
    write_shadow_file(rows, out_file)
    print(f"Wrote {len(rows)} shadow rows to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
