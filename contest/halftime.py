"""The halftime window: a short stretch, while ESPN says "Halftime", when straight bets are taken on the full game at Kalshi-derived prices.

Why it is defensible: at halftime the ball is dead, so the "bet after the dramatic play, before the line moves" exploit largely vanishes.
What remains is feed lag and stale quotes, so the window is conservative and closes at the first doubt. It ships OFF (`rules.halftime_enabled`
plus the CONTEST_HALFTIME_ENABLED environment switch); the evidence so far is one liquid basketball halftime, which is encouraging, not proof.

THE WINDOW (one row per game in `contest_halftime`, a state machine driven by `advance_window`):
  * watching: ESPN reports halftime. Opens only when the score is unchanged across TWO polls AND a quote fetched AFTER halftime was first
    seen exists, so the price post-dates the last play;
  * open: straights only, at the current quote, at most `halftime_max_stake_bp` of the balance; the bet is HIDDEN from other players until
    the window closes;
  * closed (never reopens for that game): ESPN left halftime, the score changed, the newest quote is older than `halftime_quote_max_age_s`,
    or `halftime_window_cap_s` elapsed.

PRICING (Kalshi only: BettingPros has no live lines): the mid of bid and ask is the probability, a fixed contest margin goes on top, and a
market is offered only when its bid-ask width is at most `MAX_WIDTH`. Spread and total use the rung whose mid is closest to 50%. Expect a
moneyline and a football spread on liquid games only, not a full menu. Verified against live payloads 2026-11-17: the football total series
is KXNCAAFTOTAL (not KXNCAFTOTAL, which is empty), a total is titled "Full Game: Over X points scored" with `floor_strike` X, a spread "<Team>
wins by over X points" with `floor_strike` X, a moneyline "<Team> wins"; every series carries many games, so a game's markets are selected by
its EVENT TICKER (the suffix after the series name), found from the team names on its moneyline and spread titles.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Callable, Mapping

from sqlalchemy import text

from contest import store
from contest.altlines import prob_to_american
from contest.rules import ContestRules
from contest.slips import Quote

MAX_WIDTH = 0.06
# a value may list several tickers: Kalshi has used both KXNCAAFTOTAL and KXNCAFTOTAL for football totals (the second is empty today), so both are read
SERIES = {"ncaaf": {"game": "KXNCAAFGAME", "spread": "KXNCAAFSPREAD", "total": ("KXNCAAFTOTAL", "KXNCAFTOTAL")},
          "ncaab": {"game": "KXNCAAMBGAME", "spread": "KXNCAAMBSPREAD", "total": "KXNCAAMBTOTAL"}}
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets"
_WINS_BY = re.compile(r"^(.*) wins by over ([0-9.]+) points?$")


def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ").replace("'", "").replace(".", " ")
    s = re.sub(r"\bst\b", "state", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _mid(m: dict) -> float | None:
    """The market's mid probability, or None when it is too wide or too lopsided to trust."""
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if bid is None or ask is None or ask < bid or ask - bid > MAX_WIDTH + 1e-9:
        return None
    mid = (bid + ask) / 2
    return mid if 0.03 <= mid <= 0.97 else None


def _price(p: float, margin_bp: int) -> int | None:
    implied = p * (1.0 + margin_bp / 10_000.0)
    return None if implied >= 0.985 or implied <= 0.015 else prob_to_american(implied)


def _teams_match(name: str, names: set[str]) -> bool:
    n = norm(name)
    return any(n == x or n in x or x in n for x in names if x)


def _event_key(mk: dict) -> str:
    return str(mk.get("event_ticker") or "").split("-", 1)[-1]


def _for_this_game(home_names: set[str], away_names: set[str], markets: Mapping[str, list[dict]]) -> dict[str, list[dict]] | None:
    """Keep only the markets of ONE Kalshi event: the one whose moneyline / spread titles name both teams. A series lists every game of the
    day, and totals carry no team name, so the event ticker is the only thing tying them to this game. No match or several -> None (fail closed)."""
    keys: set[str] = set()
    for series in ("game", "spread"):
        for mk in markets.get(series, []):
            title = str(mk.get("title") or "")
            mm = _WINS_BY.match(title)
            team = mm.group(1) if mm else title[:-5] if title.endswith(" wins") else None
            if team and (_teams_match(team, home_names) or _teams_match(team, away_names)):
                keys.add(_event_key(mk))
    if len(keys) != 1:
        return None
    key = next(iter(keys))
    return {s: [mk for mk in ms if _event_key(mk) == key] for s, ms in markets.items()}


