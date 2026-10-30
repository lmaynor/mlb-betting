"""
ev.sgp_book_pricer -- Parameterized Sportsbook SGP Pricer & Copula Calibrator.

Provides:
  1. True Fair Correlated Joint Probability (p_true_joint) via empirical rho_true
     under a standard Gaussian copula.
  2. Sportsbook SGP Quote Prediction (predict_book_sgp_odds) using parameterized
     (rho_book, hold_book) fitted to observed quotes.
  3. Archetype registry for canonical NBA, NHL, and NFL synthetic combos.
  4. Parameter calibration via scipy.optimize.curve_fit (fit_book_sgp_params)
     saving to data/sgp_book_params.json.
  5. Multi-leg betslip deep link generator (build_sgp_deeplink) for DraftKings,
     FanDuel, and major sportsbooks.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import multivariate_normal, norm

from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, implied_to_american

DEFAULT_PARAMS_PATH = "data/sgp_book_params.json"


# ------------------------------------------------------------------------------
# 1. Copula Math
# ------------------------------------------------------------------------------

def _ensure_psd_corr(corr: np.ndarray) -> np.ndarray:
    """Ensure a correlation matrix is symmetric positive semi-definite with unit diagonal."""
    corr = np.asarray(corr, dtype=float)
    corr = 0.5 * (corr + corr.T)
    eigvals, eigvecs = np.linalg.eigh(corr)
    if np.all(eigvals > 1e-8):
        return corr
    clipped = np.maximum(eigvals, 1e-6)
    fixed = eigvecs @ np.diag(clipped) @ eigvecs.T
    d = np.sqrt(np.maximum(np.diag(fixed), 1e-12))
    res = fixed / np.outer(d, d)
    np.fill_diagonal(res, 1.0)
    return res


def copula_joint(probs: Sequence[float], rho_or_corr: Union[float, np.ndarray]) -> float:
    """
    Joint probability of upper-tail / Yes events under a Gaussian copula.

    For m legs with marginal probabilities p_1, ..., p_m:
      z_i = norm.ppf(p_i)
      P_copula = Phi_m(z_1, ..., z_m; R)

    Properties:
      - When rho == 0: P_copula == prod(probs)
      - When rho > 0:  P_copula > prod(probs) and <= min(probs) (Frechet upper bound)
      - Clamped to [max(0.0, sum(p) - (m - 1)), min(probs)]
    """
    p = [float(x) for x in probs]
    m = len(p)
    if m == 0:
        return 0.0
    if m == 1:
        return float(min(max(p[0], 0.0), 1.0))

    for x in p:
        if x <= 0.0 or x >= 1.0:
            raise ValueError(f"Marginal probability {x} must be strictly inside (0, 1)")

    # 2-leg bivariate case
    if m == 2:
        if isinstance(rho_or_corr, (int, float, np.floating)):
            rho = float(rho_or_corr)
        else:
            arr = np.asarray(rho_or_corr, dtype=float)
            rho = float(arr[0, 1])

        # Exact independence shortcut
        if abs(rho) < 1e-9:
            return float(p[0] * p[1])

        z1 = float(norm.ppf(p[0]))
        z2 = float(norm.ppf(p[1]))
        cov = np.array([[1.0, rho], [rho, 1.0]], dtype=float)
        cov = _ensure_psd_corr(cov)

        val = float(multivariate_normal.cdf([z1, z2], cov=cov))
        lower_bound = max(0.0, p[0] + p[1] - 1.0)
        upper_bound = min(p[0], p[1])
        clamped = min(max(val, lower_bound), upper_bound)
        if rho > 0.0 and clamped <= (p[0] * p[1]):
            clamped = min(upper_bound, p[0] * p[1] + 1e-9)
        return float(clamped)

    # 3+ leg case
    if isinstance(rho_or_corr, (int, float, np.floating)):
        rho = float(rho_or_corr)
        if abs(rho) < 1e-9:
            return float(math.prod(p))
        R = np.full((m, m), rho, dtype=float)
        np.fill_diagonal(R, 1.0)
    else:
        R = np.asarray(rho_or_corr, dtype=float)
        if R.shape != (m, m):
            raise ValueError(f"Correlation matrix shape {R.shape} does not match {m} legs")
        # Check if all off-diagonals are zero
        off_diag = R - np.diag(np.diag(R))
        if np.all(np.abs(off_diag) < 1e-9):
            return float(math.prod(p))

    R = _ensure_psd_corr(R)
    z = [float(norm.ppf(x)) for x in p]
    val = float(multivariate_normal.cdf(z, cov=R))
    lower_bound = max(0.0, float(math.prod(p)) if np.all(R >= 0) else sum(p) - (m - 1))
    upper_bound = min(p)
    return float(min(max(val, lower_bound), upper_bound))


# ------------------------------------------------------------------------------
# 2. Archetype Registry
# ------------------------------------------------------------------------------

@dataclass(frozen=True)
class SgpArchetype:
    key: str
    name: str
    sport: str
    rho_true: float
    rho_book_default: float
    hold_book_default: float
    label: str
    direct_market: Optional[str] = None
    description: str = ""


SGP_ARCHETYPES: Dict[str, SgpArchetype] = {
    "nba_reb_ast_10_10": SgpArchetype(
        key="nba_reb_ast_10_10",
        name="NBA Triple-Double Milestone Proxy (10+ Reb + 10+ Ast)",
        sport="nba",
        rho_true=0.22,
        rho_book_default=0.08,
        hold_book_default=0.12,
        label="TD PROXY",
        direct_market="triple_double",
        description="Over 9.5 Reb + Over 9.5 Ast proxying official Triple-Double market",
    ),
    "nba_pts_reb_vs_pr": SgpArchetype(
        key="nba_pts_reb_vs_pr",
        name="NBA Points + Rebounds vs PR Market",
        sport="nba",
        rho_true=0.15,
        rho_book_default=0.06,
        hold_book_default=0.14,
        label="P+R PROXY",
        direct_market="pr",
        description="Over Pts + Over Reb compared against direct P+R combo market",
    ),
    "nba_pts_ast_vs_pa": SgpArchetype(
        key="nba_pts_ast_vs_pa",
        name="NBA Points + Assists vs PA Market",
        sport="nba",
        rho_true=0.18,
        rho_book_default=0.07,
        hold_book_default=0.14,
        label="P+A PROXY",
        direct_market="pa",
        description="Over Pts + Over Ast compared against direct P+A combo market",
    ),
    "nba_reb_ast_vs_ra": SgpArchetype(
        key="nba_reb_ast_vs_ra",
        name="NBA Rebounds + Assists vs RA Market",
        sport="nba",
        rho_true=0.22,
        rho_book_default=0.08,
        hold_book_default=0.14,
        label="R+A PROXY",
        direct_market="ra",
        description="Over Reb + Over Ast compared against direct R+A combo market",
    ),
    "nba_dd_proxy": SgpArchetype(
        key="nba_dd_proxy",
        name="NBA Double-Double Milestone Proxy (10+ Pts + 10+ Reb/Ast)",
        sport="nba",
        rho_true=0.16,
        rho_book_default=0.07,
        hold_book_default=0.12,
        label="DD PROXY",
        direct_market="double_double",
        description="Over 9.5 Pts + Over 9.5 Reb/Ast proxying Double-Double market",
    ),
    "nhl_goal_ast_vs_pts": SgpArchetype(
        key="nhl_goal_ast_vs_pts",
        name="NHL 1+ Goal + 1+ Assist vs 2+ Points",
        sport="nhl",
        rho_true=0.25,
        rho_book_default=0.10,
        hold_book_default=0.15,
        label="G+A PROXY",
        direct_market="points",
        description="Over 0.5 Goals + Over 0.5 Assists compared against Over 1.5 Points",
    ),
    "nfl_qb_wr_stack": SgpArchetype(
        key="nfl_qb_wr_stack",
        name="NFL QB Pass Yds + WR Rec Yds Same-Team Stack",
        sport="nfl",
        rho_true=0.35,
        rho_book_default=0.18,
        hold_book_default=0.15,
        label="QB+WR STACK",
        direct_market=None,
        description="QB Over Pass Yds + Same-Team WR Over Rec Yds stack",
    ),
    "nfl_td_yds_stack": SgpArchetype(
        key="nfl_td_yds_stack",
        name="NFL Anytime TD + Rush/Rec Yardage Stack",
        sport="nfl",
        rho_true=0.32,
        rho_book_default=0.15,
        hold_book_default=0.15,
        label="TD+YDS STACK",
        direct_market=None,
        description="Anytime TD (Over 0.5) + Over Rush/Rec Yds stack",
    ),
}


# ------------------------------------------------------------------------------
# 3. Parameters I/O
# ------------------------------------------------------------------------------

def load_book_sgp_params(path: str = DEFAULT_PARAMS_PATH) -> Dict[str, Dict[str, Any]]:
    """Load fitted (rho_book, hold_book) parameters from JSON if exists."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_book_sgp_params(params: Dict[str, Dict[str, Any]], path: str = DEFAULT_PARAMS_PATH) -> None:
    """Save fitted parameters dict to JSON."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(params, fh, indent=2, sort_keys=True)


# ------------------------------------------------------------------------------
# 4. SGP Quote Prediction
# ------------------------------------------------------------------------------

@dataclass
class PredictedBookSgp:
    decimal: float
    american: int
    implied_prob: float
    rho_used: float
    hold_used: float
    book: str
    archetype: str


def _to_implied_prob(val: Union[int, float]) -> float:
    """Convert American odds, decimal odds, or raw probability to implied probability."""
    v = float(val)
    if 0.0 < v < 1.0:
        return v
    if 1.0 <= v <= 99.0:
        return 1.0 / v
    return float(american_to_implied_prob(v))


def predict_book_sgp_odds(
    book: str,
    archetype_key: str,
    leg_odds: Sequence[Union[int, float]],
    custom_params: Optional[Dict[str, Any]] = None,
) -> PredictedBookSgp:
    """
    Predict a sportsbook's offered SGP odds for a given archetype and leg odds.

    Formula:
      q_i = implied_prob(leg_odds_i)
      P_copula = copula_joint([q_1, q_2, ...], rho_book)
      P_sgp = min(0.99, max(0.001, (1.0 + hold_book) * P_copula))
      Decimal_sgp = 1.0 / P_sgp
      American_sgp = implied_to_american(P_sgp)
    """
    b_key = book.strip().lower()
    arch = SGP_ARCHETYPES.get(archetype_key)
    if arch is None:
        rho = 0.10
        hold = 0.14
    else:
        rho = arch.rho_book_default
        hold = arch.hold_book_default

    # Check custom_params or loaded params
    lookup_key = f"{b_key}|{archetype_key}"
    params = custom_params or load_book_sgp_params()
    if lookup_key in params:
        p_entry = params[lookup_key]
        rho = float(p_entry.get("rho_book", rho))
        hold = float(p_entry.get("hold_book", hold))

    # Compute raw implied probs
    q_legs = [_to_implied_prob(a) for a in leg_odds]
    pj = copula_joint(q_legs, rho)
    p_sgp = min(0.99, max(0.001, (1.0 + hold) * pj))
    dec = float(1.0 / p_sgp)
    am = int(implied_to_american(p_sgp))

    return PredictedBookSgp(
        decimal=round(dec, 4),
        american=am,
        implied_prob=round(p_sgp, 6),
        rho_used=round(rho, 4),
        hold_used=round(hold, 4),
        book=b_key,
        archetype=archetype_key,
    )


# ------------------------------------------------------------------------------
# 5. Parameter Fitter (scipy.optimize.curve_fit)
# ------------------------------------------------------------------------------

def fit_book_sgp_params(
    quotes: Union[Sequence[Dict[str, Any]], Sequence[Tuple[Any, ...]], Any],
    out_path: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Calibrate (rho_book, hold_book) per (book, archetype) from observed quotes.

    Quotes rows must supply:
      book, archetype, leg1_odds, leg2_odds, sgp_odds, (optional leg3_odds).

    Fits:
      rho_book in [-0.50, 0.85]
      hold_book in [0.0, 0.45]
    using scipy.optimize.curve_fit on implied probability space.
    """
    # Normalize rows
    rows: List[Dict[str, Any]] = []
    if hasattr(quotes, "to_dict"):
        records = quotes.to_dict(orient="records")
        rows.extend(records)
    elif isinstance(quotes, (list, tuple)):
        for item in quotes:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 5:
                # book, archetype, leg1, leg2, sgp[, leg3]
                r = {
                    "book": item[0],
                    "archetype": item[1],
                    "leg1_odds": item[2],
                    "leg2_odds": item[3],
                    "sgp_odds": item[4],
                }
                if len(item) >= 6:
                    r["leg3_odds"] = item[5]
                rows.append(r)

    # Group by (book, archetype)
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        b = str(r.get("book", "")).strip().lower()
        arch = str(r.get("archetype", "")).strip()
        if b and arch:
            groups.setdefault((b, arch), []).append(r)

    fitted_all: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for (b, arch), grp in groups.items():
        if len(grp) < 2:
            continue

        q1_list: List[float] = []
        q2_list: List[float] = []
        q_sgp_list: List[float] = []
        dec_obs_list: List[float] = []

        for row in grp:
            try:
                l1 = _to_implied_prob(row["leg1_odds"])
                l2 = _to_implied_prob(row["leg2_odds"])
                sgp_prob = _to_implied_prob(row["sgp_odds"])
                sgp_dec = float(row.get("sgp_dec") or 1.0 / sgp_prob)
                q1_list.append(l1)
                q2_list.append(l2)
                q_sgp_list.append(sgp_prob)
                dec_obs_list.append(sgp_dec)
            except Exception:
                continue

        if len(q1_list) < 2:
            continue

        q1_arr = np.array(q1_list, dtype=float)
        q2_arr = np.array(q2_list, dtype=float)
        y_obs = np.array(q_sgp_list, dtype=float)

        def model_func(X: Tuple[np.ndarray, np.ndarray], rho: float, hold: float) -> np.ndarray:
            q1, q2 = X
            out = []
            for i in range(len(q1)):
                cj = copula_joint([float(q1[i]), float(q2[i])], rho)
                p_sgp = min(0.99, (1.0 + hold) * cj)
                out.append(p_sgp)
            return np.array(out, dtype=float)

        # Initial guess from archetype default
        def_arch = SGP_ARCHETYPES.get(arch)
        rho_init = def_arch.rho_book_default if def_arch else 0.10
        hold_init = def_arch.hold_book_default if def_arch else 0.14

        popt, _ = curve_fit(
            model_func,
            (q1_arr, q2_arr),
            y_obs,
            p0=[rho_init, hold_init],
            bounds=([-0.50, 0.0], [0.85, 0.45]),
            maxfev=2000,
        )

        fitted_rho = float(popt[0])
        fitted_hold = float(popt[1])

        # Calculate RMSE
        pred_probs = model_func((q1_arr, q2_arr), fitted_rho, fitted_hold)
        rmse_implied = float(np.sqrt(np.mean((pred_probs - y_obs) ** 2)))
        pred_decs = 1.0 / np.maximum(pred_probs, 1e-4)
        rmse_decimal = float(np.sqrt(np.mean((pred_decs - np.array(dec_obs_list)) ** 2)))

        record = {
            "book": b,
            "archetype": arch,
            "rho_book": round(fitted_rho, 4),
            "hold_book": round(fitted_hold, 4),
            "n": len(q1_list),
            "rmse_implied": round(rmse_implied, 6),
            "rmse_decimal": round(rmse_decimal, 4),
            "updated_at": now_iso,
        }
        fitted_all[f"{b}|{arch}"] = record

    if out_path or (out_path is None and fitted_all):
        target_path = out_path or DEFAULT_PARAMS_PATH
        existing = load_book_sgp_params(target_path)
        existing.update(fitted_all)
        save_book_sgp_params(existing, target_path)

    return fitted_all


