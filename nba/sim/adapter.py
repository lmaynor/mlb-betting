"""nba.sim.adapter -- turn the serving hierarchical engine's own output into simulator inputs (no new projection logic).

`engine_player_input` calls the SAME private functions the picks board calls (`ev.daily_projections._nba_component` / `_nba_projection`)
for one player and one game, so the simulator's per-player means, standard deviations and families ARE the board's marginals, and the
board's current coherent-from-marginals combos (Normal with moment-matched variance) come back beside them as the baseline to beat.
"""
from __future__ import annotations

from nba.props import hierarchical as H
from nba.sim.joint_sim import Marginal, PlayerInput

SIM_STATS = ("points", "rebounds", "assists", "threes")
COMBO_STATS = ("pra", "pr", "pa", "ra")


def _marginal(res: dict) -> Marginal:
    return Marginal(res["family"], float(res["mu"]), float(res["sd"]), None if res.get("alpha") is None else float(res["alpha"]))


def engine_player_input(name: str, side: str, prior: list, logs, team, opp, gk, start, day: str | None = None):
    """-> (PlayerInput, {combo stat: Marginal}) or None when the engine has no projection (thin history, no minutes).

    `prior` = the player's chronological rows strictly before the game (the caller cuts it); `logs` = a `GameLogs` (or the replay
    subclass); `side` = 'home' | 'away'. `minutes` is the engine's own expected minutes (EWM trimmed for back-to-back / blowout when a
    spread is known); `base_minutes` is the untrimmed EWM the minutes table is indexed by. threes is dropped when the master carries
    no attempts for the player (the engine then returns nothing for it)."""
    from ev import daily_projections as DP      # imported late: the heavy module is only needed when the engine is actually run

    mu_min = H.ewm_minutes(prior)
    if not mu_min:
        return None
    md = H.project_minutes_distribution(mu_min, spread=logs.nba_spread(gk), rest=H.rest_days(prior, day), pf36=H.pf_per36(prior))
    marg = {}
    for stat in SIM_STATS:
        res = DP._nba_component(stat, name, prior, logs, team, opp, gk, start)
        if res is None:
            if stat == "threes":
                continue
            return None
        marg[stat] = _marginal(res)
    combos = {}
    for stat in COMBO_STATS:
        res = DP._nba_projection(stat, name, prior, logs, team, opp, gk, start)
        if res is not None:
            combos[stat] = _marginal(res)
    return PlayerInput(name, side, float(md["mean"]) or float(mu_min), marg, base_minutes=float(mu_min)), combos
