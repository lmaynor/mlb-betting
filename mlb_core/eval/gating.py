"""Confidence-gated scoring of a game engine against the closing line: "does the engine beat the market WHEN it is confident?"

This is a garden of forking paths, so the protocol is fixed in code BEFORE any outcome is looked at (PRE-REGISTRATION):

  1. Gates are the small closed family in GATES below; every gate has exactly ONE threshold.
       EDGE   |p_model - p_mkt| >= the DEV 75th percentile of that statistic (per market)
       SD     Kalman rating uncertainty (c_sd) <= the DEV 33rd percentile (per market)
       AGREE  the Kalman-only margin/total, the feature-side margin/total and the blended engine are all on the same side of
              the market line AND that side is the side the engine bets (not applicable where the engine has one variant)
       PHASE  games already played this season >= PHASE_MIN[sport]  (a constant fixed in this file, not fitted)
       DQ     no dropped box-score game in either team's last 10  (declared degenerate when it removes < 2% of games)
       COMBO  EDGE & AGREE & PHASE (AGREE omitted where not applicable)
  2. Thresholds come from freeze_thresholds(), which reads DEV-split FEATURES only (never y) and returns a JSON-able dict that
     is frozen before the held-out split is scored. Seasons: chronological, earlier = DEV, later = HELD-OUT.
  3. Screen on DEV: a cell advances only if its DEV paired log-loss gain > 0, DEV blend c > 0 and n >= MIN_N_SCREEN. The held-out
     split is then scored ONCE, for every cell (an exhibit) and, as the confirmatory family, for the survivors only.
  4. Multiplicity: Holm over the confirmatory family; AND a Westfall-Young style max-statistic sign-flip over the WHOLE family of
     gates tried (date-clustered, so overlapping gates are handled), which is the honest penalty for every fork.

Event frame = mlb_core.eval.market_scoring's (date, p_model, p_mkt, y, dec_*) plus the confidence inputs, all pre-game:
    c_kal c_feat c_mu   Kalman-only / feature-side / blended expected margin (ML, spread) or total, on the line's scale
    c_line              the market's line on that scale (home margin the market implies, or the total line)
    c_sd                Kalman rating sd of that quantity     c_phase  games played this season     c_dq  1 = hole in recent data
UNVALIDATED research tool: nothing in serving reads this.
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from mlb_core.eval import market_scoring as ms

SPEC_VERSION = "gates-v1-2026-11-05"
GATES = ("EDGE", "SD", "AGREE", "PHASE", "DQ", "COMBO")
EDGE_Q = 0.75            # EDGE: keep the top quartile of dev |edge|
SD_Q = 1.0 / 3.0         # SD: keep the lowest tercile of dev Kalman sd
PHASE_MIN = {"nfl": 6, "nba": 20}      # NFL: week >= 6 (>= 5 games played); NBA: >= 20 games played by both teams
DQ_DEGENERATE = 0.98     # a gate that keeps more than this share of games is not a gate
MIN_N_SCREEN = 100       # dev cells smaller than this do not advance
SESOI = 0.001            # nats / game: the paired log-loss gain that is roughly break-even against a ~4.5% two-way vig
Z_ONE_SIDED_05, Z_POWER_80 = 1.6449, 0.8416
EPS = 1e-6


# ---------------------------------------------------------------------------------------------------- features
def derive_conf(df: pd.DataFrame) -> pd.DataFrame:
    """Add edge and the agreement flag (1 / 0 / NaN where not applicable) from the c_* inputs. No outcomes are used."""
    d = df.copy()
    d["edge"] = (d["p_model"] - d["p_mkt"]).abs()
    bet = np.sign(d["p_model"] - d["p_mkt"])
    s_mu = np.sign(d["c_mu"] - d["c_line"])
    s_k = np.sign(d["c_kal"] - d["c_line"])
    s_f = np.sign(d["c_feat"] - d["c_line"])
    ok = d[["c_mu", "c_line", "c_kal", "c_feat"]].notna().all(axis=1) & (bet != 0)
    d["agree"] = np.where(ok, ((s_mu == bet) & (s_k == bet) & (s_f == bet)).astype(float), np.nan)
    return d


def freeze_thresholds(sport: str, dev: Dict[str, pd.DataFrame]) -> Dict[str, object]:
    """Thresholds from DEV FEATURES ONLY (the y column is never read here; a test pins that)."""
    per: Dict[str, dict] = {}
    for market, df in dev.items():
        d = derive_conf(df)
        per[market] = {
            "n_dev": int(len(d)),
            "edge_min": float(d["edge"].quantile(EDGE_Q)),
            "sd_max": float(d["c_sd"].quantile(SD_Q)) if d["c_sd"].notna().any() else None,
            "agree_applicable": bool(d["agree"].notna().mean() > 0.5),
            "dq_share_flagged": float(d["c_dq"].mean()) if d["c_dq"].notna().any() else None,
        }
    return {"version": SPEC_VERSION, "sport": sport, "phase_min": PHASE_MIN[sport], "edge_q": EDGE_Q, "sd_q": SD_Q,
            "markets": per}


def gate_masks(df: pd.DataFrame, th: Dict[str, object], market: str) -> Dict[str, Optional[pd.Series]]:
    """Boolean mask per gate for one market; None = gate not applicable / degenerate on this market (not tried)."""
    d = derive_conf(df)
    m = th["markets"][market]
    out: Dict[str, Optional[pd.Series]] = {}
    out["EDGE"] = d["edge"] >= m["edge_min"]
    out["SD"] = (d["c_sd"] <= m["sd_max"]) if m["sd_max"] is not None else None
    out["AGREE"] = (d["agree"] == 1.0) if m["agree_applicable"] else None
    out["PHASE"] = d["c_phase"] >= th["phase_min"]
    dq_ok = m["dq_share_flagged"] is not None and (1.0 - m["dq_share_flagged"]) < DQ_DEGENERATE
    out["DQ"] = (d["c_dq"] == 0.0) if dq_ok else None
    combo = out["EDGE"] & out["PHASE"]
    if out["AGREE"] is not None:
        combo = combo & out["AGREE"]
    out["COMBO"] = combo
    return out


# ---------------------------------------------------------------------------------------------------- statistics
def normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0)) if np.isfinite(z) else float("nan")


def mde(se: float) -> float:
    """Minimum detectable true mean (one-sided alpha 0.05, power 80%) for an estimator with standard error se."""
    return (Z_ONE_SIDED_05 + Z_POWER_80) * se


def n_needed(n: int, se: float, target: float) -> float:
    """Games needed (same coverage) for the MDE to fall to `target`, assuming se ~ 1/sqrt(n)."""
    return float(n * (mde(se) / target) ** 2) if target > 0 and np.isfinite(se) else float("nan")


def blend_c_offset(df: pd.DataFrame, iters: int = 50) -> Dict[str, float]:
    """Logistic fit  y ~ logit(p_mkt) + c * (logit(p_model) - logit(p_mkt))  with the market coefficient FIXED at 1 (offset), so c is
    identified in every market including spread / total where p_mkt ~ 0.5. c > 0: the model adds information; c < 0: noise.
    Date-clustered robust SE."""
    lm = ms._logit(df["p_model"])
    lk = ms._logit(df["p_mkt"])
    x = lm - lk
    y = df["y"].to_numpy(dtype=float)
    c = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(lk + c * x)))
        w = np.clip(p * (1 - p), 1e-9, None)
        h = float((w * x * x).sum())
        if h <= 0:
            return {"c": float("nan"), "se_c": float("nan"), "z_c": float("nan"), "n": int(len(y))}
        step = float((x * (y - p)).sum() / h)
        c += step
        if abs(step) < 1e-9:
            break
    p = 1 / (1 + np.exp(-(lk + c * x)))
    w = np.clip(p * (1 - p), 1e-9, None)
    h = float((w * x * x).sum())
    sc = pd.Series(x * (y - p)).groupby(np.asarray(df["date"])).sum().to_numpy()
    g = len(sc)
    var = float((sc ** 2).sum()) * (g / (g - 1) if g > 1 else 1.0) / (h * h)
    se = math.sqrt(max(var, 0.0))
    return {"c": float(c), "se_c": se, "z_c": float(c / se) if se > 0 else float("nan"), "n": int(len(y))}


def model_roi(df: pd.DataFrame) -> Dict[str, float]:
    """Flat 1u on the side the model prefers over the market: ROI at the median (close) and best available price, with
    date-clustered SE and 95% CI, plus the both-sides no-model control at the same prices."""
    d = ms._side_frame(df)
    out: Dict[str, float] = {}
    for kind in ("med", "best"):
        col = f"dec_{kind}"
        if col in d and d[col].notna().any():
            r = ms.clustered_mean(d["win"] * d[col] - 1.0, d["date"])
            out[f"roi_{kind}"], out[f"roi_{kind}_se"] = r["mean"], r["se"]
            out[f"roi_{kind}_lo"], out[f"roi_{kind}_hi"] = r["mean"] - 1.96 * r["se"], r["mean"] + 1.96 * r["se"]
    ctl = ms.line_shop_control(df)
    for k in ("roi_med", "roi_best", "shopping_worth"):
        if k in ctl:
            out[f"ctl_{k}"] = ctl[k]
    return out


def evaluate_cell(sub: pd.DataFrame, n_all: int) -> Dict[str, object]:
    """Everything the protocol asks for on one (sport, market, gate) subset."""
    n = len(sub)
    out: Dict[str, object] = {"n": int(n), "coverage": float(n / n_all) if n_all else float("nan"),
                              "dates": int(sub["date"].nunique()) if n else 0}
    if n < 10:
        out["too_small"] = True
        return out
    ll = ms.paired_logloss(sub)
    out.update({"gain": ll["mean"], "gain_se": ll["se"], "gain_z": ll["z"], "gain_lo": ll["mean"] - 1.96 * ll["se"],
                "gain_hi": ll["mean"] + 1.96 * ll["se"], "p_one_sided": normal_sf(ll["z"]),
                "ll_model": ll["ll_model"], "ll_market": ll["ll_market"], "mde80": mde(ll["se"]),
                "n_for_sesoi": n_needed(n, ll["se"], SESOI)})
    try:
        b = blend_c_offset(sub)
        out.update({"c": b["c"], "c_se": b["se_c"], "c_z": b["z_c"]})
    except Exception:   # noqa: BLE001 - a degenerate subset must not kill the report
        out.update({"c": float("nan"), "c_se": float("nan"), "c_z": float("nan")})
    out.update(model_roi(sub))
    if "roi_med_se" in out:
        out["roi_mde80"] = mde(out["roi_med_se"])
    # edge-bucket monotonicity inside the subset, buckets = subset edge terciles (fixed cut points would leave empty bins)
    e = (sub["p_model"] - sub["p_mkt"]).abs()
    cuts = sorted(set(float(x) for x in e.quantile([1 / 3, 2 / 3]).to_numpy()))
    edges = [0.0] + [c for c in cuts if c > 0] + [1.0]
    if len(edges) >= 4 and n >= 90:
        eb = ms.edge_buckets(sub, edges)
        out["mono_gap_rho"] = eb["monotonic"].get("gap_spearman", float("nan"))
        out["mono_roi_rho"] = eb["monotonic"].get("roi_med_spearman", float("nan"))
        out["buckets"] = [{k: (float(v) if not isinstance(v, str) else v) for k, v in r.items()}
                          for r in eb["table"].to_dict("records")]
    return out


def cell_diffs(sub: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """(dates, d_i = ll_market - ll_model) for the sign-flip family."""
    d = ms._ll(sub["p_mkt"], sub["y"]) - ms._ll(sub["p_model"], sub["y"])
    return np.asarray(sub["date"]).astype(str), np.asarray(d, dtype=float)


# ---------------------------------------------------------------------------------------------------- multiplicity
def holm(pvals: Dict[object, float]) -> Dict[object, float]:
    """Holm step-down adjusted p-values."""
    items = sorted(((p, k) for k, p in pvals.items() if np.isfinite(p)), key=lambda t: t[0])
    m = len(items)
    adj: Dict[object, float] = {k: float("nan") for k in pvals}
    run = 0.0
    for i, (p, k) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        adj[k] = run
    return adj


def maxt_signflip(cells: Dict[object, Tuple[np.ndarray, np.ndarray]], B: int = 4000, seed: int = 20261105
                  ) -> Dict[object, Dict[str, float]]:
    """Max-statistic sign-flip over the whole family. One random +-1 per game DAY is shared by every cell (so cells that overlap in
    games stay dependent); each cell's date-clustered z is recomputed on the flipped data; the adjusted p-value of a cell is the share
    of flips whose MAXIMUM z over all cells reaches its observed z. Null: paired differences are symmetric about 0 within a day."""
    days = sorted({str(x) for dates, _ in cells.values() for x in dates})
    pos = {d: i for i, d in enumerate(days)}
    rng = np.random.default_rng(seed)
    eps = rng.choice(np.array([-1.0, 1.0]), size=(B, len(days)))
    zobs: Dict[object, float] = {}
    zflip: Dict[object, np.ndarray] = {}
    for key, (dates, d) in cells.items():
        n = len(d)
        if n < 10:
            continue
        idx = np.array([pos[str(x)] for x in dates])
        s = np.bincount(idx, weights=d, minlength=len(days))
        cnt = np.bincount(idx, minlength=len(days)).astype(float)
        present = cnt > 0
        g = int(present.sum())
        if g < 2:
            continue
        corr = g / (g - 1)

        def zstat(sv: np.ndarray) -> np.ndarray:        # sv: (R, D) cluster sums
            m = sv.sum(axis=1) / n
            r = (sv - cnt[None, :] * m[:, None])[:, present]
            se = np.sqrt(corr * (r ** 2).sum(axis=1)) / n
            return np.where(se > 0, m / np.where(se > 0, se, 1.0), np.nan)

        zobs[key] = float(zstat(s[None, :])[0])
        zflip[key] = zstat(eps * s[None, :])
    if not zflip:
        return {}
    mat = np.vstack(list(zflip.values()))
    mx = np.nanmax(mat, axis=0)
    out = {}
    for key, z in zobs.items():
        out[key] = {"z": z, "p_maxt": float((1 + (mx >= z).sum()) / (B + 1)),
                    "p_single_flip": float((1 + (zflip[key] >= z).sum()) / (B + 1))}
    return out


# ---------------------------------------------------------------------------------------------------- the protocol
def split_by_season(events: Dict[str, pd.DataFrame], dev_seasons: Iterable[int], held_seasons: Iterable[int]
                    ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    dev_s, held_s = set(dev_seasons), set(held_seasons)
    if dev_s & held_s or (dev_s and held_s and max(dev_s) >= min(held_s)):
        raise ValueError("dev seasons must all precede held-out seasons")
    dev = {k: v[v["season"].isin(dev_s)].reset_index(drop=True) for k, v in events.items()}
    held = {k: v[v["season"].isin(held_s)].reset_index(drop=True) for k, v in events.items()}
    return dev, held


def run_split(sport: str, events: Dict[str, pd.DataFrame], th: Dict[str, object]) -> Dict[Tuple[str, str, str], dict]:
    """Evaluate the ungated baseline and every applicable gate on every market of one split. Key = (sport, market, gate)."""
    res: Dict[Tuple[str, str, str], dict] = {}
    for market, df in events.items():
        if len(df) == 0:
            continue
        d = derive_conf(df).reset_index(drop=True)
        masks = gate_masks(d, th, market)
        res[(sport, market, "NONE")] = dict(evaluate_cell(d, len(d)), diffs=cell_diffs(d))
        for g in GATES:
            m = masks[g]
            if m is None:
                res[(sport, market, g)] = {"n": 0, "not_applicable": True}
                continue
            sub = d[m.fillna(False).to_numpy()]
            res[(sport, market, g)] = dict(evaluate_cell(sub, len(d)), diffs=cell_diffs(sub))
    return res


def tried(res: Dict[Tuple[str, str, str], dict]) -> List[Tuple[str, str, str]]:
    return [k for k, v in res.items() if k[2] != "NONE" and not v.get("not_applicable")]


def screen_dev(dev_res: Dict[Tuple[str, str, str], dict]) -> List[Tuple[str, str, str]]:
    """Pre-registered advancement rule."""
    out = []
    for k in tried(dev_res):
        v = dev_res[k]
        if v.get("n", 0) >= MIN_N_SCREEN and v.get("gain", -1) > 0 and v.get("c", -1) > 0:
            out.append(k)
    return out


def confirm(held_res: Dict[Tuple[str, str, str], dict], survivors: Sequence[Tuple[str, str, str]], B: int = 4000) -> Dict[str, object]:
    """Held-out verdict objects: Holm over the survivors, max-statistic sign-flip over every gate tried, per-cell pass flags."""
    fam = tried(held_res)
    p_hold = holm({k: held_res[k].get("p_one_sided", float("nan")) for k in survivors})
    mt = maxt_signflip({k: held_res[k]["diffs"] for k in fam if "diffs" in held_res[k]}, B=B)
    rows = {}
    for k in fam:
        v = held_res[k]
        r = {"survivor": k in survivors, "p_holm_confirm": p_hold.get(k, float("nan")),
             "p_maxt_all": mt.get(k, {}).get("p_maxt", float("nan"))}
        r["confirmed"] = bool(r["survivor"] and r["p_holm_confirm"] < 0.05 and v.get("c_z", -9) > Z_ONE_SIDED_05
                              and v.get("roi_med", -1) > 0)
        r["strict"] = bool(r["p_maxt_all"] < 0.05)
        rows[k] = r
    return {"n_family": len(fam), "n_survivors": len(survivors), "cells": rows,
            "any_confirmed": any(r["confirmed"] for r in rows.values()), "any_strict": any(r["strict"] for r in rows.values())}


def verdict(conf: Dict[str, object], held_res: Dict[Tuple[str, str, str], dict]) -> str:
    """YES = a pre-registered survivor is confirmed on held-out (Holm p < .05, blend c z > 1.645, ROI at close > 0).
    NO = nothing confirmed AND every tried gate's 95% CI upper bound is below SESOI (we can reject a break-even-sized effect).
    INCONCLUSIVE = nothing confirmed but some tried gate cannot rule a break-even-sized effect out."""
    if conf["any_confirmed"]:
        return "YES"
    for k in tried(held_res):
        hi = held_res[k].get("gain_hi")
        if hi is None or not np.isfinite(hi) or hi >= SESOI:
            return "INCONCLUSIVE"
    return "NO"


def verdict_by_sport(conf: Dict[str, object], held_res: Dict[Tuple[str, str, str], dict]) -> Dict[str, str]:
    """The verdict rule applied to each sport's cells separately (a sport with enough games can say NO while a small one cannot)."""
    out = {}
    for sport in sorted({k[0] for k in tried(held_res)}):
        sub = {k: v for k, v in held_res.items() if k[0] == sport}
        sub_conf = {"any_confirmed": any(c["confirmed"] for k, c in conf["cells"].items() if k[0] == sport)}
        out[sport] = verdict(sub_conf, sub)
    return out


