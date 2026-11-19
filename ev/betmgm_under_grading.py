"""
ev.betmgm_under_grading -- Verdict report and evaluation module for BetMGM Unders Shadow.

Governing Protocol: RUL-20261119 (BetMGM unders shadow design, Neon).
Specification: RES-17 Build Packet (2026-11-19).

Adaptations to the ruling (A1-A5):
  - A1: Retrospective closing-only replication is defined over ClosingPanel presence.
  - A2: Winner's-curse 12.0pp cap from capability mask (ev/capability_mask.py) is cited
        as provenance for the overflow bucket boundary.
  - A3: Standing 4-book closing consensus floor from discovery screen coverage gate.
  - A4: Sibling distribution builder for NBA {points, rebounds, assists, threes} and NHL {assists, points}.
  - A5: Consensus line is leave-BetMGM-out modal line across reference books.

Isolation fence:
  Read-only against RES17/shadow ledger (chunks and finalized rows) and telemetry.
  Writes report artifacts to RES17/shadow/reports/{as_of}.json and reports/{as_of}.md.
  No betting, no execution, no venue calls.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from ev.betmgm_under_shadow import natural_key, decimal, implied, CP_REF_BOOKS
from ev.novig.shadow_bank import make_store

_ET = ZoneInfo("America/New_York")

# Protocol constants (§4, Ruling Q3)
RULING = "RUL-20261119"
PREFIX = "RES17/shadow"

START_DATE = "2026-11-24"
BACKSTOP = "2026-12-29"
DAY15 = "2026-12-09"
GREY_END = "2027-01-12"

# Accrual floors {family: (settled_n, present_G)} (Ruling Q3 table)
FLOORS = {
    "pooled_nba": (200, 10),
    "assists": (60, 10),
    "trio": (140, 10),
    "nhl": (75, 10),
}

# Governing gate delta band [0.03, 0.12] (Ruling Q1, Q4)
GATE_LO = 0.03
GATE_HI = 0.12


# --------------------------------------------------------------------------- serialization helper
def _json_default(o):
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, bool):
        return o
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


# --------------------------------------------------------------------------- math & statistical primitives
def clustered_t(values_by_day: dict[str, list[float]]) -> dict:
    """Cluster-robust game-day Student-t confidence intervals and p-value (§4, Ruling Q2).

    Pooled mean mu over all values;
    Var = (G / (G - 1)) * sum_d (S_d - n_d * mu)^2 / N^2
    where S_d = day sum, n_d = day count, N = total, G = count of non-empty days.
    se = sqrt(Var), t = mu / se, two-sided 95% CI and p from scipy.stats.t(df=G-1).
    If G < 2 or N == 0 or se == 0: lo/hi/p = None (mean reported if N > 0).
    """
    days = [d for d, vals in values_by_day.items() if len(vals) > 0]
    G = len(days)
    N = sum(len(values_by_day[d]) for d in days)

    if N == 0:
        return {"mean": None, "lo": None, "hi": None, "p": None, "G": 0, "n": 0, "t": None, "se": None}

    all_vals = [v for d in days for v in values_by_day[d]]
    mu = float(sum(all_vals) / N)

    if G < 2:
        return {"mean": mu, "lo": None, "hi": None, "p": None, "G": G, "n": N, "t": None, "se": None}

    dev_sq_sum = 0.0
    for d in days:
        vals = values_by_day[d]
        s_d = sum(vals)
        n_d = len(vals)
        diff = s_d - (n_d * mu)
        dev_sq_sum += diff * diff

    var = (float(G) / float(G - 1)) * (dev_sq_sum / float(N * N))
    se = math.sqrt(max(0.0, var))

    if se == 0.0 or not math.isfinite(se):
        return {"mean": mu, "lo": None, "hi": None, "p": None, "G": G, "n": N, "t": None, "se": 0.0}

    t_stat = mu / se
    df = G - 1
    t_crit = float(sp_stats.t.ppf(0.975, df=df))
    lo = mu - t_crit * se
    hi = mu + t_crit * se
    p_val = float(2.0 * sp_stats.t.sf(abs(t_stat), df=df))

    return {
        "mean": mu,
        "lo": lo,
        "hi": hi,
        "p": p_val,
        "G": G,
        "n": N,
        "t": t_stat,
        "se": se,
    }


def ece(p, y, bins: int = 10) -> float:
    """House Expected Calibration Error (§1.6, §4).

    Replicates scripts/discovery_screen.py:480 verbatim (10 equal-frequency bins).
    Argument order: (probabilities, outcomes).
    """
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    if len(y) == 0:
        return float("nan")
    qs = np.quantile(p, np.linspace(0, 1, bins + 1))
    qs[0], qs[-1] = -np.inf, np.inf
    idx = np.clip(np.digitize(p, qs[1:-1]), 0, bins - 1)
    out = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            out += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(out)


# --------------------------------------------------------------------------- slice metrics
def slice_stats(df: pd.DataFrame) -> dict:
    """Compute settled sample size, game days, clustered ROI, and hit-rate bias (§4).

    Restricted to settled rows where outcome_under in ('win', 'loss').
    """
    if df.empty or "outcome_under" not in df.columns:
        empty_t = clustered_t({})
        return {"n": 0, "G": 0, "roi": empty_t, "hit_bias": empty_t}

    settled = df[df["outcome_under"].isin(["win", "loss"])].copy()
    if settled.empty:
        empty_t = clustered_t({})
        return {"n": 0, "G": 0, "roi": empty_t, "hit_bias": empty_t}

    pnl_by_day: dict[str, list[float]] = {}
    bias_by_day: dict[str, list[float]] = {}

    for d, grp in settled.groupby("game_date"):
        d_str = str(d)
        pnls = [float(v) for v in grp["flat_pnl_1u"] if v is not None and math.isfinite(float(v))]
        pnl_by_day[d_str] = pnls

        biases = []
        for r in grp.itertuples(index=False):
            y_win = 1.0 if r.outcome_under == "win" else 0.0
            p_dist = getattr(r, "p_dist_under", None)
            if p_dist is not None and math.isfinite(float(p_dist)):
                biases.append(y_win - float(p_dist))
        bias_by_day[d_str] = biases

    roi_t = clustered_t(pnl_by_day)
    bias_t = clustered_t(bias_by_day)

    return {
        "n": len(settled),
        "G": settled["game_date"].nunique(),
        "roi": roi_t,
        "hit_bias": bias_t,
    }


def family_frames(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Partition ledger into governing and diagnostic family subsets (§4, Ruling Q5).

    assists: NBA & stat == 'assists'
    trio: NBA & stat in ('points', 'rebounds', 'threes')
    pooled_nba: NBA all four stats
    nhl: NHL (assists + points)
    nhl_assists: NHL assists
    nhl_points: NHL points
    NHL rows NEVER enter NBA frames.
    """
    if df.empty:
        return {k: df.copy() for k in ("assists", "trio", "pooled_nba", "nhl", "nhl_assists", "nhl_points")}

    nba_mask = df["sport"] == "nba"
    nhl_mask = df["sport"] == "nhl"

    nba = df[nba_mask]
    nhl = df[nhl_mask]

    return {
        "assists": nba[nba["stat"] == "assists"].copy(),
        "trio": nba[nba["stat"].isin(["points", "rebounds", "threes"])].copy(),
        "pooled_nba": nba.copy(),
        "nhl": nhl.copy(),
        "nhl_assists": nhl[nhl["stat"] == "assists"].copy(),
        "nhl_points": nhl[nhl["stat"] == "points"].copy(),
    }


