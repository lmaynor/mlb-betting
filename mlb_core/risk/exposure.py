"""
mlb_core.risk.exposure — Shared bankroll and per-game exposure tracking.

Used by all runners to:
  1. Compute current bankroll (starting + settled P&L across all systems)
  2. Compute open stake on a specific game_pk (for per-game exposure cap)
  3. Apply the 2-unit per-game cap before sizing each bet

Usage in runners:
    from mlb_core.risk.exposure import get_bankroll_and_cap

    bankroll, remaining_cap = get_bankroll_and_cap(
        engine, game_pk, game_date,
        starting=1000, cap_units=2.0, unit_pct=0.01,
    )
    stake = min(kelly_stake(..., bankroll=bankroll, ...), remaining_cap)
    if stake <= 0:
        kelly_triggered = False
        stake = 0.0
"""
from __future__ import annotations
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)

STARTING_BANKROLL = 1000.0
CAP_UNITS         = 2.0
UNIT_PCT          = 0.01   # 1% of current bankroll = 1 unit


def current_bankroll(engine, starting: float = STARTING_BANKROLL) -> float:
    """Return starting bankroll + sum of all settled profits across all systems."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COALESCE(SUM(profit), 0) FROM bets WHERE result IS NOT NULL")
            ).fetchone()
        pnl = float(row[0]) if row else 0.0
        return max(starting + pnl, starting * 0.10)  # floor at 10% of starting
    except Exception as e:
        logger.warning(f"exposure: current_bankroll failed: {e} — using starting={starting}")
        return starting


def open_stake_for_game(engine, game_pk: int, game_date: str) -> float:
    """Return total open (unsettled) stake across all systems for a game_pk."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT COALESCE(SUM(stake), 0) FROM bets
                    WHERE game_pk = :gpk
                      AND game_date = :gd
                      AND result IS NULL
                      AND kelly_triggered = TRUE
                """),
                {"gpk": game_pk, "gd": game_date},
            ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.warning(f"exposure: open_stake_for_game failed: {e} — returning 0")
        return 0.0


def get_bankroll_and_cap(
    engine,
    game_pk: int,
    game_date: str,
    starting: float = STARTING_BANKROLL,
    cap_units: float = CAP_UNITS,
    unit_pct: float = UNIT_PCT,
) -> tuple[float, float]:
    """
    Return (current_bankroll, remaining_cap_dollars) for a game.

    remaining_cap is how many dollars can still be bet on this game_pk
    before hitting the cap_units limit. Callers should:

        stake = min(kelly_stake(..., bankroll=bankroll, ...), remaining_cap)
        if stake <= 0:
            kelly_triggered = False
    """
    bankroll = current_bankroll(engine, starting=starting)
    unit     = bankroll * unit_pct
    cap      = cap_units * unit
    open_s   = open_stake_for_game(engine, game_pk, game_date)
    remaining = max(0.0, cap - open_s)
    logger.debug(
        f"exposure: game_pk={game_pk} bankroll=${bankroll:.0f} "
        f"unit=${unit:.2f} cap=${cap:.2f} open=${open_s:.2f} remaining=${remaining:.2f}"
    )
    return bankroll, remaining
