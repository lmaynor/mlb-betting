"""NHL game engine vs the market: NOT RUNNABLE on historical data, by design, until closing odds exist.

There is no historical NHL moneyline / puck line / total source in this repo or the bucket (checked 2026-11-04):
Odds/bettingpros_nhl/ holds player props only, ParlayAPI's game-line archive is ~168 h deep with "nearly empty" NHL
(docs/solutions/integration-issues/parlayapi-historical-archive-window.md), and BettingPros never quotes Pinnacle.
Scoring the engine against its OWN outcomes (nhl.game.evaluate) measures calibration, not edge.

What works today: score any odds table you supply, e.g. one built from the banked forward feed
(OddsAccum/icehockey_nhl/raw/{day}/game_lines_{HHMM}.json) once enough days exist. Required columns are the ones
mlb_core.eval.market_scoring documents (date, p_model, p_mkt, y, optional prices):

    python -m nhl.game.market_eval --events events.csv --out report.md
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

import pandas as pd

from mlb_core.eval import market_scoring as ms

MIN_EVENTS = 200


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score an NHL events table against the market")
    ap.add_argument("--events", default=None, help="CSV with date,p_model,p_mkt,y[,dec_* prices]")
    ap.add_argument("--market", default="nhl game line")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if not args.events:
        print("No NHL historical game-line odds exist to score against (see this module's docstring). Bank the forward "
              "feed and pass --events.", file=sys.stderr)
        return 2
    ev = pd.read_csv(args.events)
    if len(ev) < MIN_EVENTS:
        print(f"Only {len(ev)} events; need >= {MIN_EVENTS} before any verdict means anything.", file=sys.stderr)
        return 2
    text = ms.render_markdown(ms.score_market(ev, args.market))
    print(text)
    if args.out:
        open(args.out, "w").write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
