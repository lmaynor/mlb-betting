"""Hardening for the trained NHL saves model (Argon 2026-11-10: Props D1, Pack 4 / model-improvement D1, Data D2).

Saves is beezy's only proven edge (walk-forward blend c +0.75, t 7.3, n=7,350; ROI5 +12.2% in 2024 decaying to
-1.5% in 2025, with 91% of disagreement bets on Unders). The three Argon directions converge on the same fix
list, implemented here as pure, testable pieces plus one IO loader:

  1. 28-DAY LOG-ODDS CENTERING (Props D1(i), Data D2): subtract the trailing 28-day mean log-odds residual
     (logit(p_model) - logit(p_mkt), settled rows only, strictly before the row's date) from the model's
     log-odds BEFORE blending. This is the direct attack on the Under-bias regime failure.
  2. ROLLING RESIDUAL CALIBRATION (Pack 4 D1): a market-anchored Platt / blend refit on a trailing window,
     logit(y) ~ a + b * logit(p_mkt) + c * (logit(p_centered) - logit(p_mkt)), fit walk-forward (each
     training row's model probability is centered with ITS OWN trailing 28-day residual, so no future
     information enters the fit). The calibrated probability is clamped to the market +/- MAX_CALIB_SHIFT,
     the same 8pp band the repo's blend studies use (ev.daily_projections.BLEND_MAX_SHIFT).
  3. DUAL-CONFIRMED STARTER GATE (Pack 4 D1, Data D2): a saves play is official only when the starter is
     confirmed by RotoWire AND confirmed not-scratched by a second source. NO ESPN NHL fetcher exists in
     this repo, so the second source is a typed, FAIL-CLOSED stub (EspnAvailabilityStub): it always returns
     unconfirmed, which means this gate blocks every saves official until a real ESPN fetcher replaces it.
     That is deliberate: an unbuilt second source must never silently pass. See the PR body.
  4. REGIME GATE (Props D1(iii)): block saves officials when the trailing 14-day realized over-rate
     (settled saves rows, strictly before today) is > REGIME_OVER_RATE_MAX (0.50).
  5. PRICE GATE (Props D1(iv)): a saves play is official only if the model beats the >= 4-book MEDIAN
     consensus by >= PRICE_GATE_MIN_EDGE (3.0pp) on the model's side. The board's consensus P(over)
     (ev.props.build_consensus) IS the median of per-book de-vigged fair probabilities, so the gate reads
     (p_model_side - p_median_side) with n_books >= 4. Consensus edge / market FV / best-price edge logging
     stays with the board row and the paper ledger, as before.

PROMOTION TEST (Props D1, pre-registered): saves promotes to a trusted edge at n=400 settled live rows or
2026-12-21, iff live LL gain >= +0.0020 and ROI > 0; otherwise it demotes to display-only.
`promotion_check` is that decision rule and nothing else.

WIRING: ev.daily_projections applies centering + calibration to trained saves probabilities before the
blend and stamps every gate input on the board row (saves_* fields); ev.model_picks.qualifies consults
`officials_gate_from_row` for NHL saves. Rows written before this module existed carry no saves_* stamps
and pass the gate (the same fail-open-on-old-schema rule the sharp-book check uses); every row the
current board writes carries the stamps, and with the ESPN stub in place the starter stamp is always
unconfirmed, so current-board saves rows are tracked, never official, until the ESPN fetcher lands.

HISTORY: the residual / regime / calibration history is the paper ledger's settled rows
(nhl.props.paper_track: NHL/paper_track/settled/{CT day}.jsonl, last-sighting p_model / p_mkt / won_over).
Those rows record the RAW model, which is exactly what a residual series must be computed from.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Mapping, Protocol, runtime_checkable

import numpy as np
import pandas as pd

log = logging.getLogger("nhl.props.saves_hardening")

# --- pre-registered constants (Argon directions; do not tune in code review) --------------------------------
CENTER_WINDOW_DAYS = 28
CENTER_MIN_N = 30                    # fewer settled rows than this in the window -> no centering (residual 0)
CALIB_WINDOW_DAYS = 90
CALIB_MIN_N = 100                    # fewer settled rows than this in the window -> no calibration refit
MAX_CALIB_SHIFT = 0.08               # calibrated P never sits further than this from the market (cf. BLEND_MAX_SHIFT)
REGIME_WINDOW_DAYS = 14
REGIME_OVER_RATE_MAX = 0.50          # block officials when the trailing realized over-rate is ABOVE this
REGIME_MIN_N = 20                    # fewer settled rows than this -> regime unknown, gate does not trip
PRICE_GATE_MIN_BOOKS = 4
PRICE_GATE_MIN_EDGE = 0.03           # 3.0pp over the median consensus, on the model's side
PROMOTION_N = 400
PROMOTION_DATE = "2026-12-21"
PROMOTION_MIN_LL_GAIN = 0.0020
SETTLED_KEY = "NHL/paper_track/settled/{day}.jsonl"     # mirrors nhl.props.paper_track.SETTLED_KEY

_EPS = 1e-6


# ---------------------------------------------------------------------------------------------------------------------
# numerics
# ---------------------------------------------------------------------------------------------------------------------
def logit(p: float) -> float:
    p = min(max(float(p), _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _clip_prob(p: float) -> float:
    return min(max(float(p), 0.005), 0.995)


# ---------------------------------------------------------------------------------------------------------------------
# history handling. A history row: {"date": "YYYY-MM-DD", "p_model": float, "p_mkt": float,
# "won_over": 1.0 | 0.0 | None}. DataFrames with those columns are accepted everywhere a list is.
# ---------------------------------------------------------------------------------------------------------------------
def history_frame(rows) -> pd.DataFrame:
    """Normalise history (list of dicts / DataFrame / None) -> sorted DataFrame with parsed dates."""
    if rows is None:
        return pd.DataFrame(columns=["date", "p_model", "p_mkt", "won_over"])
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(list(rows))
    if not len(df):
        return pd.DataFrame(columns=["date", "p_model", "p_mkt", "won_over"])
    df = df.copy()
    df["date"] = df["date"].astype(str).str[:10]
    for c in ("p_model", "p_mkt", "won_over"):
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def _window(df: pd.DataFrame, as_of: str, days: int) -> pd.DataFrame:
    """Rows dated in [as_of - days, as_of): strictly before as_of, so a row never sees its own day or later."""
    lo = (date.fromisoformat(str(as_of)[:10]) - timedelta(days=days)).isoformat()
    return df[(df["date"] >= lo) & (df["date"] < str(as_of)[:10])]


def _residuals(df: pd.DataFrame) -> np.ndarray:
    pm = np.clip(df["p_model"].to_numpy(dtype=float), _EPS, 1.0 - _EPS)
    pk = np.clip(df["p_mkt"].to_numpy(dtype=float), _EPS, 1.0 - _EPS)
    return np.log(pm / (1.0 - pm)) - np.log(pk / (1.0 - pk))


# ---------------------------------------------------------------------------------------------------------------------
# 1. 28-day log-odds centering
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class CenteringResult:
    residual_mean: float             # trailing mean of logit(p_model) - logit(p_mkt); 0.0 when not applied
    n: int                           # settled rows in the window with both probabilities present
    applied: bool


def trailing_residual(rows, as_of: str, window_days: int = CENTER_WINDOW_DAYS,
                      min_n: int = CENTER_MIN_N) -> CenteringResult:
    """The trailing log-odds residual of the raw model vs the de-vigged median market, walk-forward."""
    df = history_frame(rows)
    w = _window(df, as_of, window_days).dropna(subset=["p_model", "p_mkt"])
    n = int(len(w))
    if n < min_n:
        return CenteringResult(0.0, n, False)
    return CenteringResult(float(np.mean(_residuals(w))), n, True)


def center_probability(p_model: float, residual_mean: float) -> float:
    """logit(p_centered) = logit(p_model) - residual_mean. A model running hot vs the market is pulled back."""
    return _clip_prob(sigmoid(logit(p_model) - float(residual_mean)))


def walk_forward_centered(df: pd.DataFrame, window_days: int = CENTER_WINDOW_DAYS,
                          min_n: int = CENTER_MIN_N) -> np.ndarray:
    """Each row's model probability centered by the trailing residual of rows STRICTLY BEFORE its date
    (same-day rows never center each other). Vectorised over dates via per-date cumulative sums."""
    if not len(df):
        return np.array([], dtype=float)
    resid = _residuals(df)
    valid = np.isfinite(resid)
    daily = pd.DataFrame({"date": df["date"].to_numpy(), "r": np.where(valid, resid, 0.0),
                          "n": valid.astype(float)}).groupby("date").sum().sort_index()
    cum_r = np.concatenate([[0.0], np.cumsum(daily["r"].to_numpy())])
    cum_n = np.concatenate([[0.0], np.cumsum(daily["n"].to_numpy())])
    days = daily.index.to_numpy()
    pm = df["p_model"].to_numpy(dtype=float)
    out = np.full(len(df), np.nan)
    for i, day in enumerate(df["date"]):
        if not np.isfinite(pm[i]):
            continue
        hi = int(np.searchsorted(days, day, side="left"))                    # dates < day
        lo_day = (date.fromisoformat(str(day)[:10]) - timedelta(days=window_days)).isoformat()
        lo = int(np.searchsorted(days, lo_day, side="left"))                 # dates >= day - window
        n = cum_n[hi] - cum_n[lo]
        mean = (cum_r[hi] - cum_r[lo]) / n if n >= min_n and n > 0 else 0.0
        out[i] = sigmoid(logit(float(pm[i])) - mean)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# 2. Rolling residual calibration (market-anchored Platt / blend refit)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class CalibrationFit:
    a: float
    b: float
    c: float
    n: int
    window_days: int

    def apply(self, p_centered: float, p_mkt: float) -> float:
        z = self.a + self.b * logit(p_mkt) + self.c * (logit(p_centered) - logit(p_mkt))
        p = _clip_prob(sigmoid(z))
        return min(max(p, float(p_mkt) - MAX_CALIB_SHIFT), float(p_mkt) + MAX_CALIB_SHIFT)


def fit_residual_calibration(rows, as_of: str, window_days: int = CALIB_WINDOW_DAYS,
                             min_n: int = CALIB_MIN_N) -> CalibrationFit | None:
    """Fit logit(y) ~ a + b*logit(mkt) + c*(logit(centered model) - logit(mkt)) on settled rows in the
    trailing window (reusing nhl.props.market.blend_regression, cluster = game date). None when the
    window is too thin or the fit fails: no calibration is applied rather than a bad one."""
    df = history_frame(rows)
    w = _window(df, as_of, window_days).dropna(subset=["p_model", "p_mkt", "won_over"])
    if len(w) < min_n:
        return None
    w = w.assign(p_centered=walk_forward_centered(w))
    w = w.dropna(subset=["p_centered"])
    if len(w) < min_n:
        return None
    try:
        from nhl.props.market import blend_regression
        fit = blend_regression(w["won_over"].to_numpy(), w["p_mkt"].to_numpy(),
                               w["p_centered"].to_numpy(), w["date"].to_numpy())
    except Exception as exc:                                                            # noqa: BLE001
        log.info("saves calibration fit failed (%s); serving centered probabilities", exc)
        return None
    return CalibrationFit(float(fit["a"]), float(fit["b"]), float(fit["c"]), int(len(w)), window_days)


# ---------------------------------------------------------------------------------------------------------------------
# 4. Regime gate
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class RegimeResult:
    blocked: bool
    over_rate: float | None          # trailing realized over-rate; None when the window is too thin to know
    n: int
    window_days: int = REGIME_WINDOW_DAYS
    threshold: float = REGIME_OVER_RATE_MAX

    @property
    def reason(self) -> str:
        if self.over_rate is None:
            return f"regime_unknown (n={self.n} < {REGIME_MIN_N})"
        return (f"regime_blocked (trailing {self.window_days}d over-rate {self.over_rate:.3f} > {self.threshold})"
                if self.blocked else
                f"regime_ok (trailing {self.window_days}d over-rate {self.over_rate:.3f} <= {self.threshold})")


def regime_gate(rows, as_of: str, window_days: int = REGIME_WINDOW_DAYS,
                threshold: float = REGIME_OVER_RATE_MAX, min_n: int = REGIME_MIN_N) -> RegimeResult:
    """Blocked iff the trailing realized over-rate is strictly ABOVE `threshold` (Props D1(iii): > 0.50).
    An over-rate of exactly 0.50 does not trip it. Too-thin windows report over_rate None and do not block
    (the number is unknowable, and the starter + price gates still apply to every official)."""
    df = history_frame(rows)
    w = _window(df, as_of, window_days).dropna(subset=["won_over"])
    n = int(len(w))
    if n < min_n:
        return RegimeResult(False, None, n, window_days, threshold)
    rate = float(w["won_over"].mean())
    return RegimeResult(bool(rate > threshold), rate, n, window_days, threshold)


# ---------------------------------------------------------------------------------------------------------------------
# 3. Dual-confirmed starter gate (RotoWire + a second source; ESPN is a fail-closed stub)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class SecondSourceResult:
    source: str
    confirmed: bool                  # the second source positively confirms this goalie starts
    scratched: bool                  # the second source lists this goalie as scratched / not dressing
    status: str                      # machine-readable: "confirmed" | "unconfirmed_stub" | ...
    detail: str = ""


@runtime_checkable
class SecondSource(Protocol):
    """The second-source interface the dual gate is built against. A real ESPN NHL availability fetcher
    (Data D2 / Pack 4 D1) implements this protocol and replaces the stub without touching the gate."""

    name: str

    def check(self, *, team: str, goalie: str, date: str) -> SecondSourceResult:
        ...


class EspnAvailabilityStub:
    """STUB -- NOT AN ESPN FETCHER. No ESPN NHL availability fetcher exists in this repo yet.

    Fail-closed by construction: every check returns confirmed=False, status="unconfirmed_stub", so the
    dual gate can never pass on this source. `scratched` is False only because the stub knows nothing;
    the gate treats unconfirmed as blocking regardless, so that False can never leak a play through.
    Replace with a real fetcher implementing SecondSource; do not weaken the gate to accommodate the stub.
    """

    name = "espn"

    def check(self, *, team: str, goalie: str, date: str) -> SecondSourceResult:
        return SecondSourceResult(
            source=self.name, confirmed=False, scratched=False, status="unconfirmed_stub",
            detail="ESPN NHL availability fetcher not built; stub is fail-closed and confirms nobody")


def default_second_source() -> SecondSource:
    """The wired second source today: the ESPN stub. One place to swap when the real fetcher lands."""
    return EspnAvailabilityStub()


def _norm_name(name: str) -> list:
    import re
    import unicodedata
    x = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", x.replace("-", " ").replace(".", ". ").replace(".", "")).split()


def _names_match(a: str, b: str) -> bool:
    """Same rule as nhl.data.rotowire_lineups._same_player: full-name equal, or an initialled first name."""
    x, y = _norm_name(a), _norm_name(b)
    if not x or not y or x[-1] != y[-1]:
        return False
    return x == y or (len(y[0]) == 1 and x[0][:1] == y[0]) or (len(x[0]) == 1 and y[0][:1] == x[0])


@dataclass
class StarterGateResult:
    official_ok: bool
    rotowire_confirmed: bool
    second: SecondSourceResult
    reason: str


def dual_confirmed_starter(*, team: str, goalie: str, date: str,
                           rotowire_confirmed: Mapping[str, str] | None,
                           second_source: SecondSource) -> StarterGateResult:
    """Official only when RotoWire confirms THIS goalie for THIS team AND the second source confirms him
    AND the second source does not list him scratched. Every other state blocks, with a named reason."""
    rw_name = (rotowire_confirmed or {}).get(team)
    rw_ok = bool(rw_name) and _names_match(str(rw_name), goalie)
    second = second_source.check(team=team, goalie=goalie, date=date)
    if not rw_ok:
        reason = f"rotowire_not_confirmed (team {team}: {rw_name or 'no confirmed goalie'})"
    elif second.scratched:
        reason = f"second_source_scratched ({second.source})"
    elif not second.confirmed:
        reason = f"second_source_unconfirmed ({second.source}: {second.status})"
    else:
        reason = "dual_confirmed"
    return StarterGateResult(bool(rw_ok and second.confirmed and not second.scratched),
                             rw_ok, second, reason)


def rotowire_confirmed_for(day: str) -> dict:
    """{team: goalie} RotoWire-confirmed starters for an ET day, from the 30-minute refresh cache
    (nhl.data.rotowire_lineups.cached_confirmed_goalies; NHL/lineups/rotowire/ archives the same
    payload per day). {} on any failure: no cache means nobody is confirmed, which is fail-closed."""
    try:
        from nhl.data import rotowire_lineups as rl
        return rl.cached_confirmed_goalies(day)
    except Exception as exc:                                                            # noqa: BLE001
        log.info("rotowire confirmed goalies unavailable (%s)", exc)
        return {}


# ---------------------------------------------------------------------------------------------------------------------
# 5 + combined officials gate
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class OfficialsGateResult:
    ok: bool
    reasons: tuple = ()              # every failing (or passing) check, named
    checks: dict = field(default_factory=dict)


def price_gate(p_model_side: float | None, p_median_side: float | None, n_books) -> tuple:
    """(ok, reason): the model must beat the >= 4-book median by >= 3.0pp on its own side."""
    if p_model_side is None or p_median_side is None or n_books is None:
        return False, "price_gate_no_median (model / median / book count missing)"
    edge = float(p_model_side) - float(p_median_side)
    if int(n_books) < PRICE_GATE_MIN_BOOKS:
        return False, f"price_gate_thin_books (n_books={int(n_books)} < {PRICE_GATE_MIN_BOOKS})"
    if edge < PRICE_GATE_MIN_EDGE:
        return False, f"price_gate_edge_short (edge {edge:+.3f} < {PRICE_GATE_MIN_EDGE:.3f} vs median)"
    return True, f"price_gate_ok (edge {edge:+.3f} vs >= {PRICE_GATE_MIN_BOOKS}-book median)"


def officials_gate(*, p_model_side, p_median_side, n_books,
                   starter: StarterGateResult | None, regime: RegimeResult | None) -> OfficialsGateResult:
    """All saves officials gates at once (starter, regime, price). A gate whose inputs are missing FAILS:
    at decision time, unknown is not a pass. (The row-level adapter below is the only fail-open path,
    and only for pre-hardening board schemas.)"""
    checks, reasons = {}, []
    ok_price, r_price = price_gate(p_model_side, p_median_side, n_books)
    checks["price"] = ok_price
    reasons.append(r_price)
    if starter is None:
        checks["starter"] = False
        reasons.append("starter_gate_missing (no dual-confirmation result)")
    else:
        checks["starter"] = bool(starter.official_ok)
        reasons.append(f"starter: {starter.reason}")
    if regime is None:
        checks["regime"] = False
        reasons.append("regime_gate_missing (no settled history supplied)")
    else:
        checks["regime"] = not regime.blocked
        reasons.append(regime.reason)
    return OfficialsGateResult(bool(all(checks.values())), tuple(reasons), checks)


# Board-row stamp keys written by ev.daily_projections.make_row for NHL saves rows. officials_gate_from_row
# reads exactly these; their ABSENCE (a board written before hardening) is the legacy fail-open case.
ROW_STAMPS = ("saves_starter_dual_confirmed", "saves_regime_blocked", "saves_median_edge", "saves_median_n_books")


def officials_gate_from_row(row: Mapping) -> OfficialsGateResult | None:
    """The officials verdict for a board row, or None when the row carries no hardening stamps at all
    (a pre-hardening board: the caller applies its legacy rules, exactly as the sharp-book check does).
    When ANY stamp is present the gate is fail-closed: a missing stamp fails its check."""
    if not any(k in row for k in ROW_STAMPS):
        return None
    checks, reasons = {}, []
    dual = row.get("saves_starter_dual_confirmed")
    checks["starter"] = dual is True
    reasons.append(f"starter: {row.get('saves_starter_gate_reason') or ('dual_confirmed' if dual else 'not_dual_confirmed')}")
    blocked = row.get("saves_regime_blocked")
    checks["regime"] = blocked is False
    reasons.append(f"regime: {'blocked' if blocked else 'not_blocked'}"
                   f" (14d over-rate {row.get('saves_regime_over_rate')})")
    edge, n_books = row.get("saves_median_edge"), row.get("saves_median_n_books")
    if edge is None or n_books is None:
        checks["price"] = False
        reasons.append("price_gate_no_median (stamps incomplete)")
    else:
        checks["price"] = bool(int(n_books) >= PRICE_GATE_MIN_BOOKS and float(edge) >= PRICE_GATE_MIN_EDGE)
        reasons.append(f"price: edge {float(edge):+.3f} over median, n_books={int(n_books)}")
    return OfficialsGateResult(bool(all(checks.values())), tuple(reasons), checks)


# ---------------------------------------------------------------------------------------------------------------------
# combined state + probability pipeline (what the board calls)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class HardeningState:
    as_of: str
    centering: CenteringResult
    regime: RegimeResult
    calibration: CalibrationFit | None

    def harden(self, p_model: float, p_mkt: float | None) -> dict:
        """Raw model P(over) -> {p_raw, p_centered, p_final, center_residual, center_n, calibrated}.
        Centering needs no current market price; calibration does (it is market-anchored), so without a
        market price the centered probability is final."""
        p_centered = center_probability(p_model, self.centering.residual_mean) if self.centering.applied \
            else _clip_prob(float(p_model))
        p_final, calibrated = p_centered, False
        if self.calibration is not None and p_mkt is not None:
            p_final, calibrated = self.calibration.apply(p_centered, float(p_mkt)), True
        return {"p_raw": float(p_model), "p_centered": p_centered, "p_final": p_final,
                "center_residual": self.centering.residual_mean, "center_n": self.centering.n,
                "calibrated": calibrated}


def build_state(rows, as_of: str) -> HardeningState:
    """All trailing state for one board date from settled history rows (raw model vs market)."""
    df = history_frame(rows)
    return HardeningState(as_of=str(as_of)[:10],
                          centering=trailing_residual(df, as_of),
                          regime=regime_gate(df, as_of),
                          calibration=fit_residual_calibration(df, as_of))


def promotion_check(*, n_settled: int, ll_gain: float | None, roi: float | None, as_of: str) -> dict:
    """Props D1's pre-registered promotion test, as a decision: judged at n >= 400 settled live rows OR
    on/after 2026-12-21; promotes iff LL gain >= +0.0020 and ROI > 0, else demotes to display-only.
    Before the judging point the verdict is 'pending' and nothing changes."""
    judged = n_settled >= PROMOTION_N or str(as_of)[:10] >= PROMOTION_DATE
    if not judged:
        return {"judged": False, "promote": False, "verdict": "pending",
                "reason": f"n={n_settled} < {PROMOTION_N} and {str(as_of)[:10]} < {PROMOTION_DATE}"}
    ok = ll_gain is not None and roi is not None and ll_gain >= PROMOTION_MIN_LL_GAIN and roi > 0
    return {"judged": True, "promote": bool(ok),
            "verdict": "promote" if ok else "display_only",
            "reason": f"LL gain {ll_gain} (bar {PROMOTION_MIN_LL_GAIN}), ROI {roi} (bar > 0)"}


# ---------------------------------------------------------------------------------------------------------------------
# IO: settled-history loader (the only non-pure part)
# ---------------------------------------------------------------------------------------------------------------------
def settled_history_from_records(records) -> list:
    """Paper-ledger settled records -> history rows (pure). Uses the LAST pre-game sighting's raw model /
    market probabilities (the closing proxy the ledger grades on) and the game date inside game_key."""
    out = []
    for r in records or []:
        if r.get("stat") != "saves" or r.get("won_over") is None or r.get("void"):
            continue
        parts = str(r.get("game_key") or "").split(":")
        last = r.get("last") or {}
        if len(parts) < 2 or last.get("p_model") is None or last.get("p_mkt") is None:
            continue
        out.append({"date": parts[1], "p_model": float(last["p_model"]), "p_mkt": float(last["p_mkt"]),
                    "won_over": float(r["won_over"])})
    return out


def load_settled_saves_history(now, days: int = CALIB_WINDOW_DAYS, read=None) -> list:
    """History rows from the settled paper ledger for the `days` days ending today (Central). `read(key)`
    -> bytes | None is injectable for tests; the default reads mlb_core.storage. Missing days are normal
    (no games, no ledger yet) and simply contribute nothing. Never raises."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    if read is None:
        def read(key):                                                                  # noqa: E731
            try:
                from mlb_core import storage
                return storage.read_bytes(key)
            except Exception:                                                           # noqa: BLE001
                return None
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo("America/Chicago")).date()
    rows = []
    for back in range(days + 1):
        day = (today - timedelta(days=back)).isoformat()
        try:
            raw = read(SETTLED_KEY.format(day=day))
            if not raw:
                continue
            recs = [json.loads(x) for x in raw.decode().splitlines() if x.strip()]
        except Exception:                                                               # noqa: BLE001
            continue
        rows.extend(settled_history_from_records(recs))
    return rows
