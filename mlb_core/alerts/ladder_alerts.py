"""
mlb_core.alerts.ladder_alerts -- Discord ladder ping presentation layer (RUL-20261119 Q4/Q6).

Forward-shadow presentation for verified multi-book ladder dislocations.
This module is a pure presentation layer; nothing calls it yet.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from mlb_core.alerts.discord_routes import (
    EDGE_URL,
    bot_configured,
    canonical_book,
    post_via_bot,
    webhook_for,
)

log = logging.getLogger("mlb_core.alerts.ladder_alerts")

# -- Constants & Categories (mirrors beezy-vip/lib/ladders.ts) ------------------------------------------------

YARDAGE_STATS = frozenset({"rec_yds", "rush_yds", "pass_yds"})
COUNT_STATS = frozenset({"receptions", "pass_tds"})
LADDER_STATS = YARDAGE_STATS | COUNT_STATS

CATEGORY_FLOORS: dict[str, float] = {
    "yardage": 2.5,
    "count": 5.0,
}

LADDER_MIN_REF = 4
LADDER_DISP_MAX = 0.06
CAP_LO = -5000
CAP_HI = 10000

EMBED_COLOR = 0x57F287
GOVERNANCE_TAG = "[FORWARD SHADOW · UNGRADED] LADDER"

# Mirrors sport_alerts.STAT_NAMES for the ladder market stats
STAT_NAMES: dict[str, str] = {
    "rec_yds": "Rec Yds",
    "rush_yds": "Rush Yds",
    "pass_yds": "Pass Yds",
    "receptions": "Receptions",
    "pass_tds": "Pass TDs",
    "anytime_td": "Anytime TD",
}

BOOK_LABELS: dict[str, str] = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "caesars": "Caesars",
    "bet365": "Bet365",
    "espnbet": "ESPN BET",
    "thescore": "theScore",
    "fanatics": "Fanatics",
    "hardrock": "Hard Rock",
    "betrivers": "BetRivers",
    "pinnacle": "Pinnacle",
    "kalshi": "Kalshi",
    "prophetx": "ProphetX",
}


def ladder_category(stat: str | None) -> str | None:
    """Return 'yardage' or 'count' if the stat is an eligible ladder stat, else None."""
    if not stat or not isinstance(stat, str):
        return None
    s = stat.strip().lower()
    if s in YARDAGE_STATS:
        return "yardage"
    if s in COUNT_STATS:
        return "count"
    return None


def book_label(book: str | None) -> str:
    """Friendly book display label, fallback canonical key title-cased."""
    if not book:
        return ""
    canon = canonical_book(book)
    if not isinstance(canon, str):
        return str(book)
    c_lower = canon.strip().lower()
    return BOOK_LABELS.get(c_lower, canon.title())


def american_to_decimal(american: int | float) -> float:
    """Convert American odds to decimal payout multiplier."""
    am = float(american)
    if am > 0:
        return 1.0 + am / 100.0
    elif am < 0:
        return 1.0 + 100.0 / abs(am)
    return 1.0


def format_american(price: int | float) -> str:
    """Format American price with explicit sign (+240, -110)."""
    p = int(round(price))
    return f"{p:+d}"


def format_line(line: float | int) -> str:
    """Format line numbers without trailing .0 (64.5 -> '64.5', 65.0 -> '65')."""
    val = float(line)
    if val.is_integer():
        return str(int(val))
    s = str(val)
    if s.endswith(".0"):
        return s[:-2]
    return s


def parse_ts(ts: str | datetime | None) -> datetime | None:
    """Parse ISO timestamp or datetime into a UTC-aware datetime."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def format_tip(commence: str | datetime | None) -> str:
    """Format commence timestamp as '%a %b %d, %I:%M %p UTC' when parseable, else raw string."""
    if not commence:
        return ""
    dt = parse_ts(commence)
    if dt is not None:
        return dt.astimezone(timezone.utc).strftime("%a %b %d, %I:%M %p UTC")
    return str(commence)


