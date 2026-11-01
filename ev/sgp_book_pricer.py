"""
ev.sgp_book_pricer -- Parameterized Sportsbook SGP Pricer & Copula Calibrator.

Provides:
  1. True Fair Correlated Joint Probability (p_true_joint) via empirical rho_true
     under a standard Gaussian copula.
  2. Sportsbook SGP Quote Prediction (predict_book_sgp_odds) using parameterized
     (rho_book, hold_book) fitted to observed quotes.
  3. Archetype registry for canonical NBA, NHL, and NFL synthetic combos.
  4. Parameter calibration via scipy.optimize.curve_fit (fit_book_sgp_params)
     saving to data/sgp_book_params.json. Calibration needs REAL observed quotes
     (>= MIN_QUOTES_PER_GROUP per book+archetype, a holdout check, no fit pinned at a
     bound); anything weaker is rejected and the documented archetype defaults stay.
  5. Multi-leg betslip deep link generator (build_sgp_deeplink) for DraftKings,
     FanDuel, and major sportsbooks (https only, allowlisted hosts, encoded parts).

Provenance note: an earlier data/sgp_quotes.csv (20 hand-typed rows, source=initial_seed)
and the data/sgp_book_params.json fitted to them were removed: the fit pinned hold at its
0.45 bound and rho at -0.46 and predicted nonsense out of sample. They remain in git
history only. With no params file the module uses the SGP_ARCHETYPES defaults, which are
ASSUMPTIONS, not measurements: every price this module predicts is a modelled estimate.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote, urlparse

import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import multivariate_normal, norm

from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, implied_to_american

log = logging.getLogger("ev.sgp_book_pricer")

DEFAULT_PARAMS_PATH = "data/sgp_book_params.json"

# Calibration acceptance rules (fit_book_sgp_params)
MIN_QUOTES_PER_GROUP = 8          # valid 2-leg quotes per (book, archetype) before a fit is even attempted
RHO_BOUNDS = (0.0, 0.85)          # same-game props of one player / teammates are not negatively correlated
HOLD_BOUNDS = (0.0, 0.45)
BOUND_TOL = 1e-3                  # a parameter within this of a bound means the optimiser ran out of room
MAX_HOLDOUT_MAPE = 0.10           # mean |pred - obs| / obs of implied probability on held-out quotes
# rows tagged with one of these sources are placeholders, never observations
NON_OBSERVATION_SOURCES = frozenset({"initial_seed", "seed", "synthetic", "placeholder", "estimate"})


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
    "nhl_linemate_pts_stack": SgpArchetype(
        key="nhl_linemate_pts_stack",
        name="NHL Linemates 1+ Point Stack",
        sport="nhl",
        rho_true=0.32,
        rho_book_default=0.10,
        hold_book_default=0.14,
        label="LINEMATE PTS",
        direct_market=None,
        description="Top-line / PP1 same-team skaters Over 0.5 Points stack",
    ),
    "nba_teammate_ast_pts_stack": SgpArchetype(
        key="nba_teammate_ast_pts_stack",
        name="NBA Playmaker Ast + Scorer Pts Stack",
        sport="nba",
        rho_true=0.26,
        rho_book_default=0.08,
        hold_book_default=0.14,
        label="AST+PTS STACK",
        direct_market=None,
        description="Primary playmaker Over Assists + top scorer teammate Over Points stack",
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
}


# ------------------------------------------------------------------------------
# 3. Parameters I/O
# ------------------------------------------------------------------------------

def _params_entry_ok(entry: Any) -> bool:
    """A stored fit is only trusted if it is inside the sane window and not pinned at a bound."""
    try:
        rho = float(entry["rho_book"])
        hold = float(entry["hold_book"])
    except Exception:
        return False
    return (
        RHO_BOUNDS[0] + BOUND_TOL < rho < RHO_BOUNDS[1] - BOUND_TOL
        and HOLD_BOUNDS[0] + BOUND_TOL < hold < HOLD_BOUNDS[1] - BOUND_TOL
    )


def load_book_sgp_params(path: str = DEFAULT_PARAMS_PATH) -> Dict[str, Dict[str, Any]]:
    """Load fitted (rho_book, hold_book) parameters from JSON. Missing / unreadable file -> {} (archetype defaults)."""
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


def _book_sgp_prob(q_legs: Sequence[float], rho: float, hold: float) -> float:
    """
    Modelled SGP implied probability: (1 + hold) * copula_joint(q_legs, rho), clamped so it can never be
    below the independent product of the legs (a book does not price positive-correlation legs cheaper than
    independence) nor above the smallest leg probability (a parlay cannot be likelier than its weakest leg),
    and kept inside [0.001, 0.99]. The upper cap wins if the two ever conflict.
    """
    pj = copula_joint(list(q_legs), rho)
    p = (1.0 + hold) * pj
    p = max(p, float(math.prod(q_legs)))
    p = min(p, float(min(q_legs)))
    return min(0.99, max(0.001, p))


def predict_book_sgp_odds(
    book: str,
    archetype_key: str,
    leg_odds: Sequence[Union[int, float]],
    custom_params: Optional[Dict[str, Any]] = None,
) -> PredictedBookSgp:
    """
    Predict (model, not quote) a sportsbook's offered SGP odds for an archetype and leg odds.

    Formula:
      q_i = implied_prob(leg_odds_i)
      P_sgp = clamp((1 + hold_book) * copula_joint(q, rho_book),
                    lower = prod(q_i), upper = min(q_i))
      Decimal_sgp = 1.0 / P_sgp

    (rho_book, hold_book) come from the archetype defaults unless a validated fit exists for this book and
    archetype. Fits are 2-leg fits, so they are only applied to 2-leg tickets; 3-leg tickets use the defaults.
    """
    b_key = book.strip().lower()
    arch = SGP_ARCHETYPES.get(archetype_key)
    if arch is None:
        rho = 0.10
        hold = 0.14
    else:
        rho = arch.rho_book_default
        hold = arch.hold_book_default

    if len(leg_odds) == 2:
        lookup_key = f"{b_key}|{archetype_key}"
        params = custom_params if custom_params is not None else load_book_sgp_params()
        p_entry = params.get(lookup_key)
        if p_entry is not None and _params_entry_ok(p_entry):
            rho = float(p_entry["rho_book"])
            hold = float(p_entry["hold_book"])

    q_legs = [_to_implied_prob(a) for a in leg_odds]
    p_sgp = _book_sgp_prob(q_legs, rho, hold)
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

def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or str(v).strip() == ""


def _normalize_quote_rows(quotes: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if hasattr(quotes, "to_dict"):
        rows.extend(quotes.to_dict(orient="records"))
    elif isinstance(quotes, (list, tuple)):
        for item in quotes:
            if isinstance(item, dict):
                rows.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 5:
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
    return rows


def _fit_group(
    arch: str,
    q1_arr: np.ndarray,
    q2_arr: np.ndarray,
    y_obs: np.ndarray,
    dec_obs: np.ndarray,
    min_quotes: int,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Fit one (book, archetype) group. Returns (record, "") when accepted else (None, reason)."""
    n = len(y_obs)
    if n < min_quotes:
        return None, f"only {n} valid 2-leg quotes (< {min_quotes})"

    def model_func(X: Tuple[np.ndarray, np.ndarray], rho: float, hold: float) -> np.ndarray:
        qa, qb = X
        return np.array([_book_sgp_prob([float(qa[i]), float(qb[i])], rho, hold) for i in range(len(qa))], dtype=float)

    def_arch = SGP_ARCHETYPES.get(arch)
    rho_init = def_arch.rho_book_default if def_arch else 0.10
    hold_init = def_arch.hold_book_default if def_arch else 0.14
    lo = [RHO_BOUNDS[0], HOLD_BOUNDS[0]]
    hi = [RHO_BOUNDS[1], HOLD_BOUNDS[1]]

    def _curve_fit(idx: np.ndarray) -> Tuple[float, float]:
        popt, _ = curve_fit(
            model_func, (q1_arr[idx], q2_arr[idx]), y_obs[idx],
            p0=[rho_init, hold_init], bounds=(lo, hi), maxfev=2000,
        )
        return float(popt[0]), float(popt[1])

    # Holdout check: every 4th quote is held out of a first fit and must be predicted within MAX_HOLDOUT_MAPE.
    idx_all = np.arange(n)
    test_idx = idx_all[idx_all % 4 == 3]
    train_idx = idx_all[idx_all % 4 != 3]
    try:
        rho_tr, hold_tr = _curve_fit(train_idx)
    except Exception as exc:
        return None, f"curve_fit failed on training split ({exc})"
    pred_te = model_func((q1_arr[test_idx], q2_arr[test_idx]), rho_tr, hold_tr)
    holdout_mape = float(np.mean(np.abs(pred_te - y_obs[test_idx]) / y_obs[test_idx]))
    if holdout_mape > MAX_HOLDOUT_MAPE:
        return None, f"holdout MAPE {holdout_mape:.3f} > {MAX_HOLDOUT_MAPE}"

    try:
        fitted_rho, fitted_hold = _curve_fit(idx_all)
    except Exception as exc:
        return None, f"curve_fit failed ({exc})"

    rec = {"rho_book": fitted_rho, "hold_book": fitted_hold}
    if not _params_entry_ok(rec):
        return None, f"fit pinned at a bound or implausible (rho={fitted_rho:.4f}, hold={fitted_hold:.4f})"

    pred_probs = model_func((q1_arr, q2_arr), fitted_rho, fitted_hold)
    rmse_implied = float(np.sqrt(np.mean((pred_probs - y_obs) ** 2)))
    pred_decs = 1.0 / np.maximum(pred_probs, 1e-4)
    rmse_decimal = float(np.sqrt(np.mean((pred_decs - dec_obs) ** 2)))
    return {
        "rho_book": round(fitted_rho, 4),
        "hold_book": round(fitted_hold, 4),
        "n": n,
        "rmse_implied": round(rmse_implied, 6),
        "rmse_decimal": round(rmse_decimal, 4),
        "holdout_mape": round(holdout_mape, 4),
    }, ""