def game_quotes(home_names: set[str], away_names: set[str], markets: Mapping[str, list[dict]], rules: ContestRules, fetched_at: datetime) -> dict[tuple, Quote]:
    """{(market, side): Quote} for ONE game from Kalshi markets of the sport's three series. `home_names` / `away_names` are normalised
    spellings of each team (ESPN's location, short and display names). Whatever is not liquid enough is simply absent."""
    out: dict[tuple, Quote] = {}
    m = rules.halftime_margin_bp
    markets = _for_this_game(home_names, away_names, markets)
    if markets is None:
        return out
    # moneyline: "<Team> wins"
    ml: dict[str, float] = {}
    for mk in markets.get("game", []):
        title = str(mk.get("title") or "")
        p = _mid(mk)
        if title.endswith(" wins") and p is not None:
            for side, names in (("home", home_names), ("away", away_names)):
                if _teams_match(title[:-5], names):
                    ml[side] = p
    if len(ml) == 2 and abs(ml["home"] + ml["away"] - 1.0) <= 0.08:
        ph, pa = _price(ml["home"], m), _price(ml["away"], m)
        if ph is not None and pa is not None:
            out[("ml", "home")], out[("ml", "away")] = Quote(None, ph, fetched_at), Quote(None, pa, fetched_at)
    # spread: ladders of "<Team> wins by over X points"; the rung nearest 50% is the main line
    best: tuple[float, str, float, float] | None = None          # (distance from 0.5, team side, X, P(team margin > X))
    for mk in markets.get("spread", []):
        mm = _WINS_BY.match(str(mk.get("title") or ""))
        p, x = _mid(mk), _f(mk.get("floor_strike"))
        if not mm or p is None or x is None:
            continue
        side = "home" if _teams_match(mm.group(1), home_names) else "away" if _teams_match(mm.group(1), away_names) else None
        if side and (best is None or abs(p - 0.5) < best[0]):
            best = (abs(p - 0.5), side, x, p)
    if best:
        _, side, x, p = best
        # P(side margin > x): the side covers a handicap of -x. The other side covers +x with probability 1 - p.
        home_line = -x if side == "home" else x
        p_home = p if side == "home" else 1.0 - p
        ph, pa = _price(p_home, m), _price(1.0 - p_home, m)
        if ph is not None and pa is not None:
            out[("spread", "home")] = Quote(home_line, ph, fetched_at)
            out[("spread", "away")] = Quote(-home_line, pa, fetched_at)
    # total: rungs with floor_strike X, P(total > X)
    tb: tuple[float, float, float] | None = None
    for mk in markets.get("total", []):
        p, x = _mid(mk), _f(mk.get("floor_strike"))
        title = str(mk.get("title") or "").lower()
        if "points scored" not in title or "half" in title or "quarter" in title:          # full-game totals only
            continue
        if p is not None and x is not None and (tb is None or abs(p - 0.5) < tb[0]):
            tb = (abs(p - 0.5), x, p)
    if tb:
        po, pu = _price(tb[2], m), _price(1.0 - tb[2], m)
        if po is not None and pu is not None:
            out[("total", "over")], out[("total", "under")] = Quote(tb[1], po, fetched_at), Quote(tb[1], pu, fetched_at)
    return out


# ------------------------------------------------------------------------------------------------------------ the window

def window_row(engine, game_id: int) -> dict | None:
    with engine.connect() as c:
        r = c.execute(text("SELECT state, first_seen, opened_at, closed_at, close_reason, home_score, away_score, polls FROM contest_halftime WHERE game_id = :g"), {"g": game_id}).first()
    return None if r is None else dict(zip(("state", "first_seen", "opened_at", "closed_at", "close_reason", "home", "away", "polls"), r))


