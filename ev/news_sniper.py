"""ev/news_sniper.py -- Module 7.3: Event-Triggered Availability Sniper Library.

Library-only module for identifying stale quotes across sportsbooks and exchanges
immediately following availability transitions (e.g. starter confirmed OUT or backup
goalie confirmed).

NO background poller, NO daemons, NO network calls, NO timers.
Pure decision evaluation over supplied events and quote snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Any

from ev.props import norm_name


@dataclass(frozen=True)
class AvailabilityTransition:
    """Represents a player availability status change."""
    player: str
    sport: str
    prev_status: str
    new_status: str
    news_ts: datetime
    source: str
    team: str | None = None
    game_key: str | None = None
    player_norm: str | None = None

    def __post_init__(self):
        if self.player_norm is None:
            object.__setattr__(self, "player_norm", norm_name(self.player))

    @property
    def is_actionable(self) -> bool:
        """True if the transition represents a starter OUT or backup goalie confirmed."""
        st = (self.new_status or "").strip().upper()
        return st in ("OUT", "SCRATCHED", "INACTIVE", "BACKUP_CONFIRMED", "BACKUP_STARTER")


@dataclass(frozen=True)
class QuoteSnapshot:
    """Represents a point-in-time quote from a venue."""
    venue: str
    game_key: str
    player_norm: str
    stat: str
    line: float
    side: str
    price: float  # decimal odds or implied prob / ask
    quote_ts: datetime
    player: str | None = None
    ask_size: float | None = None


@dataclass(frozen=True)
class StaleQuoteCandidate:
    """A quote flagged as stale relative to an availability transition."""
    transition: AvailabilityTransition
    quote: QuoteSnapshot
    stale_seconds: float
    p_lambda_recomputed: float | None = None
    edge_recomputed: float | None = None


def find_stale_quote_candidates(
    events: list[AvailabilityTransition],
    quotes: list[QuoteSnapshot],
    window_minutes: float = 10.0,
    current_ts: datetime | None = None,
    recompute: Callable[..., float | None] | None = None,
) -> list[StaleQuoteCandidate]:
    """Find quotes whose timestamp predates an availability transition within the [0, +10m] window.

    Args:
        events: AvailabilityTransition records.
        quotes: QuoteSnapshot records across venues.
        window_minutes: Evaluation window duration (default 10 minutes).
        current_ts: Evaluation timestamp. If provided, checks news_ts <= current_ts <= news_ts + window_minutes.
        recompute: Optional callback (ev, q) or (player, game) -> recomputed P_lambda.
    """
    candidates: list[StaleQuoteCandidate] = []

    for ev in events:
        if not ev.is_actionable:
            continue

        news_ts = ev.news_ts
        if news_ts.tzinfo is None:
            news_ts = news_ts.replace(tzinfo=timezone.utc)

        # Evaluation window check if current_ts is provided
        if current_ts is not None:
            curr = current_ts if current_ts.tzinfo is not None else current_ts.replace(tzinfo=timezone.utc)
            elapsed = (curr - news_ts).total_seconds()
            if elapsed < 0 or elapsed > window_minutes * 60.0:
                continue

        for q in quotes:
            # Match player or game scope
            player_match = bool(ev.player_norm and q.player_norm and ev.player_norm == q.player_norm)
            game_match = bool(ev.game_key and q.game_key and ev.game_key == q.game_key)

            if not player_match and not game_match:
                continue

            q_ts = q.quote_ts
            if q_ts.tzinfo is None:
                q_ts = q_ts.replace(tzinfo=timezone.utc)

            # Quote must PREDATE the news timestamp
            if q_ts >= news_ts:
                continue

            stale_sec = (news_ts - q_ts).total_seconds()

            p_recomputed = None
            edge_recomputed = None
            if recompute is not None:
                try:
                    p_recomputed = recompute(ev, q)
                except TypeError:
                    try:
                        p_recomputed = recompute(ev.player, ev.game_key)
                    except Exception:
                        p_recomputed = None

                if p_recomputed is not None:
                    q_prob = 1.0 / q.price if q.price > 1.0 else q.price
                    edge_recomputed = p_recomputed - q_prob

            candidates.append(
                StaleQuoteCandidate(
                    transition=ev,
                    quote=q,
                    stale_seconds=stale_sec,
                    p_lambda_recomputed=p_recomputed,
                    edge_recomputed=edge_recomputed,
                )
            )

    return candidates
