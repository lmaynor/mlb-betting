"""
ev.autopilot.config -- Phase 0 shadow parameters (env-overridable, AUTOPILOT_ prefix,
same convention as the Kalshi KEXEC_ and Novig NOVIG_GATE_ configs).

Every gate constant is from the two rulings, not a local invention:
  RUL-20261115-autopilot-design s3/s5  -- EV floor 1.5% net (Novig form), 2.30% gross
      (Kalshi form, logged), EV cap 10%, odds ceiling +300 (ask >= 0.25), anchor
      n_books >= 4, dispersion (MAD) <= 0.04, limit ceiling = min(signal + 1.0pp,
      price where net EV == 1.50%), no confluence.
  RUL-20261115-autopilot-clv-definition -- dual CLV bar, post-Nov-13 regime only,
      tiered consensus-age cap (median <= 25m / oldest <= 45m; <= 15m / <= 30m inside
      T-60m), target quote <= 5m old at firing (hard reject), thin-anchor guardrail
      (exactly-4-book signals need max-min dispersion <= 5.0pp), the 0.37% fill-cost
      drag in the net CLV definitions, and the venue move-rate split (sticky < 60%).
  RUL-20261116-autopilot-clv-drift -- AMENDS the Phase 0 pass spec for the live
      window: the >= 0.00pp filled-drift floor is replaced by a two-population
      Drift & Dislocation-Retention bar (all-intent drift >= -0.50pp on the
      ALL_GATE_PASS population, filled drift >= -1.00pp, gross retention >= 0.50),
      a volume span (n >= 150 across >= 7 game days, per-slice admission n >= 40),
      and the Active-Exchange venue cutoff raised from 60% to >= 80% Closing Quote
      Move Rate (closing two-way hold <= 4.5% retained).  The amended constants
      live below; bar_drift_pp_min / sticky_move_rate_max remain only as the
      superseded backtest-window form used by evaluate.evaluate_window.

The 0.37% fill-cost drag is an Argon-supplied constant from the CLV ruling.  It is NOT
derivable from the banked record (NOV-01 models Novig pre-game fees as 0), so the bar
evaluation reports CLV both with the drag (the ruling's official form) and without it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from mlb.execution.kalshi.config import _coerce

ENV_PREFIX = "AUTOPILOT_"
VENUE = "novig"

# Odds API market key -> (sport, stat, system label).  Saves are a first-class entry
# whose enable flag defaults OFF (backtest: 3 signals in 15 days; the Dec 21 saves
# promotion gate governs enabling, AUT-07).
MARKETS = {
    "player_shots_on_goal": ("nhl", "sog", "NHL_SOG"),
    "player_saves": ("nhl", "saves", "NHL_SAVES"),
    "player_threes": ("nba", "threes", "NBA_THREES"),
    "player_rebounds": ("nba", "rebounds", "NBA_REB"),
}
SPORT_FEEDS = {"nhl": "icehockey_nhl", "nba": "basketball_nba"}   # OddsAccum GCS layout

REGIME_START = datetime(2026, 11, 13, 0, 0, tzinfo=timezone.utc)  # CLV ruling Q2: post-Nov-13 only


@dataclass(frozen=True)
class ShadowConfig:
    armed: bool = False                    # AUTOPILOT_SHADOW_ARMED: the kill switch. OFF = runner refuses (outside --dry-run)
    enable_saves: bool = False             # AUTOPILOT_ENABLE_SAVES: per-market flag, default OFF (Dec 21 gate)

    # --- gates (design ruling s3) ---
    ev_floor: float = 0.015                # net EV floor, Novig form (shrunk p_hat / ask - 1)
    ev_floor_kalshi_gross: float = 0.023   # Kalshi gross form: computed + logged, not the shadow gate
    ev_cap: float = 0.10
    ask_min: float = 0.25                  # +300 odds ceiling
    n_books_min: int = 4                   # leave-Novig-out consensus anchor
    disp_max: float = 0.04                 # MAD of per-book fairs
    thin_anchor_max_range: float = 0.05    # exactly-4-book signals: max-min fair range must be <= 5.0pp
    close_buffer_min: float = 10.0         # production close buffer (no firing inside it)

    # --- consensus-age caps (CLV ruling Q2) ---
    target_quote_max_age_min: float = 5.0  # Novig quote age at firing: hard reject beyond this
    age_median_max_min: float = 25.0       # > 60m to game start
    age_oldest_max_min: float = 45.0
    age_median_max_min_late: float = 15.0  # <= 60m to game start
    age_oldest_max_min_late: float = 30.0
    late_window_min: float = 60.0

    # --- shadow execution (design ruling s5) ---
    slippage_cap_pp: float = 0.01          # limit ceiling = min(signal + 1.0pp, breakeven at the EV floor)

    # --- shadow sizing for ledger fields (design ruling s4 defaults; paper only) ---
    bankroll: float = 1000.0
    kelly_fraction: float = 0.15
    per_bet_cap_pct: float = 0.02
    per_bet_cap_usd: float = 100.0
    min_order_usd: float = 5.0

    # --- CLV (CLV ruling) ---
    fill_cost_drag: float = 0.0037         # Argon-supplied; see module docstring
    t15_min: float = 15.0                  # same-book close = Novig quote at T-15m
    moved_tick: float = 0.005              # one mid-band Novig grid tick
    sticky_move_rate_max: float = 0.60     # venue classification split

    # --- Phase 0 bars (CLV ruling restated table) ---
    bar_volume: int = 150
    bar_consensus_clv_pct: float = 1.50
    bar_consensus_ci_lo_gt: float = 0.50
    bar_same_book_clv_pct: float = 0.50
    bar_same_book_ci_lo_ge: float = 0.00
    bar_drift_pp_min: float = 0.00         # SUPERSEDED for the live window by RUL-20261116 (see below)
    ci_level: float = 0.95

    # --- Phase 0 bars, amended (RUL-20261116-autopilot-clv-drift, Q1/Q2/Q4) ---
    bar_volume_days_min: int = 7           # Bar 1 span: FILLED across >= 7 distinct game days
    bar_slice_admission_n: int = 40        # per-slice admission for graduation / holdout
    bar_slice_consensus_ci_lo_gt: float = 0.00   # Bar 2 per-slice CI floor (pooled floor stays +0.50)
    bar_all_intent_drift_pp_min: float = -0.50   # Bar 4: ALL_GATE_PASS drift from t0
    bar_filled_drift_pp_min: float = -1.00       # Bar 5: FILLED drift from t0
    bar_retention_min: float = 0.50              # Bar 6: gross dislocation retention at T-15m close
    active_move_rate_min: float = 0.80           # Q2: Active-Exchange cutoff (amends sticky_move_rate_max)
    active_hold_max: float = 0.045               # Q2: closing two-way hold <= 4.5% (retained; unmeasured in Phase 0)
    gate_pass_disp_max_4: float = 0.045          # ALL_GATE_PASS dispersion tier, exactly 4 books
    gate_pass_disp_max_5p: float = 0.060         # ALL_GATE_PASS dispersion tier, >= 5 books

    def market_enabled(self, stat: str) -> bool:
        if stat == "saves":
            return self.enable_saves
        return stat in ("sog", "threes", "rebounds")


def load_config(env=None) -> ShadowConfig:
    """ShadowConfig from AUTOPILOT_<FIELD> env vars (e.g. AUTOPILOT_SHADOW_ARMED=1)."""
    env = os.environ if env is None else env
    defaults, kwargs = ShadowConfig(), {}
    for f in fields(ShadowConfig):
        raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    # the kill switch's env name is the one the design ruling's breach protocol uses
    raw = env.get("AUTOPILOT_SHADOW_ARMED")
    if raw not in (None, ""):
        kwargs["armed"] = _coerce(raw, False)
    return ShadowConfig(**kwargs)
