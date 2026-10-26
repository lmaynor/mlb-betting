"""
ev.odds_board -- the full per-book price matrix behind the /ev/odds screen (NFL / NHL / NBA / MLB player props).

Every other prop surface exports one price (the best) or only the books of a FLAGGED outlier (`all_books`). The screen
needs every real book's two-sided price for every posted line, +EV or not, so this module keeps what
`ev.props.build_consensus` already computes and then throws away: the per-(game, player, stat, point) list of
`{book, over, under}` quotes from the same unioned ParlayAPI + BettingPros events the projection board reads.

No book-consensus fair value is computed here. The reference books and the de-vig method are the viewer's choice on the
screen, so the payload carries raw American prices only. One-sided quotes (an anytime-TD "Yes" ladder, a BettingPros alt
rung) are kept with `under: null`.

Alternate lines are kept too, tagged so the screen can tell them apart: `is_alt` (the line is off the player's modal line
by >= 1.0, or every quote at it came from an alternate / one-sided ladder market), `modal_point` (the line most books
price two-sided) and `curve_fair_over`, a per-player fair P(over) read off a monotone curve through the player's own
multi-book rungs (see `fit_curve`). The curve is the only fair-looking number in the payload and it is a fallback for
lines too thinly quoted to de-vig; it is unvalidated, like every row here.

Written to EVSweep/{sport}_props/odds_board_latest.json by ev.daily_projections.run_sport (the `daily-projections` job),
served by mlb/runners/public_api.py::get_odds_board at GET /api/public/odds/board. Never computed on a web request.
"""

from __future__ import annotations

import json
import logging
import time
import math
from datetime import datetime, timezone
from statistics import median

from ev.props import DFS_BOOKS, OFFSHORE_BOOKS, POOL_SD_FRAC, parse_event_props, shift_p_over
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_two_way

log = logging.getLogger("ev.odds_board")

SPORTS = ("nfl", "nhl", "nba", "mlb")
MLB_RAW_PREFIX = "OddsAccum/baseball_mlb/raw"   # props_{HHMM}.json: a list of ParlayAPI single-event odds objects, banked by the snapshot job
OUT_KEY = "EVSweep/{sport}_props/odds_board_latest.json"
MAX_ROWS = 3000                      # main lines first, then actionable alts; nearest game first within a tier
MIN_BOOKS = 2                        # a one-book line is not a comparison (an actionable alt rung is the exception)
ALT_GAP = 1.0                        # a line this far from the player's modal line is an alt line
MIN_ALT_EDGE = 0.015                 # raw edge vs the curve that keeps a single-book alt rung under the cap
NEAR_ARB = -0.02                     # cross-book margin (1 - sum of inverse decimals) above which an alt rung is kept
CURVE_MIN_BOOKS = 2                  # two-sided books behind a rung for it to anchor the curve
CURVE_SINGLE_MIN_BOOKS = 3           # ... or behind the lone anchor when the normal tail model is used (yardage stats)
CURVE_SINGLE_MAX_SD = 1.0            # the single-anchor tail model is not extended past this many sd from the anchor
CURVE_LO, CURVE_HI = 0.01, 0.99
CACHE_TTL_SEC = 5 * 60               # the job rewrites the file every 30 min
STALE_MINUTES = 90

_CACHE: dict = {}                    # sport -> (time.monotonic() loaded, payload)


def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1.0 - p))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _pav_non_increasing(ys: list[float], ws: list[float]) -> list[float]:
    """Weighted pool-adjacent-violators: the closest non-increasing sequence to `ys` (points are in ascending order)."""
    blocks: list[list[float]] = []                       # [mean, weight, count]
    for y, w in zip(ys, ws):
        blocks.append([y, w, 1])
        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
            m2, w2, c2 = blocks.pop()
            m1, w1, c1 = blocks.pop()
            blocks.append([(m1 * w1 + m2 * w2) / (w1 + w2), w1 + w2, c1 + c2])
    out: list[float] = []
    for m, _, c in blocks:
        out.extend([m] * int(c))
    return out


