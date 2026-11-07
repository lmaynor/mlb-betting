"""NBA adapter for the confidence-gated evaluation (shared machinery: mlb_core.eval.gating / gated_run).

Reads the per-game replay records written by `python -m nba.game.market_eval --records-pkl`, turns them into event frames with
`nba.game.market_eval.to_events`, and runs the pre-registered two-stage protocol for the NBA alone.

    GCS_BUCKET=<bucket> python -m nba.game.market_eval --local-dir <quotes dir> --records-pkl nba_rec.pkl
    python -m nba.game.gated_eval dev  --records nba_rec.pkl --out-dir docs/analysis/nba_confidence_gated_eval_2026-11-05
    python -m nba.game.gated_eval held --records nba_rec.pkl --out-dir docs/analysis/nba_confidence_gated_eval_2026-11-05

Splits (fixed before any outcome was scored; season = start year): dev 2021-22 .. 2023-24, held-out 2024-25 and 2025-26. PHASE = both teams
have played at least 20 games this season. UNVALIDATED research tool; nothing in serving reads this.
"""
from __future__ import annotations

import argparse
from typing import Dict, List, Optional

import pandas as pd

from mlb_core.eval import gated_run
from nba.game import market_eval as nba_market_eval

NBA_DEV_SEASONS = [2021, 2022, 2023]
NBA_HELD_SEASONS = [2024, 2025]
NBA_PHASE_MIN = 20


def nba_events(records_pkl: str) -> Dict[str, pd.DataFrame]:
    return nba_market_eval.to_events(pd.read_pickle(records_pkl))


def nba_source(records_pkl: str) -> gated_run.GatedSource:
    return gated_run.GatedSource(label="nba", events_fn=lambda: nba_events(records_pkl), records_path=records_pkl,
                                 dev_seasons=NBA_DEV_SEASONS, held_seasons=NBA_HELD_SEASONS, phase_min=NBA_PHASE_MIN)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["dev", "held"])
    ap.add_argument("--records", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--allow-rescore", action="store_true")
    ap.add_argument("--B", type=int, default=4000)
    a = ap.parse_args(argv)
    nba_src = nba_source(a.records)
    if a.stage == "dev":
        gated_run.run_dev(nba_src, a.out_dir)
    else:
        gated_run.run_held(nba_src, a.out_dir, a.allow_rescore, a.B)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
