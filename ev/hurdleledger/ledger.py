"""
ev.hurdleledger.ledger -- the monthly rollup and the hurdle state.

One row per calendar month from the window's opening month through the current
month: Kalshi net, recreational net, subscriptions net, combined net, and whether
the month clears the $500 bar. The hurdle is MET when the required run of
consecutive clearing months is complete (the last month of the run has ended);
a run whose last month is still in progress reads ON TRACK. Months before the
window opened never appear; the opening month is flagged partial (the window
opened on the 16th) but counts like any other month if it clears.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import streams
from .config import LedgerConfig

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]


def month_label(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{_MONTH_NAMES[m - 1]} {y}"


def _month_range(first: str, last: str) -> list:
    y, m = int(first[:4]), int(first[5:7])
    ey, em = int(last[:4]), int(last[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


def build_ledger(cfg: LedgerConfig, today: date | None = None) -> dict:
    """The full scoreboard dict: per-month rows, hurdle state, stream notes."""
    today = today or date.today()
    start = date.fromisoformat(cfg.window_start)
    window_end = start + timedelta(days=cfg.window_days)
    kalshi = streams.read_kalshi(cfg.kalshi_audit_path)
    subs = streams.read_subscriptions(cfg.sub_records)
    rec = streams.read_rec(cfg.rec_log)

    first_month = cfg.window_start[:7]
    current_month = today.strftime("%Y-%m")
    months = []
    for month in _month_range(first_month, current_month):
        k = round(kalshi.monthly_live.get(month, 0.0), 2)
        r = round(rec.monthly.get(month, 0.0), 2)
        s = round(subs.monthly.get(month, 0.0), 2)
        combined = round(k + r + s, 2)
        months.append({
            "month": month,
            "label": month_label(month),
            "kalshi": k, "recreational": r, "subscriptions": s,
            "combined": combined,
            "clears": combined >= cfg.target_usd,
            "complete": month < current_month,
            "partial": month == first_month and start.day > 1,
        })

    # Hurdle state: find the best run of consecutive clearing months.
    need = cfg.consecutive_months
    state, met_pair, on_track_pair = "in_progress", None, None
    run = []
    for row in months:
        run = run + [row["month"]] if row["clears"] else []
        if len(run) >= need:
            pair = run[-need:]
            if months[[m["month"] for m in months].index(pair[-1])]["complete"]:
                met_pair = pair
            else:
                on_track_pair = pair
    if met_pair:
        state = "met"
    elif on_track_pair:
        state = "on_track"
    elif today > window_end:
        state = "not_met_window_closed"

    best = max(months, key=lambda r: r["combined"]) if months else None
    return {
        "target_usd": cfg.target_usd,
        "consecutive_months": need,
        "window_start": cfg.window_start,
        "window_end": window_end.isoformat(),
        "today": today.isoformat(),
        "months": months,
        "hurdle": {"state": state, "met_months": met_pair, "on_track_months": on_track_pair},
        "best_month": best,
        "kalshi_paper": {"monthly": kalshi.monthly_paper,
                         "count": kalshi.paper_count, "live_count": kalshi.live_count},
        "open_rec_bets": rec.open_bets,
        "notes": kalshi.notes + subs.notes + rec.notes,
    }
