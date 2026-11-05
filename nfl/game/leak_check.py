"""Garble test for the NFL game replay (nfl.game.market_eval.replay): scramble every post-kickoff field of the games on / after a
cut-off day -- all play-by-play columns except identifiers, and the schedule's final scores -- and require the engine's predictions
for every game on / before that day to be identical. See mlb_core.eval.garble.

    GCS_BUCKET=<bucket> python -m nfl.game.leak_check --seasons 2024-2025 --cutoffs 2024-10-20,2025-11-02

Nothing in serving reads this.
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, Iterable, List, Optional

import pandas as pd

from mlb_core.eval import garble as gb
from nfl.game import market_eval as me

PBP_KEEP = {"game_id", "season", "week", "game_date", "home_team", "away_team", "season_type", "game_type"}
SCHED_POST = ("home_score", "away_score", "result", "total", "overtime")
PRED_COLS = ("mu_margin", "mu_total", "p_home_ml", "p_home_cover", "p_away_cover", "p_over", "p_under", "kal_margin",
             "kal_total", "feat_margin", "kal_std_margin", "kal_std_total", "n_prior_min")


def garble_inputs(pbp: pd.DataFrame, sched: pd.DataFrame, cutoff: str, seed: int = 0):
    """(pbp, sched) with every post-kickoff field of games on / after `cutoff` scrambled."""
    sd = sched["gameday"].astype(str).str[:10]
    gids = set(sched.loc[sd >= cutoff, "game_id"])
    p2 = gb.garble_columns(pbp, [c for c in pbp.columns if c not in PBP_KEEP], pbp["game_id"].isin(gids).to_numpy(), seed)
    s2 = gb.garble_columns(sched, [c for c in SCHED_POST if c in sched.columns], sched["game_id"].isin(gids).to_numpy(), seed)
    return p2, s2


def run_check(pbp: pd.DataFrame, sched: pd.DataFrame, test_seasons: List[int], cutoffs: Iterable[str], seed: int = 0
              ) -> Dict[str, dict]:
    base = me.replay(test_seasons, pbp=pbp, sched=sched)
    out: Dict[str, dict] = {}
    for t in cutoffs:
        p2, s2 = garble_inputs(pbp, sched, t, seed)
        got = me.replay(test_seasons, pbp=p2, sched=s2)
        before = gb.prediction_diff(base, got, "game_id", PRED_COLS, "date", t)
        after = gb.prediction_diff(base, got, "game_id", PRED_COLS, "date", t, after=True)
        out[t] = gb.summarize(before, after, int((base["date"].astype(str) <= t).sum()))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seasons", default="2024-2025")
    ap.add_argument("--cutoffs", required=True, help="comma-separated YYYY-MM-DD days")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    lo, _, hi = a.seasons.partition("-")
    seasons = list(range(int(lo), int(hi or lo) + 1))
    from nfl.data.read import load as nfl_load
    all_s = list(range(me.WARM_FROM, max(seasons) + 1))
    pbp = pd.concat([nfl_load("pbp", seasons=[s]) for s in all_s], ignore_index=True)
    sched = nfl_load("schedules", seasons=all_s)
    res = run_check(pbp, sched, seasons, a.cutoffs.split(","), a.seed)
    print(json.dumps(res, indent=1))
    return 1 if any(v["leak"] or not v["power_ok"] for v in res.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
