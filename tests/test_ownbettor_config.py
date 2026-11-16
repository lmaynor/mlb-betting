"""ev.ownbettor config: Luc's v1 defaults, env overrides, the hard switch ships OFF."""
from ev.ownbettor.config import BettorConfig, load_config


def test_defaults_are_lucs_v1_shape():
    cfg = BettorConfig()
    assert cfg.live_armed is False                 # the hard switch ships OFF
    assert cfg.venue == "kalshi"
    assert cfg.bankroll == 1000.0                  # Luc's Nov 16 amendment: $1,000 bankroll
    assert cfg.kelly_fraction == 0.15              # the standing RUL-20261115 fraction
    assert cfg.per_bet_cap_usd == 100.0            # standing per-bet hard cap
    assert cfg.ev_floor == 0.015                   # net EV floor 1.5%
    assert cfg.price_min == 0.25                   # odds ceiling +300
    assert cfg.daily_loss_stop_usd == 50.0


def test_env_overrides_are_one_line_changes():
    cfg = load_config({"OWNBETTOR_BANKROLL": "2500", "OWNBETTOR_KELLY_FRACTION": "0.25",
                       "OWNBETTOR_PER_BET_CAP_USD": "150", "OWNBETTOR_DAILY_BET_CAP": "10"})
    assert cfg.bankroll == 2500.0
    assert cfg.kelly_fraction == 0.25
    assert cfg.per_bet_cap_usd == 150.0
    assert cfg.daily_bet_cap == 10
    assert cfg.live_armed is False                 # untouched by unrelated overrides


def test_live_switch_requires_explicit_env():
    assert load_config({}).live_armed is False
    assert load_config({"OWNBETTOR_LIVE_ARMED": "1"}).live_armed is True
    assert load_config({"OWNBETTOR_LIVE_ARMED": "0"}).live_armed is False
