"""Sharp-weighted sportsbook consensus fair-value anchor for Kalshi NBA props.

RUL-20261125 Half 2 / MAK-01, shadow-only.
Anchors strictly to de-vigged two-way sportsbook consensus from Beezy's
existing snapshot feed. No prop model probabilities enter anywhere (c = 0.00).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from ev.first_scorer_grader import devig_n
from ev.kalshi_wide_quoter_rules import WideSpreadConfig
from ev.props import DFS_BOOKS, Quote
from mlb_core.odds.utils import american_to_implied_prob

# Hardcoded per RUL-20261125 Half 2: zero prop-model weight enters this pricer.
PROP_MODEL_WEIGHT_C = 0.00


@dataclass(frozen=True)
class BookLine:
    """Single sportsbook's two-way Over/Under American quote at a point."""

    book: str
    over_american: int
    under_american: int
    ts: datetime | str | None


@dataclass(frozen=True)
class AnchorResult:
    """Consensus fair-value anchor output and diagnostic metadata."""

    fv_yes_prob: float | None
    ok: bool
    reason: str
    n_fresh_books: int
    max_book_age_sec: float | None
    dispersion_pp: float | None
    books_used: tuple[tuple[str, float, float], ...]
    n_stale_dropped: int = 0


def _parse_utc_ts(raw: datetime | str | None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=timezone.utc) if raw.tzinfo is None else raw.astimezone(timezone.utc)
    if not isinstance(raw, str):
        return None
    txt = raw.strip()
    if not txt:
        return None
    try:
        dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def from_ev_quotes(
    quotes: Sequence[Quote],
    stat: str,
    point: float,
    player_norm: str | None = None,
) -> list[BookLine]:
    """Filter ev.props.Quote rows to exact stat + point, two-sided only, UTC ts."""
    out: list[BookLine] = []
    target_pt = float(point)
    for q in quotes:
        if q.stat != stat:
            continue
        if abs(float(q.point) - target_pt) > 1e-9:
            continue
        if player_norm is not None and q.player_norm != player_norm:
            continue
        if not q.two_sided or q.over is None or q.under is None:
            continue
        out.append(
            BookLine(
                book=q.book,
                over_american=int(q.over),
                under_american=int(q.under),
                ts=_parse_utc_ts(q.ts),
            )
        )
    return out


def _weighted_median(items: list[tuple[str, float, float]]) -> float:
    """Deterministic weighted median over (book, p_i, weight) sorted by (p_i, book).

    Tie handling: when cumulative weight hits exactly total_weight / 2.0 at item i
    (analogous to an even-length unweighted sample), average p_i and p_{i+1}.
    Otherwise return the first p_i whose cumulative weight strictly exceeds half.
    """
    ordered = sorted(items, key=lambda row: (row[1], row[0]))
    total_w = sum(row[2] for row in ordered)
    half = total_w / 2.0
    cum = 0.0
    for idx, (_, p_i, w_i) in enumerate(ordered):
        cum += w_i
        if abs(cum - half) <= 1e-12 and idx + 1 < len(ordered):
            return (p_i + ordered[idx + 1][1]) / 2.0
        if cum >= half - 1e-12:
            return p_i
    return ordered[-1][1]


def compute_anchor(
    lines: Sequence[BookLine],
    now: datetime,
    cfg: WideSpreadConfig,
) -> AnchorResult:
    """Compute sharp-weighted median fair Over probability from two-way BookLines."""
    now_utc = _parse_utc_ts(now)
    assert now_utc is not None

    staleness_limit_s = min(
        cfg.staleness_cap_s,
        max(cfg.staleness_base_s, 2 * cfg.feed_poll_interval_s),
    )

    fresh_records: list[tuple[str, float, float, float, bool]] = []
    n_stale_dropped = 0

    for line in lines:
        book_key = str(line.book or "").strip().lower()
        if not book_key or book_key == "kalshi":
            continue
        if line.over_american is None or line.under_american is None:
            continue
        if book_key in DFS_BOOKS:
            continue

        q_over = american_to_implied_prob(line.over_american)
        q_under = american_to_implied_prob(line.under_american)
        if math.isnan(q_over) or math.isnan(q_under) or not (0.0 < q_over < 1.0) or not (0.0 < q_under < 1.0):
            continue

        hold = q_over + q_under - 1.0
        if hold <= cfg.sharp_hold_max:
            weight = cfg.weight_sharp
            is_sharp = True
        elif hold <= cfg.retail_hold_max:
            weight = cfg.weight_retail
            is_sharp = False
        else:
            # Spec classes retail up to cfg.retail_hold_max (7.5%) and excludes above
            # cfg.hold_exclude_above (8.0%); the 7.5%-8.0% gap is resolved fail-closed = excluded.
            _ = cfg.hold_exclude_above
            continue

        ts_utc = _parse_utc_ts(line.ts)
        if ts_utc is None:
            n_stale_dropped += 1
            continue
        age_sec = (now_utc - ts_utc).total_seconds()
        if age_sec > staleness_limit_s:
            n_stale_dropped += 1
            continue

        p_i = devig_n([q_over, q_under], method="power")[0]
        fresh_records.append((str(line.book), p_i, weight, max(0.0, age_sec), is_sharp))

    n_fresh = len(fresh_records)
    max_age = max((r[3] for r in fresh_records), default=None)
    dispersion_pp = (
        (max(r[1] for r in fresh_records) - min(r[1] for r in fresh_records)) * 100.0
        if n_fresh >= 1
        else None
    )
    books_used = tuple(
        sorted(((r[0], r[1], r[2]) for r in fresh_records), key=lambda row: (row[1], row[0]))
    )

    has_sharp = any(r[4] for r in fresh_records)
    quorum_needed = cfg.quorum_with_sharp if has_sharp else cfg.quorum_without_sharp
    if n_fresh < quorum_needed:
        return AnchorResult(
            fv_yes_prob=None,
            ok=False,
            reason="quorum",
            n_fresh_books=n_fresh,
            max_book_age_sec=max_age,
            dispersion_pp=dispersion_pp,
            books_used=books_used,
            n_stale_dropped=n_stale_dropped,
        )

    assert dispersion_pp is not None
    if dispersion_pp > cfg.dispersion_max_pp:
        return AnchorResult(
            fv_yes_prob=None,
            ok=False,
            reason="dispersion",
            n_fresh_books=n_fresh,
            max_book_age_sec=max_age,
            dispersion_pp=dispersion_pp,
            books_used=books_used,
            n_stale_dropped=n_stale_dropped,
        )

    fv = _weighted_median(list(books_used))
    return AnchorResult(
        fv_yes_prob=fv,
        ok=True,
        reason="ok",
        n_fresh_books=n_fresh,
        max_book_age_sec=max_age,
        dispersion_pp=dispersion_pp,
        books_used=books_used,
        n_stale_dropped=n_stale_dropped,
    )