def same_book_clv(df_slice: pd.DataFrame) -> dict:
    """BetMGM same-book closing price ratio and line move rate (§4, Ruling Q2).

    For rows with line_mgm_close == line_mgm and p_imp_under_mgm_close present:
      ratio = dec_under_mgm / dec_under_mgm_close - 1.0
    line_move_rate = share of close-paired rows where line_mgm_close != line_mgm.
    """
    if df_slice.empty:
        return {"mean": None, "line_move_rate": None, "n_same_line": 0, "n_close_paired": 0}

    # Close-paired rows have line_mgm_close or t_close not None
    paired = df_slice[df_slice["line_mgm_close"].notna()].copy()
    n_paired = len(paired)
    if n_paired == 0:
        return {"mean": None, "line_move_rate": None, "n_same_line": 0, "n_close_paired": 0}

    moved_count = int((paired["line_mgm_close"] != paired["line_mgm"]).sum())
    line_move_rate = float(moved_count / n_paired)

    same_line = paired[paired["line_mgm_close"] == paired["line_mgm"]].copy()
    valid_ratios = []
    for r in same_line.itertuples(index=False):
        dec_open = getattr(r, "dec_under_mgm", None)
        odds_close = getattr(r, "odds_under_mgm_close", None)
        p_imp_close = getattr(r, "p_imp_under_mgm_close", None)

        if dec_open is None:
            continue

        if odds_close is not None and not pd.isna(odds_close):
            dec_close = decimal(odds_close)
        elif p_imp_close is not None and not pd.isna(p_imp_close) and float(p_imp_close) > 0:
            dec_close = 1.0 / float(p_imp_close)
        else:
            continue

        if dec_close > 0:
            valid_ratios.append(float(dec_open) / float(dec_close) - 1.0)

    mean_ratio = float(np.mean(valid_ratios)) if valid_ratios else None
    return {
        "mean": mean_ratio,
        "line_move_rate": line_move_rate,
        "n_same_line": len(valid_ratios),
        "n_close_paired": n_paired,
    }


def consensus_clv(df_slice: pd.DataFrame) -> dict:
    """Leave-BetMGM-out consensus close CLV and line drift (§4, Ruling Q2).

    Rows where p_cons_under_close is present (>= 4 ref books quoted line_mgm at close):
      clv = p_cons_under_close / p_imp_under_mgm - 1.0
    Δ line_cons = line_cons_close - line_cons over close-paired rows.
    """
    if df_slice.empty:
        return {"mean": None, "delta_line_cons": None, "n": 0}

    valid_clvs = []
    for r in df_slice.itertuples(index=False):
        p_cons = getattr(r, "p_cons_under_close", None)
        p_imp = getattr(r, "p_imp_under_mgm", None)
        if p_cons is not None and not pd.isna(p_cons) and p_imp is not None and not pd.isna(p_imp):
            if float(p_imp) > 0:
                valid_clvs.append(float(p_cons) / float(p_imp) - 1.0)

    delta_lines = []
    for r in df_slice.itertuples(index=False):
        l_close = getattr(r, "line_cons_close", None)
        l_open = getattr(r, "line_cons", None)
        if l_close is not None and not pd.isna(l_close) and l_open is not None and not pd.isna(l_open):
            delta_lines.append(float(l_close) - float(l_open))

    mean_clv = float(np.mean(valid_clvs)) if valid_clvs else None
    mean_delta = float(np.mean(delta_lines)) if delta_lines else None

    return {
        "mean": mean_clv,
        "delta_line_cons": mean_delta,
        "n": len(valid_clvs),
    }


