"""ev/lambda_crossing.py -- Module 2: Point-in-Time Lambda-Crossing Engine.

Evaluates intraday quote trajectories across [T-14h, T-30m] to detect intraday
soft-book mispricings and quantify line decay without reverse hindsight bias.

Core concepts:
  - D_strike(lambda, tau): Target strike decimal price = (1.0 + tau) / P_lambda
    DECISION (Luc, Nov 17, 2026): this corrected form is ACCEPTED and canonical.
    Argon's spec (RUL-20261117-profitable-market-spec) literally wrote
    1.0 + tau / P_lambda; that form does not solve EV >= tau. Do not "fix" this
    back to the literal spec form.
  - First-Passage (t_0): Earliest fresh quote in [T-14h, T-30m] clearing strike
    without active consensus-velocity lockout.
  - Two-Snapshot Persistent (t_1): Consecutive quote >= 300s later still clearing
    strike without adverse consensus move >= 1.0pp.
  - Peak Odds (t_max): Maximum odds hung by the book in [T-14h, T-30m].
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _to_timestamp_sec(ts: datetime | float | int | str) -> float:
    """Normalize any timestamp representation to epoch seconds."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        return ts.timestamp()
    if isinstance(ts, str):
        try:
            return float(ts)
        except ValueError:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
    raise TypeError(f"Unsupported timestamp type: {type(ts)}")


def american_to_decimal(odds: float) -> float:
    """Convert American moneyline odds to decimal odds."""
    if odds > 0:
        return 1.0 + (odds / 100.0)
    elif odds < 0:
        return 1.0 + (100.0 / abs(odds))
    return 1.0


def calc_d_strike(p_lambda: float, tau: float = 0.045) -> float:
    """Strike decimal price for EV >= tau.

    EV = P_lambda * d - 1 >= tau  <=>  d >= (1.0 + tau) / P_lambda.
    """
    if p_lambda <= 0:
        return float("inf")
    return (1.0 + tau) / p_lambda


# Alias matching spec formula notation
d_strike = calc_d_strike


@dataclass
class QuoteSnapshot:
    """Point-in-time sportsbook quote snapshot."""

    game_id: str
    player_norm: str
    stat: str
    line: float
    side: str
    book: str
    timestamp: datetime | float | int | str
    game_start: datetime | float | int | str
    odds_decimal: float = 0.0
    p_cons: float = 0.5
    odds_american: float | None = None
    age_sec: float = 0.0
    outcome: float | int | None = None
    settled_won: bool | None = None

    def __post_init__(self) -> None:
        if self.odds_decimal == 0.0 and self.odds_american is not None:
            self.odds_decimal = american_to_decimal(self.odds_american)
        elif self.odds_american is None and self.odds_decimal > 1.0:
            if self.odds_decimal >= 2.0:
                self.odds_american = (self.odds_decimal - 1.0) * 100.0
            else:
                self.odds_american = -100.0 / (self.odds_decimal - 1.0)


@dataclass
class TrajectoryGrade:
    """Grade and metrics for a single quote trajectory."""

    game_id: str
    player_norm: str
    stat: str
    line: float
    side: str
    book: str
    p_lambda: float
    tau: float = 0.045
    crossed_t0: bool = False
    crossed_t1: bool = False
    d_t0: float | None = None
    d_t1: float | None = None
    d_peak: float | None = None
    t_max_offset_min: float | None = None
    t0_timestamp: Any | None = None
    t1_timestamp: Any | None = None
    t_max_timestamp: Any | None = None
    d_close: float | None = None
    settled_won: bool | None = None
    settled_roi_unmasked_close: float | None = None
    settled_roi_masked_close: float | None = None
    settled_roi_t0: float | None = None
    settled_roi_t1: float | None = None
    settled_roi_peak: float | None = None
    delta_retention_pct: float | None = None

    @property
    def dislocation_retention(self) -> float | None:
        """Percentage gain from first-passage strike price to peak odds."""
        if self.d_t0 is None or self.d_peak is None:
            return None
        return ((self.d_peak - self.d_t0) / self.d_t0) * 100.0 if self.d_t0 != 0 else 0.0


