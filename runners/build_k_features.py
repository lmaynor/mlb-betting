"""
runners/build_k_features.py — K Pro v1 nightly feature builder.

Ports K_Pro_v1.ipynb Sections 1 (Statcast load), 4 (pitcher features),
5 (lineup features), and 6 (feature join) against mlb_core infra. Writes
the joined K_Pro_System/data/model_features.csv to GCS for the runner
(scoring) and the retrain job (training) to consume.

Output GCS keys (matches K_Pro_System/config_k.py):
  - K_Pro_System/data/pitcher_k_features.csv  (per starter, per game; historical)
  - K_Pro_System/data/lineup_k_features.csv   (today's slate only)
  - K_Pro_System/data/model_features.csv      (the union — used by both retrain
                                                and runner)

Differences from the notebook:
  - Section 3 (Selenium DK scraper) is GONE. Odds come from SGO via
    runners.snapshot_odds + run_k.py.
  - Section 5's MLB Stats API lineup pull is replaced by
    mlb_core.data.lineups.get_today_schedule() (the notebook crashed on
    KeyError: 'abbreviation'; our helper handles that schema).
  - Dome list comes from mlb_core.data.weather.STADIUMS (the notebook's
    inline DOME_TEAMS set is wrong — flags LAD/ATL/TEX as domes).
  - All paths flow through mlb_core.storage.

Entrypoint: build_features(cfg) — called by main.py for {"system": "K"}.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Section 1 — Statcast load ────────────────────────────────────────────────

def _load_statcast(cfg: dict) -> pd.DataFrame:
    """Load statcast_master.csv (full pitch-level frame, 2021+) from GCS.

    Filters to cfg['season_start']+. bat_speed is dropped — it's 2024+ only
    and would break walk-forward CV (notebook Section 1 note).
    """
    from mlb_core.storage import read_csv
    df = read_csv("Statcast/statcast_master.csv", low_memory=False)

    numeric_cols = [
        "release_speed", "pfx_x", "pfx_z", "balls", "strikes",
        "outs_when_up", "spin_axis", "release_extension", "effective_speed",
        "release_pos_x", "release_pos_z",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "bat_speed" in df.columns:
        df = df.drop(columns=["bat_speed"])

    df["game_date"] = pd.to_datetime(df["game_date"], errors="coerce")
    df = df[df["game_date"].dt.year >= cfg["season_start"]].copy()

    logger.info(f"K build: statcast {len(df):,} rows | "
                f"{df['game_date'].min().date()} → {df['game_date'].max().date()} | "
                f"{df['pitcher'].nunique():,} pitchers | "
                f"{df['game_pk'].nunique():,} games")
    return df


# ── Section 4 — pitcher feature aggregation ──────────────────────────────────

def _identify_starters(sc: pd.DataFrame) -> pd.DataFrame:
    """Starter = pitcher with most batters faced (PA-ending events) per game."""
    pa = sc[sc["events"].notna()].copy()
    bf = pa.groupby(["game_pk", "pitcher"]).size().reset_index(name="bf")
    idx = bf.groupby("game_pk")["bf"].idxmax()
    starters = bf.loc[idx].copy()

    gdates = sc.groupby("game_pk")["game_date"].first().reset_index()
    starters = starters.merge(gdates, on="game_pk", how="left")

    starter_ks = (
        pa[pa["events"] == "strikeout"]
          .groupby(["game_pk", "pitcher"])
          .size()
          .reset_index(name="starter_ks")
    )
    starters = starters.merge(starter_ks, on=["game_pk", "pitcher"], how="left")
    starters["starter_ks"] = starters["starter_ks"].fillna(0).astype(int)

    if "outs_when_up" in sc.columns:
        ip_df = (
            sc.groupby(["game_pk", "pitcher"])["outs_when_up"].max().reset_index()
        )
        ip_df["starter_ip"] = (ip_df["outs_when_up"] + 1) / 3.0
        starters = starters.merge(
            ip_df[["game_pk", "pitcher", "starter_ip"]],
            on=["game_pk", "pitcher"], how="left",
        )
    else:
        starters["starter_ip"] = 5.0
    starters["starter_ip"] = starters["starter_ip"].fillna(5.0)

    if "p_throws" in sc.columns:
        pt = sc.groupby("pitcher")["p_throws"].first().reset_index()
        starters = starters.merge(pt, on="pitcher", how="left")
    else:
        starters["p_throws"] = "R"

    if "home_team" in sc.columns:
        cols = ["home_team"] + (["away_team"] if "away_team" in sc.columns else [])
        home = sc.groupby("game_pk")[cols].first().reset_index()
        starters = starters.merge(home, on="game_pk", how="left")

    return starters


def _build_pitch_game_aggs(sc: pd.DataFrame) -> pd.DataFrame:
    """Per-(game_pk, pitcher) aggregates from pitch-level Statcast."""
    df = sc.copy()

    if "description" in df.columns:
        df["is_swing"] = df["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        ).astype(int)
        df["is_whiff"] = df["description"].isin(
            ["swinging_strike", "swinging_strike_blocked"]
        ).astype(int)
    else:
        df["is_swing"] = 0
        df["is_whiff"] = 0

    if "zone" in df.columns:
        df["is_in_zone"]  = df["zone"].between(1, 9).astype(int)
        df["is_out_zone"] = (df["zone"] > 9).astype(int)
    else:
        df["is_in_zone"] = 0
        df["is_out_zone"] = 0

    df["is_zone_contact"] = (
        df["is_in_zone"] & ~df["is_whiff"].astype(bool) & df["is_swing"].astype(bool)
    ).astype(int)
    df["is_chase_whiff"] = (df["is_out_zone"] & df["is_whiff"].astype(bool)).astype(int)

    if "balls" in df.columns and "strikes" in df.columns:
        df["is_first_pitch"] = ((df["balls"] == 0) & (df["strikes"] == 0)).astype(int)
        df["is_fps"]         = (df["is_first_pitch"] & df["is_in_zone"]).astype(int)
        df["is_hitter_count"] = (
            ((df["balls"] == 2) & (df["strikes"] == 0)) |
            ((df["balls"] == 3) & (df["strikes"] == 0)) |
            ((df["balls"] == 3) & (df["strikes"] == 1))
        ).astype(int)
        df["is_two_strike"] = (df["strikes"] == 2).astype(int)
    else:
        df["is_first_pitch"] = 0
        df["is_fps"]         = 0
        df["is_hitter_count"] = 0
        df["is_two_strike"]   = 0

    if "pitch_type" in df.columns:
        df["is_fb"]       = df["pitch_type"].isin(["FF", "SI"]).astype(int)
        df["is_breaking"] = df["pitch_type"].isin(["SL", "CU", "KC", "CS"]).astype(int)
    else:
        df["is_fb"]       = 0
        df["is_breaking"] = 0

    def _agg(g):
        n         = len(g)
        n_swing   = g["is_swing"].sum()
        n_whiff   = g["is_whiff"].sum()
        n_in_zone = g["is_in_zone"].sum()
        n_zone_co = g["is_zone_contact"].sum()
        n_out_z   = g["is_out_zone"].sum()
        n_chase_w = g["is_chase_whiff"].sum()
        n_fp      = g["is_first_pitch"].sum()
        n_fps     = g["is_fps"].sum()
        n_hc      = g["is_hitter_count"].sum()

        # two_strike_k_rate: fraction of two-strike PA-ending events == strikeout
        pa_2s = g[g["is_two_strike"] == 1]
        pa_2s_t = pa_2s[pa_2s["events"].notna()] if "events" in pa_2s.columns else pa_2s.head(0)
        two_k = (pa_2s_t["events"] == "strikeout").sum() / max(len(pa_2s_t), 1)

        pri_whiff = np.nan
        if "pitch_type" in g.columns and n > 0:
            pt_counts = g["pitch_type"].value_counts()
            if len(pt_counts) > 0:
                pri = pt_counts.index[0]
                m = g["pitch_type"] == pri
                pri_sw = g.loc[m, "is_swing"].sum()
                pri_wh = g.loc[m, "is_whiff"].sum()
                pri_whiff = pri_wh / max(pri_sw, 1)

        return pd.Series({
            "n_pitches":          n,
            "whiff_pct_game":     n_whiff / max(n_swing, 1),
            "zone_contact_pct":   n_zone_co / max(n_in_zone, 1),
            "chase_pct_game":     n_chase_w / max(n_out_z, 1),
            "fps_pct":            n_fps / max(n_fp, 1),
            "hitter_count_pct":   n_hc / max(n, 1),
            "two_strike_k_rate":  two_k,
            "fb_pct":             g["is_fb"].sum() / max(n, 1),
            "breaking_pct":       g["is_breaking"].sum() / max(n, 1),
            "velo_mean":          g["release_speed"].mean() if "release_speed" in g.columns else np.nan,
            "primary_whiff_rate": pri_whiff,
        })

    return df.groupby(["game_pk", "pitcher"]).apply(_agg).reset_index()


def _rolling_pitcher(df: pd.DataFrame, col: str, window: int,
                      min_p: int = 3, expand: bool = False) -> pd.Series:
    """Per-pitcher rolling mean of `col`, shift(1) to prevent leakage."""
    def _r(s):
        s = s.shift(1)
        if expand:
            return s.expanding(min_periods=min_p).mean()
        return s.rolling(window, min_periods=min_p).mean()
    return df.groupby("pitcher")[col].transform(_r)


def _build_pitcher_features(sc: pd.DataFrame) -> pd.DataFrame:
    """Returns one row per (starter, game) with all rolling K features."""
    starters = _identify_starters(sc)
    starters = starters.sort_values(["pitcher", "game_date"]).reset_index(drop=True)

    pitch_agg = _build_pitch_game_aggs(sc)
    starters = starters.merge(pitch_agg, on=["game_pk", "pitcher"], how="left")

    starters["k_pct"]   = starters["starter_ks"] / starters["bf"].replace(0, np.nan)
    starters["k_per_9"] = starters["starter_ks"] / starters["starter_ip"].replace(0, np.nan) * 9

    starters = starters.sort_values(["pitcher", "game_date"]).reset_index(drop=True)

    starters["k_pct_L5"]    = _rolling_pitcher(starters, "k_pct",   5)
    starters["k_pct_L10"]   = _rolling_pitcher(starters, "k_pct",  10)
    starters["k_pct_STD"]   = _rolling_pitcher(starters, "k_pct",  10, expand=True)
    starters["k_per_9_L5"]  = _rolling_pitcher(starters, "k_per_9", 5)
    starters["k_per_9_L10"] = _rolling_pitcher(starters, "k_per_9",10)

    starters["first_pitch_strike_pct_L10"] = _rolling_pitcher(starters, "fps_pct",          10)
    starters["hitter_count_rate_L10"]      = _rolling_pitcher(starters, "hitter_count_pct", 10)
    starters["two_strike_k_rate_L10"]      = _rolling_pitcher(starters, "two_strike_k_rate",10)

    starters["whiff_pct_L10"]        = _rolling_pitcher(starters, "whiff_pct_game",   10)
    starters["zone_contact_pct_L10"] = _rolling_pitcher(starters, "zone_contact_pct", 10)
    starters["chase_pct_L10"]        = _rolling_pitcher(starters, "chase_pct_game",   10)
    starters["velo_mean_L5"]         = _rolling_pitcher(starters, "velo_mean",         5)

    def _velo_trend(s):
        def _slope(vals):
            v = vals.dropna()
            if len(v) < 3:
                return np.nan
            return np.polyfit(range(len(v)), v, 1)[0]
        return s.shift(1).rolling(5, min_periods=3).apply(_slope, raw=False)

    starters["velo_trend_L5"] = starters.groupby("pitcher")["velo_mean"].transform(_velo_trend)

    starters["fb_pct_L10"]             = _rolling_pitcher(starters, "fb_pct",            10)
    starters["breaking_pct_L10"]       = _rolling_pitcher(starters, "breaking_pct",      10)
    starters["primary_whiff_rate_L10"] = _rolling_pitcher(starters, "primary_whiff_rate",10)

    starters["avg_ip_L5"] = _rolling_pitcher(starters, "starter_ip", 5)
    starters["avg_bf_L5"] = _rolling_pitcher(starters, "bf",         5)

    starters["days_rest"] = (
        starters.groupby("pitcher")["game_date"]
                .transform(lambda s: s.diff().dt.days.fillna(5))
    )
    starters["short_rest"] = (starters["days_rest"] <= 4).astype(int)
    return starters


# ── Section 5 — opponent (lineup) features ───────────────────────────────────

def _build_opponent_team_features(sc: pd.DataFrame, target_date: pd.Timestamp) -> pd.DataFrame:
    """Per-team batting K-vulnerability windows ending at target_date.

    Returns DataFrame with columns:
        bat_team, p_throws,
        opp_k_rate_L14, opp_k_rate_vs_hand_L14,
        opp_chase_rate_L14, opp_whiff_rate_L14, opp_lineup_pct_L,
        opp_top3_k_rate_L50

    Unlike the notebook (which keys by game_pk on today's slate), we aggregate
    at the team level so the runner can join by (bat_team, p_throws).
    """
    pa = sc[sc["events"].notna()].copy()

    if "inning_topbot" in pa.columns and "home_team" in pa.columns and "away_team" in pa.columns:
        pa["bat_team"] = np.where(
            pa["inning_topbot"] == "Bot", pa["home_team"], pa["away_team"]
        )
    else:
        logger.warning("K build: cannot infer bat_team — lineup features will be empty")
        return pd.DataFrame()

    pa["is_k"]   = (pa["events"] == "strikeout").astype(int)
    if "description" in pa.columns:
        pa["is_swing"] = pa["description"].isin(
            ["swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"]
        ).astype(int)
        pa["is_whiff"] = pa["description"].isin(
            ["swinging_strike", "swinging_strike_blocked"]
        ).astype(int)
    else:
        pa["is_swing"] = 0
        pa["is_whiff"] = 0
    pa["is_out_zone"] = (pa["zone"] > 9).astype(int) if "zone" in pa.columns else 0
    pa["is_chase"]    = (pa["is_out_zone"] & pa["is_swing"]).astype(int)
    pa["is_lhb"]      = (pa["stand"] == "L").astype(int) if "stand" in pa.columns else 0

    td = pd.Timestamp(target_date)
    pa_14 = pa[(pa["game_date"] >= td - timedelta(days=14)) & (pa["game_date"] < td)]
    pa_50 = pa[(pa["game_date"] >= td - timedelta(days=50)) & (pa["game_date"] < td)]

    # Team-level L14
    g14 = pa_14.groupby("bat_team")
    base = pd.DataFrame({
        "bat_team":           list(g14.groups.keys()),
        "opp_k_rate_L14":     g14["is_k"].mean().values,
        "opp_chase_rate_L14": g14["is_chase"].mean().values,
        "opp_whiff_rate_L14": g14["is_whiff"].mean().values,
        "opp_lineup_pct_L":   g14["is_lhb"].mean().values,
    })

    # K rate vs pitcher hand — needs p_throws column; expand per team x {L,R}
    rows = []
    if "p_throws" in pa_14.columns:
        g14h = pa_14.groupby(["bat_team", "p_throws"])["is_k"].mean().reset_index()
        g14h = g14h.rename(columns={"is_k": "opp_k_rate_vs_hand_L14"})
        for hand in ("L", "R"):
            merged = base.merge(
                g14h[g14h["p_throws"] == hand][["bat_team", "opp_k_rate_vs_hand_L14"]],
                on="bat_team", how="left",
            )
            merged["p_throws"] = hand
            rows.append(merged)
    else:
        for hand in ("L", "R"):
            merged = base.copy()
            merged["opp_k_rate_vs_hand_L14"] = merged["opp_k_rate_L14"]
            merged["p_throws"] = hand
            rows.append(merged)
    team_df = pd.concat(rows, ignore_index=True)

    # Top-3 K rate (L50). Notebook uses bat_order; Statcast doesn't reliably have
    # it, so the notebook's own fallback was opp_k_rate_L14. Preserve that.
    if "bat_order" in pa_50.columns and pa_50["bat_order"].notna().any():
        top3 = pa_50[pa_50["bat_order"].isin([1, 2, 3])].copy()
        if len(top3) > 0:
            weights = {1: 3, 2: 2, 3: 1}
            top3["w"] = top3["bat_order"].map(weights).fillna(1)
            top3["wk"] = top3["is_k"] * top3["w"]
            agg = (
                top3.groupby("bat_team")
                    .apply(lambda x: x["wk"].sum() / x["w"].sum())
                    .reset_index(name="opp_top3_k_rate_L50")
            )
            team_df = team_df.merge(agg, on="bat_team", how="left")
        else:
            team_df["opp_top3_k_rate_L50"] = team_df["opp_k_rate_L14"]
    else:
        team_df["opp_top3_k_rate_L50"] = team_df["opp_k_rate_L14"]

    team_df["opp_top3_k_rate_L50"] = team_df["opp_top3_k_rate_L50"].fillna(team_df["opp_k_rate_L14"])
    return team_df


# ── Section 6 — feature join ─────────────────────────────────────────────────

def _join_umpires(pf: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Join umpire L30 metrics (game-level) onto pitcher rows by game_pk.

    The umpire master CSV in the lake carries L30 rolling per-umpire stats.
    Notebook Section 2 also derives ump_k_boost_L30 from Statcast K rates;
    we lift that derivation upstream into the umpire master if it's there,
    otherwise leave NaN (XGBoost handles it).
    """
    from mlb_core.storage import read_csv, exists
    if not exists("Umpires/umpscorecards_master.csv"):
        logger.info("K build: no umpire master — ump_* features will be NaN")
        for col in ("ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30"):
            if col not in pf.columns:
                pf[col] = np.nan
        return pf

    try:
        umps = read_csv("Umpires/umpscorecards_master.csv", low_memory=False)
    except Exception as e:
        logger.warning(f"K build: umpire master read failed: {e}")
        for col in ("ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30"):
            if col not in pf.columns:
                pf[col] = np.nan
        return pf

    keep = ["game_pk"]
    for col in ("ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30"):
        if col in umps.columns:
            keep.append(col)
    if "game_pk" in umps.columns and len(keep) > 1:
        umps["game_pk"] = pd.to_numeric(umps["game_pk"], errors="coerce")
        umps = umps[keep].drop_duplicates("game_pk")
        pf = pf.merge(umps, on="game_pk", how="left")
    for col in ("ump_overall_accuracy_L30", "ump_k_boost_L30", "ump_consistency_L30"):
        if col not in pf.columns:
            pf[col] = np.nan
    return pf


def _join_weather(pf: pd.DataFrame) -> pd.DataFrame:
    """Join historical weather + dome flags.

    Uses mlb_core.data.weather.STADIUMS as the authoritative roof/dome dict.
    For training rows we look at the weather master if available; otherwise
    fall back to dome=1 → 70°F, else NaN. The runner overrides with live
    weather at predict time (Section 8 in run_k.py).
    """
    from mlb_core.data.weather import STADIUMS

    dome_set = {abbr for abbr, info in STADIUMS.items() if info[2] == "dome"}
    if "home_team" in pf.columns:
        pf["is_dome"] = pf["home_team"].apply(lambda t: int(t in dome_set))
    else:
        pf["is_dome"] = 0

    # Historical weather — best-effort
    from mlb_core.storage import read_csv, exists
    if "temperature_f" not in pf.columns:
        pf["temperature_f"] = np.nan
    if exists("Weather/weather_master.csv"):
        try:
            wx = read_csv("Weather/weather_master.csv", low_memory=False)
            if "game_pk" in wx.columns and "temperature_f" in wx.columns:
                wx = wx[["game_pk", "temperature_f"]].drop_duplicates("game_pk")
                wx = wx.rename(columns={"temperature_f": "_wx_temp"})
                pf = pf.merge(wx, on="game_pk", how="left")
                pf["temperature_f"] = pf["temperature_f"].fillna(pf["_wx_temp"])
                pf = pf.drop(columns=["_wx_temp"])
        except Exception as e:
            logger.warning(f"K build: weather master read failed: {e}")

    # Dome venues — fixed 70F, prevents weather noise (notebook convention)
    pf.loc[pf["is_dome"] == 1, "temperature_f"] = 70.0
    return pf


def _attach_today_slate(pf: pd.DataFrame, sc: pd.DataFrame, run_date: str) -> pd.DataFrame:
    """Append today's slate rows so the runner has matchups to score.

    For each probable starter (home + away per game), append a row with
    target NaN (no actual K count yet) but featurized today_opponent + weather
    overrides. The retrain script drops rows where starter_ks is NaN.
    """
    from mlb_core.data.lineups import get_today_schedule
    sched = get_today_schedule(run_date)
    if sched.empty:
        logger.info(f"K build: no games scheduled for {run_date} — skipping slate append")
        return pf

    # Per-pitcher latest snapshot to inherit rolling features from
    pf_sorted = pf.sort_values("game_date")
    latest_per_p = pf_sorted.groupby("pitcher", as_index=False).last()
    latest_per_p = latest_per_p.set_index("pitcher")

    # Opponent team features as-of run_date
    opp_df = _build_opponent_team_features(sc, pd.Timestamp(run_date))

    new_rows = []
    for _, g in sched.iterrows():
        for side in ("home", "away"):
            pid = g.get(f"{side}_pitcher_id")
            if pd.isna(pid):
                continue
            pid = int(pid)
            if pid not in latest_per_p.index:
                logger.info(f"K build: no snapshot for {g.get(f'{side}_pitcher_name')} "
                            f"(id={pid}) — skipping today slate row")
                continue
            row = latest_per_p.loc[pid].to_dict()
            row["pitcher"]    = pid
            row["game_pk"]    = g["game_pk"]
            row["game_date"]  = pd.Timestamp(run_date)
            row["home_team"]  = g["home_team"]
            row["away_team"]  = g["away_team"]
            # Target unknowable today
            row["starter_ks"] = np.nan
            row["starter_ip"] = np.nan
            row["bf"]         = np.nan

            row["is_home"] = 1 if side == "home" else 0
            row["implied_win_pct"] = 0.5  # overridden by runner if available

            bat_team = g["away_team"] if side == "home" else g["home_team"]
            p_throws = row.get("p_throws", "R")
            opp_match = opp_df[
                (opp_df["bat_team"] == bat_team) & (opp_df["p_throws"] == p_throws)
            ]
            if not opp_match.empty:
                m = opp_match.iloc[0]
                row["opp_k_rate_L14"]         = m["opp_k_rate_L14"]
                row["opp_k_rate_vs_hand_L14"] = m["opp_k_rate_vs_hand_L14"]
                row["opp_chase_rate_L14"]     = m["opp_chase_rate_L14"]
                row["opp_whiff_rate_L14"]     = m["opp_whiff_rate_L14"]
                row["opp_lineup_pct_L"]       = m["opp_lineup_pct_L"]
                row["opp_top3_k_rate_L50"]    = m["opp_top3_k_rate_L50"]
                kp = row.get("k_pct_L10")
                row["opp_platoon_k_edge"] = (m["opp_lineup_pct_L"] - 0.5) * (
                    kp if kp is not None and not pd.isna(kp) else 0.2
                )
            new_rows.append(row)

    if not new_rows:
        logger.info("K build: 0 slate rows appended")
        return pf

    new_df = pd.DataFrame(new_rows)
    # Best-effort dome/temperature for today
    from mlb_core.data.weather import STADIUMS
    dome_set = {abbr for abbr, info in STADIUMS.items() if info[2] == "dome"}
    new_df["is_dome"] = new_df["home_team"].apply(lambda t: int(t in dome_set))
    new_df["temperature_f"] = new_df.apply(
        lambda r: 70.0 if r["is_dome"] == 1 else r.get("temperature_f", np.nan),
        axis=1,
    )

    logger.info(f"K build: appended {len(new_df)} slate rows for {run_date}")
    return pd.concat([pf, new_df], ignore_index=True, sort=False)


# ── Backfill historical opponent stats ───────────────────────────────────────

def _backfill_opponent_history(pf: pd.DataFrame, sc: pd.DataFrame) -> pd.DataFrame:
    """For each historical (pitcher, game_pk) row, compute opp features as-of
    that game_date. This is expensive — we approximate by aggregating once per
    unique date and joining.

    Notebook Section 5 only built today's lineup features, but the retrain
    needs historical opp values too, or we end up training a model whose
    opp_* features are all NaN for training rows. We use Statcast as the
    source of truth for opponent and grouped batter stats.
    """
    # The pitcher row's bat_team is the team the pitcher's team was facing,
    # which is the home_team if the pitcher's start was away, else away_team.
    # We don't know which side the pitcher was on without inning_topbot per
    # PA row — so derive it from the Statcast PA frame.
    pa = sc[sc["events"].notna()][["game_pk", "pitcher", "inning_topbot",
                                     "home_team", "away_team"]].copy()
    pa["bat_team"] = np.where(
        pa["inning_topbot"] == "Bot", pa["home_team"], pa["away_team"]
    )
    pitcher_bat_team = (
        pa.groupby(["game_pk", "pitcher"])["bat_team"].first().reset_index()
    )
    pf = pf.merge(pitcher_bat_team, on=["game_pk", "pitcher"], how="left")

    # Aggregate once per unique date
    unique_dates = sorted(pf["game_date"].dropna().unique())
    out_chunks = []
    for d in unique_dates:
        opp_d = _build_opponent_team_features(sc, pd.Timestamp(d))
        if opp_d.empty:
            continue
        opp_d["game_date"] = pd.Timestamp(d)
        out_chunks.append(opp_d)
    if not out_chunks:
        for col in ("opp_k_rate_L14", "opp_k_rate_vs_hand_L14", "opp_chase_rate_L14",
                    "opp_whiff_rate_L14", "opp_lineup_pct_L", "opp_top3_k_rate_L50"):
            if col not in pf.columns:
                pf[col] = np.nan
        pf["opp_platoon_k_edge"] = np.nan
        return pf

    opp_all = pd.concat(out_chunks, ignore_index=True)
    pf = pf.merge(
        opp_all, left_on=["bat_team", "p_throws", "game_date"],
        right_on=["bat_team", "p_throws", "game_date"], how="left",
    )
    # is_home, implied_win_pct
    pf["is_home"] = (
        pf.get("home_team", pd.Series([None] * len(pf))) ==
        pf.get("pitcher_team", pd.Series([None] * len(pf)))
    ).astype(int)
    # We don't track pitcher_team historically; infer from bat_team + home_team
    pf["is_home"] = np.where(pf["bat_team"] == pf["home_team"], 0, 1)
    pf["implied_win_pct"] = 0.5  # historical placeholder; runner overrides
    pf["opp_platoon_k_edge"] = (pf["opp_lineup_pct_L"] - 0.5) * pf["k_pct_L10"].fillna(0.2)
    return pf


# ── Entry point ──────────────────────────────────────────────────────────────

def build_features(cfg: dict, run_date: str | None = None) -> dict:
    """Build K Pro feature CSVs (historical + today's slate) and write to GCS.

    Steps mirror notebook sections 1, 4, 5, 6:
        1. Load Statcast (filtered to cfg['season_start']+)
        4. Per-starter rolling pitcher features
        5. Historical opponent team features (one per game_date)
        6. Join umpire + weather + today's slate (with target NaN)

    Returns a status dict suitable for the /build-features HTTP response.
    """
    run_date = run_date or date.today().isoformat()
    logger.info(f"K build: starting for run_date={run_date}")

    sc = _load_statcast(cfg)

    pf = _build_pitcher_features(sc)
    logger.info(f"K build: pitcher features {len(pf):,} rows")

    pf = _backfill_opponent_history(pf, sc)
    logger.info(f"K build: opponent history joined ({len(pf):,} rows)")

    pf = _join_umpires(pf, cfg)
    pf = _join_weather(pf)
    pf = _attach_today_slate(pf, sc, run_date)

    # Ensure every K_FEATURES column exists (NaN if not built — XGBoost handles it)
    from K_Pro_System.config_k import K_FEATURES
    missing_cols = [c for c in K_FEATURES if c not in pf.columns]
    for c in missing_cols:
        pf[c] = np.nan
    if missing_cols:
        logger.warning(f"K build: created NaN placeholder for "
                       f"{len(missing_cols)} unbuilt features: {missing_cols}")

    # Write outputs
    from mlb_core.storage import write_csv
    write_csv(pf, cfg["gcs_model_features"])
    logger.info(f"K build: wrote {cfg['gcs_model_features']} | {len(pf):,} rows")

    # Pitcher-only and lineup-only outputs are convenience artifacts; we keep
    # them in sync with the notebook layout for forensic purposes.
    pitcher_only_cols = [c for c in pf.columns
                          if not c.startswith("opp_") and c not in ("temperature_f", "is_dome")]
    write_csv(pf[pitcher_only_cols], cfg["gcs_pitcher_features"])

    slate_only = pf[pf["starter_ks"].isna()].copy()
    if not slate_only.empty:
        write_csv(slate_only, cfg["gcs_lineup_features"])

    return {
        "status":          "ok",
        "system":          "K",
        "run_date":        run_date,
        "rows":            int(len(pf)),
        "slate_rows":      int(len(slate_only)),
        "features":        len(K_FEATURES),
        "missing_columns": missing_cols,
        "gcs_key":         cfg["gcs_model_features"],
    }