# --------------------------------------------------------------------------- telemetry monitors
def premise_kill(telemetry: dict[str, dict], as_of: str) -> dict:
    """Evaluate Premise Kill (Early Kill 3: consensus convergence) (§4, Ruling Q6).

    Pools NBA telemetry over all dates <= as_of.
    Triggered iff:
      N >= 400 total NBA pairs,
      G >= 4 total present days (with NBA pairs >= 1),
      eq_rate >= 0.75,
      AND longest consecutive present day run >= 4.
    """
    valid_dates = sorted([d for d in telemetry.keys() if d <= as_of])
    total_n = 0
    total_eq = 0
    present_dates = []

    for d in valid_dates:
        d_info = telemetry[d]
        if "nba" in d_info and isinstance(d_info["nba"], dict):
            n_pairs = d_info["nba"].get("n_pairs", 0)
            n_eq = d_info["nba"].get("n_line_eq", 0)
        elif "by_stat" in d_info:
            n_pairs = sum(
                v.get("n_pairs", 0) for k, v in d_info["by_stat"].items()
                if v.get("sport") == "nba" or k.startswith("nba:")
            )
            n_eq = sum(
                v.get("n_line_eq", 0) for k, v in d_info["by_stat"].items()
                if v.get("sport") == "nba" or k.startswith("nba:")
            )
        else:
            n_pairs = d_info.get("n_pairs", 0)
            n_eq = d_info.get("n_line_eq", 0)

        total_n += n_pairs
        total_eq += n_eq
        if n_pairs >= 1:
            present_dates.append(d)

    eq_rate = float(total_eq / total_n) if total_n > 0 else 0.0

    # Longest consecutive present calendar days run
    longest_run = 0
    cur_run = 0
    prev_dt = None
    for d_str in present_dates:
        try:
            dt = datetime.strptime(d_str, "%Y-%m-%d").date()
        except Exception:
            continue
        if prev_dt is not None and dt == prev_dt + timedelta(days=1):
            cur_run += 1
        else:
            cur_run = 1
        if cur_run > longest_run:
            longest_run = cur_run
        prev_dt = dt

    # Triggered requires N >= 400, >= 4 present days, eq_rate >= 0.75, and run >= 4
    triggered = bool(
        total_n >= 400 and
        len(present_dates) >= 4 and
        eq_rate >= 0.75 and
        longest_run >= 4
    )

    return {
        "triggered": triggered,
        "N": total_n,
        "eq_rate": eq_rate,
        "present_days": len(present_dates),
        "longest_run": longest_run,
        "as_of": as_of,
    }


def hold_drift(telemetry: dict[str, dict]) -> dict:
    """Evaluate two-sided implied sum drift against the 1.070 baseline (§4, Ruling Q7).

    Bands:
      median > 1.085 -> regime_shift True
      median < 1.055 -> tightened True
      else -> comparable True
    """
    nba_sums = []
    nhl_sums = []

    for d, d_info in telemetry.items():
        if "nba" in d_info and isinstance(d_info["nba"], dict):
            nba_sums.extend(d_info["nba"].get("sums", []))
        elif "by_stat" in d_info:
            for k, v in d_info["by_stat"].items():
                if v.get("sport") == "nba" or k.startswith("nba:"):
                    nba_sums.extend(v.get("sums", []))
        elif "sums" in d_info:
            nba_sums.extend(d_info.get("sums", []))

        if "nhl" in d_info and isinstance(d_info["nhl"], dict):
            nhl_sums.extend(d_info["nhl"].get("sums", []))
        elif "by_stat" in d_info:
            for k, v in d_info["by_stat"].items():
                if v.get("sport") == "nhl" or k.startswith("nhl:"):
                    nhl_sums.extend(v.get("sums", []))

    nba_med = float(statistics.median(nba_sums)) if nba_sums else None
    nhl_med = float(statistics.median(nhl_sums)) if nhl_sums else None

    regime_shift = bool(nba_med is not None and nba_med > 1.085)
    tightened = bool(nba_med is not None and nba_med < 1.055)
    comparable = bool(not regime_shift and not tightened)

    return {
        "nba_median": nba_med,
        "nhl_median": nhl_med,
        "regime_shift": regime_shift,
        "tightened": tightened,
        "comparable": comparable,
    }


def hold_normalized_roi(df_slice: pd.DataFrame, sums_by_row: list[float] | None = None) -> dict:
    """Diagnostic hold-normalized ROI re-centered to 1.070 baseline (§4, Ruling Q7).

    p_adj = p_imp_under_mgm * (1.070 / two_sided_sum_mgm)
    dec_adj = 1.0 / p_adj
    pnl_adj = dec_adj - 1.0 if win else -1.0
    """
    if df_slice.empty:
        return clustered_t({})

    settled = df_slice[df_slice["outcome_under"].isin(["win", "loss"])].copy()
    if settled.empty:
        return clustered_t({})

    adj_by_day: dict[str, list[float]] = {}
    for d, grp in settled.groupby("game_date"):
        day_pnls = []
        for r in grp.itertuples(index=False):
            p_imp = getattr(r, "p_imp_under_mgm", None)
            s_sum = getattr(r, "two_sided_sum_mgm", None)
            outcome = getattr(r, "outcome_under", None)
            if p_imp is not None and s_sum is not None and float(s_sum) > 0 and float(p_imp) > 0:
                p_adj = float(p_imp) * (1.070 / float(s_sum))
                dec_adj = 1.0 / p_adj if p_adj > 0 else 0.0
                pnl_adj = dec_adj - 1.0 if outcome == "win" else -1.0
                day_pnls.append(pnl_adj)
        adj_by_day[str(d)] = day_pnls

    return clustered_t(adj_by_day)


