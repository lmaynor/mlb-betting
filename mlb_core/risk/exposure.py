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


def prefetch_exposure(
    engine,
    game_pks: list[int],
    game_date: str,
    starting: float = STARTING_BANKROLL,
    system: str = None,
) -> tuple[float, dict[int, float]]:
    """
    Prefetch bankroll and open stakes for all game_pks in one pass.
    Call once per runner invocation, before the prediction loop.

    Returns:
        (bankroll, {game_pk: open_stake_dollars})
    """
    bankroll = current_bankroll(engine, starting=starting)
    if not game_pks:
        return bankroll, {}
    try:
        from sqlalchemy import text as _text
        placeholders = ",".join(str(g) for g in game_pks)
        with engine.connect() as conn:
            rows = conn.execute(_text(f"""
                SELECT game_pk, COALESCE(SUM(stake), 0) as open_stake
                FROM bets
                WHERE game_pk IN ({placeholders})
                  AND game_date = :gd
                  AND result IS NULL
                  AND kelly_triggered = TRUE
                  {('AND system = :sys' if system else '')}
                GROUP BY game_pk
            """), {"gd": game_date, "sys": system} if system else {"gd": game_date}).fetchall()
        return bankroll, {int(r[0]): float(r[1]) for r in rows}
    except Exception as e:
        logger.warning(f"exposure: prefetch_exposure failed: {e} -- returning zeros")
        return bankroll, {}


def apply_cap(
    bankroll: float,
    game_pk: int,
    prefetched: dict[int, float],
    pending: dict[int, float],
    cap_units: float = CAP_UNITS,
    unit_pct: float = UNIT_PCT,
) -> tuple[float, float]:
    """
    Compute (bankroll, remaining_cap) using prefetched + in-run pending stakes.

    prefetched: open stakes from DB at start of runner (does not change).
    pending:    stakes logged so far THIS runner run (local accumulator).
    """
    unit = bankroll * unit_pct
    cap  = cap_units * unit
    open_s = prefetched.get(game_pk, 0.0) + pending.get(game_pk, 0.0)
    remaining = max(0.0, cap - open_s)
    logger.debug(
        f"exposure: game_pk={game_pk} bankroll=${bankroll:.0f} "
        f"unit=${unit:.2f} cap=${cap:.2f} open=${open_s:.2f} remaining=${remaining:.2f}"
    )
    return bankroll, remaining
