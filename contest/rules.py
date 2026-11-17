"""ContestRules: every number a contest organiser might change, in one frozen dataclass stored as JSON on the contest row.

THE TEASER PAYOUT TABLES AND STAKE LIMITS BELOW ARE PLACEHOLDERS. They are NOT taken from the owner's real contest rulebook (not
yet supplied) or from any sportsbook. Replace them from the rulebook before a contest opens.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

# PLACEHOLDER tables: {teaser points: {legs: American price}}.
_FOOTBALL_TEASERS = {6.0: {2: -120, 3: 160, 4: 260}, 6.5: {2: -130, 3: 140, 4: 240}, 7.0: {2: -140, 3: 120, 4: 200}}
_BASKETBALL_TEASERS = {4.0: {2: -120, 3: 160, 4: 260}, 4.5: {2: -130, 3: 140, 4: 240}, 5.0: {2: -140, 3: 120, 4: 200}}
_DEFAULT_TEASERS = {"ncaaf": _FOOTBALL_TEASERS, "ncaab": _BASKETBALL_TEASERS}
# PLACEHOLDER standard deviations (points) of the final margin and total, for cash-out line moves. Not fitted to any data.
_DEFAULT_SIGMAS = {"ncaaf": {"spread": 13.5, "total": 13.0}, "ncaab": {"spread": 11.0, "total": 15.0}}


@dataclass(frozen=True)
class ContestRules:
    sport: str = "ncaaf"
    start_bankroll_cents: int = 500_000          # $5,000
    min_stake_cents: int = 2_500                 # PLACEHOLDER
    max_stake_bp: int = 2_500                    # PLACEHOLDER: max stake per bet, basis points of the current balance (25%)
    max_open_risk_bp: int = 10_000               # PLACEHOLDER: cap on (open stakes + new stake) as bp of total bankroll (100% = off)
    parlay_min_legs: int = 2
    parlay_max_legs: int = 8
    teaser_min_legs: int = 2
    teaser_max_legs: int = 4
    round_robin_min_picks: int = 3
    round_robin_max_picks: int = 11              # PLACEHOLDER: also the biggest "by k" size (a by-11 is one 11-leg parlay)
    round_robin_max_parlays: int = 500           # PLACEHOLDER: C(11,5) = 462 is the biggest single "by k" of 11 picks; the total-risk cap binds first
    round_robin_min_unit_cents: int = 500        # PLACEHOLDER: minimum stake PER PARLAY in a round robin ($5; 55 parlays at $25 would blow the cap)
    close_buffer_min: int = 15                   # bets close this long before kickoff
    quote_max_age_s: int = 1_800                 # reject a quote older than this (NCAAF 20-min cadence; NCAAB hourly needs 4_500)
    teaser_tables: dict = field(default_factory=dict)   # {points: {legs: american}}; empty -> the sport's placeholder table
    cashout_enabled: bool = True
    cashout_margin_bp: int = 500                 # PLACEHOLDER: 5% off the fair value of the bet, taken once
    cashout_sigmas: dict = field(default_factory=dict)  # {"spread": pts, "total": pts}; empty -> the sport's placeholder (UNVALIDATED)

    def cashout_sigma(self, market: str) -> float:
        """Std dev (points) of the final margin / total, used to move a held line to the current one. PLACEHOLDERS, not fitted."""
        return float((self.cashout_sigmas or _DEFAULT_SIGMAS[self.sport])[market])

    def teaser_table(self) -> dict:
        return self.teaser_tables or _DEFAULT_TEASERS[self.sport]

    def teaser_price(self, points: float, legs: int) -> int | None:
        row = self.teaser_table().get(float(points)) or {}
        return row.get(int(legs))

    # --- JSON (JSON object keys are strings, so the table is re-keyed on the way back in) ---
    def to_json(self) -> str:
        d = asdict(self)
        d["teaser_tables"] = {str(p): {str(n): a for n, a in row.items()} for p, row in self.teaser_table().items()}
        return json.dumps(d, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "ContestRules":
        d = json.loads(raw)
        d["teaser_tables"] = {float(p): {int(n): int(a) for n, a in row.items()} for p, row in (d.get("teaser_tables") or {}).items()}
        return cls(**d)