def gate_floor_american(p_fair: float | None, category: str) -> int | None:
    """
    Calculate the American odds required to hit the category's EV floor.
    Mirrors lib/ladders.ts:gateFloorAmerican exactly.
    """
    if category not in ("yardage", "count"):
        return None
    if p_fair is None or not isinstance(p_fair, (int, float)) or math.isnan(p_fair) or p_fair <= 0 or p_fair >= 1:
        return None

    e = 0.05 if category == "count" else 0.025
    d = (1.0 + e) / float(p_fair)

    if d >= 2.0:
        a = math.ceil((d - 1.0) * 100.0)
    elif d > 1.0:
        a = -math.floor(100.0 / (d - 1.0))
    else:
        return None

    if a >= CAP_HI or a <= CAP_LO:
        return None

    return a


def make_rung_label(line: float | int, side: str, category: str) -> str:
    """
    Rung label:
    - Count category + over: '{ceil(line)}+' (e.g. 4.5 -> '5+')
    - Over otherwise: 'Over {line}'
    - Under: 'Under {line}'
    """
    side_lower = side.lower()
    line_float = float(line)
    if category == "count" and side_lower == "over":
        return f"{math.ceil(line_float)}+"
    elif side_lower == "over":
        return f"Over {format_line(line_float)}"
    else:
        return f"Under {format_line(line_float)}"


# -- Qualification (VERIFIED DISLOCATION) ---------------------------------------------------------------------

def qualify_rung_side(row: dict, side: str) -> dict | None:
    """
    Check if a rung side S in {over, under} qualifies as a VERIFIED DISLOCATION.
    Returns the qualified pick dict if valid, else None.
    """
    side = side.lower()
    if side not in ("over", "under"):
        return None

    stat = row.get("stat")
    category = ladder_category(stat)
    if category is None:
        return None

    best_s = row.get(f"best_{side}")
    if not best_s or not isinstance(best_s, dict):
        return None

    book = best_s.get("book")
    if not book or canonical_book(book) == "novig":
        return None

    n_ref_loo = best_s.get("n_ref_loo")
    if n_ref_loo is None or n_ref_loo < LADDER_MIN_REF:
        return None

    disp_loo = best_s.get("disp_loo")
    if disp_loo is None or float(disp_loo) > LADDER_DISP_MAX:
        return None

    american = best_s.get("american")
    if american is None or american <= CAP_LO or american >= CAP_HI:
        return None

    p_cons_loo = best_s.get("p_cons_loo")
    if p_cons_loo is None:
        return None
    p_cons_float = float(p_cons_loo)
    p_side = p_cons_float if side == "over" else (1.0 - p_cons_float)
    if p_side <= 0.0 or p_side >= 1.0:
        return None

    dec = american_to_decimal(american)
    net_ev_pct = (p_side * dec - 1.0) * 100.0
    floor = CATEGORY_FLOORS[category]
    if net_ev_pct < floor:
        return None

    # Check best book's bar in row.books
    books = row.get("books", [])
    if isinstance(books, dict):
        books = list(books.values())

    target_bar = next((b for b in books if b.get("book") == book), None)
    if target_bar is None:
        target_bar = next((b for b in books if canonical_book(b.get("book")) == canonical_book(book)), None)
    if target_bar is None:
        return None

    # Target bar must be two-sided
    if target_bar.get("over") is None or target_bar.get("under") is None:
        return None

    # stale_opener must be falsy
    if bool(target_bar.get("stale_opener")):
        return None

    # Freshness check:
    # Within 30 minutes of the rung's freshest two-sided bar ts
    two_sided_bars = [b for b in books if b.get("over") is not None and b.get("under") is not None]
    valid_dts = [parse_ts(b.get("ts")) for b in two_sided_bars]
    valid_dts = [dt for dt in valid_dts if dt is not None]
    freshest_ts = max(valid_dts) if valid_dts else None
    target_ts = parse_ts(target_bar.get("ts"))

    if target_ts is not None and freshest_ts is not None:
        if (freshest_ts - target_ts).total_seconds() > 30 * 60:
            return None

    line = float(row.get("line", 0.0))
    rung_lbl = make_rung_label(line, side, category)

    return {
        "row": row,
        "side": side,
        "book": book,
        "price": int(american),
        "net_ev": round(net_ev_pct, 4),
        "p_side": p_side,
        "p_cons_loo": p_cons_float,
        "disp_loo": float(disp_loo),
        "n_ref_loo": int(n_ref_loo),
        "line": line,
        "rung_label": rung_lbl,
        "category": category,
    }