def evaluate_intraday_trajectory(
    snapshots: list[QuoteSnapshot],
    p_lambda: float,
    tau: float = 0.045,
    outcome: float | int | None = None,
    settled_won: bool | None = None,
) -> TrajectoryGrade:
    """Evaluates chronological quote snapshots against lambda strike price.

    Window: [T-14h, T-30m], i.e., 1800s to 50400s before game_start.
    First-Passage (t_0):
      - In window [T-14h, T-30m]
      - d_b(t_k) >= D_strike(lambda, tau)
      - age_sec <= 300
      - |P_cons(t_k) - P_cons(t_{k-1})| < 0.025 (no velocity lockout)
    Two-Snapshot Persistent (t_1):
      - Snapshot >= 300s after t_0
      - STILL clears D_strike(lambda, tau)
      - No adverse consensus move >= 1.0pp (0.010)
    Peak Odds (t_max):
      - Maximum d_b(t) in [T-14h, T-30m]
    """
    if not snapshots:
        raise ValueError("evaluate_intraday_trajectory requires at least one snapshot")

    # Sort snapshots chronologically by timestamp
    sorted_snaps = sorted(snapshots, key=lambda s: _to_timestamp_sec(s.timestamp))
    first = sorted_snaps[0]
    last = sorted_snaps[-1]

    grade = TrajectoryGrade(
        game_id=first.game_id,
        player_norm=first.player_norm,
        stat=first.stat,
        line=first.line,
        side=first.side,
        book=first.book,
        p_lambda=p_lambda,
        tau=tau,
    )

    d_strike_val = calc_d_strike(p_lambda, tau)
    d_close = last.odds_decimal
    grade.d_close = d_close

    # Determine peak odds and t_0 in window [T-14h, T-30m]
    # Window seconds before tip: [1800, 50400]
    t0_idx: int | None = None
    t0_time_sec: float | None = None
    t0_p_cons: float | None = None

    for i, s in enumerate(sorted_snaps):
        s_time = _to_timestamp_sec(s.timestamp)
        g_start = _to_timestamp_sec(s.game_start)
        time_to_tip_sec = g_start - s_time

        in_window = 1800.0 <= time_to_tip_sec <= 50400.0

        if in_window:
            # Track peak odds in window
            if grade.d_peak is None or s.odds_decimal > grade.d_peak:
                grade.d_peak = s.odds_decimal
                grade.t_max_timestamp = s.timestamp
                grade.t_max_offset_min = time_to_tip_sec / 60.0

            # Check velocity lockout
            vel_locked = False
            if i > 0:
                p_cons_prev = sorted_snaps[i - 1].p_cons
                if abs(s.p_cons - p_cons_prev) >= 0.025:
                    vel_locked = True

            # First-Passage (t_0)
            if not grade.crossed_t0:
                clears_strike = s.odds_decimal >= d_strike_val
                is_fresh = s.age_sec <= 300.0
                if clears_strike and is_fresh and not vel_locked:
                    grade.crossed_t0 = True
                    grade.d_t0 = s.odds_decimal
                    grade.t0_timestamp = s.timestamp
                    t0_idx = i
                    t0_time_sec = s_time
                    t0_p_cons = s.p_cons

    # Two-Snapshot Persistent (t_1)
    if grade.crossed_t0 and t0_idx is not None and t0_time_sec is not None and t0_p_cons is not None:
        for j in range(t0_idx + 1, len(sorted_snaps)):
            s_j = sorted_snaps[j]
            s_j_time = _to_timestamp_sec(s_j.timestamp)
            dt_from_t0 = s_j_time - t0_time_sec

            if dt_from_t0 >= 300.0:  # >= 5 minutes later
                clears_strike = s_j.odds_decimal >= d_strike_val
                # Check adverse consensus move >= 1.0pp
                # For Over: adverse is consensus probability dropping >= 0.010
                # For Under: adverse is consensus probability rising >= 0.010
                adverse_cons_move = False
                if first.side.lower() in ("over", "yes"):
                    if (t0_p_cons - s_j.p_cons) >= 0.010:
                        adverse_cons_move = True
                else:
                    if (s_j.p_cons - t0_p_cons) >= 0.010:
                        adverse_cons_move = True

                if clears_strike and not adverse_cons_move:
                    grade.crossed_t1 = True
                    grade.d_t1 = s_j.odds_decimal
                    grade.t1_timestamp = s_j.timestamp
                    break

    grade.delta_retention_pct = grade.dislocation_retention

    # Settle ROI if outcome or settled_won is known
    won: bool | None = settled_won
    if won is None and outcome is not None:
        if isinstance(outcome, bool):
            won = outcome
        elif first.side.lower() in ("over", "yes"):
            won = outcome > first.line
        else:
            won = outcome < first.line

    if won is None and last.settled_won is not None:
        won = last.settled_won
    if won is None and last.outcome is not None:
        if isinstance(last.outcome, bool):
            won = last.outcome
        elif first.side.lower() in ("over", "yes"):
            won = last.outcome > first.line
        else:
            won = last.outcome < first.line

    grade.settled_won = won

    if won is not None:
        # ROI = (odds - 1.0) on win, -1.0 on loss
        def calc_roi(d: float | None) -> float | None:
            if d is None:
                return None
            return (d - 1.0) if won else -1.0

        grade.settled_roi_t0 = calc_roi(grade.d_t0)
        grade.settled_roi_t1 = calc_roi(grade.d_t1)
        grade.settled_roi_peak = calc_roi(grade.d_peak)
        grade.settled_roi_masked_close = calc_roi(grade.d_close)
        grade.settled_roi_unmasked_close = calc_roi(grade.d_close)

    return grade
