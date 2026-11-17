"""ESPN scoreboard -> the contest's own view of a game. Pure parsing: no network, no database.

One scoreboard event gives everything the contest needs: the teams (ESPN team ids drive the logos), the kickoff, the status, the live
score and (football) the situation, and the pre-game DraftKings lines (`competitions[0].odds[0]`). ESPN does not publish odds on a
game that has started or finished, so quotes are only read from `pre` events; the board keeps the last pre-game quote for a live game.

Field conventions (checked against a live 2026-11-21 college football payload): `spread` is the HOME handicap (home +5.5 when the away
team is favoured); `pointSpread.<side>.close.line` is "+5.5" / "-5.5"; `total.over.close.line` is "o47.5"; every `odds` is an American
price string ("-112", "+185"). `curatedRank.current` is 99 for an unranked team. Football `situation` fields are read defensively: they
have not been confirmed on a live payload yet.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_UNRANKED = 99


def _num(x) -> float | None:
    try:
        return float(re.sub(r"[^0-9.+-]", "", str(x)))
    except (TypeError, ValueError):
        return None


def _american(x) -> int | None:
    n = _num(x)
    return None if n is None or abs(n) < 100 else int(n)


def _team(c: dict) -> dict:
    t = c.get("team") or {}
    rank = (c.get("curatedRank") or {}).get("current")
    return {"abbr": t.get("abbreviation") or "", "name": t.get("shortDisplayName") or t.get("displayName") or "",
            "espn_id": int(t["id"]) if str(t.get("id", "")).isdigit() else None,
            "rank": int(rank) if isinstance(rank, int) and 0 < rank < _UNRANKED else None}


def status_of(ev: dict) -> str:
    """scheduled | live | final | void (postponed / cancelled / suspended)."""
    t = ((ev.get("status") or {}).get("type") or {})
    name, state = t.get("name", ""), t.get("state", "")
    if t.get("completed") or name == "STATUS_FINAL":
        return "final"
    if name in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_FORFEIT"):
        return "void"
    if state == "in":
        return "live"
    return "scheduled"


def parse_odds(comp: dict) -> dict | None:
    """The pre-game main lines as {spread: {line (home), home, away}, total: {line, over, under}, ml: {home, away}}; any market may be None."""
    items = comp.get("odds") or []
    if not items:
        return None
    o = items[0]
    ps, tot, ml = o.get("pointSpread") or {}, o.get("total") or {}, o.get("moneyline") or {}
    close = lambda block, side: (((block.get(side) or {}).get("close")) or {})
    spread = None
    h_line, a_line = _num(close(ps, "home").get("line")), _num(close(ps, "away").get("line"))
    h_px, a_px = _american(close(ps, "home").get("odds")), _american(close(ps, "away").get("odds"))
    if h_line is None and a_line is not None:
        h_line = -a_line
    if h_line is None:
        h_line = _num(o.get("spread"))
    if h_line is not None and h_px is not None and a_px is not None:
        spread = {"line": h_line, "home": h_px, "away": a_px}
    total = None
    t_line = _num(close(tot, "over").get("line")) if close(tot, "over").get("line") else None
    t_line = t_line if t_line is not None else _num(o.get("overUnder"))
    o_px, u_px = _american(close(tot, "over").get("odds")), _american(close(tot, "under").get("odds"))
    if t_line is not None and o_px is not None and u_px is not None:
        total = {"line": t_line, "over": o_px, "under": u_px}
    moneyline = None
    mh, ma = _american(close(ml, "home").get("odds")), _american(close(ml, "away").get("odds"))
    if mh is not None and ma is not None:
        moneyline = {"home": mh, "away": ma}
    if spread is None and total is None and moneyline is None:
        return None
    return {"provider": (o.get("provider") or {}).get("name"), "spread": spread, "total": total, "ml": moneyline}


def parse_event(ev: dict) -> dict | None:
    """A scoreboard event -> the contest's game dict, or None when it has no home / away pair."""
    comps = ev.get("competitions") or [{}]
    comp = comps[0]
    side = {c.get("homeAway"): c for c in comp.get("competitors") or []}
    if "home" not in side or "away" not in side:
        return None
    status = status_of(ev)
    try:
        commence = datetime.fromisoformat(str(ev.get("date")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if commence.tzinfo is None:
        commence = commence.replace(tzinfo=timezone.utc)
    notes = comp.get("notes") or ev.get("notes") or []
    headline = (notes[0].get("headline") if notes else None) or None
    stype = (ev.get("status") or {}).get("type") or {}
    out = {
        "espn_id": str(ev["id"]), "commence": commence, "status": status,
        "round": headline or ("Regular Season" if (ev.get("season") or {}).get("slug") == "regular-season" else "College Football"),
        "home": _team(side["home"]), "away": _team(side["away"]),
        "odds": parse_odds(comp) if status == "scheduled" else None,
        "neutral": bool(comp.get("neutralSite")),
    }
    hs, as_ = _num(side["home"].get("score")), _num(side["away"].get("score"))
    if status in ("live", "final") and hs is not None and as_ is not None:
        out["home_score"], out["away_score"] = int(hs), int(as_)
        state = {"detail": stype.get("detail") or stype.get("description") or ("Final" if status == "final" else "In play")}
        sit = comp.get("situation") or {}
        if status == "live" and sit:
            poss = str(sit.get("possession", ""))
            state["possession"] = "home" if poss and poss == str(side["home"]["team"].get("id")) else "away" if poss and poss == str(side["away"]["team"].get("id")) else None
            dd = sit.get("downDistanceText") or sit.get("shortDownDistanceText")
            if dd:
                state["down_distance"] = str(dd)
            if sit.get("isRedZone"):
                state["red_zone"] = True
        out["state"] = state
    return out
