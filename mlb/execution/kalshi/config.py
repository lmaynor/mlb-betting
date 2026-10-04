"""
mlb.execution.kalshi.config -- executor parameters (all env-overridable, KEXEC_ prefix).

Every default is an UNVALIDATED starting point (design doc s4), to be calibrated in
shadow mode -- not a measured result. Dollar caps are derived from the owner-stated
$1,000 bankroll and the repo's existing risk conventions:
  per-bet cap   5% of bankroll   (mlb_core.odds.utils.kelly_stake max_pct)
  per-game cap  4% of bankroll   (exposure.py CAP_UNITS=2 x UNIT_PCT=1%, doubled for
                                  a taker that can build a position over several runs)
  daily deploy  10% of bankroll  (CONTEXT s16 E11 global daily exposure cap)
  daily loss    5% of bankroll   (kill switch)

Only the Kalshi DEMO environment is supported. Production is refused in code
(see client.KalshiClient) until the owner explicitly provisions it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

DEMO_ENV = "demo"


@dataclass(frozen=True)
class ExecConfig:
    kalshi_env: str = DEMO_ENV
    bankroll: float = 1000.0

    # --- strategy gates ---
    ev_min: float = 0.03            # NET of fee + shrinkage
    ev_cap: float = 0.10            # reject (never clamp) above this
    kelly_lambda: float = 0.25      # confirmed-lag tier
    kelly_lambda_persistent: float = 0.10   # persistent / unknown-lag tier
    n_min_books: int = 4
    disp_max: float = 0.06          # MAD of per-book fair_prob
    kdev_max: float = 0.08          # |kalshi mid - book fair| beyond this => news, not lag
    spread_max: float = 0.04
    min_top_size: float = 25.0      # contracts at best ask
    price_min: float = 0.03         # liquid game-level markets
    price_max: float = 0.97
    extreme_min: float = 0.02       # never trade past these (market-maker ADR 0017)
    extreme_max: float = 0.98

    # --- freshness ---
    book_max_age_s: float = 2.0     # Kalshi order book
    cons_max_age_min: float = 15.0  # book-pack consensus; refresh (ParlayAPI) NOT built yet
    close_buffer_min: float = 10.0  # no new orders this close to first pitch

    # --- shrinkage: p_hat = p_B - (h0 + h_age*max(0, age-age0) + h_disp*disp) ---
    h0: float = 0.005
    h_age_per_min: float = 0.001
    h_age0_min: float = 5.0
    h_disp: float = 0.5

    # --- lag signature ---
    lag_lookback_min: float = 60.0
    lag_move: float = 0.02

    # --- sizing / risk caps (dollars) ---
    per_bet_cap_pct: float = 0.05
    per_game_cap_pct: float = 0.04
    daily_deploy_cap_pct: float = 0.10
    daily_loss_cap_pct: float = 0.05
    max_orders_per_day: int = 20
    max_consecutive_rejects: int = 5
    min_contracts: int = 1
    min_stake: float = 2.0

    # --- execution ---
    enabled: bool = False           # EXEC_ENABLED: must be set explicitly to place demo orders
    series: tuple = ("KXMLBGAME", "KXMLBRFI", "KXMLBTOTAL")   # v1 allowlist (s1)
    taker_multiplier: float = 1.0

    @property
    def per_bet_cap(self) -> float:
        return self.bankroll * self.per_bet_cap_pct

    @property
    def per_game_cap(self) -> float:
        return self.bankroll * self.per_game_cap_pct

    @property
    def daily_deploy_cap(self) -> float:
        return self.bankroll * self.daily_deploy_cap_pct

    @property
    def daily_loss_cap(self) -> float:
        return self.bankroll * self.daily_loss_cap_pct


def _coerce(raw: str, default):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, tuple):
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    return raw


def load_config(env=None) -> ExecConfig:
    """ExecConfig from KEXEC_<FIELD> env vars (e.g. KEXEC_BANKROLL=1000, KEXEC_EV_MIN=0.04)."""
    env = os.environ if env is None else env
    defaults = ExecConfig()
    kwargs = {}
    for f in fields(ExecConfig):
        raw = env.get(f"KEXEC_{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    return ExecConfig(**kwargs)