def qualify_ladder(rows: list[dict]) -> dict | None:
    """
    Qualify all rungs of a ladder group (same game_key, player_norm, stat).
    Returns a ladder summary dict with 'primary' and 'siblings' if at least one rung side qualifies,
    else None.
    """
    if not rows:
        return None

    stat = next((r.get("stat") for r in rows if r.get("stat")), None)
    category = ladder_category(stat)
    if category is None:
        return None

    qualifying_picks: list[dict] = []
    for r in rows:
        for side in ("over", "under"):
            q = qualify_rung_side(r, side)
            if q is not None:
                qualifying_picks.append(q)

    if not qualifying_picks:
        return None

    # Primary pick: highest net EV (ties: lower disp_loo, then lower line)
    def pick_sort_key(p: dict):
        return (
            -float(p["net_ev"]),
            float(p["disp_loo"]),
            float(p["line"]),
            0 if p["side"] == "over" else 1,
        )

    qualifying_picks.sort(key=pick_sort_key)
    primary = qualifying_picks[0]
    siblings = qualifying_picks[1:]

    player = next((r.get("player") for r in rows if r.get("player")), "")
    player_norm = next((r.get("player_norm") for r in rows if r.get("player_norm")), "")
    game_key = next((r.get("game_key") for r in rows if r.get("game_key")), "")
    away = next((r.get("away") for r in rows if r.get("away")), "")
    home = next((r.get("home") for r in rows if r.get("home")), "")
    commence = next((r.get("commence") for r in rows if r.get("commence")), "")

    return {
        "game_key": game_key,
        "player": player,
        "player_norm": player_norm,
        "stat": stat,
        "category": category,
        "away": away,
        "home": home,
        "commence": commence,
        "primary": primary,
        "siblings": siblings,
        "rows": rows,
    }


# -- Embed Builder --------------------------------------------------------------------------------------------