# ---------------------------------------------------------------------------------------------------- rendering
def _f(x, spec="+.4f"):
    return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else format(x, spec)


def render_table(sport: str, res: Dict[Tuple[str, str, str], dict], conf: Optional[Dict[str, object]] = None) -> str:
    cols = ["market", "gate", "n", "cover", "LL gain", "95% CI", "z", "MDE80", "c (z)", "ROI close [95% CI]",
            "ROI best", "ctl best", "gap rho"]
    if conf:
        cols += ["p maxT", "p Holm"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for (s, market, gate), v in res.items():
        if s != sport:
            continue
        if v.get("not_applicable"):
            out.append(f"| {market} | {gate} | - | not applicable / degenerate |" + " |" * (len(cols) - 4))
            continue
        row = [market, gate, str(v["n"]), _f(v.get("coverage"), ".0%"), _f(v.get("gain")),
               f"[{_f(v.get('gain_lo'))}, {_f(v.get('gain_hi'))}]", _f(v.get("gain_z"), "+.2f"), _f(v.get("mde80"), ".4f"),
               f"{_f(v.get('c'), '+.2f')} ({_f(v.get('c_z'), '+.1f')})",
               f"{_f(v.get('roi_med'), '+.1%')} [{_f(v.get('roi_med_lo'), '+.1%')}, {_f(v.get('roi_med_hi'), '+.1%')}]",
               _f(v.get("roi_best"), "+.1%"), _f(v.get("ctl_roi_best"), "+.1%"), _f(v.get("mono_gap_rho"), "+.2f")]
        if conf:
            c = conf["cells"].get((s, market, gate))
            row += [_f(c["p_maxt_all"], ".3f") if c else "-", _f(c["p_holm_confirm"], ".3f") if c and c["survivor"] else "-"]
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)
