"""
ev.ownbettor.config -- own-account bettor parameters (env-overridable, OWNBETTOR_
prefix, same convention as AUTOPILOT_ / KEXEC_ / NOVIG_GATE_).

Defaults are Luc's stated v1 shape (Nov 16, 2026) plus the standing design rules from
RUL-20261115-autopilot-design:
  * bankroll $1,000, Kelly fraction 0.15 (the ruling's standing fraction), per-bet
    hard cap $100 -- Luc's Nov 16 staking amendment (supersedes the flat-$5 sketch;
    bankroll and fraction are config values, one-line changes).
  * rule gate: net EV floor 1.5%, odds ceiling +300 (price >= 0.25), EV cap 10%,
    limit orders only.
  * daily loss stop $50 (5% of the shipped bankroll, the repo's standing convention
    from mlb.execution.kalshi.config) and conservative daily bet/spend caps.
  * live_armed ships False.  Nothing in this package turns it on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

from mlb.execution.kalshi.config import _coerce

ENV_PREFIX = "OWNBETTOR_"


@dataclass(frozen=True)
class BettorConfig:
    # --- the hard switch -------------------------------------------------
    live_armed: bool = False               # OWNBETTOR_LIVE_ARMED: OFF by default; Luc's later call
    venue: str = "kalshi"                  # venue 1 per the standing venue order (AUT-08)
    kalshi_env: str = "demo"               # live mode REQUIRES "production"; demo is never real money

    # --- staking (Luc, Nov 16, 2026 amendment) ---------------------------
    bankroll: float = 1000.0               # config value; dry-run sizes against this virtual bankroll
    kelly_fraction: float = 0.15           # the standing RUL-20261115 fraction; one-line change
    per_bet_cap_usd: float = 100.0         # standing per-bet hard cap
    min_stake_usd: float = 1.0             # below this planned stake no order is intended

    # --- rule gate (RUL-20261115-autopilot-design s3/s5) ------------------
    ev_floor: float = 0.015                # net EV floor
    ev_cap: float = 0.10                   # reject (never clamp) above this
    price_min: float = 0.25                # odds ceiling +300: never bet a longer price

    # --- daily risk caps (shipped conservative) ---------------------------
    daily_bet_cap: int = 20                # max placed orders per ET day
    daily_spend_cap_usd: float = 200.0     # max filled cost per ET day
    daily_loss_stop_usd: float = 50.0      # realized loss at/past this trips the kill switch
    max_consecutive_errors: int = 5        # venue error breaker (repo convention, KEXEC_)

    # --- venue / books -----------------------------------------------------
    taker_multiplier: float = 1.0          # Kalshi series fee multiplier (fees.taker_fee)
    mapping_path: str = ""                 # recorded signal->ticker table (JSON); empty = nothing maps
    audit_dir: str = ""                    # empty = ~/.ownbettor

    @property
    def audit_path(self) -> str:
        return self.audit_dir or os.path.expanduser("~/.ownbettor")


def load_config(env=None) -> BettorConfig:
    """BettorConfig from OWNBETTOR_<FIELD> env vars (e.g. OWNBETTOR_BANKROLL=1000)."""
    env = os.environ if env is None else env
    defaults, kwargs = BettorConfig(), {}
    for f in fields(BettorConfig):
        raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    return BettorConfig(**kwargs)