def fit_curve(anchors: list, stat: str):
    """A monotone fair-P(over) curve over the line, from one player's rungs. `anchors` = [(point, median de-vigged
    P(over), n_two_sided_books)], every rung already backed by >= CURVE_MIN_BOOKS books, or a single well-covered line.

    >= 2 anchors: logit-linear between neighbours after a weighted monotone (non-increasing) fit, and past the end
    anchors along the end segment's slope for at most the anchors' own span (never further: a curve through two points 10
    yards apart says nothing about 80 yards out). A flat end segment falls back to the overall slope; a flat curve does
    not extrapolate.
    1 anchor (>= CURVE_SINGLE_MIN_BOOKS books, yardage stats only): the normal tail ev.props.shift_p_over uses for pooling
    (sd = POOL_SD_FRAC * line), not extended past CURVE_SINGLE_MAX_SD. An unvalidated prior; count stats get none.
    Returns f(point) -> P(over) in [CURVE_LO, CURVE_HI] rounded to 4 places, or None when the curve cannot speak there."""
    pts = sorted({a[0]: a for a in anchors}.values())
    if len(pts) >= 2:
        xs = [a[0] for a in pts]
        ys = _pav_non_increasing([_logit(a[1]) for a in pts], [float(a[2]) for a in pts])
        span = xs[-1] - xs[0]

        def end_slope(i: int, j: int) -> float | None:
            sl = (ys[j] - ys[i]) / (xs[j] - xs[i])
            if sl < -1e-9:
                return sl
            sl = (ys[-1] - ys[0]) / span
            return sl if sl < -1e-9 else None

        def f_multi(x: float):
            if x < xs[0] or x > xs[-1]:
                lo = x < xs[0]
                if (xs[0] - x if lo else x - xs[-1]) > span:
                    return None
                sl = end_slope(0, 1) if lo else end_slope(-2, -1)
                if sl is None:
                    return None
                z = ys[0] + sl * (x - xs[0]) if lo else ys[-1] + sl * (x - xs[-1])
            else:
                k = max(i for i in range(len(xs)) if xs[i] <= x)
                if k == len(xs) - 1:
                    z = ys[-1]
                else:
                    z = ys[k] + (ys[k + 1] - ys[k]) * (x - xs[k]) / (xs[k + 1] - xs[k])
            return round(min(max(_sigmoid(z), CURVE_LO), CURVE_HI), 4)
        return f_multi
    if len(pts) == 1 and pts[0][2] >= CURVE_SINGLE_MIN_BOOKS and stat in POOL_SD_FRAC:
        pt, p, _ = pts[0]
        sd = POOL_SD_FRAC[stat] * max(pt, 1.0)

        def f_single(x: float):
            if abs(x - pt) > CURVE_SINGLE_MAX_SD * sd:
                return None
            return round(min(max(shift_p_over(p, pt, x, sd), CURVE_LO), CURVE_HI), 4)
        return f_single
    return None


def _two_sided_fair(b: dict) -> float | None:
    if b["over"] is None or b["under"] is None:
        return None
    fair, _ = devig_two_way(american_to_implied_prob(b["over"]), american_to_implied_prob(b["under"]), "proportional")
    return float(fair)


def _best_decimals(books: list[dict]) -> tuple[list, list]:
    """Per side, (decimal, book) of every real price, best first."""
    over = sorted(((american_to_decimal(b["over"]), b["book"]) for b in books if b["over"]), reverse=True)
    under = sorted(((american_to_decimal(b["under"]), b["book"]) for b in books if b["under"]), reverse=True)
    return over, under


def _arb_margin(books: list[dict]) -> float | None:
    """1 - (1/best over + 1/best under) across two DIFFERENT books (positive = a risk-free two-way); None without one."""
    over, under = _best_decimals(books)
    best = None
    for do, bo in over[:2]:
        for du, bu in under[:2]:
            if bo != bu:
                m = 1.0 - (1.0 / do + 1.0 / du)
                best = m if best is None else max(best, m)
    return best


def _actionable(row: dict) -> bool:
    """A thinly quoted alt rung worth keeping: a real price whose edge against the player's curve is >= MIN_ALT_EDGE, or a
    cross-book margin within NEAR_ARB of an arbitrage."""
    cf = row["curve_fair_over"]
    over, under = _best_decimals(row["books"])
    if cf is not None:
        if over and cf * over[0][0] - 1.0 >= MIN_ALT_EDGE:
            return True
        if under and (1.0 - cf) * under[0][0] - 1.0 >= MIN_ALT_EDGE:
            return True
    m = _arb_margin(row["books"])
    return m is not None and m > NEAR_ARB


def _prefer(new: dict, old: dict) -> bool:
    """A book quoting one line from two markets: keep the two-sided, then the main-market, quote."""
    sides = lambda b: (b["over"] is not None) + (b["under"] is not None)               # noqa: E731
    return (sides(new), not new["alt"]) > (sides(old), not old["alt"])