# --------------------------------------------------------------------------- Holm step-down
def holm(pvals: dict[str, float | None]) -> dict:
    """Holm-Bonferroni step-down procedure over governing families (§4, Ruling Q5).

    Governing families: assists, trio, nhl.
    Only non-None p-values enter; m = count of entered families.
    Sorted ascending; threshold = alpha / (m - rank) with alpha = 0.05.
    Pass while p < threshold in order.
    """
    out: dict[str, dict] = {}
    valid = [(fam, float(p)) for fam, p in pvals.items() if p is not None and math.isfinite(p)]

    for fam in pvals:
        out[fam] = {"p": pvals[fam], "threshold": None, "pass": False}

    if not valid:
        return out

    valid.sort(key=lambda x: x[1])
    m = len(valid)
    step_down_intact = True

    for rank, (fam, p) in enumerate(valid):
        thresh = 0.05 / float(m - rank)
        passed = bool(step_down_intact and (p < thresh))
        if not passed:
            step_down_intact = False
        out[fam] = {
            "p": p,
            "threshold": thresh,
            "pass": passed,
        }

    return out


# --------------------------------------------------------------------------- family verdict state machine
def verdict_for_family(
    family: str,
    stats: dict,
    ece_val: float,
    clv_same_book: dict | None,
    clv_consensus: dict | None,
    as_of: str,
    premise_kill_triggered: bool = False,
    cadence_state: dict | None = None,
    holm_info: dict | None = None,
    family_df: pd.DataFrame | None = None,
    diagnostics: dict | None = None,
) -> dict:
    """Execute ruling Q3-Q7 decision state machine for a single family (§4)."""
    n = stats.get("n", 0)
    G = stats.get("G", 0)
    floor_n, floor_g = FLOORS.get(family, (200, 10))

    roi_dict = stats.get("roi", {})
    roi_mean = roi_dict.get("mean")
    roi_lo = roi_dict.get("lo")
    roi_hi = roi_dict.get("hi")

    bias_dict = stats.get("hit_bias", {})
    bias_mean = bias_dict.get("mean")
    bias_hi = bias_dict.get("hi")

    sb_clv_mean = clv_same_book.get("mean") if clv_same_book else None
    cons_clv_mean = clv_consensus.get("mean") if clv_consensus else None

    reasons: list[str] = []
    verdict: str | None = None
    dropped_stats: list[str] = []

    present_days = cadence_state.get("present_days", G) if cadence_state else G

    # 1. Premise kill check (NBA families only) (§4, Ruling Q6 Early Kill 3)
    if premise_kill_triggered and family in ("pooled_nba", "assists", "trio"):
        verdict = "KILLED_PREMISE"
        reasons.append("Premise kill: BetMGM consensus convergence rate >= 75% on N >= 400 across G >= 4")

    # 2. Cadence circuit-breaker (§4, Ruling Q7)
    if verdict is None and as_of >= DAY15 and present_days < 4 and (n < floor_n or G < floor_g):
        verdict = "PAUSED_INSUFFICIENT_COVERAGE"
        reasons.append(f"Day-15 cadence circuit-breaker: present_days={present_days} < 4 at as_of={as_of}")

    # 3. Early kill checks (§4, Ruling Q6)
    if verdict is None:
        if family == "pooled_nba" and n >= 75 and G >= 5:
            if roi_mean is not None and roi_hi is not None and roi_mean <= -0.02 and roi_hi < 0.08:
                verdict = "KILLED_EARLY_ROI"
                reasons.append(f"Early Kill 1 (ROI): mean={roi_mean:.4f} <= -0.02 and hi={roi_hi:.4f} < 0.08")
            elif bias_mean is not None and bias_hi is not None and bias_mean < -0.05 and bias_hi < -0.015:
                verdict = "KILLED_EARLY_WC"
                reasons.append(f"Early Kill 2 (Winner's Curse): bias_mean={bias_mean:.4f} < -0.05 and bias_hi={bias_hi:.4f} < -0.015")
            elif roi_mean is not None and sb_clv_mean is not None and roi_mean <= 0.0 and sb_clv_mean <= -0.015:
                verdict = "KILLED_EARLY_ADVERSE"
                reasons.append(f"Early Kill 4 (Adverse Steam): roi_mean={roi_mean:.4f} <= 0.0 and same_book_clv={sb_clv_mean:.4f} <= -0.015")
        elif family == "assists" and n >= 35 and G >= 5:
            if roi_mean is not None and roi_hi is not None and roi_mean <= -0.02 and roi_hi < 0.08:
                verdict = "KILLED_EARLY_ROI"
                reasons.append(f"Early Kill 1 (ROI): mean={roi_mean:.4f} <= -0.02 and hi={roi_hi:.4f} < 0.08")
            elif bias_mean is not None and bias_hi is not None and bias_mean < -0.05 and bias_hi < -0.015:
                verdict = "KILLED_EARLY_WC"
                reasons.append(f"Early Kill 2 (Winner's Curse): bias_mean={bias_mean:.4f} < -0.05 and bias_hi={bias_hi:.4f} < -0.015")

    # 4. Floors met evaluation (§4, Ruling Q4)
    if verdict is None and n >= floor_n and G >= floor_g:
        # Check PASS conditions:
        # - Settled Flat ROI mean >= +4.0%
        # - 95% lower CI > 0.0%
        # - Holm step-down passed
        # - ECE <= 0.015
        # - Fired-slice hit-rate bias >= -2.0pp
        # - Same-Book Close CLV mean >= -0.5% (CLV subset empty -> HOLD_UNEVALUABLE_CLV)
        holm_passed = holm_info.get("pass", True) if holm_info else True

        pass_roi = bool(roi_mean is not None and roi_lo is not None and roi_mean >= 0.04 and roi_lo > 0.0)
        pass_ece = bool(math.isfinite(ece_val) and ece_val <= 0.015)
        pass_bias = bool(bias_mean is not None and bias_mean >= -0.02)

        if sb_clv_mean is None:
            pass_clv = False
            clv_empty_block = True
        else:
            pass_clv = bool(sb_clv_mean >= -0.005)
            clv_empty_block = False

        if pass_roi and holm_passed and pass_ece and pass_bias and pass_clv:
            # Check Trio drop rule
            if family == "trio" and family_df is not None:
                for st in ("points", "rebounds", "threes"):
                    st_df = family_df[family_df["stat"] == st]
                    st_stats = slice_stats(st_df)
                    st_n = st_stats["n"]
                    st_roi_m = st_stats["roi"].get("mean")
                    if st_n >= 35 and st_roi_m is not None and st_roi_m < -0.05:
                        dropped_stats.append(st)
                if dropped_stats:
                    verdict = "PASS_WITH_DROPPED_STAT"
                    reasons.append(f"Trio passed with dropped stat(s): {dropped_stats}")
                else:
                    verdict = "PASS"
                    reasons.append("All PASS criteria met across Trio stats")
            elif family == "nhl" and family_df is not None:
                # NHL graduation: assists must clear PASS independently
                nhl_assists_df = family_df[family_df["stat"] == "assists"]
                nhl_ast_stats = slice_stats(nhl_assists_df)
                ast_roi_m = nhl_ast_stats["roi"].get("mean")
                ast_roi_l = nhl_ast_stats["roi"].get("lo")
                ast_pass = bool(ast_roi_m is not None and ast_roi_l is not None and ast_roi_m >= 0.04 and ast_roi_l > 0.0)
                if not ast_pass:
                    verdict = "HOLD_POINTS_BLOCKED"
                    reasons.append("Pooled NHL cleared bars but NHL assists sub-slice failed PASS; points cannot graduate before assists")
                else:
                    verdict = "PASS"
                    reasons.append("NHL pooled and assists sub-slice passed")
            else:
                verdict = "PASS"
                reasons.append("All PASS criteria satisfied")

        elif clv_empty_block and pass_roi and holm_passed and pass_ece and pass_bias:
            verdict = "HOLD_UNEVALUABLE_CLV"
            reasons.append("PASS blocked: same-book CLV subset is empty")

        else:
            # Check KILL conditions (any one kills permanently)
            if roi_mean is not None and (roi_mean < 0.01 or (roi_hi is not None and roi_hi < 0.04)):
                verdict = "KILLED_ROI"
                reasons.append(f"ROI failure: mean={roi_mean:.4f} < 0.01 or hi={roi_hi} < 0.04")
            elif bias_mean is not None and bias_hi is not None and bias_mean < -0.04 and bias_hi < -0.01:
                verdict = "KILLED_WC"
                reasons.append(f"Winner's curse emergence: bias_mean={bias_mean:.4f} < -0.04 and bias_hi={bias_hi:.4f} < -0.01")
            elif math.isfinite(ece_val) and ece_val > 0.015:
                verdict = "KILLED_CALIB_FLAG"
                reasons.append(f"Calibration failure: ECE={ece_val:.4f} > 0.015 (flag pending recalibration judgment)")
            # Check Grey zone
            elif (
                roi_mean is not None and
                0.01 <= roi_mean < 0.04 and
                (roi_lo is None or roi_lo <= 0.0) and
                pass_ece and
                (sb_clv_mean is None or sb_clv_mean >= -0.005)
            ):
                if as_of < GREY_END:
                    verdict = "EXTENDED_GREY"
                    reasons.append(f"Grey zone extension to {GREY_END}: roi_mean={roi_mean:.4f} in [0.01, 0.04)")
                else:
                    verdict = "KILLED_ROI"
                    reasons.append(f"Grey zone expired after {GREY_END} without clearing PASS bar")
            else:
                verdict = "HOLD"
                reasons.append("Floors met but neither PASS nor KILL conditions resolved")

    # 5. Floors unmet evaluation (Ruling Q3, verbatim disposition)
    # "Any family still short of n or G on Dec 29 receives an automatic
    # INSUFFICIENT_N / SHELVED_FOR_COVERAGE verdict." No kill-bar evaluation
    # on sub-floor samples at the backstop; genuine kills fire earlier via
    # the early-kill step above or the floors-met step.
    if verdict is None:
        if as_of >= BACKSTOP:
            verdict = "INSUFFICIENT_N_SHELVED"
            reasons.append(f"Backstop reached at {as_of} with insufficient n/G ({n}/{floor_n}, {G}/{floor_g}); shelved for coverage")
        else:
            verdict = "ACCRUING"
            reasons.append(f"Accruing volume: n={n}/{floor_n}, G={G}/{floor_g} before backstop {BACKSTOP}")

    return {
        "family": family,
        "verdict": verdict,
        "n": n,
        "G": G,
        "floors": (floor_n, floor_g),
        "roi": roi_dict,
        "bias": bias_dict,
        "ece": ece_val,
        "clv_same_book": clv_same_book,
        "clv_consensus": clv_consensus,
        "holm": holm_info or {"p": roi_dict.get("p"), "threshold": None, "pass": False},
        "diagnostics": diagnostics or {},
        "reasons": reasons,
        "dropped_stats": dropped_stats,
    }


