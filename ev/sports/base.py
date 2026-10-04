"""
ev.sports.base -- SportAdapter: everything sport-specific about mapping a Kalshi market to a
proposition, and nothing else.

Facts here were read from the LIVE Kalshi public API (rules_primary / rules_secondary), not assumed
(2026-10-04). Re-verify before enabling a series; a changed rules text must fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SportAdapter:
    sport: str                      # 'nfl' | 'nba' | 'nhl'
    kalshi_prefix: str              # 'KXNFL'
    league_phrase: str              # as it appears in rules text: '<phrase> game originally scheduled for'
    unit: str                       # 'points' | 'goals' (spread/total wording)
    codes: frozenset                # complete Kalshi team-code universe for the league
    tie_policy: str = "none"        # 'half' => a tie resolves to $0.50 per team (NFL); 'none' => no ties
    # kinds the mapper must refuse, kind -> reason (a rules difference vs the sportsbook proposition)
    disabled: dict = field(default_factory=dict)

    def series(self, kind: str) -> str:
        return self.kalshi_prefix + {"ml": "GAME", "spread": "SPREAD", "total": "TOTAL"}[kind]
