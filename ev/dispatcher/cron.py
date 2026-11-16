"""Cron parsing, matching, and expansion with Cloud Scheduler semantics.

The dispatcher (ev.dispatcher.run) and the equivalence proof
(ev.dispatcher.prove) share this module, so the fire sets the proof expands
are produced by the exact code path the dispatcher matches with at runtime.

Semantics implemented (standard cron, as Cloud Scheduler documents):
  - five fields: minute hour day-of-month month day-of-week
  - lists (1,2,3), ranges (1-5), steps (*/15, 9-19/2, 5/20), and '*'
  - day-of-week 0 and 7 are both Sunday
  - when BOTH day-of-month and day-of-week are restricted (not '*'), a day
    matches when EITHER matches; otherwise both must match
  - matching is against local wall-clock time in the entry's timezone

Expansion walks local days and materializes candidate (hour, minute) pairs,
so it stays fast over long windows. A candidate wall time that does not
exist (spring-forward gap) is skipped; an ambiguous wall time (fall-back)
is emitted once, at fold=0. Both choices mirror how a scheduler anchored
to wall-clock time behaves, and the proof window used for OPS-15
(2026-11-17 to 2027-02-17) contains no US DST transition in any case.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


def _atom(token: str, names: dict[str, int] | None) -> int:
    token = token.strip().lower()
    if names and token in names:
        return names[token]
    return int(token)


def parse_field(field: str, lo: int, hi: int,
                names: dict[str, int] | None = None) -> frozenset[int]:
    """Expand one cron field to the set of values it selects."""
    values: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty list element in cron field {field!r}")
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"non-positive step in cron field {field!r}")
        else:
            base, step = part, 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _atom(a, names), _atom(b, names)
        else:
            start = _atom(base, names)
            # '5/20' means 5..max step 20 in standard cron.
            end = hi if "/" in part else start
        if start < lo or end > hi or start > end:
            raise ValueError(
                f"cron field {field!r} selects outside [{lo}, {hi}]")
        values.update(range(start, end + 1, step))
    return frozenset(values)


class Cron:
    """A parsed five-field cron expression."""

    def __init__(self, expression: str):
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError(f"cron expression needs 5 fields: {expression!r}")
        self.expression = expression
        self.minutes = parse_field(fields[0], 0, 59)
        self.hours = parse_field(fields[1], 0, 23)
        self.months = parse_field(fields[3], 1, 12, _MONTH_NAMES)
        self._dom_restricted = fields[2] != "*"
        self._dow_restricted = fields[4] != "*"
        self.doms = parse_field(fields[2], 1, 31) if self._dom_restricted else None
        dow_raw = parse_field(fields[4], 0, 7, _DOW_NAMES) if self._dow_restricted else None
        if dow_raw is not None:
            # Normalize 7 -> 0 (both are Sunday).
            self.dows = frozenset(0 if d == 7 else d for d in dow_raw)
        else:
            self.dows = None

    def _day_matches(self, d: date) -> bool:
        if d.month not in self.months:
            return False
        dom_ok = self.doms is None or d.day in self.doms
        cron_dow = (d.weekday() + 1) % 7  # Python Mon=0 -> cron Sun=0
        dow_ok = self.dows is None or cron_dow in self.dows
        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        return dom_ok and dow_ok

    def matches_local(self, local_dt: datetime) -> bool:
        """True when the aware local datetime is a fire minute."""
        return (
            local_dt.minute in self.minutes
            and local_dt.hour in self.hours
            and self._day_matches(local_dt.date())
        )

    def matches(self, instant_utc: datetime, tz: ZoneInfo) -> bool:
        return self.matches_local(instant_utc.astimezone(tz))

    def expand(self, tz: ZoneInfo, start_utc: datetime,
               end_utc: datetime) -> list[datetime]:
        """All fire instants (aware UTC, minute precision) in [start, end)."""
        fires: list[datetime] = []
        day = start_utc.astimezone(tz).date()
        last_day = end_utc.astimezone(tz).date()
        one_day = timedelta(days=1)
        while day <= last_day:
            if self._day_matches(day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        wall = datetime.combine(day, time(hour, minute))
                        aware = wall.replace(tzinfo=tz, fold=0)
                        as_utc = aware.astimezone(timezone.utc)
                        # Nonexistent wall time (spring-forward gap): the
                        # round trip lands on a different wall clock. Skip.
                        if as_utc.astimezone(tz).replace(tzinfo=None) != wall:
                            continue
                        if start_utc <= as_utc < end_utc:
                            fires.append(as_utc)
            day += one_day
        return fires