# --------------------------------------------------------------------------- closing artifact cuts
def artifact_cuts(df_pooled_nba_governing_settled: pd.DataFrame) -> dict:
    """Retrospective stratified cuts to detect closing-snapshot artifacts (§4, Ruling Q4).

    Cuts:
      (1) roi_all: all actionable triggers
      (2) roi_present_true: triggers where BetMGM was present at close
      (3) roi_present_false: triggers where BetMGM pulled its board
      (4) roi_closing_only: triggers where BetMGM quoted exact same line at close
    Signature: (roi_all <= 0 and same_book_clv < -0.01) and
               (roi_closing_only - roi_all >= 0.05 or roi_present_false <= roi_present_true - 0.05)
    """
    df = df_pooled_nba_governing_settled
    if df.empty:
        empty_t = clustered_t({})
        return {
            "roi_all": empty_t,
            "roi_present_true": empty_t,
            "roi_present_false": empty_t,
            "roi_closing_only": empty_t,
            "same_book_clv": None,
            "signature": None,
        }

    s_all = slice_stats(df)["roi"]
    s_pres_t = slice_stats(df[df["mgm_present_at_close"] == True])["roi"]  # noqa: E712
    s_pres_f = slice_stats(df[df["mgm_present_at_close"] == False])["roi"]  # noqa: E712

    same_line_mask = (df["mgm_present_at_close"] == True) & (df["line_mgm_close"] == df["line_mgm"])  # noqa: E712
    s_close_only = slice_stats(df[same_line_mask])["roi"]

    sb_clv = same_book_clv(df).get("mean")

    m_all = s_all.get("mean")
    m_pres_t = s_pres_t.get("mean")
    m_pres_f = s_pres_f.get("mean")
    m_close = s_close_only.get("mean")

    signature = None
    if m_all is not None and sb_clv is not None:
        cond1 = (m_all <= 0.0 and sb_clv < -0.01)
        cond2 = False
        if m_close is not None and (m_close - m_all >= 0.05):
            cond2 = True
        if m_pres_f is not None and m_pres_t is not None and (m_pres_f <= m_pres_t - 0.05):
            cond2 = True
        signature = bool(cond1 and cond2)

    return {
        "roi_all": s_all,
        "roi_present_true": s_pres_t,
        "roi_present_false": s_pres_f,
        "roi_closing_only": s_close_only,
        "same_book_clv": sb_clv,
        "signature": signature,
    }


