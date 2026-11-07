"""Garble test for the NBA game replay (build_team_game_stats_from_raw -> evaluate_walk_forward): scramble every box-score statistic
of the games on / after a cut-off day (team box AND the player box that backfills rebounds / turnovers) and require the engine's
predictions for every game on / before that day to be identical. See mlb_core.eval.garble.

    GCS_BUCKET=<bucket> python -m nba.game.leak_check --cutoffs 2024-12-01,2026-01-15

Nothing in serving reads this.
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, Iterable, List, Optional

import pandas as pd

from mlb_core.eval import garble as gb
from nba.game.evaluate import evaluate_walk_forward
from nba.game.features import build_team_game_stats_from_raw

ID_COLS = {"game_id", "team_abbr", "opp_abbr", "date", "season_year", "season_type", "is_home", "player_id", "player_name",
           "person_id", "team_id", "opp_id", "team_name", "tricode"}
PRED_COLS = ("pred_margin", "pred_total", "pred_p_home_ml", "kal_margin", "kal_total", "feat_margin", "feat_total",
             "kal_std_margin", "kal_std_total", "sd_margin", "sd_total", "n_prior_min")


def predictions(raw: pd.DataFrame, players: Optional[pd.DataFrame], warmup_games: int = 50) -> pd.DataFrame:
    games = build_team_game_stats_from_raw(raw, df_player_boxscores=players)
    rep = evaluate_walk_forward(games, warmup_games=warmup_games)
    return pd.DataFrame([{"game_id": r.game_id, "date": r.game_date[:10], **{c: getattr(r, c) for c in PRED_COLS}}
                         for r in rep.records])


def _stat_cols(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c not in ID_COLS and (pd.api.types.is_numeric_dtype(df[c]) or c == "played")]


def garble_inputs(raw: pd.DataFrame, players: Optional[pd.DataFrame], cutoff: str, seed: int = 0):
    games = build_team_game_stats_from_raw(raw, df_player_boxscores=players)
    gids = {g.game_id for g in games if g.game_date[:10] >= cutoff}
    r2 = gb.garble_columns(raw, _stat_cols(raw), raw["game_id"].astype(str).isin(gids).to_numpy(), seed)
    p2 = None
    if players is not None and len(players):
        p2 = gb.garble_columns(players, _stat_cols(players), players["game_id"].astype(str).isin(gids).to_numpy(), seed)
    return r2, p2


def run_check(raw: pd.DataFrame, players: Optional[pd.DataFrame], cutoffs: Iterable[str], seed: int = 0,
              warmup_games: int = 50) -> Dict[str, dict]:
    base = predictions(raw, players, warmup_games)
    out: Dict[str, dict] = {}
    for t in cutoffs:
        r2, p2 = garble_inputs(raw, players, t, seed)
        got = predictions(r2, p2, warmup_games)
        before = gb.prediction_diff(base, got, "game_id", PRED_COLS, "date", t)
        after = gb.prediction_diff(base, got, "game_id", PRED_COLS, "date", t, after=True)
        out[t] = gb.summarize(before, after, int((base["date"].astype(str) <= t).sum()))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cutoffs", required=True, help="comma-separated YYYY-MM-DD days")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    from nba.game.run_daily import load_historical_boxscores, load_player_boxscores
    res = run_check(load_historical_boxscores(), load_player_boxscores(), a.cutoffs.split(","), a.seed)
    print(json.dumps(res, indent=1))
    return 1 if any(v["leak"] or not v["power_ok"] for v in res.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