def advance_window(engine, game_id: int, in_halftime: bool, score: tuple[int, int] | None, quote_at: datetime | None, now: datetime,
                   rules: ContestRules) -> str:
    """One poll of one game. Returns the state afterwards: none | watching | open | closed."""
    row = window_row(engine, game_id)
    t = store.iso(now)
    with engine.begin() as c:
        if row is None:
            if not in_halftime:
                return "none"
            c.execute(text("INSERT INTO contest_halftime (game_id, state, first_seen, home_score, away_score, polls) VALUES (:g, 'watching', :t, :h, :a, 1)"),
                      {"g": game_id, "t": t, "h": score[0] if score else None, "a": score[1] if score else None})
            return "watching"
        if row["state"] == "closed":
            return "closed"
        def close(reason: str) -> str:
            c.execute(text("UPDATE contest_halftime SET state = 'closed', closed_at = :t, close_reason = :r WHERE game_id = :g"), {"t": t, "r": reason, "g": game_id})
            return "closed"
        if not in_halftime:
            return close("left_halftime") if row["state"] == "open" else _reset(c, game_id)
        same = score is not None and (row["home"], row["away"]) == score
        if row["state"] == "open":
            if not same:
                return close("score_changed")
            if quote_at is None or (now - quote_at).total_seconds() > rules.halftime_quote_max_age_s:
                return close("stale_quote")
            if (now - store.parse_ts(row["opened_at"])).total_seconds() > rules.halftime_window_cap_s:
                return close("cap")
            return "open"
        # watching
        if not same:
            c.execute(text("UPDATE contest_halftime SET first_seen = :t, home_score = :h, away_score = :a, polls = 1 WHERE game_id = :g"),
                      {"t": t, "h": score[0] if score else None, "a": score[1] if score else None, "g": game_id})
            return "watching"
        polls = int(row["polls"]) + 1
        c.execute(text("UPDATE contest_halftime SET polls = :p WHERE game_id = :g"), {"p": polls, "g": game_id})
        if polls >= 2 and quote_at is not None and quote_at > store.parse_ts(row["first_seen"]) and (now - quote_at).total_seconds() <= rules.halftime_quote_max_age_s:
            c.execute(text("UPDATE contest_halftime SET state = 'open', opened_at = :t WHERE game_id = :g"), {"t": t, "g": game_id})
            return "open"
        return "watching"


def _reset(c, game_id: int) -> str:
    c.execute(text("DELETE FROM contest_halftime WHERE game_id = :g AND state = 'watching'"), {"g": game_id})
    return "none"


def open_games(engine, contest_id: int) -> set[int]:
    with engine.connect() as c:
        rows = c.execute(text("""SELECT h.game_id FROM contest_halftime h JOIN contest_game g ON g.id = h.game_id
                                 WHERE g.contest_id = :c AND h.state = 'open'"""), {"c": contest_id}).all()
    return {int(r[0]) for r in rows}


# ------------------------------------------------------------------------------------------------------------ one pass

def fetch_kalshi(sport: str, http: Callable) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key, series_list in SERIES[sport].items():
        ms: list[dict] = []
        for series in (series_list,) if isinstance(series_list, str) else series_list:
            cursor = None
            while True:
                d = http(KALSHI, {"series_ticker": series, "status": "open", "limit": 1000, **({"cursor": cursor} if cursor else {})})
                ms += d.get("markets") or []
                cursor = d.get("cursor")
                if not cursor:
                    break
        out[key] = ms
    return out


def halftime_pass(engine, contest_id: int, events: list[dict], kalshi: Mapping[str, list[dict]] | None, now: datetime) -> dict:
    """Apply one poll: for every contest game ESPN lists, write halftime quotes while it is at halftime and move its window. `kalshi` is None
    when no game is at halftime (nothing was fetched). Returns {game_id: state}."""
    c = store.contest_by_slug_id(engine, contest_id)
    rules: ContestRules = c["rules"]
    states: dict[int, str] = {}
    if not rules.halftime_enabled:
        return states
    by_espn = {g["espn_event_id"]: g for g in store.games_of(engine, contest_id)}
    for ev in events:
        g = by_espn.get(ev["espn_id"])
        if g is None or g["status"] != "scheduled":
            continue
        in_half = bool(ev.get("halftime")) and "home_score" in ev
        quote_at = None
        if in_half and kalshi is not None:
            names = lambda team: {norm(x) for x in (team.get("name"), team.get("full"), team.get("abbr")) if x}
            qs = game_quotes(names(ev["home"]), names(ev["away"]), kalshi, rules, now)
            if qs:
                store.write_quotes(engine, g["id"], qs, now, source="kalshi-halftime")
                quote_at = now
        if in_half and quote_at is None:                       # no fresh quote this poll: the newest stored one decides
            with engine.connect() as cn:
                r = cn.execute(text("SELECT MAX(fetched_at) FROM contest_quote WHERE game_id = :g AND source = 'kalshi-halftime'"), {"g": g["id"]}).scalar()
            quote_at = store.parse_ts(r) if r else None
        score = (ev["home_score"], ev["away_score"]) if "home_score" in ev else None
        states[g["id"]] = advance_window(engine, g["id"], in_half, score, quote_at, now, rules)
    return states