# --------------------------------------------------------------------------- ledger loader
def load_ledger(store) -> pd.DataFrame:
    """Load and deduplicate the complete RES17/shadow ledger from store (§4, §1.5).

    Reads finalized rows from RES17/shadow/finalized/ and raw chunks from RES17/shadow/chunks/.
    Finalized rows take precedence over unfinalized chunk rows on natural_key match.
    """
    rows_by_key: dict[str, dict] = {}
    finalized_keys: set[str] = set()

    # 1. Finalized rows
    fin_files = store.list(f"{PREFIX}/finalized/")
    for ff in fin_files:
        if not ff.endswith(".jsonl.gz"):
            continue
        try:
            data = gzip.decompress(store.read(ff)).decode("utf-8")
            for line in data.splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                nk = natural_key(r)
                rows_by_key[nk] = r
                finalized_keys.add(nk)
        except Exception as e:
            print(f"betmgm_under_grading: warning reading finalized file {ff}: {e}", file=sys.stderr)

    # 2. Raw chunks (fallback for unfinalized days)
    chunk_files = store.list(f"{PREFIX}/chunks/")
    for cf in chunk_files:
        if not cf.endswith(".jsonl.gz"):
            continue
        try:
            data = gzip.decompress(store.read(cf)).decode("utf-8")
            for line in data.splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                nk = natural_key(r)
                if nk not in finalized_keys and nk not in rows_by_key:
                    rows_by_key[nk] = r
        except Exception as e:
            print(f"betmgm_under_grading: warning reading chunk file {cf}: {e}", file=sys.stderr)

    if not rows_by_key:
        return pd.DataFrame()

    return pd.DataFrame(list(rows_by_key.values()))