# ------------------------------------------------------------------------------
# 6. Deep Link Generator
# ------------------------------------------------------------------------------

def build_sgp_deeplink(book: str, event_id: str, legs: Sequence[Dict[str, Any]]) -> str:
    """
    Construct sportsbook deep link with multi-outcome betslip parameters.

    DraftKings:
      https://sportsbook.draftkings.com/event/{event_id}?outcomes={sid_1},{sid_2}
    FanDuel:
      https://sportsbook.fanduel.com/addToBetslip?marketId[0]={m1}&selectionId[0]={s1}&marketId[1]={m2}&selectionId[1]={s2}
    Fallback:
      Sportsbook event or root URL.
    """
    b = str(book).strip().lower()
    ev_id = str(event_id or "").strip()

    if b in ("draftkings", "dk"):
        sids = []
        for leg in legs:
            sid = leg.get("sid") or leg.get("outcome_id") or leg.get("selection_id")
            if sid:
                sids.append(str(sid))
        if len(sids) >= 2 and ev_id:
            return f"https://sportsbook.draftkings.com/event/{ev_id}?outcomes={','.join(sids)}"
        if ev_id:
            return f"https://sportsbook.draftkings.com/event/{ev_id}"
        return "https://sportsbook.draftkings.com"

    if b in ("fanduel", "fd"):
        pairs = []
        for leg in legs:
            mid = leg.get("market_id") or leg.get("marketId")
            sid = leg.get("selection_id") or leg.get("selectionId")
            if mid and sid:
                pairs.append((str(mid), str(sid)))
        if len(pairs) >= 2:
            query = "&".join(f"marketId[{i}]={m}&selectionId[{i}]={s}" for i, (m, s) in enumerate(pairs))
            return f"https://sportsbook.fanduel.com/addToBetslip?{query}"
        if ev_id:
            return f"https://sportsbook.fanduel.com/event/{ev_id}"
        return "https://sportsbook.fanduel.com"

    if b == "betmgm":
        if ev_id:
            return f"https://sports.betmgm.com/en/sports/events/{ev_id}"
        return "https://sports.betmgm.com"

    if b == "caesars":
        if ev_id:
            return f"https://www.caesars.com/sportsbook-and-casino/us/event/{ev_id}"
        return "https://www.caesars.com/sportsbook-and-casino"

    if b == "bet365":
        return "https://www.bet365.com/#/AC/B1/"

    if b == "fanatics":
        return "https://sportsbook.fanatics.com"

    if b == "hardrock":
        return "https://hardrock.bet"

    return f"https://www.{b}.com"