def build_ladder_embed(ladder: dict | list[dict]) -> dict:
    """
    Build a Discord embed for a qualified ladder.
    Accepts a qualified ladder dict, a list of row dicts for a ladder group, or a single row dict.
    """
    if isinstance(ladder, list):
        ql = qualify_ladder(ladder)
        if ql is None:
            raise ValueError("No qualifying rungs in ladder rows")
        ladder = ql
    elif isinstance(ladder, dict) and "primary" not in ladder:
        ql = qualify_ladder([ladder])
        if ql is None:
            raise ValueError("Row does not qualify as a ladder")
        ladder = ql

    primary: dict = ladder["primary"]
    primary_row: dict = primary["row"]
    side = primary["side"]
    category = ladder.get("category") or ladder_category(ladder.get("stat")) or "yardage"
    stat = ladder.get("stat") or primary_row.get("stat", "")
    stat_lbl = STAT_NAMES.get(stat, stat.replace("_", " ").title())
    player = ladder.get("player") or primary_row.get("player", "")

    rung_lbl = primary["rung_label"]
    title = f"{player} — {rung_lbl} ({stat_lbl})"

    lines: list[str] = [
        GOVERNANCE_TAG,
    ]

    # b. Main-line anchor when modal_open and modal_point are both present
    m_open = primary_row.get("modal_open")
    m_now = primary_row.get("modal_point") if primary_row.get("modal_point") is not None else primary_row.get("modal_now")
    if m_open is not None and m_now is not None:
        open_s = format_line(m_open)
        now_s = format_line(m_now)
        if float(m_open) == float(m_now):
            lines.append(f"Main: {now_s} · Unchanged")
        else:
            lines.append(f"Main: Open {open_s} → Now {now_s}")

    # c. Top books: top 3 bars on the primary side by decimal descending (ties: freshest ts first)
    # excluding novig (canonical) and cap prices on that side
    row_books = primary_row.get("books", [])
    if isinstance(row_books, dict):
        row_books = list(row_books.values())

    cands = []
    for b in row_books:
        price = b.get(side)
        if price is None:
            continue
        if canonical_book(b.get("book")) == "novig":
            continue
        cap_flag = b.get(f"{side}_cap")
        if cap_flag or price <= CAP_LO or price >= CAP_HI:
            continue
        dec = american_to_decimal(price)
        ts_dt = parse_ts(b.get("ts"))
        cands.append((dec, ts_dt, b, price))

    def top_book_key(cand):
        dec, ts_dt, _, _ = cand
        has_ts = 1 if ts_dt is not None else 0
        return (
            dec,
            has_ts,
            ts_dt or datetime.min.replace(tzinfo=timezone.utc),
        )

    cands.sort(key=top_book_key, reverse=True)

    top_bars = []
    seen_canonical = set()
    for dec, ts_dt, b, price in cands:
        cb = canonical_book(b.get("book"))
        if cb in seen_canonical:
            continue
        seen_canonical.add(cb)
        top_bars.append((b.get("book"), price))
        if len(top_bars) == 3:
            break

    top_books_str = " · ".join(f"{book_label(b_name)} {format_american(p)}" for b_name, p in top_bars)
    lines.append(f"Top books: {top_books_str}")

    # d. Consensus Fair
    p_side = primary["p_side"]
    prob = p_side * 100.0
    if p_side >= 0.5:
        fair_am = -round(p_side / (1.0 - p_side) * 100.0)
    else:
        fair_am = round((1.0 - p_side) / p_side * 100.0)
    lines.append(f"Consensus Fair {format_american(fair_am)} · {prob:.1f}%")

    # e. Gate floor
    floor_val = gate_floor_american(p_side, category)
    if floor_val is not None:
        floor_pct = 5.0 if category == "count" else 2.5
        lines.append(f"Gate floor (+{floor_pct:.1f}%): {format_american(floor_val)}")

    # f. Shadow EV @ Best
    lines.append(f"Shadow EV @ Best: +{primary['net_ev']:.1f}%")

    # g. Ref books
    ref_str = f"Ref books: {primary['n_ref_loo']} (2-sided) · Disp: {primary['disp_loo']:.3f}"
    rung_vig = primary_row.get("rung_vig")
    if rung_vig is not None and not (isinstance(rung_vig, float) and math.isnan(rung_vig)):
        vig_pct = float(rung_vig) * 100.0
        ref_str += f" · Rung Vig: {vig_pct:.1f}%"
    lines.append(ref_str)

    # h. Siblings
    siblings: list[dict] = ladder.get("siblings", [])
    if siblings:
        inline = siblings[:2]
        inline_items = [
            f"{s['rung_label']} ({format_american(s['price'])}, +{s['net_ev']:.1f}% shadow EV)"
            for s in inline
        ]
        k = len(siblings) - len(inline)
        extra = f" · +{k} more in /edge" if k > 0 else ""
        lines.append(f"Other qualifying rungs: {' · '.join(inline_items)}{extra}")

    description = "\n".join(lines)

    # Footer
    away = primary_row.get("away") or ladder.get("away", "")
    home = primary_row.get("home") or ladder.get("home", "")
    commence = primary_row.get("commence") or ladder.get("commence", "")
    tip = format_tip(commence)
    if tip:
        footer_text = f"{away} @ {home} · {tip}"
    else:
        footer_text = f"{away} @ {home}".strip()

    return {
        "title": title,
        "color": EMBED_COLOR,
        "url": EDGE_URL,
        "description": description,
        "footer": {"text": footer_text},
    }


