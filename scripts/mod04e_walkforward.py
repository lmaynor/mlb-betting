#!/usr/bin/env python3
"""MOD-04e E1: Walk-forward evaluation harness on the BettingPros NFL prop bank.

Grades the MOD-04e hierarchical game-script opportunity model (nfl.mod04e.model)
and the repo XGBoost benchmark (nfl.models.props) against BettingPros market consensus
over seasons S in {2024, 2025} (default).

Fold protocol (strictly expanding/rolling pre-week):
  For each scored season S and each week w:
    train on seasons S-3..S-1 (all weeks) + season S weeks 1..w-1
    score week w untouched.

Usage:
  python3 scripts/mod04e_walkforward.py --seasons 2024,2025 --out /tmp/mod04e/wf.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd
from scipy import optimize, special

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mlb_core import storage  # noqa: E402
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way  # noqa: E402
from nfl.data import outcome_matching as om  # noqa: E402
from nfl.data import read as nfl_read  # noqa: E402
from nfl.features import props_features as pf  # noqa: E402
from nfl.models import props as xgb_props  # noqa: E402
from nfl.mod04e import features as mod_feat  # noqa: E402
from nfl.mod04e import labels as mod_lbl  # noqa: E402
from nfl.mod04e import model as mod_mdl  # noqa: E402

BP_CACHE_DIR = "/tmp/mod04e/bp_cache"
BP_GCS_PREFIX = "Odds/bettingpros_nfl"

MARKET_MAP: dict[str, str] = {
    "receptions": "receptions",
    "rec_yds": "receiving-yards",
    "rush_attempts": "rushing-attempts",
    "anytime_td": "touchdown-scored",
    "pass_attempts": "passing-attempts",
    "pass_completions": "passing-completions",
    "interceptions": "interceptions",
    "rush_yds": "rushing-yards",
}

# Mapping from MOD-04e family name -> nfl.models.props.SPECS key (None = no spec; skip)
XGB_SPEC_MAP: dict[str, Optional[str]] = {
    "receptions": "receptions",
    "rec_yds": "rec_yds",
    "rush_attempts": "rush_attempts",
    "anytime_td": "anytime_td",
    "pass_attempts": "pass_attempts",
    "pass_completions": "completions",
    "interceptions": None,
    "rush_yds": "rush_yds",
}

_EXCLUDED_BOOK_KINDS = frozenset({"dfs", "consensus", "open", "sharp"})


def iter_walkforward_folds(
    available_sw: Iterable[tuple[int, int]],
    scored_seasons: Iterable[int] = (2024, 2025),
    limit_weeks: Optional[int] = None,
) -> list[tuple[int, int, list[tuple[int, int]], tuple[int, int]]]:
    """Build walk-forward folds: train on (S-3..S-1 all weeks) + (S weeks 1..w-1), score (S, w).

    Returns list of (S, w, train_sw_list, (S, w)).
    """
    sw_set = sorted({(int(s), int(w)) for s, w in available_sw})
    folds: list[tuple[int, int, list[tuple[int, int]], tuple[int, int]]] = []
    for season in scored_seasons:
        s_int = int(season)
        weeks = sorted({w for s, w in sw_set if s == s_int})
        if limit_weeks is not None:
            weeks = weeks[: int(limit_weeks)]
        for wk in weeks:
            train_sw = [
                (s, w)
                for (s, w) in sw_set
                if (s_int - 3 <= s <= s_int - 1) or (s == s_int and w < wk)
            ]
            folds.append((s_int, wk, train_sw, (s_int, wk)))
    return folds


def _is_consensus_row(df: pd.DataFrame) -> pd.Series:
    book_name = df["book"].astype(str).str.strip().str.lower() if "book" in df.columns else pd.Series("", index=df.index)
    book_id = pd.to_numeric(df["book_id"], errors="coerce") if "book_id" in df.columns else pd.Series(np.nan, index=df.index)
    book_kind = df["book_kind"].astype(str).str.strip().str.lower() if "book_kind" in df.columns else pd.Series("", index=df.index)
    return (book_name == "consensus") | (book_id == 0) | (book_kind == "consensus")


def _is_kalshi_row(df: pd.DataFrame) -> pd.Series:
    book_name = df["book"].astype(str).str.strip().str.lower() if "book" in df.columns else pd.Series("", index=df.index)
    book_id = pd.to_numeric(df["book_id"], errors="coerce") if "book_id" in df.columns else pd.Series(np.nan, index=df.index)
    return (book_id == 68) | book_name.str.contains("kalshi", regex=False)


def _pair_devig_prob(over_odds: float, under_odds: float) -> float:
    return float(
        devig_two_way(
            american_to_implied_prob(float(over_odds)),
            american_to_implied_prob(float(under_odds)),
            "proportional",
        )[0]
    )


def consensus_from_bp(df: pd.DataFrame) -> pd.DataFrame:
    """Build market consensus per (date, player, market) from a BettingPros parquet frame.

    Rule:
      1. If a two-sided 'Consensus' book row is present, use its line and de-vigged two-sided over prob.
      2. Else use real books (excluding dfs/consensus/open/sharp/kalshi and is_open rows), pair
         two-sided over/under per (book, line), pick the modal line (most two-sided books; ties broken
         by median fair_over closest to 0.5, then lower line), and take median fair_over at that line.
    """
    cols = ["date", "player", "player_norm", "team", "market", "line", "p_market", "n_books", "source"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    d = df.copy()
    if "is_open" in d.columns:
        d = d[~d["is_open"].fillna(False).astype(bool)].copy()
    side_raw = d["side"].astype(str).str.strip().str.lower()
    d["side_n"] = np.where(side_raw.isin(["over", "yes"]), "over", np.where(side_raw.isin(["under", "no"]), "under", ""))
    d = d[d["side_n"] != ""].copy()
    d["odds_n"] = pd.to_numeric(d["odds"], errors="coerce")
    d["line_n"] = pd.to_numeric(d["line"], errors="coerce")
    # anytime_td Yes/No rows occasionally omit numeric line -> 0.5
    mkt_col = d["market"].astype(str).str.lower() if "market" in d.columns else pd.Series("", index=d.index)
    d.loc[d["line_n"].isna() & (mkt_col == "touchdown-scored"), "line_n"] = 0.5
    d = d[d["odds_n"].notna() & d["line_n"].notna() & (d["odds_n"].abs() >= 100)].copy()
    if d.empty:
        return pd.DataFrame(columns=cols)

    d["date_s"] = d["date"].astype(str).str.slice(0, 10)
    d["player_norm"] = d["player"].astype(str).map(om.normalize_name)
    d = d[d["player_norm"] != ""].copy()
    d["market_s"] = d["market"].astype(str) if "market" in d.columns else ""
    d["_is_cons"] = _is_consensus_row(d)
    d["_is_kalshi"] = _is_kalshi_row(d)
    if "book_kind" in d.columns:
        bk = d["book_kind"].astype(str).str.strip().str.lower()
        d["_is_real"] = (~d["_is_cons"]) & (~d["_is_kalshi"]) & (~bk.isin(_EXCLUDED_BOOK_KINDS))
    else:
        d["_is_real"] = (~d["_is_cons"]) & (~d["_is_kalshi"])

    if "book_id" not in d.columns:
        d["book_id"] = d["book"].astype(str) if "book" in d.columns else "book"

    # Sort by updated timestamp if available so latest quote wins per (key, line, book, side)
    if "updated" in d.columns:
        d["_upd"] = pd.to_datetime(d["updated"], utc=True, errors="coerce")
        d = d.sort_values("_upd", kind="mergesort", na_position="first")

    out_rows: list[dict[str, Any]] = []
    for (dt, pnorm, mkt), grp in d.groupby(["date_s", "player_norm", "market_s"], sort=False):
        player_disp = str(grp["player"].iloc[-1])
        team_val = str(grp["team"].iloc[-1]) if "team" in grp.columns and pd.notna(grp["team"].iloc[-1]) else ""

        # 1. Try Consensus book row first
        cons_g = grp[grp["_is_cons"]]
        cons_picked = None
        if not cons_g.empty:
            for ln, lgrp in cons_g.groupby("line_n", sort=False):
                ov = lgrp[lgrp["side_n"] == "over"]
                un = lgrp[lgrp["side_n"] == "under"]
                if not ov.empty and not un.empty:
                    o_odds = float(ov["odds_n"].iloc[-1])
                    u_odds = float(un["odds_n"].iloc[-1])
                    cons_picked = (float(ln), _pair_devig_prob(o_odds, u_odds), 1, "consensus")
                    break

        if cons_picked is not None:
            line_v, p_mkt, n_bk, src = cons_picked
            out_rows.append({
                "date": dt, "player": player_disp, "player_norm": pnorm, "team": team_val,
                "market": mkt, "line": line_v, "p_market": p_mkt, "n_books": n_bk, "source": src,
            })
            continue

        # 2. Fallback: real books, two-sided at modal line
        real_g = grp[grp["_is_real"]]
        if real_g.empty:
            continue
        by_line: list[tuple[float, list[float]]] = []
        for ln, lgrp in real_g.groupby("line_n", sort=False):
            fairs: list[float] = []
            for _bk, bgrp in lgrp.groupby("book_id", sort=False):
                ov = bgrp[bgrp["side_n"] == "over"]
                un = bgrp[bgrp["side_n"] == "under"]
                if not ov.empty and not un.empty:
                    fairs.append(_pair_devig_prob(float(ov["odds_n"].iloc[-1]), float(un["odds_n"].iloc[-1])))
            if fairs:
                by_line.append((float(ln), fairs))
        if not by_line:
            continue

        # Modal line: max book count, then median prob closest to 0.5, then lower line
        by_line.sort(key=lambda item: (-len(item[1]), abs(float(np.median(item[1])) - 0.5), item[0]))
        modal_line, modal_fairs = by_line[0]
        out_rows.append({
            "date": dt,
            "player": player_disp,
            "player_norm": pnorm,
            "team": team_val,
            "market": mkt,
            "line": modal_line,
            "p_market": float(np.median(modal_fairs)),
            "n_books": len(modal_fairs),
            "source": "real_books_modal",
        })

    return pd.DataFrame(out_rows, columns=cols)


def compute_metrics(
    y_over: np.ndarray,
    p_model: np.ndarray,
    p_market: np.ndarray,
    p_xgb: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """Compute n, model_ll, market_ll, paired delta_ll (model - market) with z, logistic slope, 10-bin ECE, xgb_ll."""
    y = np.asarray(y_over, dtype=float)
    pm = np.clip(np.asarray(p_model, dtype=float), 1e-6, 1.0 - 1e-6)
    pk = np.clip(np.asarray(p_market, dtype=float), 1e-6, 1.0 - 1e-6)
    ok = np.isfinite(y) & np.isfinite(pm) & np.isfinite(pk)
    y, pm, pk = y[ok], pm[ok], pk[ok]
    n = int(len(y))
    if n == 0:
        return {
            "n": 0, "model_ll": None, "market_ll": None, "delta_ll": None, "z": None,
            "slope": None, "ece": None, "xgb_ll": None,
        }

    ll_m = -(y * np.log(pm) + (1.0 - y) * np.log(1.0 - pm))
    ll_k = -(y * np.log(pk) + (1.0 - y) * np.log(1.0 - pk))
    diff = ll_m - ll_k
    d_mean = float(np.mean(diff))
    if n > 1:
        se = float(np.std(diff, ddof=1) / math.sqrt(n))
        z = float(d_mean / se) if se > 1e-12 else 0.0
    else:
        z = 0.0

    # Logistic recalibration slope: y ~ a + b * logit(p_model)
    lm = np.log(pm / (1.0 - pm))
    slope = None
    if np.std(lm) > 1e-9 and len(np.unique(y)) > 1:
        def nll_cal(ab: np.ndarray) -> float:
            z_c = ab[0] + ab[1] * lm
            return float(np.mean(np.logaddexp(0.0, z_c) - y * z_c)) + 1e-4 * float((ab[1] - 1.0) ** 2)

        res = optimize.minimize(nll_cal, x0=np.array([0.0, 1.0]), method="BFGS")
        slope = float(res.x[1])

    # 10-bin ECE
    edges = np.linspace(0.0, 1.0, 11)
    ece = 0.0
    for i in range(10):
        lo, hi = edges[i], edges[i + 1]
        in_bin = (pm >= lo) & (pm <= hi if i == 9 else pm < hi)
        cnt = int(in_bin.sum())
        if cnt > 0:
            ece += (cnt / n) * abs(float(pm[in_bin].mean()) - float(y[in_bin].mean()))

    xgb_ll = None
    if p_xgb is not None:
        px = np.asarray(p_xgb, dtype=float)[ok]
        valid_x = np.isfinite(px)
        if valid_x.any():
            px_c = np.clip(px[valid_x], 1e-6, 1.0 - 1e-6)
            yx = y[valid_x]
            xgb_ll = float(-np.mean(yx * np.log(px_c) + (1.0 - yx) * np.log(1.0 - px_c)))

    return {
        "n": n,
        "model_ll": float(np.mean(ll_m)),
        "market_ll": float(np.mean(ll_k)),
        "delta_ll": d_mean,
        "z": z,
        "slope": slope,
        "ece": float(ece),
        "xgb_ll": xgb_ll,
    }


def load_bp_market_cached(
    market_slug: str,
    dates: Iterable[str],
    cache_dir: str = BP_CACHE_DIR,
    prefix: str = BP_GCS_PREFIX,
) -> pd.DataFrame:
    """Load BettingPros daily parquets for `market_slug` on `dates`, caching locally under cache_dir."""
    cdir = Path(cache_dir) / market_slug
    cdir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    avail_keys: Optional[set[str]] = None

    for ds in sorted(set(dates)):
        local_p = cdir / f"{ds}.parquet"
        if local_p.exists():
            df = pd.read_parquet(local_p)
            if len(df):
                frames.append(df)
            continue
        if avail_keys is None:
            avail_keys = set(storage.list_keys(f"{prefix}/{market_slug}/"))
        key = f"{prefix}/{market_slug}/{ds}.parquet"
        if key not in avail_keys:
            continue
        df = storage.read_parquet(key)
        df.to_parquet(local_p, index=False)
        if len(df):
            frames.append(df)

    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run_walkforward(
    *,
    seasons: list[int] = [2024, 2025],
    families: list[str] = list(MARKET_MAP.keys()),
    limit_weeks: Optional[int] = None,
    cache_dir: str = BP_CACHE_DIR,
) -> dict[str, Any]:
    """Run full walk-forward evaluation across requested seasons and families."""
    min_train_season = min(seasons) - 3
    max_season = max(seasons)
    load_seasons = list(range(min_train_season, max_season + 1))

    schedules = nfl_read.load("schedules")
    schedules = schedules[schedules["season"].isin(load_seasons)].copy()
    players = nfl_read.load("players")
    player_stats = nfl_read.load("player_stats", seasons=load_seasons, strict=False)
    team_stats = nfl_read.load("team_stats", seasons=load_seasons, strict=False)
    snap_counts = nfl_read.load("snap_counts", seasons=load_seasons, strict=False)
    injuries = nfl_read.load("injuries", seasons=load_seasons, strict=False)
    depth_charts = nfl_read.load("depth_charts", seasons=load_seasons, strict=False)
    rosters_weekly = nfl_read.load("rosters_weekly", seasons=load_seasons, strict=False)

    reg_sched = schedules[schedules.get("game_type", "REG") == "REG"].copy() if "game_type" in schedules.columns else schedules.copy()
    reg_sched["gameday_s"] = reg_sched["gameday"].astype(str).str.slice(0, 10)
    sw_dates = (
        reg_sched.groupby(["season", "week"], as_index=False)["gameday_s"]
        .agg(list)
        .sort_values(["season", "week"])
    )
    available_sw = [(int(r.season), int(r.week)) for r in sw_dates.itertuples(index=False)]
    folds = iter_walkforward_folds(available_sw, scored_seasons=seasons, limit_weeks=limit_weeks)

    frames_bundle = {
        "schedules": schedules,
        "player_stats": player_stats,
        "team_stats": team_stats,
        "snap_counts": snap_counts,
        "injuries": injuries,
        "depth_charts": depth_charts,
        "rosters_weekly": rosters_weekly,
        "players": players,
    }

    # Pre-build per-(season, week) features & labels strictly per date
    sw_feat: dict[tuple[int, int], pd.DataFrame] = {}
    sw_lbl: dict[tuple[int, int], pd.DataFrame] = {}
    needed_sw = set()
    for _s, _w, tr_sw, sc_sw in folds:
        needed_sw.update(tr_sw)
        needed_sw.add(sc_sw)

    for r in sw_dates.itertuples(index=False):
        sw = (int(r.season), int(r.week))
        if sw not in needed_sw:
            continue
        day_feats = []
        for gdate in sorted(set(r.gameday_s)):
            cutoff = f"{gdate}T23:59:59Z"
            f_day = mod_feat.build_pregame_features(gdate, cutoff, frames_bundle)
            if not f_day.empty:
                day_feats.append(f_day)
        if day_feats:
            f_all = pd.concat(day_feats, ignore_index=True)
            l_all = mod_lbl.build_labels(
                f_all,
                player_stats=player_stats,
                schedules=schedules,
                players=players,
                snap_counts=snap_counts,
                rosters_weekly=rosters_weekly,
            )
            sw_feat[sw] = f_all
            sw_lbl[sw] = l_all

    # Build XGBoost props_features frame once if any requested family has an XGB spec
    pf_frame: Optional[pd.DataFrame] = None
    if any(XGB_SPEC_MAP.get(f) is not None for f in families):
        fr_xgb = pf.Frames(
            player_stats=player_stats,
            schedules=schedules,
            team_stats=team_stats,
            snap_counts=snap_counts,
            injuries=injuries,
            players=players,
        )
        pf_frame = pf.build_from_frames(fr_xgb, emit_seasons=load_seasons)

    # Scored game dates
    scored_dates = sorted({
        gd
        for r in sw_dates.itertuples(index=False)
        if (int(r.season), int(r.week)) in {f[3] for f in folds}
        for gd in r.gameday_s
    })

    report: dict[str, Any] = {
        "engine_id": mod_mdl.ENGINE_ID,
        "seasons": seasons,
        "limit_weeks": limit_weeks,
        "n_folds": len(folds),
        "families": {},
    }

    for fam in families:
        mkt_slug = MARKET_MAP[fam]
        bp_raw = load_bp_market_cached(mkt_slug, scored_dates, cache_dir=cache_dir)
        cons = consensus_from_bp(bp_raw)
        if cons.empty:
            report["families"][fam] = {"market": mkt_slug, **compute_metrics([], [], [])}
            continue

        cons["pnorm"] = cons["player_norm"]
        cons_by_date = {dt: grp for dt, grp in cons.groupby("date")}

        all_y: list[float] = []
        all_pm: list[float] = []
        all_pk: list[float] = []
        all_px: list[float] = []

        xgb_spec = XGB_SPEC_MAP.get(fam)

        for season_s, week_w, train_sw, score_sw in folds:
            if score_sw not in sw_feat:
                continue
            assert max(train_sw) < score_sw if train_sw else True
            tr_f_parts = [sw_feat[sw] for sw in train_sw if sw in sw_feat]
            tr_l_parts = [sw_lbl[sw] for sw in train_sw if sw in sw_lbl]
            if not tr_f_parts:
                continue
            tr_f = pd.concat(tr_f_parts, ignore_index=True)
            tr_l = pd.concat(tr_l_parts, ignore_index=True)
            try:
                mdl = mod_mdl.fit(tr_f, tr_l, fam)
            except ValueError:
                continue

            sc_f = sw_feat[score_sw].copy()
            sc_l = sw_lbl[score_sw].copy()
            sc_f["pnorm"] = sc_f["player_name"].map(om.normalize_name)

            # Optional XGBoost benchmark on exact same train_sw -> score_sw
            xgb_probs: dict[tuple[str, str], float] = {}
            if xgb_spec is not None and pf_frame is not None:
                tr_sw_set = set(train_sw)
                mask_tr = [
                    (int(s), int(w)) in tr_sw_set
                    for s, w in zip(pf_frame["season"], pf_frame["week"])
                ]
                pf_tr = pf_frame[mask_tr]
                pf_sc = pf_frame[(pf_frame["season"] == season_s) & (pf_frame["week"] == week_w)]
                if len(pf_sc) and len(xgb_props.training_rows(pf_tr, xgb_spec)) >= 300:
                    try:
                        xmdl = xgb_props.train_stat(pf_tr, xgb_spec, oof_folds=2)
                        mu_x = xgb_props.predict_mu(xmdl, pf_sc)
                        for pid, gid, mv in zip(pf_sc["player_id"].astype(str), pf_sc["game_id"].astype(str), mu_x):
                            xgb_probs[(pid, gid)] = float(mv)
                        xgb_disp = xmdl.dispersion
                    except Exception:
                        xgb_probs = {}
                        xgb_disp = None
                else:
                    xgb_disp = None
            else:
                xgb_disp = None

            for idx, row in sc_f.iterrows():
                gdate = str(row["game_date"])[:10]
                c_day = cons_by_date.get(gdate)
                if c_day is None:
                    continue
                hit = c_day[c_day["pnorm"] == row["pnorm"]]
                if hit.empty:
                    continue
                crow = hit.iloc[0]
                lrow = sc_l.loc[idx]
                if not bool(lrow["gradeable"]) or pd.isna(lrow[fam]):
                    continue
                actual = float(lrow[fam])
                line = float(crow["line"])
                if fam != "anytime_td" and math.isclose(actual, line):
                    continue  # push removed
                y_val = 1.0 if (actual >= 1.0 if fam == "anytime_td" else actual > line) else 0.0
                p_m = mdl.p_over(row.to_dict(), 0.5 if fam == "anytime_td" else line)
                p_k = float(crow["p_market"])

                p_x = float("nan")
                pid_gid = (str(row["player_id"]), str(row["game_id"]))
                if xgb_spec is not None and xgb_disp is not None and pid_gid in xgb_probs:
                    po, pu, _pp = xgb_props.p_over(xgb_spec, xgb_probs[pid_gid], 0.5 if fam == "anytime_td" else line, xgb_disp)
                    denom = po + pu
                    if denom > 1e-9:
                        p_x = float(po / denom)

                all_y.append(y_val)
                all_pm.append(p_m)
                all_pk.append(p_k)
                all_px.append(p_x)

        report["families"][fam] = {
            "market": mkt_slug,
            **compute_metrics(all_y, all_pm, all_pk, all_px if xgb_spec is not None else None),
        }

    return report


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seasons", default="2024,2025", help="Comma-separated scored seasons (default: 2024,2025)")
    ap.add_argument("--families", default=",".join(MARKET_MAP.keys()), help="Comma-separated MOD-04e families")
    ap.add_argument("--limit-weeks", type=int, default=None, help="Limit weeks per scored season for smoke runs")
    ap.add_argument("--cache-dir", default=BP_CACHE_DIR, help=f"Local BP cache dir (default: {BP_CACHE_DIR})")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args(argv)

    seasons = [int(s.strip()) for s in args.seasons.split(",") if s.strip()]
    families = [f.strip() for f in args.families.split(",") if f.strip()]
    for f in families:
        if f not in MARKET_MAP:
            ap.error(f"Unknown family {f!r}; expected subset of {list(MARKET_MAP)}")

    res = run_walkforward(seasons=seasons, families=families, limit_weeks=args.limit_weeks, cache_dir=args.cache_dir)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(res, indent=2))
    print(f"Wrote walk-forward report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