def build_board(sport: str, events: list | None, now: datetime, meta: dict | None = None) -> dict:
    """Banked events -> the odds-board payload for one sport (rows sorted nearest game first, then player, then stat).

    Main lines and alternate rungs are both kept (see the module docstring for `is_alt` / `modal_point` /
    `curve_fair_over`); DFS / pick'em apps are skipped (they carry the number, not a price), and a game that has started
    is dropped. Under the MAX_ROWS cap main lines come first (nearest game first), then alt rungs that are quoted two-sided
    by >= 2 books or are actionable against the curve, then the remaining multi-book alt rungs."""
    meta = meta or {}
    # imported here, not at module top: ev.daily_projections imports this module lazily from run_sport
    from ev.daily_projections import canon_team as _canon_team, game_key_of

    def canon_team(sp, x):
        # ev.daily_projections.canon_team knows nfl / nhl / nba; MLB names resolve through the snapshot job's own resolver
        if sp == "mlb":
            from mlb_core.odds.dk_scraper import resolve_team
            return resolve_team(str(x)) if x else None
        return _canon_team(sp, x)

    rows: dict = {}
    n_events = n_started = 0
    for ev in events or []:
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            n_started += 1
            continue
        n_events += 1
        away, home = canon_team(sport, ev.get("away_team")), canon_team(sport, ev.get("home_team"))
        gk = game_key_of(sport, start, away, home)
        quotes, _ = parse_event_props(ev, sport)
        for q in quotes:
            if q.book in DFS_BOOKS or (q.over is None and q.under is None):
                continue
            key = f"{gk}|{q.player_norm}|{q.stat}|{q.point}"
            row = rows.get(key)
            if row is None:
                row = rows[key] = {"key": key, "game_key": gk, "commence": start.astimezone(timezone.utc).isoformat(),
                                   "away": away, "home": home, "player": q.player, "player_norm": q.player_norm,
                                   "player_id": q.player_id, "team": q.team_hint, "stat": q.stat, "line": q.point,
                                   "books": {}}
            new = {"book": q.book, "over": q.over, "under": q.under, "alt": bool(q.alt)}
            old = row["books"].get(q.book)
            if old is None or _prefer(new, old):
                row["books"][q.book] = new

    # ---- per (game, player, stat): the modal line, then is_alt and the curve for every line of that player
    groups: dict = {}
    for row in rows.values():
        books = sorted(row["books"].values(), key=lambda b: b["book"])
        row["alt_src"] = all(b["alt"] for b in books)
        row["one_sided"] = all(b["over"] is None or b["under"] is None for b in books)
        two = [f for f in (_two_sided_fair(b) for b in books) if f is not None]
        row["n_two"], row["med"] = len(two), (median(two) if two else None)
        row["books"] = [{k: v for k, v in b.items() if k != "alt"} for b in books]
        groups.setdefault((row["game_key"], row["player_norm"], row["stat"]), []).append(row)

    cands = []
    for (_, _, stat), grp in groups.items():
        def modal_of(rs):
            rs = [r for r in rs if r["n_two"] >= 1]
            return min(rs, key=lambda r: (-r["n_two"], abs(r["med"] - 0.5), r["line"]))["line"] if rs else None
        modal = modal_of([r for r in grp if not r["alt_src"]])
        if modal is None:
            modal = modal_of(grp)
        modal_row = next((r for r in grp if r["line"] == modal), None)
        anchors = [(r["line"], r["med"], r["n_two"]) for r in grp if r["n_two"] >= CURVE_MIN_BOOKS]
        if not anchors and modal_row is not None and modal_row["n_two"] >= CURVE_SINGLE_MIN_BOOKS:
            anchors = [(modal_row["line"], modal_row["med"], modal_row["n_two"])]
        curve = fit_curve(anchors, stat) if anchors else None
        for r in grp:
            if modal is None:
                is_alt = r["alt_src"]
            elif r["line"] == modal:
                is_alt = False
            else:
                is_alt = abs(r["line"] - modal) >= ALT_GAP or r["alt_src"] or r["one_sided"]
            r["is_alt"], r["modal_point"] = bool(is_alt), modal
            r["curve_fair_over"] = curve(r["line"]) if curve else None
            r["curve_n"] = len({a[0] for a in anchors}) if curve else 0
            n_books = len(r["books"])
            if not is_alt:
                tier = 1 if n_books >= MIN_BOOKS else None
            elif n_books >= MIN_BOOKS:
                tier = 2 if (r["n_two"] >= 2 or _actionable(r)) else 3
            else:
                tier = 2 if _actionable(r) else None
            if tier is not None:
                cands.append((tier, r))

    cands.sort(key=lambda t: (t[0], t[1]["commence"], t[1]["player_norm"], t[1]["stat"], t[1]["line"]))
    cut = max(len(cands) - MAX_ROWS, 0)
    kept = [r for _, r in cands[:MAX_ROWS]]
    kept.sort(key=lambda r: (r["commence"], r["player_norm"], r["stat"], r["line"]))
    out = [{k: v for k, v in r.items() if k not in ("alt_src", "one_sided", "n_two", "med")} for r in kept]
    book_keys = sorted({b["book"] for r in out for b in r["books"]})
    return {
        "sport": sport, "ts": now.isoformat(), "validated": False, "count": len(out), "rows": out,
        "books": book_keys, "offshore_books": sorted(b for b in book_keys if b in OFFSHORE_BOOKS),
        "stats": sorted({r["stat"] for r in out}),
        "games": [{"game_key": g[0], "start": g[1], "away": g[2], "home": g[3]}
                  for g in sorted({(r["game_key"], r["commence"], r["away"], r["home"]) for r in out},
                                  key=lambda g: (g[1], g[0]))],
        "status": "live" if out else "idle",
        "lines_age_minutes": meta.get("age_min"), "lines_source": list(meta.get("lines_source") or []),
        "summary": {"events": n_events, "events_started": n_started, "rows_cut": cut,
                    "alt_rows": sum(1 for r in out if r["is_alt"]),
                    "curve_rows": sum(1 for r in out if r["curve_fair_over"] is not None)},
    }