# -- Window Planner -------------------------------------------------------------------------------------------

def plan_ladder_pings(
    rows: list[dict], now: datetime | None = None
) -> tuple[list[dict], dict | None]:
    """
    Qualify all ladders in rows, sort by:
      primary net EV descending, commence ascending, primary n_ref_loo descending, primary disp_loo ascending.
    Return (top 3 embeds, overflow) where overflow is None when <= 3 ladders qualify,
    else a digest embed.
    """
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for r in rows:
        stat = r.get("stat")
        if not stat or ladder_category(stat) is None:
            continue
        gk = r.get("game_key", "")
        pn = r.get("player_norm") or r.get("player", "")
        groups.setdefault((gk, pn, stat), []).append(r)

    qualified_ladders: list[dict] = []
    for grp_rows in groups.values():
        ql = qualify_ladder(grp_rows)
        if ql is not None:
            qualified_ladders.append(ql)

    if not qualified_ladders:
        return [], None

    def ladder_sort_key(ladder: dict):
        p = ladder["primary"]
        commence_dt = parse_ts(ladder.get("commence")) or datetime.max.replace(tzinfo=timezone.utc)
        return (
            -float(p["net_ev"]),
            commence_dt,
            -int(p.get("n_ref_loo", 0)),
            float(p.get("disp_loo", 0.0)),
        )

    qualified_ladders.sort(key=ladder_sort_key)

    if len(qualified_ladders) <= 3:
        embeds = [build_ladder_embed(l) for l in qualified_ladders]
        return embeds, None

    top_embeds = [build_ladder_embed(l) for l in qualified_ladders[:3]]
    remaining = qualified_ladders[3:]
    k = len(remaining)

    desc_lines = [GOVERNANCE_TAG]
    for l in remaining:
        p = l["primary"]
        player = l.get("player", "")
        rung_lbl = p["rung_label"]
        ev = p["net_ev"]
        book_lbl = book_label(p["book"])
        price_str = format_american(p["price"])
        desc_lines.append(f"{player} — {rung_lbl}: +{ev:.1f}% shadow EV · {book_lbl} {price_str}")

    overflow = {
        "title": f"More ladder dislocations ({k} more)",
        "color": EMBED_COLOR,
        "url": EDGE_URL,
        "description": "\n".join(desc_lines),
        "footer": {"text": "Full ladders in /edge"},
    }

    return top_embeds, overflow


# -- Sender ---------------------------------------------------------------------------------------------------

def post_ladder_pings(
    rows: list[dict], sport: str = "nfl", now: datetime | None = None
) -> bool:
    """
    Build the ladder plan (embeds + overflow appended when present) and deliver.
    Returns False when nothing qualifies.
    If bot_configured(): post each embed via post_via_bot('card', None, [embed], sport=sport).
    Else fall back to webhook_for('card', sport=sport).
    Never raises.
    """
    try:
        embeds, overflow = plan_ladder_pings(rows, now=now)
        all_embeds = list(embeds)
        if overflow is not None:
            all_embeds.append(overflow)

        if not all_embeds:
            return False

        if bot_configured():
            posted = 0
            for embed in all_embeds:
                if post_via_bot("card", None, [embed], sport=sport):
                    posted += 1
            return posted > 0

        url = webhook_for("card", sport=sport)
        if not url:
            log.warning("ladder alerts: no card webhook configured for %s; %d embed(s) not posted", sport, len(all_embeds))
            return False

        import requests
        resp = requests.post(url, json={"embeds": all_embeds}, timeout=20)
        return resp.status_code < 300
    except Exception as exc:  # noqa: BLE001
        log.warning("post_ladder_pings failed: %s", exc)
        return False
