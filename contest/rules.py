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
# (docs/analysis/contest_sportsbook_experiments_2026-11-17.md); ncaab: 700 closing lines from two weeks of January 2026 (a thin sample, and
# not March: re-fit before the tournament). Used by cash out and alt lines. UNVALIDATED.
# Same-game parlay correlation of the two EVENTS in basis points: (spread x total, moneyline x total). ncaaf measured +/-0.17 and about 0.05
# (1.02x lift); ncaab measured +/-0.02 (1.0x lift, indistinguishable from independent).
_DEFAULT_SGP_RHO_BP = {"ncaaf": (1_700, 500), "ncaab": (200, 0)}
_DEFAULT_SIGMAS = {"ncaaf": {"spread": 15.0, "total": 15.5}, "ncaab": {"spread": 11.1, "total": 17.2}}
# Key-number weights for the spread margin: how many times likelier a final margin of exactly k is than a normal curve says.
# ncaaf: 3 -> 2.51, 7 -> 1.92 on the same 943 games, rounded; the out-of-sample log-loss gain is within noise, so UNVALIDATED.
_DEFAULT_KEY_WEIGHTS = {"ncaaf": {3: 2.5, 7: 1.9}, "ncaab": {}}
# PLACEHOLDER: the farthest an alt line may sit from the main line, in points
_DEFAULT_QUOTE_AGE_S = {"ncaaf": 1_800, "ncaab": 4_500}
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
    quote_max_age_s_override: int | None = None  # reject a quote older than this; None -> the sport's default (NCAAF 20-min cadence 1_800; NCAAB hourly 4_500)
    teaser_tables: dict = field(default_factory=dict)   # {points: {legs: american}}; empty -> the sport's placeholder table
    blackjack_enabled: bool = True               # the hidden lifeline (contest/lifeline.py)
    blackjack_threshold_cents: int = 10_000      # PLACEHOLDER: the table opens under a $100 bankroll, and winnings never lift you above it
    blackjack_min_chip_cents: int = 100          # PLACEHOLDER: $1; a player is only eliminated when they cannot afford one chip
    cashout_enabled: bool = True
    cashout_margin_bp: int = 500                 # PLACEHOLDER: 5% off the fair value of the bet, taken once
    cashout_sigmas: dict = field(default_factory=dict)  # {"spread": pts, "total": pts}; empty -> the sport's placeholder (UNVALIDATED)
    halftime_enabled: bool = False               # the halftime window (contest/halftime.py): OFF until the owner turns it on after the rehearsals
    halftime_max_stake_bp: int = 1_000           # PLACEHOLDER: a halftime bet is at most 10% of the balance
    halftime_quote_max_age_s: int = 90           # a halftime quote older than this closes the window
    halftime_window_cap_s: int = 480             # the window never stays open longer than 8 minutes
    halftime_margin_bp: int = 700                # PLACEHOLDER: 7% on the Kalshi mid, taken once
    alt_lines_enabled: bool = True
    sgp_enabled: bool = True
    sgp_margin_bp: int = 700                     # PLACEHOLDER: 7% on the joint implied probability, taken once
    sgp_rho_bp: int | None = None                # spread x total correlation of the two events; None -> the sport's measured default (UNVALIDATED)
    sgp_rho_ml_bp: int | None = None             # moneyline x total; None -> the sport's default
    alt_margin_bp: int = 500                     # PLACEHOLDER: 5% on the fair implied probability, taken once (about -110 at a coin flip)
    alt_max_points_override: float = 0.0         # 0 -> the sport's default reach
    key_weights_override: dict = field(default_factory=dict)  # {margin: weight}; empty -> the sport's default

    def cashout_sigma(self, market: str) -> float:
        """Std dev (points) of the final margin / total, used to move a held line to the current one. PLACEHOLDERS, not fitted."""
        return float((self.cashout_sigmas or _DEFAULT_SIGMAS[self.sport])[market])

    @property
    def quote_max_age_s(self) -> int:
        return int(self.quote_max_age_s_override if self.quote_max_age_s_override is not None else _DEFAULT_QUOTE_AGE_S[self.sport])

    def sgp_rho(self, market: str) -> float:
        """Correlation of the two events of a same-game parlay: 'spread' (spread x total) or 'ml' (moneyline x total). Positive = move together."""
        default = _DEFAULT_SGP_RHO_BP[self.sport]
        bp = (self.sgp_rho_ml_bp if market == "ml" else self.sgp_rho_bp)
        return (default[1] if market == "ml" else default[0]) / 10_000.0 if bp is None else bp / 10_000.0

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
        if "quote_max_age_s" in d:                                      # rows written before the per-sport default
            d["quote_max_age_s_override"] = d.pop("quote_max_age_s")
        d["key_weights_override"] = {int(k): float(v) for k, v in (d.get("key_weights_override") or {}).items()}
        d["teaser_tables"] = {float(p): {int(n): int(a) for n, a in row.items()} for p, row in (d.get("teaser_tables") or {}).items()}
        return cls(**d)