def load_telemetry(store) -> dict[str, dict]:
    """Load all daily telemetry files from RES17/shadow/telemetry/."""
    out: dict[str, dict] = {}
    telem_files = store.list(f"{PREFIX}/telemetry/")
    for tf in telem_files:
        if not tf.endswith(".json"):
            continue
        date_str = os.path.basename(tf).replace(".json", "")
        try:
            data = json.loads(store.read(tf).decode("utf-8"))
            out[date_str] = data
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- report builder
def build_report(ledger_df: pd.DataFrame, telemetry: dict[str, dict], as_of: str) -> dict:
    """Assemble complete verdict report across all families and diagnostics (§4)."""
    p_kill = premise_kill(telemetry, as_of)
    h_drift = hold_drift(telemetry)

    # Filter ledger to the registered cohort window: game_date in [START_DATE, as_of].
    # Pre-window rows (e.g. a backfill of Nov 15-23) never enter the verdict cohort.
    if not ledger_df.empty and "game_date" in ledger_df.columns:
        df = ledger_df[(ledger_df["game_date"] >= START_DATE) & (ledger_df["game_date"] <= as_of)].copy()
    else:
        df = ledger_df.copy()

    f_frames = family_frames(df)

    # Calibration ECE computed over ALL priced rows in family scope (with win/loss outcomes)
    # §4: "ECE uses rows with outcome win/loss — calibration needs outcomes; use settled rows regardless of gate"
    ece_by_fam: dict[str, float] = {}
    for fam_name, fam_df in f_frames.items():
        if fam_df.empty or "outcome_under" not in fam_df.columns:
            ece_by_fam[fam_name] = float("nan")
        else:
            settled = fam_df[fam_df["outcome_under"].isin(["win", "loss"])]
            if settled.empty:
                ece_by_fam[fam_name] = float("nan")
            else:
                p_vec = settled["p_dist_under"].to_numpy(dtype=float)
                y_vec = (settled["outcome_under"] == "win").to_numpy(dtype=float)
                ece_by_fam[fam_name] = ece(p_vec, y_vec)

    # Slices for governing gate: gate_bucket in ('3pp', '5pp', '8pp') (delta in [0.03, 0.12])
    gov_slices: dict[str, pd.DataFrame] = {}
    for fam_name, fam_df in f_frames.items():
        if fam_df.empty or "gate_bucket" not in fam_df.columns:
            gov_slices[fam_name] = fam_df.copy()
        else:
            gov_slices[fam_name] = fam_df[fam_df["gate_bucket"].isin(["3pp", "5pp", "8pp"])].copy()

    # Compute slice stats for governing slices
    gov_stats = {fam_name: slice_stats(sdf) for fam_name, sdf in gov_slices.items()}

    # CLV benchmarks
    sb_clvs = {fam_name: same_book_clv(sdf) for fam_name, sdf in gov_slices.items()}
    cons_clvs = {fam_name: consensus_clv(sdf) for fam_name, sdf in gov_slices.items()}

    # Diagnostic slices (5pp and 8pp)
    diag_stats = {}
    for fam_name, fam_df in f_frames.items():
        if fam_df.empty or "gate_bucket" not in fam_df.columns:
            diag_stats[fam_name] = {"diag_5pp": slice_stats(fam_df), "diag_8pp": slice_stats(fam_df)}
        else:
            s_5pp = fam_df[fam_df["gate_bucket"].isin(["5pp", "8pp"])]
            s_8pp = fam_df[fam_df["gate_bucket"] == "8pp"]
            diag_stats[fam_name] = {
                "diag_5pp": slice_stats(s_5pp),
                "diag_8pp": slice_stats(s_8pp),
            }

    # Holm correction across the 3 governing families: assists, trio, nhl
    pvals = {
        "assists": gov_stats["assists"]["roi"].get("p"),
        "trio": gov_stats["trio"]["roi"].get("p"),
        "nhl": gov_stats["nhl"]["roi"].get("p"),
    }
    holm_results = holm(pvals)

    # Cadence state
    cadence = {
        "present_days": p_kill.get("present_days", 0),
        "longest_consecutive_run": p_kill.get("longest_run", 0),
        "nba_pairs_total": p_kill.get("N", 0),
        "consensus_convergence_rate": p_kill.get("eq_rate", 0.0),
    }

    # Evaluate verdicts
    verdicts = {}
    for fam in ("assists", "trio", "nhl", "pooled_nba"):
        verdicts[fam] = verdict_for_family(
            family=fam,
            stats=gov_stats[fam],
            ece_val=ece_by_fam[fam],
            clv_same_book=sb_clvs[fam],
            clv_consensus=cons_clvs[fam],
            as_of=as_of,
            premise_kill_triggered=p_kill["triggered"],
            cadence_state=cadence,
            holm_info=holm_results.get(fam),
            family_df=f_frames[fam],
            diagnostics=diag_stats[fam],
        )

    # Retrospective artifact cuts on settled pooled NBA
    pooled_nba_settled = gov_slices["pooled_nba"]
    if not pooled_nba_settled.empty and "outcome_under" in pooled_nba_settled.columns:
        pooled_nba_settled = pooled_nba_settled[pooled_nba_settled["outcome_under"].isin(["win", "loss"])]
    art_cuts = artifact_cuts(pooled_nba_settled)

    report = {
        "meta": {
            "ruling": RULING,
            "as_of": as_of,
            "start_date": START_DATE,
            "backstop": BACKSTOP,
            "day15_checkpoint": DAY15,
            "grey_zone_end": GREY_END,
        },
        "cadence": cadence,
        "premise_kill": p_kill,
        "hold_drift": h_drift,
        "holm_step_down": holm_results,
        "verdicts": verdicts,
        "artifact_cuts": art_cuts,
    }

    return report


