"""
ev.hurdleledger.report -- the plain-language monthly summary Iggy hands to Luc.

Answer first (the hurdle state), then the months, then the notes that explain
any zero. Every figure comes from the ledger dict; this module only words it.
"""

from __future__ import annotations

from .ledger import month_label


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _date_label(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    month_name = month_label(d.strftime("%Y-%m")).split(" ")[0]
    return f"{month_name} {d.day}, {d.year}"


def _hurdle_line(led: dict) -> str:
    h = led["hurdle"]
    target = _money(led["target_usd"])
    need = led["consecutive_months"]
    end = _date_label(led["window_end"])
    if h["state"] == "met":
        pair = " and ".join(month_label(m) for m in h["met_months"])
        return (f"Hurdle: MET. {pair} each cleared {target} in combined net "
                f"profit, {need} months in a row.")
    if h["state"] == "on_track":
        pair = " and ".join(month_label(m) for m in h["on_track_months"])
        return (f"Hurdle: ON TRACK. {pair} are both at or above {target}; the "
                "hurdle is met when the current month ends if its figure holds.")
    if h["state"] == "not_met_window_closed":
        return (f"Hurdle: NOT MET. The 90-day window closed on {end} without "
                f"{need} straight months at {target}.")
    best = led["best_month"]
    if best and best["combined"] > 0:
        return (f"Hurdle: not met yet. Best month so far is {best['label']} at "
                f"{_money(best['combined'])} combined. The bar is {target} a "
                f"month, {need} months in a row, by {end}.")
    return (f"Hurdle: not met yet. No month has posted combined profit so far. "
            f"The bar is {target} a month, {need} months in a row, by {end}.")


def render_text(led: dict) -> str:
    lines = ["Mission hurdle — monthly profit ledger", "=" * 42, "", _hurdle_line(led), ""]
    for row in led["months"]:
        flags = []
        if row["partial"]:
            flags.append("partial month — the window opened mid-month")
        if not row["complete"]:
            flags.append("month still in progress")
        verdict = "clears the bar" if row["clears"] else "does not clear the bar"
        lines.append(f"{row['label']}" + (f" ({'; '.join(flags)})" if flags else ""))
        lines.append(f"  Kalshi (automated):   {_money(row['kalshi'])}")
        lines.append(f"  Recreational books:   {_money(row['recreational'])}")
        lines.append(f"  Subscriptions:        {_money(row['subscriptions'])}")
        lines.append(f"  Combined:             {_money(row['combined'])} — {verdict}")
        lines.append("")
    open_bets = led["open_rec_bets"]
    if open_bets:
        total_stake = sum(float(b.get("stake_usd") or 0.0) for b in open_bets)
        lines.append(f"Open recreational bets not yet settled: {len(open_bets)} "
                     f"({_money(total_stake)} staked). They count in the month "
                     "they settle, not before.")
        lines.append("")
    if led["notes"]:
        lines.append("Notes:")
        for note in led["notes"]:
            lines.append(f"  - {note}")
    return "\n".join(lines).rstrip() + "\n"
