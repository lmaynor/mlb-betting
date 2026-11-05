"""Prior-only rolling features from the skater TOI-split table (nhl.data.toi_splits): even-strength / power-play / penalty-kill time on
ice and first-vs-second power-play unit deployment.

LEAKAGE CONTRACT (pinned by tests/test_nhl_toi_feat.py): every feature of a row (player P, game G, date D) is a function of P's rows in
the split table STRICTLY BEFORE G (every rolling window is `shift(1)` first). The split table's own values for G, and for any later
game, never enter G's row: scrambling the table's values on/after date t leaves every feature on dates <= t unchanged. Rows with no
split (a game outside the table, an upcoming-game placeholder) go through the same code path with NaN, and a NaN is simply not part
of any window: it is never filled with 0.

OFF BY DEFAULT: nothing imports this module on a serving path. Whether the features help is a measured question
(`python -m nhl.props.eval_toi_splits`; docs/analysis/nhl_toi_splits_*.md) and the serving engine is untouched.

Features (minutes unless noted; a window is the player's previous N appearances in `base`, and an appearance with no split is a NaN the
mean skips):
    toi_ev_l10, toi_pp_l10, toi_pk_l10   mean minutes per game at even strength / on the power play / on the penalty kill
    pp_frac_l20                          sum PP seconds / sum total seconds over the previous 20 (a share of his own ice time)
    pp_share_l10                         mean share of his TEAM's power-play time he was on the ice for
    pp1_share_l10                        share of previous games (where his team had >= MIN_TEAM_PP_S of power play) he was a PP1 guess
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nhl.props.xgfeat import prior_roll

TOI_FEATURES = ["toi_ev_l10", "toi_pp_l10", "toi_pk_l10", "pp_frac_l20", "pp_share_l10", "pp1_share_l10"]
MIN_TEAM_PP_S = 30          # a team power play shorter than this in a game says nothing about unit deployment


def toi_features(base: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """`base` = one row per (player_id, game_id) with `date` (any frame carrying those columns; upcoming-game placeholder rows allowed);
    `splits` = the toi_splits table (game_id, player_id, toi_total, toi_ev, toi_pp, toi_pk, team_pp_s, pp_share, pp_unit_guess).
    -> frame (game_id, player_id) + TOI_FEATURES, aligned to `base` rows (NaN where the player has no earlier split)."""
    cols = ["game_id", "player_id", "toi_total", "toi_ev", "toi_pp", "toi_pk", "team_pp_s", "pp_share", "pp_unit_guess"]
    sp = splits[[c for c in cols if c in splits.columns]].copy()
    sp["game_id"], sp["player_id"] = sp["game_id"].astype("int64"), sp["player_id"].astype("int64")
    b = base[["game_id", "player_id", "date"]].copy()
    b["game_id"], b["player_id"] = b["game_id"].astype("int64"), b["player_id"].astype("int64")
    b["date"] = b["date"].astype(str)
    d = b.drop_duplicates(["game_id", "player_id"]).merge(sp.drop_duplicates(["game_id", "player_id"]), on=["game_id", "player_id"], how="left")
    d = d.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    for k in ("ev", "pp", "pk"):
        d[f"min_{k}"] = d[f"toi_{k}"] / 60.0
    d["pp1"] = np.where(d["team_pp_s"] >= MIN_TEAM_PP_S, (d["pp_unit_guess"] == 1).astype(float), np.nan)
    d.loc[d["pp_unit_guess"].isna(), "pp1"] = np.nan
    d["pp_share_ok"] = d["pp_share"].where(d["team_pp_s"] >= MIN_TEAM_PP_S)
    out = d[["game_id", "player_id"]].copy()
    out["toi_ev_l10"] = prior_roll(d, "player_id", "min_ev", 10)
    out["toi_pp_l10"] = prior_roll(d, "player_id", "min_pp", 10)
    out["toi_pk_l10"] = prior_roll(d, "player_id", "min_pk", 10)
    pp_sum = prior_roll(d, "player_id", "toi_pp", 20, "sum", min_periods=3)
    tot_sum = prior_roll(d, "player_id", "toi_total", 20, "sum", min_periods=3)
    out["pp_frac_l20"] = pp_sum / tot_sum.where(tot_sum > 0)
    out["pp_share_l10"] = prior_roll(d, "player_id", "pp_share_ok", 10)
    out["pp1_share_l10"] = prior_roll(d, "player_id", "pp1", 10)
    return base[["game_id", "player_id"]].astype("int64").merge(out, on=["game_id", "player_id"], how="left")