def write_board(payload: dict, sport: str) -> None:
    """Best-effort write of one sport's board: a failed write never fails the projections compute."""
    if payload.get("status") == "unavailable":
        return
    try:
        from mlb_core import storage
        storage.write_bytes(json.dumps(payload, default=str).encode(), OUT_KEY.format(sport=sport))
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("odds board write failed for %s: %s", sport, exc)


def load_mlb_events(now: datetime) -> tuple[list | None, dict]:
    """The newest banked MLB prop snapshot for today and tomorrow (Eastern) -> (events | None, meta).

    MLB is not part of the 30-minute projections compute, so its events come from the odds snapshot job's own bank
    (OddsAccum/baseball_mlb/raw/{date}/props_{HHMM}.json, ~8 pulls a day, no extra credits). Two dates are read because the
    evening pulls also bank tomorrow's slate; within a date the newest file wins, and an event id seen twice keeps the newer."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from mlb_core import storage
    et = ZoneInfo("America/New_York")
    events: dict = {}
    newest_key, newest_stamp = None, ""
    for d in sorted({(now + timedelta(days=n)).astimezone(et).date().isoformat() for n in (0, 1)}):
        files = [k for k in storage.list_keys(f"{MLB_RAW_PREFIX}/{d}/") if k.rsplit("/", 1)[-1].startswith("props_")]
        if not files:
            continue
        key = max(files, key=lambda k: k.rsplit("/", 1)[-1])
        try:
            batch = json.loads(storage.read_bytes(key))
        except Exception:                                                              # noqa: BLE001
            continue
        for obj in batch if isinstance(batch, list) else []:
            if isinstance(obj, dict):
                events[obj.get("id") or f"{key}:{len(events)}"] = obj
        if d + key.rsplit("_", 1)[-1] > newest_stamp:
            newest_stamp, newest_key = d + key.rsplit("_", 1)[-1], key
    if not events:
        return None, {"reason": "no MLB prop snapshot banked for today/tomorrow"}
    age = None
    try:
        st = storage.stat(newest_key) or {}
        age = (now - st["mtime_utc"]).total_seconds() / 60.0 if st.get("mtime_utc") else None
    except Exception:                                                                  # noqa: BLE001
        age = None
    return list(events.values()), {"age_min": None if age is None else round(age, 1), "lines_source": ["parlayapi"],
                                    "banked_events": len(events)}


def run_mlb(now: datetime | None = None, write: bool = True) -> dict:
    """Build (and by default write) the MLB board from the banked snapshot. Best-effort like the other sports' boards:
    no snapshot -> an 'unavailable' payload that write_board skips."""
    now = now or datetime.now(timezone.utc)
    try:
        events, meta = load_mlb_events(now)
    except Exception as exc:                                                           # noqa: BLE001
        log.warning("mlb lines unavailable: %s", exc)
        events, meta = None, {"reason": str(exc)[:200]}
    if not events:
        return {"sport": "mlb", "ts": now.isoformat(), "validated": False, "status": "unavailable", "rows": [], "count": 0,
                "summary": meta}
    payload = build_board("mlb", events, now, meta)
    if write:
        write_board(payload, "mlb")
    return payload


def read_board(sport: str) -> dict | None:
    """The stored board for one sport, or None when it has not been produced yet. Process-cached for CACHE_TTL_SEC."""
    hit = _CACHE.get(sport)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SEC:
        return hit[1]
    from mlb_core import storage
    try:
        payload = json.loads(storage.read_bytes(OUT_KEY.format(sport=sport)))
    except Exception:                                                                  # noqa: BLE001
        return None
    _CACHE[sport] = (time.monotonic(), payload)
    return payload


def board_age_min(payload: dict | None, now: datetime | None = None) -> float | None:
    try:
        return ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(str(payload.get("ts")))).total_seconds() / 60.0
    except Exception:                                                                  # noqa: BLE001
        return None
