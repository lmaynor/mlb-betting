"""ev.props_config -- executor/sweep parameters for NFL props (all UNVALIDATED starting points, shadow-gated).

Starts from the MLB executor's ExecConfig (KEXEC_* env overrides still apply) and overrides what is
prop-specific: a looser consensus-book minimum (props have fewer books), a longer consensus age (the sweep
polls ~15 min), a larger pre-kick buffer (NFL inactives land ~90 min before kickoff), and bigger caps because
this is a $1,000 NOTIONAL paper/demo bankroll collecting evidence.
"""
from dataclasses import replace

from mlb.execution.kalshi.config import ExecConfig, load_config


def props_config(env=None, **over) -> ExecConfig:
    base = load_config(env)
    d = dict(n_min_books=3, disp_max=0.06, cons_max_age_min=25.0, close_buffer_min=20.0,
             spread_max=0.04, min_top_size=10.0, kdev_max=0.10,
             daily_deploy_cap_pct=0.25, per_game_cap_pct=0.06, max_orders_per_day=80)
    d.update(over)
    return replace(base, **d)