# --------------------------------------------------------------------------- markdown renderer
def render_markdown(report: dict) -> str:
    """Render human-readable plain language markdown summary of grading report (§4)."""
    meta = report["meta"]
    cad = report["cadence"]
    pk = report["premise_kill"]
    hd = report["hold_drift"]
    verdicts = report["verdicts"]
    cuts = report["artifact_cuts"]

    lines = [
        f"# RES-17 BetMGM Unders Shadow Verdict Report — As of {meta['as_of']}",
        "",
        f"**Governing Protocol**: {meta['ruling']} | **Cohort Window**: {meta['start_date']} to {meta['backstop']}",
        "",
        "## 1. Cadence & Monitoring Telemetry",
        f"- **NBA Present Days**: {cad['present_days']} (Longest run: {cad['longest_consecutive_run']} days)",
        f"- **Premise Kill (Convergence)**: {'TRIGGERED (VETO)' if pk['triggered'] else 'Clear'} (N={pk['N']}, Line-equals-consensus={pk['eq_rate']*100:.1f}%)",
        f"- **Hold Baseline Telemetry**: NBA Median={hd['nba_median']} | Regime shift: {hd['regime_shift']} | Tightened: {hd['tightened']}",
        "",
        "## 2. Family Verdicts Summary",
        "",
        "| Family | Verdict | Settled n (Floor) | Days G (Floor) | Flat ROI (95% CI) | ECE | Bias | Same-Book CLV | Holm p (Thresh) |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]

    for fam_name in ("assists", "trio", "nhl", "pooled_nba"):
        v = verdicts.get(fam_name, {})
        n_val = v.get("n", 0)
        g_val = v.get("G", 0)
        fn, fg = v.get("floors", (0, 0))
        roi = v.get("roi", {})
        r_m = f"{roi.get('mean')*100:+.2f}%" if roi.get("mean") is not None else "N/A"
        r_ci = f"[{roi.get('lo')*100:+.1f}%, {roi.get('hi')*100:+.1f}%]" if roi.get("lo") is not None else "[N/A]"
        ece_str = f"{v.get('ece'):.4f}" if math.isfinite(v.get("ece", float("nan"))) else "N/A"
        bias = v.get("bias", {})
        bias_str = f"{bias.get('mean')*100:+.2f}pp" if bias.get("mean") is not None else "N/A"
        sb = v.get("clv_same_book", {})
        sb_str = f"{sb.get('mean')*100:+.2f}%" if sb and sb.get("mean") is not None else "N/A"
        h = v.get("holm", {})
        h_str = f"{h.get('p'):.4f} ({h.get('threshold'):.4f})" if h.get("threshold") is not None else "N/A"

        label = fam_name.upper() if fam_name != "pooled_nba" else "POOLED_NBA (Context)"
        lines.append(
            f"| **{label}** | `{v.get('verdict')}` | {n_val} ({fn}) | {g_val} ({fg}) | {r_m} {r_ci} | {ece_str} | {bias_str} | {sb_str} | {h_str} |"
        )

    lines.extend([
        "",
        "## 3. Decision Reasons & Family Notes",
    ])
    for fam_name in ("assists", "trio", "nhl", "pooled_nba"):
        v = verdicts.get(fam_name, {})
        lines.append(f"### {fam_name.upper()}")
        lines.append(f"- **Verdict**: `{v.get('verdict')}`")
        for r in v.get("reasons", []):
            lines.append(f"- {r}")
        if v.get("dropped_stats"):
            lines.append(f"- **Dropped Stats**: {v['dropped_stats']}")

        diags = v.get("diagnostics", {})
        if diags:
            lines.append("- **Diagnostic Slices (Non-Governing)**:")
            d5 = diags.get("diag_5pp", {}).get("roi", {})
            d8 = diags.get("diag_8pp", {}).get("roi", {})
            d5_m = f"{d5.get('mean')*100:+.2f}%" if d5.get("mean") is not None else "N/A"
            d8_m = f"{d8.get('mean')*100:+.2f}%" if d8.get("mean") is not None else "N/A"
            lines.append(f"  - 5pp gate ROI: {d5_m} (n={diags.get('diag_5pp', {}).get('n', 0)})")
            lines.append(f"  - 8pp gate ROI: {d8_m} (n={diags.get('diag_8pp', {}).get('n', 0)})")

    lines.extend([
        "",
        "## 4. Retrospective Closing-Snapshot Artifact Check",
        f"- **Artifact Signature Present**: `{cuts.get('signature')}`",
        f"- Realized ROI (All Triggers): {cuts.get('roi_all', {}).get('mean', 'N/A')}",
        f"- Realized ROI (Board Pulled / Present=False): {cuts.get('roi_present_false', {}).get('mean', 'N/A')}",
        f"- Realized ROI (Closing-Snapshot-Only): {cuts.get('roi_closing_only', {}).get('mean', 'N/A')}",
        "",
        "---",
        "_Report generated deterministically by `ev.betmgm_under_grading` under RUL-20261119 protocol._",
    ])

    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="BetMGM Unders Shadow Verdict & Grading Reporter (RES-17)")
    ap.add_argument("--store", default="gcs", help="Store type: gcs | gcloud | dir:PATH")
    ap.add_argument("--bucket", default=None, help="GCS bucket name")
    ap.add_argument("--as-of", default=None, help="As-of evaluation date (YYYY-MM-DD); default today ET")
    ap.add_argument("--out-dir", default=None, help="Optional local directory to write report JSON and MD")
    ap.add_argument("--no-bank", action="store_true", help="Do not write reports to the store")
    args = ap.parse_args(argv)

    as_of = args.as_of or datetime.now(timezone.utc).astimezone(_ET).date().isoformat()
    store = make_store(args.store, args.bucket)

    ledger_df = load_ledger(store)
    telem = load_telemetry(store)

    report = build_report(ledger_df, telem, as_of)
    md_text = render_markdown(report)

    if not args.no_bank:
        r_json_key = f"{PREFIX}/reports/{as_of}.json"
        r_md_key = f"{PREFIX}/reports/{as_of}.md"
        store.put(r_json_key, json.dumps(report, indent=1, default=_json_default).encode("utf-8"))
        store.put(r_md_key, md_text.encode("utf-8"))

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        with open(os.path.join(args.out_dir, f"{as_of}.json"), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, default=_json_default)
        with open(os.path.join(args.out_dir, f"{as_of}.md"), "w", encoding="utf-8") as fh:
            fh.write(md_text)

    print(md_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
