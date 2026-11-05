"""
ev.novig.liability -- per-bet / per-player / per-game LIABILITY caps for Novig plans (pure functions + one ledger read).

Buying a $1-payout binary at price p costs p (+ fee) and that cost is the whole maximum loss, so liability == all-in cost.
The fee model is CONFIG, not logic: `LiabilityConfig.fee_rate` (default 0.0) is the pre-game taker fee as a fraction of
cost, justified only while the market's `fee.charged == 'WHEN_LIVE'` (Novig charges its coefficient only in play).  A leg
whose `fee_charged` differs from `required_fee_charged` is REJECTED, so the 0% assumption can never silently apply to a
market that charges pre-game.

Caps are fractions of a NOTIONAL bankroll and every one is overridable with a NOVIG_LIAB_<FIELD> env var (same shape as
the Kalshi executor's KEXEC_ config).  The defaults are deliberately tighter than the Kalshi props shadow caps
(ev.props_config: 6% per game): 1% per bet, 1.5% per player, 3% per game.  They only RESTRICT -- check_liability can
shrink or reject a plan, never enlarge one.  Nothing here touches the Kalshi RiskGate or the (loosened, DEMO-only)
Kalshi RFQ caps.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, fields

from ev.props import norm_name
from mlb.execution.kalshi.config import _coerce

ENV_PREFIX = "NOVIG_LIAB_"


@dataclass(frozen=True)
class LiabilityConfig:
    bankroll: float = 1000.0               # notional dollars the percentages apply to
    fee_rate: float = 0.0                  # pre-game taker fee as a fraction of cost: 0%-fee assumption, stated here
    required_fee_charged: str = "WHEN_LIVE"   # the venue market flag that justifies fee_rate (NovigLeg.fee_charged)
    per_bet_cap_pct: float = 0.01
    per_player_cap_pct: float = 0.015      # all stats of one player in one game/day
    per_game_cap_pct: float = 0.03         # every position in one game: props in a game are correlated
    min_stake: float = 2.0                 # a shrunk order below this is rejected, not placed

    @property
    def per_bet_cap(self) -> float:
        return self.bankroll * self.per_bet_cap_pct

    @property
    def per_player_cap(self) -> float:
        return self.bankroll * self.per_player_cap_pct

    @property
    def per_game_cap(self) -> float:
        return self.bankroll * self.per_game_cap_pct


def load_liability_config(env=None) -> LiabilityConfig:
    env = os.environ if env is None else env
    defaults, kwargs = LiabilityConfig(), {}
    for f in fields(LiabilityConfig):
        raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    return LiabilityConfig(**kwargs)


@dataclass(frozen=True)
class Exposure:
    game_key: str
    player_norm: str
    stat: str
    cost: float                            # all-in dollars already at risk


@dataclass(frozen=True)
class LiabilityResult:
    allowed: bool
    contracts: int                         # whole $1-payout contracts allowed (0 when rejected)
    stake: float                           # all-in liability of those contracts
    reason: str = ""                       # "" | REJ_LIABILITY_FEE | REJ_LIABILITY_CAP | REJ_LIABILITY_MIN
    binding: str | None = None             # which cap limited the size: bet | player | game
    headroom: dict | None = None


def unit_liability(price: float, cfg: LiabilityConfig) -> float:
    """Max loss of ONE $1-payout contract bought at `price`."""
    return price * (1.0 + cfg.fee_rate)


def check_liability(cfg: LiabilityConfig, *, contracts: int, price: float, game_key: str, player_norm: str,
                    exposures, fee_charged: str = "WHEN_LIVE") -> LiabilityResult:
    """Size a plan DOWN to the tightest cap, or reject it.  `price` is the plan's worst-case per-contract price (its IOC
    limit), so the liability is an upper bound on what a fill can cost.  `exposures`: Exposure rows already held today."""
    if fee_charged != cfg.required_fee_charged:
        return LiabilityResult(False, 0, 0.0, "REJ_LIABILITY_FEE")
    if not (0.0 < price < 1.0) or contracts <= 0:
        return LiabilityResult(False, 0, 0.0, "REJ_LIABILITY_CAP")
    held_player = sum(e.cost for e in exposures if e.game_key == game_key and e.player_norm in (player_norm, ""))   # unknown player: counts against everyone
    held_game = sum(e.cost for e in exposures if e.game_key == game_key)
    room = {"bet": cfg.per_bet_cap, "player": cfg.per_player_cap - held_player, "game": cfg.per_game_cap - held_game}
    binding = min(room, key=room.get)
    head = max(0.0, room[binding])
    per = unit_liability(price, cfg)
    n = min(int(contracts), int(math.floor(head / per + 1e-9)))
    if n <= 0 or n * per < cfg.min_stake - 1e-9:
        return LiabilityResult(False, 0, 0.0, "REJ_LIABILITY_CAP" if n <= 0 else "REJ_LIABILITY_MIN", binding, room)
    return LiabilityResult(True, n, n * per, "", binding if n < contracts else None, room)


def ledger_exposures(ledger, day: str, mode: str) -> list:
    """Filled cost already held today in one ledger mode, per (game, player, stat).  Read-only SQL on the ledger."""
    with ledger._lock:
        rows = ledger._c.execute(
            "SELECT o.game_pk, o.filled_cost, o.fee, d.inputs_json FROM orders o "
            "LEFT JOIN decisions d ON d.intent_id = o.intent_id AND d.mode = o.mode AND d.outcome IN ('EXECUTED','PARTIAL') "
            "WHERE o.day = ? AND o.mode = ? AND o.fill_count > 0 GROUP BY o.intent_id", (day, mode)).fetchall()
    out = []
    for r in rows:
        try:
            inp = json.loads(r["inputs_json"] or "{}")
            player, stat = norm_name(inp.get("player") or ""), str(inp.get("stat") or "")
        except (TypeError, ValueError):
            player, stat = "", ""
        out.append(Exposure(str(r["game_pk"]), player, stat, float(r["filled_cost"] or 0.0) + float(r["fee"] or 0.0)))
    return out