def fit_book_sgp_params(
    quotes: Union[Sequence[Dict[str, Any]], Sequence[Tuple[Any, ...]], Any],
    out_path: Optional[str] = None,
    *,
    min_quotes: int = MIN_QUOTES_PER_GROUP,
    rejections: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Calibrate (rho_book, hold_book) per (book, archetype) from observed 2-leg quotes.

    Quote rows supply: book, archetype, leg1_odds, leg2_odds, sgp_odds, optional leg3_odds, optional source.
    Rows with a leg3_odds are 3-leg tickets and are SKIPPED (the 2-leg copula is not their model). Rows whose
    source marks them as a placeholder (see NON_OBSERVATION_SOURCES) are skipped.

    A group is accepted only if it has >= min_quotes valid quotes, predicts held-out quotes within
    MAX_HOLDOUT_MAPE, and neither parameter ends up at a bound (rho in RHO_BOUNDS, hold in HOLD_BOUNDS).
    Rejected groups are omitted (their reason goes into `rejections` when given) and never persisted, so
    predict_book_sgp_odds keeps the archetype defaults for them.
    """
    rows = _normalize_quote_rows(quotes)

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    skipped_3leg = 0
    skipped_source = 0
    for r in rows:
        b = str(r.get("book", "")).strip().lower()
        arch = str(r.get("archetype", "")).strip()
        if not (b and arch):
            continue
        if str(r.get("source", "")).strip().lower() in NON_OBSERVATION_SOURCES:
            skipped_source += 1
            continue
        if not _is_blank(r.get("leg3_odds")):
            skipped_3leg += 1
            continue
        groups.setdefault((b, arch), []).append(r)
    if skipped_3leg:
        log.info("fit_book_sgp_params: skipped %d 3-leg quotes (2-leg model)", skipped_3leg)
    if skipped_source:
        log.warning("fit_book_sgp_params: skipped %d placeholder-source quotes", skipped_source)

    fitted_all: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()

    for (b, arch), grp in groups.items():
        q1_list: List[float] = []
        q2_list: List[float] = []
        q_sgp_list: List[float] = []
        dec_obs_list: List[float] = []
        for row in grp:
            try:
                l1 = _to_implied_prob(row["leg1_odds"])
                l2 = _to_implied_prob(row["leg2_odds"])
                sgp_prob = _to_implied_prob(row["sgp_odds"])
                if not all(0.0 < v < 1.0 for v in (l1, l2, sgp_prob)):
                    continue
                sgp_dec = float(row.get("sgp_dec") or 1.0 / sgp_prob)
            except Exception:
                continue
            q1_list.append(l1)
            q2_list.append(l2)
            q_sgp_list.append(sgp_prob)
            dec_obs_list.append(sgp_dec)

        rec, reason = _fit_group(
            arch,
            np.array(q1_list, dtype=float),
            np.array(q2_list, dtype=float),
            np.array(q_sgp_list, dtype=float),
            np.array(dec_obs_list, dtype=float),
            min_quotes,
        )
        key = f"{b}|{arch}"
        if rec is None:
            log.warning("fit_book_sgp_params: rejected %s: %s", key, reason)
            if rejections is not None:
                rejections[key] = reason
            continue
        rec.update({"book": b, "archetype": arch, "updated_at": now_iso})
        fitted_all[key] = rec

    if fitted_all and (out_path or out_path is None):
        target_path = out_path or DEFAULT_PARAMS_PATH
        existing = load_book_sgp_params(target_path)
        existing.update(fitted_all)
        save_book_sgp_params(existing, target_path)

    return fitted_all


# ------------------------------------------------------------------------------
# 6. Deep Link Generator
# ------------------------------------------------------------------------------

SPORT_LEAGUE_URLS: Dict[str, Dict[str, str]] = {
    "draftkings": {
        "nba": "https://sportsbook.draftkings.com/leagues/basketball/nba",
        "nfl": "https://sportsbook.draftkings.com/leagues/football/nfl",
        "nhl": "https://sportsbook.draftkings.com/leagues/hockey/nhl",
        "mlb": "https://sportsbook.draftkings.com/leagues/baseball/mlb",
    },
    "fanduel": {
        "nba": "https://sportsbook.fanduel.com/navigation/nba",
        "nfl": "https://sportsbook.fanduel.com/navigation/nfl",
        "nhl": "https://sportsbook.fanduel.com/navigation/nhl",
        "mlb": "https://sportsbook.fanduel.com/navigation/mlb",
    },
    "betmgm": {
        "nba": "https://sports.betmgm.com/en/sports/basketball-7/betting/usa-9/nba-6004",
        "nfl": "https://sports.betmgm.com/en/sports/football-11/betting/usa-9/nfl-35",
        "nhl": "https://sports.betmgm.com/en/sports/hockey-12/betting/usa-9/nhl-43",
        "mlb": "https://sports.betmgm.com/en/sports/baseball-23/betting/usa-9/mlb-75",
    },
    "caesars": {
        "nba": "https://sportsbook.caesars.com/us/il/bet/basketball/events/all",
        "nfl": "https://sportsbook.caesars.com/us/il/bet/americanfootball/events/all",
        "nhl": "https://sportsbook.caesars.com/us/il/bet/icehockey/events/all",
        "mlb": "https://sportsbook.caesars.com/us/il/bet/baseball/events/all",
    },
}


def _resolve_sport(sport: Optional[str], event_id: str, legs: Sequence[Dict[str, Any]]) -> str:
    if sport:
        return str(sport).strip().lower()
    ev = str(event_id or "").strip().lower()
    if ":" in ev:
        prefix = ev.split(":")[0]
        if prefix in ("nba", "nfl", "nhl", "mlb"):
            return prefix
    if "_" in ev:
        prefix = ev.split("_")[0]
        if prefix in ("nba", "nfl", "nhl", "mlb"):
            return prefix
    for leg in legs:
        st = str(leg.get("stat", "")).lower()
        if st in ("points", "rebounds", "assists", "pra", "pr", "pa", "ra", "threes", "triple_double", "double_double"):
            return "nba"
        if st in ("pass_yds", "rush_yds", "rec_yds", "receptions", "pass_tds", "anytime_td"):
            return "nfl"
        if st in ("goals", "sog", "saves", "blocked_shots"):
            return "nhl"
    return "nba"


def _is_internal_key(event_id: str) -> bool:
    if not event_id:
        return True
    ev = str(event_id).strip()
    return ":" in ev or "@" in ev or ev.startswith(("nba_", "nfl_", "nhl_", "mlb_"))


# Registrable domains each book's links may point at: the exact host or any subdomain of it, https only.
# Mirrored in beezy-vip/lib/odds-screen.ts (buildSgpDeeplinkTs).
ALLOWED_LINK_DOMAINS: Dict[str, Tuple[str, ...]] = {
    "draftkings": ("draftkings.com",),
    "fanduel": ("fanduel.com",),
    "betmgm": ("betmgm.com",),
    "caesars": ("caesars.com",),
}

# Fixed landing pages for books with no event / league deep link. A book not listed here gets no link (None):
# a host is never built from the book name.
BOOK_HOMEPAGES: Dict[str, str] = {
    "bet365": "https://www.bet365.com/#/AC/B1/",
    "fanatics": "https://sportsbook.fanatics.com",
    "hardrock": "https://hardrock.bet",
    "novig": "https://novig.us",
    "prophetx": "https://prophetx.co",
    "kalshi": "https://kalshi.com",
    "espnbet": "https://espnbet.com",
    "betrivers": "https://www.betrivers.com",
}

_BOOK_ALIASES = {"dk": "draftkings", "fd": "fanduel"}


def _host_allowed(host: Optional[str], domains: Sequence[str]) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in domains)


def safe_https_url(url: Any, domains: Sequence[str]) -> Optional[str]:
    """`url` unchanged if it is an https URL with no userinfo whose host is an allowlisted domain (or a subdomain)."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    try:
        parsed = urlparse(u)
        host = parsed.hostname
        parsed.port  # raises ValueError on a malformed port
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or parsed.username is not None or parsed.password is not None:
        return None
    if any(ch in u for ch in ("\\", " ", "\t", "\r", "\n")):
        return None
    return u if _host_allowed(host, domains) else None


def link_allowed_for_book(book: str, url: Any) -> bool:
    """True if `url` is an https link on `book`'s own allowlisted domain (used before opening any link in a browser)."""
    raw = str(book or "").strip().lower()
    b = _BOOK_ALIASES.get(raw, raw)
    domains: Sequence[str] = ALLOWED_LINK_DOMAINS.get(b, ())
    if not domains and b in BOOK_HOMEPAGES:
        host = urlparse(BOOK_HOMEPAGES[b]).hostname or ""
        domains = (host[4:] if host.startswith("www.") else host,)
    return bool(domains) and safe_https_url(url, domains) is not None


def _seg(value: Any) -> str:
    """One percent-encoded URL path segment / query value (no characters survive that could change the structure)."""
    return quote(str(value), safe="")


def _event_segment(event_id: str) -> Optional[str]:
    """Encoded event id for an /event/<id> path, or None when it cannot be one safe segment."""
    if not event_id or _is_internal_key(event_id) or event_id in (".", ".."):
        return None
    return _seg(event_id)


def build_sgp_deeplink(
    book: str,
    event_id: str,
    legs: Sequence[Dict[str, Any]],
    sport: Optional[str] = None,
) -> Optional[str]:
    """
    Construct sportsbook deep link with multi-outcome betslip parameters.

    Rule 1: Direct link / outcome IDs when available.
    Rule 2: Never pass internal game_keys ('nba:2026-10-30:BOS@MIL') into /event/ URLs;
            route to clean sport/league pages instead.
    Rule 3: Every URL is https on an allowlisted host (ALLOWED_LINK_DOMAINS / fixed constants). Leg-supplied links
            are used only if they parse as https on the book's own domain; event ids, outcome ids and market ids are
            percent-encoded. A book name never becomes part of a host: unknown books return None.

    NOTE: the sids / marketId branches are effectively dead today (the odds board does not carry outcome ids) but
    stay in place and stay inside the same allowlist, because the target hosts are hard-coded constants.
    """
    raw_b = str(book).strip().lower()
    b = _BOOK_ALIASES.get(raw_b, raw_b)
    ev_id = str(event_id or "").strip()
    sp = _resolve_sport(sport, ev_id, legs)
    ev_seg = _event_segment(ev_id)

    domains = ALLOWED_LINK_DOMAINS.get(b, ())
    direct_urls: List[str] = []
    for leg in legs:
        lnk = leg.get("over_link") or leg.get("link") or leg.get("deeplink")
        safe = safe_https_url(lnk, domains) if domains else None
        if safe:
            direct_urls.append(safe)

    if b == "draftkings":
        sids = []
        for leg in legs:
            sid = leg.get("over_sid") or leg.get("sid") or leg.get("outcome_id") or leg.get("selection_id")
            if sid:
                sids.append(_seg(sid))
        dk_eid = ev_seg
        if not dk_eid:
            for u in direct_urls:
                parsed = urlparse(u)
                if (parsed.hostname or "").lower() == "sportsbook.draftkings.com" and parsed.path.startswith("/event/"):
                    part = parsed.path[len("/event/"):].split("/")[0]
                    if part and part not in (".", "..") and not _is_internal_key(part):
                        dk_eid = _seg(part)
                        break
        if len(sids) >= 2 and dk_eid:
            return f"https://sportsbook.draftkings.com/event/{dk_eid}?outcomes={','.join(sids)}"
        if dk_eid:
            return f"https://sportsbook.draftkings.com/event/{dk_eid}"
        if direct_urls:
            return direct_urls[0]
        return SPORT_LEAGUE_URLS.get("draftkings", {}).get(sp, "https://sportsbook.draftkings.com")

    if b == "fanduel":
        pairs = []
        for leg in legs:
            mid = leg.get("market_id") or leg.get("marketId")
            sid = leg.get("over_sid") or leg.get("selection_id") or leg.get("selectionId") or leg.get("sid")
            if mid and sid:
                pairs.append((_seg(mid), _seg(sid)))
        if len(pairs) >= 2:
            query = "&".join(f"marketId[{i}]={m}&selectionId[{i}]={s}" for i, (m, s) in enumerate(pairs))
            return f"https://sportsbook.fanduel.com/addToBetslip?{query}"
        if direct_urls:
            return direct_urls[0]
        if ev_seg:
            return f"https://sportsbook.fanduel.com/event/{ev_seg}"
        return SPORT_LEAGUE_URLS.get("fanduel", {}).get(sp, "https://sportsbook.fanduel.com")

    if b == "betmgm":
        if ev_seg:
            return f"https://sports.betmgm.com/en/sports/events/{ev_seg}"
        return SPORT_LEAGUE_URLS.get("betmgm", {}).get(sp, "https://sports.betmgm.com")

    if b == "caesars":
        if ev_seg:
            return f"https://www.caesars.com/sportsbook-and-casino/us/event/{ev_seg}"
        return SPORT_LEAGUE_URLS.get("caesars", {}).get(sp, "https://sportsbook.caesars.com")

    return BOOK_HOMEPAGES.get(b)
