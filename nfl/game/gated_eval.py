"""NFL adapter for the confidence-gated evaluation (shared machinery: mlb_core.eval.gating / gated_run).

Reads the per-game replay records written by `python -m nfl.game.market_eval --records-csv`, turns them into event frames with
`nfl.game.market_eval.to_events`, and runs the pre-registered two-stage protocol for the NFL alone.

    GCS_BUCKET=<bucket> python -m nfl.game.market_eval --seasons 2021-2025 --records-csv nfl_rec.csv
    python -m nfl.game.gated_eval dev  --records nfl_rec.csv --out-dir docs/analysis/nfl_confidence_gated_eval_2026-11-05
    python -m nfl.game.gated_eval held --records nfl_rec.csv --out-dir docs/analysis/nfl_confidence_gated_eval_2026-11-05

Splits (fixed before any outcome was scored): dev 2021-2023, held-out 2024-2025. PHASE = NFL week >= 6 (at least 5 games played).
UNVALIDATED research tool; nothing in serving reads this.
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional

import pandas as pd

from mlb_core.eval import gated_run
from nfl.game import market_eval as nfl_market_eval

NFL_DEV_SEASONS = [2021, 2022, 2023]
NFL_HELD_SEASONS = [2024, 2025]
NFL_PHASE_MIN = 6


def nfl_events(records_csv: str) -> Dict[str, pd.DataFrame]:
    return nfl_market_eval.to_events(pd.read_csv(records_csv))


def nfl_source(records_csv: str) -> gated_run.GatedSource:
    return gated_run.GatedSource(label="nfl", events_fn=lambda: nfl_events(records_csv), records_path=records_csv,
                                 dev_seasons=NFL_DEV_SEASONS, held_seasons=NFL_HELD_SEASONS, phase_min=NFL_PHASE_MIN)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["dev", "held"])
    ap.add_argument("--records", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-rescore", action="store_true")
    ap.add_argument("--B", type=int, default=4000)
    a = ap.parse_args(argv)
    nfl_src = nfl_source(a.records)
    if a.stage == "dev":
        gated_run.run_dev(nfl_src, a.out_dir)
    else:
        gated_run.run_held(nfl_src, a.out_dir, a.allow_rescore, a.B)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
