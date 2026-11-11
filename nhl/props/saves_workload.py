"""Shift-split workload hierarchy for the NHL saves model (Argon Pack 4 / model-improvement D1).

THE HIERARCHY (Pack 4 D1): projected saves factor as

    sum over situations s in {EV, PK, PP-for-the-goalie} of
        projected situation TOI  x  opponent situation shot-attempt pace  x  shot conversion
        x  the goalie's (empirical-Bayes shrunken) save rate

where the goalie's PK is the OPPONENT's power play and the goalie's PP is the opponent's penalty kill.
Situation TOI and attempt pace come from the stats-API reports route proven by PR #216
(nhl.data.sog_backfill: summary / timeonice / realtime / powerplay reports), banked per game date at
GCS `NHL/sog_backfill/{season}/{date}.jsonl` (EV/PP/SH TOI splits + shot attempts, 2023-24 through
2025-26, 150,891 rows). This module does NOT use shift charts (nhl.data.toi_splits is the older,
one-request-per-game route the design superseded for this purpose) and does NOT use MoneyPuck
(barred by the Oct 16, 2026 licensing review).

LEAKAGE CONTRACT (pinned by tests/test_nhl_saves_workload.py): every feature of a row (goalie, game,
date D) is a function of team games dated STRICTLY BEFORE D (every window is prior-only, like
nhl.props.xgfeat / nhl.props.toi_feat). Scrambling team-game values on/after date t leaves every
feature on dates <= t unchanged.

WHAT THE BANKED DATA DOES NOT HAVE (gaps, also flagged in the PR body):
  * The backfill is SKATER rows only. There is no goalie TOI by situation and no shots faced / saves
    by situation, so the hierarchy prices every situation at the goalie's OVERALL EB save rate
    (`g_sv_l60` from the saves frame when present, else the league prior) instead of situation-
    specific save rates.
  * Attempts are split only as PP attempts vs total attempts. EV and SH attempts are not separable
    in the banked reports, so the EV segment and the goalie's-PP segment share one non-PP attempt
    rate (over EV+SH time). The SH rate is in reality far below the EV rate, so the PP segment is an
    upper-bound proxy; it is also the smallest segment (~5 min), which bounds the error.
  * Team situation seconds are estimated as summed skater TOI / SKATERS_ON_ICE (5): the reports give
    skater TOI, not team state seconds, and 4v4 / 3v3 / pulled-goalie states make the true divisor
    differ slightly from 5. Rates are per 60 minutes of that estimated team time.

OFFLINE / FEATURE-BUILD ONLY: nothing imports this module on a serving path. Whether the features
help is a measured question for the next saves refit (the trained bundle in nhl.props.trained keeps
its current feature list until a refit proves these belong in it; heavy refits do not run on GCP).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from nhl.data import sog_backfill as SB

LG_SV = 0.905                    # league save% prior, mirrors nhl.props.xgfeat.LG_SV
SKATERS_ON_ICE = 5.0             # summed skater TOI / this ~= team situation seconds (see docstring)
CONV_PRIOR = 0.50                # league shots-on-goal per shot attempt, shrinkage target for conversion
CONV_PRIOR_ATTEMPTS = 200.0      # prior strength, in attempts
WINDOW_GAMES = 10                # prior team games per rolling feature

WORKLOAD_FEATURES = [
    "wl_opp_att_pp_l10", "wl_opp_att_nonpp_l10", "wl_opp_pp_toi_l10", "wl_opp_ev_toi_l10",
    "wl_opp_sh_toi_l10", "wl_own_sh_toi_l10", "wl_opp_sog_conv_l10",
    "wl_proj_saves_ev", "wl_proj_saves_pk", "wl_proj_saves_pp", "wl_proj_saves_hier",
    "wl_rest_x_starts7d",
]

TEAM_GAME_COLS = ["date", "game_id", "team", "opponent", "n_skaters",
                  "toi_ev_s", "toi_pp_s", "toi_sh_s", "att_total", "att_pp", "att_nonpp", "shots",
                  "att_rate_pp_per60", "att_rate_nonpp_per60"]


# ---------------------------------------------------------------------------------------------------------------------
# banked data in
# ---------------------------------------------------------------------------------------------------------------------
def parse_jsonl(data) -> list:
    """JSONL bytes / text (one object per line, the sog_backfill file shape) -> list of row dicts."""
    text = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def banked_keys(seasons) -> list:
    """Every GCS key the backfill may have written for `seasons` (season labels like '2024-25'):
    one per date in each season window. Existence is the caller's check (a missing / empty file is
    an off day or an unplayed date, not an error)."""
    keys = []
    for season in seasons:
        _, label, _ = SB.parse_season(season)
        start, end = SB.season_window(season)
        keys.extend(SB.gcs_key(SB.GCS_PREFIX, label, d) for d in SB.iter_dates(start, end))
    return keys


def load_banked_rows(read, seasons) -> list:
    """All banked skater rows for `seasons` via `read(key) -> bytes | None` (mlb_core.storage.read_bytes
    in production, a dict lookup in tests). Offline feature-build helper: one read per date."""
    out = []
    for key in banked_keys(seasons):
        try:
            raw = read(key)
        except Exception:                                                               # noqa: BLE001
            raw = None
        if raw:
            out.extend(parse_jsonl(raw))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# skater rows -> team games
# ---------------------------------------------------------------------------------------------------------------------
def team_game_table(rows) -> pd.DataFrame:
    """Banked skater rows -> one row per (date, game_id, team) with situation TOI (estimated team
    seconds), attempt totals and per-60 attempt rates by situation. Rows missing TOI splits keep the
    row with NaN rates rather than dropping a played game."""
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if not len(df):
        return pd.DataFrame(columns=TEAM_GAME_COLS)
    for c in ("toi_ev_sec", "toi_pp_sec", "toi_sh_sec", "attempts_total", "pp_attempts", "shots"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    g = df.groupby(["date", "game_id", "team", "opponent"], sort=True).agg(
        n_skaters=("player_id", "nunique"),
        toi_ev_s=("toi_ev_sec", "sum"), toi_pp_s=("toi_pp_sec", "sum"), toi_sh_s=("toi_sh_sec", "sum"),
        att_total=("attempts_total", "sum"), att_pp=("pp_attempts", "sum"),
        shots=("shots", "sum")).reset_index()
    for c in ("toi_ev_s", "toi_pp_s", "toi_sh_s"):
        g[c] = g[c] / SKATERS_ON_ICE
    g["att_nonpp"] = g["att_total"] - g["att_pp"]
    pp_hours = g["toi_pp_s"] / 3600.0
    nonpp_hours = (g["toi_ev_s"] + g["toi_sh_s"]) / 3600.0
    g["att_rate_pp_per60"] = (g["att_pp"] / pp_hours.where(pp_hours > 0)).where(pp_hours > 0)
    g["att_rate_nonpp_per60"] = (g["att_nonpp"] / nonpp_hours.where(nonpp_hours > 0)).where(nonpp_hours > 0)
    return g[TEAM_GAME_COLS].sort_values(["team", "date", "game_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------------------
# prior-only rolling helpers
# ---------------------------------------------------------------------------------------------------------------------
def _prior_window(dates: np.ndarray, values: np.ndarray, query: str, window: int) -> np.ndarray:
    """Values of the last `window` team games dated strictly before `query` (NaNs preserved)."""
    idx = int(np.searchsorted(dates, query, side="left"))
    return values[max(0, idx - window):idx]


def _mean_prior(dates, values, query, window=WINDOW_GAMES, min_games=3):
    w = _prior_window(dates, np.asarray(values, dtype=float), query, window)
    w = w[np.isfinite(w)]
    return float(np.mean(w)) if len(w) >= min_games else float("nan")


def _shrunk_conversion(dates, shots, attempts, query, window=WINDOW_GAMES, min_games=3):
    """EB-shrunken shots/attempts over the prior window: (shots + prior*strength) / (attempts + strength)."""
    ws = _prior_window(dates, np.asarray(shots, dtype=float), query, window)
    wa = _prior_window(dates, np.asarray(attempts, dtype=float), query, window)
    ok = np.isfinite(ws) & np.isfinite(wa)
    if int(ok.sum()) < min_games:
        return float("nan")
    return float((np.nansum(ws) + CONV_PRIOR * CONV_PRIOR_ATTEMPTS) / (np.nansum(wa) + CONV_PRIOR_ATTEMPTS))


def project_saves_hierarchy(*, ev_min: float, pk_min: float, pp_min: float,
                            att_rate_pp_per60: float, att_rate_nonpp_per60: float,
                            sog_conv: float, sv_pct: float) -> dict:
    """The hierarchy itself, pure: projected TOI by goalie situation (minutes) x opponent attempt pace
    (per 60) x conversion x save rate -> per-situation projected saves and their sum.

    Situation mapping: EV = opponent at even strength; PK = opponent on the power play (its PP attempt
    rate); PP = opponent short-handed (priced at the non-PP rate, an upper-bound proxy -- see module
    docstring). Any NaN input makes every component NaN: a hierarchy with a missing leg is not a number.
    """
    vals = (ev_min, pk_min, pp_min, att_rate_pp_per60, att_rate_nonpp_per60, sog_conv, sv_pct)
    if any(v is None or not np.isfinite(v) for v in vals):
        return {"ev": float("nan"), "pk": float("nan"), "pp": float("nan"), "total": float("nan")}
    ev = ev_min / 60.0 * att_rate_nonpp_per60 * sog_conv * sv_pct
    pk = pk_min / 60.0 * att_rate_pp_per60 * sog_conv * sv_pct
    pp = pp_min / 60.0 * att_rate_nonpp_per60 * sog_conv * sv_pct
    return {"ev": float(ev), "pk": float(pk), "pp": float(pp), "total": float(ev + pk + pp)}


# ---------------------------------------------------------------------------------------------------------------------
# features for saves rows
# ---------------------------------------------------------------------------------------------------------------------
def workload_features(frame: pd.DataFrame, team_games: pd.DataFrame) -> pd.DataFrame:
    """Saves rows -> the same rows + WORKLOAD_FEATURES.

    `frame` = one row per goalie appearance / upcoming placeholder with at least
    (game_id, player_id, date, team, opp); `g_sv_l60`, `g_rest`, `g_starts_7d` are used when present
    (the nhl.props.xgfeat saves_features names). `team_games` = team_game_table(banked rows).
    Opponent features describe the team the goalie faces; own-team SH time is the goalie's own PK
    exposure as his skaters record it (it should track the opponent's PP time; both are carried so a
    data problem in one does not silently become the other)."""
    out = frame.copy().reset_index(drop=True)
    for c in WORKLOAD_FEATURES:
        out[c] = np.nan
    if not len(team_games):
        return out
    tg = team_games.copy()
    tg["date"] = tg["date"].astype(str)
    by_team = {team: (g["date"].to_numpy(), g) for team, g in tg.groupby("team", sort=False)}
    sv = out["g_sv_l60"].to_numpy(dtype=float) if "g_sv_l60" in out.columns else np.full(len(out), np.nan)
    for i, r in enumerate(out.itertuples(index=False)):
        day = str(r.date)[:10]
        opp = by_team.get(r.opp)
        if opp is not None:
            dates, g = opp
            out.at[i, "wl_opp_att_pp_l10"] = _mean_prior(dates, g["att_rate_pp_per60"].to_numpy(), day)
            out.at[i, "wl_opp_att_nonpp_l10"] = _mean_prior(dates, g["att_rate_nonpp_per60"].to_numpy(), day)
            out.at[i, "wl_opp_pp_toi_l10"] = _mean_prior(dates, g["toi_pp_s"].to_numpy(), day)
            out.at[i, "wl_opp_ev_toi_l10"] = _mean_prior(dates, g["toi_ev_s"].to_numpy(), day)
            out.at[i, "wl_opp_sh_toi_l10"] = _mean_prior(dates, g["toi_sh_s"].to_numpy(), day)
            out.at[i, "wl_opp_sog_conv_l10"] = _shrunk_conversion(
                dates, g["shots"].to_numpy(), g["att_total"].to_numpy(), day)
        own = by_team.get(r.team)
        if own is not None:
            dates, g = own
            out.at[i, "wl_own_sh_toi_l10"] = _mean_prior(dates, g["toi_sh_s"].to_numpy(), day)
        rate = sv[i] if np.isfinite(sv[i]) else LG_SV
        proj = project_saves_hierarchy(
            ev_min=out.at[i, "wl_opp_ev_toi_l10"] / 60.0,
            pk_min=out.at[i, "wl_opp_pp_toi_l10"] / 60.0,
            pp_min=out.at[i, "wl_opp_sh_toi_l10"] / 60.0,
            att_rate_pp_per60=out.at[i, "wl_opp_att_pp_l10"],
            att_rate_nonpp_per60=out.at[i, "wl_opp_att_nonpp_l10"],
            sog_conv=out.at[i, "wl_opp_sog_conv_l10"], sv_pct=rate)
        out.at[i, "wl_proj_saves_ev"] = proj["ev"]
        out.at[i, "wl_proj_saves_pk"] = proj["pk"]
        out.at[i, "wl_proj_saves_pp"] = proj["pp"]
        out.at[i, "wl_proj_saves_hier"] = proj["total"]
    if "g_rest" in out.columns and "g_starts_7d" in out.columns:
        # rest-days interaction (Pack 4 D1): a goalie's workload risk compounds when short rest meets
        # a heavy recent start load; the product is the interaction the tree would otherwise have to find.
        out["wl_rest_x_starts7d"] = (pd.to_numeric(out["g_rest"], errors="coerce")
                                     * pd.to_numeric(out["g_starts_7d"], errors="coerce"))
    return out
