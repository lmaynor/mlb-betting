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
# Standard deviations (points) of the final margin and total around the closing line. ncaaf: fitted on 943 closing lines of the 2025 season
# (docs/analysis/contest_sportsbook_experiments_2026-11-17.md); ncaab is still a PLACEHOLDER. Used by cash out and alt lines. UNVALIDATED.
_DEFAULT_SIGMAS = {"ncaaf": {"spread": 15.0, "total": 15.5}, "ncaab": {"spread": 11.0, "total": 15.0}}
# Key-number weights for the spread margin: how many times likelier a final margin of exactly k is than a normal curve says.
# ncaaf: 3 -> 2.51, 7 -> 1.92 on the same 943 games, rounded; the out-of-sample log-loss gain is within noise, so UNVALIDATED.
_DEFAULT_KEY_WEIGHTS = {"ncaaf": {3: 2.5, 7: 1.9}, "ncaab": {}}
# PLACEHOLDER: the farthest an alt line may sit from the main line, in points
_DEFAULT_ALT_MAX = {"ncaaf": 14.0, "ncaab": 10.0}


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
    blackjack_enabled: bool = True               # the hidden lifeline (contest/lifeline.py)
    blackjack_threshold_cents: int = 10_000      # PLACEHOLDER: the table opens under a $100 bankroll, and winnings never lift you above it
    blackjack_min_chip_cents: int = 100          # PLACEHOLDER: $1; a player is only eliminated when they cannot afford one chip
    cashout_enabled: bool = True
    cashout_margin_bp: int = 500                 # PLACEHOLDER: 5% off the fair value of the bet, taken once
    cashout_sigmas: dict = field(default_factory=dict)  # {"spread": pts, "total": pts}; empty -> the sport's placeholder (UNVALIDATED)
    alt_lines_enabled: bool = True
    sgp_enabled: bool = True
    sgp_margin_bp: int = 700                     # PLACEHOLDER: 7% on the joint implied probability, taken once
    sgp_rho_bp: int = 1_700                      # spread x total correlation of the two events, +/-0.17 (940 football games; UNVALIDATED)
    sgp_rho_ml_bp: int = 500                     # moneyline x total: measured about 0 (1.02x lift); a small placeholder
    alt_margin_bp: int = 500                     # PLACEHOLDER: 5% on the fair implied probability, taken once (about -110 at a coin flip)
    alt_max_points_override: float = 0.0         # 0 -> the sport's default reach
    key_weights_override: dict = field(default_factory=dict)  # {margin: weight}; empty -> the sport's default

    def cashout_sigma(self, market: str) -> float:
        """Std dev (points) of the final margin / total, used to move a held line to the current one. PLACEHOLDERS, not fitted."""
        return float((self.cashout_sigmas or _DEFAULT_SIGMAS[self.sport])[market])

    def key_weights(self) -> dict:
        """Spread-margin weights for the margin model: how much likelier a margin of exactly k is than a normal curve says. UNVALIDATED."""
        return dict(self.key_weights_override or _DEFAULT_KEY_WEIGHTS[self.sport])

    @property
    def alt_max_points(self) -> float:
        return float(self.alt_max_points_override or _DEFAULT_ALT_MAX[self.sport])

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
        d["key_weights_override"] = {int(k): float(v) for k, v in (d.get("key_weights_override") or {}).items()}
        d["teaser_tables"] = {float(p): {int(n): int(a) for n, a in row.items()} for p, row in (d.get("teaser_tables") or {}).items()}
        return cls(**d)
