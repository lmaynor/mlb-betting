"""Leakage-safe rolling features for the NHL SOG model.

Contract (pinned by tests/test_nhl_props_features.py): the feature vector of a row (player P, game G, date D) is a
function of P's appearances STRICTLY BEFORE G and of team-level results from games STRICTLY BEFORE G. Implemented with
`shift(1)` on every per-entity series before any rolling/ewm window, so a row can never see its own game or any later
one. Rows whose own outcome is unknown (upcoming-game placeholders: sog / toi_sec NaN) go through the SAME code path --
that is how the live scorer builds today's features, so train and serve cannot drift.

NO market-derived feature (line, price, implied total, consensus) is used anywhere: the model must be independent of the
market so that comparing the two is meaningful.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from nhl.props.gamelog import team_games

FEATURES = [
    "sog_l5", "sog_l10", "sog_l20", "sog_ewm10", "sog_per60_l20", "sog_per60_l60",
    "toi_l5", "toi_l10", "toi_ewm10",
    "att_l10", "att_per60_l20", "pp_att_l10",
    "team_sf_l10", "opp_sa_l10", "opp_sa_l30", "lg_sf_l30",
    "is_home", "rest_days", "opp_rest_days", "is_d", "is_c", "n_prior",
]
KEYS = ["player_id", "game_id", "date", "team", "opp"]


def _roll(s: pd.Series, by: pd.Series, w: int, how: str = "mean", min_periods: int = 1) -> pd.Series:
    """Rolling window over the PRIOR rows of each group (shift(1) first -> never the current row)."""
    sh = s.groupby(by).shift(1)
    return sh.groupby(by).transform(lambda x: getattr(x.rolling(w, min_periods=min_periods), how)())


def _ewm(s: pd.Series, by: pd.Series, halflife: float) -> pd.Series:
    sh = s.groupby(by).shift(1)
    return sh.groupby(by).transform(lambda x: x.ewm(halflife=halflife, min_periods=1).mean())


def build_features(gl: pd.DataFrame) -> pd.DataFrame:
    """Canonical game log (see gamelog.py; may include NaN-outcome placeholder rows) -> one feature row per game-log row."""
    gl = gl.sort_values(["date", "game_id", "player_id"]).reset_index(drop=True).copy()
    gl["dt"] = pd.to_datetime(gl["date"])
    pid = gl["player_id"]
    toi_min = gl["toi_sec"] / 60.0
    ordered = gl.assign(_p=pid).sort_values(["_p", "dt", "game_id"])      # per-player chronological order for rolling
    inv = ordered.index
    o_pid = ordered["player_id"]
    o_toi_min = ordered["toi_sec"] / 60.0

    out = pd.DataFrame(index=ordered.index)
    out["sog_l5"] = _roll(ordered["sog"], o_pid, 5)
    out["sog_l10"] = _roll(ordered["sog"], o_pid, 10)
    out["sog_l20"] = _roll(ordered["sog"], o_pid, 20)
    out["sog_ewm10"] = _ewm(ordered["sog"], o_pid, 10)
    for w, name in ((20, "sog_per60_l20"), (60, "sog_per60_l60")):
        s_sog = _roll(ordered["sog"], o_pid, w, "sum", min_periods=3)
        s_toi = _roll(ordered["toi_sec"], o_pid, w, "sum", min_periods=3)
        out[name] = s_sog / s_toi.where(s_toi > 0) * 3600.0
    out["toi_l5"] = _roll(o_toi_min, o_pid, 5)
    out["toi_l10"] = _roll(o_toi_min, o_pid, 10)
    out["toi_ewm10"] = _ewm(o_toi_min, o_pid, 10)
    out["att_l10"] = _roll(ordered["attempts"], o_pid, 10)
    a_sum = _roll(ordered["attempts"], o_pid, 20, "sum", min_periods=3)
    t_sum = _roll(ordered["toi_sec"].where(ordered["attempts"].notna()), o_pid, 20, "sum", min_periods=3)
    out["att_per60_l20"] = a_sum / t_sum.where(t_sum > 0) * 3600.0
    out["pp_att_l10"] = _roll(ordered["pp_attempts"], o_pid, 10)
    out["n_prior"] = ordered.groupby("player_id").cumcount().astype(float)
    out = out.reindex(gl.index)          # back to the date-sorted order of `gl`

    # ---- team-level: own offence, opposing defence, rest. Built from played rows, shifted per team.
    tg = team_games(gl)
    tg["dt"] = pd.to_datetime(tg["date"])
    tby = tg["team"]
    tg["team_sf_l10"] = _roll(tg["sf"], tby, 10)
    tg["opp_sa_l10_own"] = _roll(tg["sa"], tby, 10)        # this team's shots-allowed form (used when it is the OPPONENT)
    tg["opp_sa_l30_own"] = _roll(tg["sa"], tby, 30)
    prev = tg.groupby("team")["dt"].shift(1)
    tg["rest_days"] = ((tg["dt"] - prev).dt.days).clip(upper=7).fillna(7)
    # league-wide shot environment (era drift: ~31 team SOG/game in 2019-20 vs ~28 in 2025-26): mean team shots-for over the
    # previous 30 distinct game DAYS, strictly before this game's date.
    daily = tg.dropna(subset=["sf"]).groupby("dt")["sf"].mean().sort_index()
    roll = daily.rolling(30, min_periods=1).mean()                    # window ENDING on each data date (inclusive)
    pos = daily.index.searchsorted(tg["dt"].to_numpy(), side="left") - 1       # last data date STRICTLY before this game
    tg["lg_sf_l30"] = np.where(pos >= 0, roll.to_numpy()[np.maximum(pos, 0)], np.nan) if len(daily) else np.nan
    own = tg[["game_id", "team", "team_sf_l10", "rest_days", "lg_sf_l30"]]
    opp = tg[["game_id", "team", "opp_sa_l10_own", "opp_sa_l30_own", "rest_days"]].rename(
        columns={"team": "opp", "opp_sa_l10_own": "opp_sa_l10", "opp_sa_l30_own": "opp_sa_l30",
                 "rest_days": "opp_rest_days"})
    feat = gl[KEYS + ["is_home", "position", "sog", "toi_sec"]].join(out)
    feat = feat.merge(own, on=["game_id", "team"], how="left").merge(opp, on=["game_id", "opp"], how="left")
    feat["is_d"] = (feat["position"] == "D").astype(float)
    feat["is_c"] = (feat["position"] == "C").astype(float)
    feat["is_home"] = feat["is_home"].astype(float)
    return feat[KEYS + FEATURES + ["position", "sog", "toi_sec"]].reset_index(drop=True)
