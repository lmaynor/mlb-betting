"""
Read-only query helpers for the public API endpoints in main.py.
All queries use text() wrapper -- required for pg8000 named params (CONTEXT.md ss8).
"""
import logging
from datetime import date as _date, timedelta
from zoneinfo import ZoneInfo as _ZoneInfo
from sqlalchemy import text


def _ct_today() -> str:
    """Return today's date in US/Central as an isoformat string."""
    from datetime import datetime
    return datetime.now(_ZoneInfo("America/Chicago")).date().isoformat()


logger = logging.getLogger(__name__)


def _require_api_key(request_headers: dict, site_api_key: str) -> bool:
    """Return True if the X-API-Key header matches the configured key."""
    # HTTP/2 lowercases headers -- check both forms
    key = request_headers.get("X-API-Key", "") or request_headers.get("x-api-key", "")
    return key.strip() == site_api_key


def get_today_picks(engine, include_all: bool = False):
    """Today's picks. include_all=True returns EVERY scored prediction
    (kelly_triggered false rows have stake 0) -- The Edge cockpit uses this
    so every player with a model probability is browsable, not just the
    qualified card."""
    gate = "" if include_all else "AND kelly_triggered = true "
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at, "
            # Added 2026-08-17 (finding C6.6): the historical fix that added
            # closing_odds/clv_pct/morning_odds/line_move_pct covered
            # get_picks and get_clv_data below but missed this third query
            # function -- the endpoint backing The Edge cockpit itself.
            "closing_odds, clv_pct, morning_odds, line_move_pct "
            "FROM bets "
            f"WHERE game_date = :_today {gate}"
            "ORDER BY system, created_at DESC"
        ), {"_today": _ct_today()}).mappings().all()
    return [dict(r) for r in rows]


def get_picks(engine, system=None, date=None, status=None, limit=50, offset=0, book=None):
    conditions = ["kelly_triggered = true"]
    params = {}

    if system:
        conditions.append("system = :system")
        params["system"] = system
    if book:
        conditions.append("book = :book")
        params["book"] = book

    if date == "today":
        params["_today"] = _ct_today()
        conditions.append("game_date = :_today")
    elif date == "yesterday":
        params["_yesterday"] = (_date.fromisoformat(_ct_today()) - timedelta(days=1)).isoformat()
        conditions.append("game_date = :_yesterday")
    elif date == "last7":
        params["_last7"] = (_date.fromisoformat(_ct_today()) - timedelta(days=7)).isoformat()
        conditions.append("game_date >= :_last7")
    elif date:
        conditions.append("game_date = :game_date")
        params["game_date"] = date

    if status == "pending":
        conditions.append("result IS NULL")
    elif status == "settled":
        conditions.append("result IS NOT NULL")
    elif status == "won":
        conditions.append("result = 'win'")
    elif status == "lost":
        conditions.append("result = 'loss'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    # Capped 2026-08-17 (finding C6.14): this is the one endpoint hit on
    # every public page load and had no upper bound on caller-supplied
    # limit -- get_clv_data below already hardcodes LIMIT 2000, so the
    # pattern was known but never applied here.
    # Capped 2026-08-17 (finding C6.14): this is the one endpoint hit on
    # every public page load and had no upper bound on caller-supplied
    # limit -- get_clv_data below already hardcodes LIMIT 2000, so the
    # pattern was known but never applied here.
    params["limit"] = min(int(limit), 200)
    params["offset"] = int(offset)

    sql = text(
        "SELECT id, system, game_date, game_pk, bet_type, player, "
        "away_team, home_team, odds, stake, model_prob, market_prob, "
        "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at, book, "
        "closing_odds, clv_pct, morning_odds, line_move_pct "
        f"FROM bets {where} "
        "ORDER BY game_date DESC, created_at DESC "
        "LIMIT :limit OFFSET :offset"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def get_recent_settled(engine, limit=20):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at, book, "
            "closing_odds, clv_pct, morning_odds, line_move_pct "
            "FROM bets "
            "WHERE result IS NOT NULL AND kelly_triggered = true AND stake > 0 "
            "ORDER BY game_date DESC, created_at DESC "
            "LIMIT :limit"
        ), {"limit": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


def get_pnl_sparkline(engine, days=30, system=None):
    """Daily cumulative P&L for the last N days, kelly_triggered=true only.
    Optionally scoped to a single system (e.g. "NRFI"); when omitted, covers
    all systems. Returns list of {date, daily_pnl, cum_pnl} dicts sorted
    ascending by date.
    """
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    conditions = [
        "result IS NOT NULL",
        "kelly_triggered = true",
        "stake > 0",
        "game_date >= :cutoff",
    ]
    params: dict = {"cutoff": cutoff}
    if system and str(system).strip():
        conditions.append("system = :system")
        params["system"] = str(system).strip().upper()
    where = "WHERE " + " AND ".join(conditions)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT game_date, COALESCE(SUM(profit), 0) AS daily_pnl "
            "FROM bets "
            f"{where} "
            "GROUP BY game_date "
            "ORDER BY game_date ASC"
        ), params).mappings().all()
    cum = 0.0
    out = []
    for r in rows:
        cum += float(r["daily_pnl"])
        out.append({
            "date":      r["game_date"],
            "daily_pnl": round(float(r["daily_pnl"]), 2),
            "cum_pnl":   round(cum, 2),
        })
    return out


def get_clv_data(engine, days=90, systems=None):
    """
    Returns settled, kelly-triggered bets with closing line data for CLV scatter tool.
    model_edge_pct: edge column (decimal) * 100 -> percentage like 8.2
    clv_pct: price-based CLV, percentage like 5.21 = (decimal_entry/decimal_close - 1)*100.
        Positive => we beat the close (got a better price).
    Only returns bets where closing_odds IS NOT NULL and clv_pct IS NOT NULL.
    """
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=int(days))).isoformat()

    conditions = [
        "kelly_triggered = true",
        "result IS NOT NULL",
        "result != 'void'",
        "closing_odds IS NOT NULL",
        "clv_pct IS NOT NULL",
        "game_date >= :cutoff",
    ]
    params: dict = {"cutoff": cutoff}

    if systems:
        sys_list = [s.strip().upper() for s in str(systems).split(",") if s.strip()]
        if sys_list:
            placeholders = ", ".join(f":s{i}" for i in range(len(sys_list)))
            conditions.append(f"system IN ({placeholders})")
            for i, s in enumerate(sys_list):
                params[f"s{i}"] = s

    where = "WHERE " + " AND ".join(conditions)
    sql = text(
        "SELECT system, game_date, bet_type, player, away_team, home_team, "
        "ROUND((COALESCE(edge, 0) * 100)::numeric, 2) AS model_edge_pct, "
        "ROUND(clv_pct::numeric, 2) AS clv_pct, "
        "result, odds AS opening_odds, closing_odds "
        f"FROM bets {where} "
        "ORDER BY game_date DESC "
        "LIMIT 2000"
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, params).mappings().all()
    return [dict(r) for r in rows]


def get_today_slate(engine):
    """
    Returns today's MLB slate with game metadata (teams, time, starters)
    and Beezy kelly-triggered picks grouped by game_pk.
    Uses MLB Stats API for schedule + DB for picks.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from mlb_core.data.lineups import get_today_schedule

    ct_now  = datetime.now(ZoneInfo("America/Chicago"))
    run_date = ct_now.date().isoformat()

    # Load today's schedule (game_pk, teams, time, probable pitchers)
    try:
        schedule = get_today_schedule(run_date)
    except Exception:
        schedule = None

    # Load today's picks from DB
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT game_pk, system, bet_type, "
            "ROUND((model_prob * 100)::numeric, 1) AS model_prob_pct, "
            "ROUND((market_prob * 100)::numeric, 1) AS market_prob_pct, "
            "ROUND((COALESCE(edge, 0) * 100)::numeric, 2) AS edge_pct, "
            "odds, result, notes, player, away_team, home_team "
            "FROM bets "
            "WHERE game_date = :today AND kelly_triggered = true "
            "ORDER BY system, edge DESC NULLS LAST"
        ), {"today": run_date}).mappings().all()

    picks_by_game: dict = {}
    for r in rows:
        gpk = r["game_pk"]
        if gpk not in picks_by_game:
            picks_by_game[gpk] = []
        picks_by_game[gpk].append(dict(r))

    # Format game time UTC -> ET
    def _fmt_time(utc_str: str) -> str | None:
        if not utc_str:
            return None
        try:
            dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            dt_et  = dt_utc.astimezone(ZoneInfo("America/New_York"))
            h, m   = dt_et.hour, dt_et.minute
            suffix = "AM" if h < 12 else "PM"
            h12    = h % 12 or 12
            return f"{h12}:{m:02d} {suffix} ET"
        except Exception:
            return None

    games = []
    if schedule is not None and not schedule.empty:
        for _, row in schedule.iterrows():
            gpk = int(row["game_pk"])
            games.append({
                "game_pk":      gpk,
                "away_team":    str(row.get("away_team") or ""),
                "home_team":    str(row.get("home_team") or ""),
                "start_time":   _fmt_time(str(row.get("game_time_utc") or "")),
                "away_pitcher": row.get("away_pitcher_name") or None,
                "home_pitcher": row.get("home_pitcher_name") or None,
                "picks":        picks_by_game.get(gpk, []),
            })
    else:
        # Fallback: build game list from picks only (no schedule data)
        seen: set = set()
        for gpk, picks in picks_by_game.items():
            if gpk in seen:
                continue
            seen.add(gpk)
            first = picks[0]
            games.append({
                "game_pk":      gpk,
                "away_team":    str(first.get("away_team") or ""),
                "home_team":    str(first.get("home_team") or ""),
                "start_time":   None,
                "away_pitcher": None,
                "home_pitcher": None,
                "picks":        picks,
            })

    # Sort: games with picks first, then by start_time
    games.sort(key=lambda g: (0 if g["picks"] else 1, g["start_time"] or "99:99 ZZ"))

    return {
        "games":       games,
        "run_date":    run_date,
        "as_of":       ct_now.isoformat(),
        "total_picks": sum(len(g["picks"]) for g in games),
        "total_games": len(games),
    }


def get_summary_stats(engine):
    with engine.connect() as conn:
        by_system = conn.execute(text(
            "SELECT system, "
            "COUNT(*) AS total_bets, "
            "COUNT(*) FILTER (WHERE result = 'win') AS wins, "
            "COUNT(*) FILTER (WHERE result = 'loss') AS losses, "
            "COUNT(*) FILTER (WHERE result = 'push') AS pushes, "
            "ROUND(COUNT(*) FILTER (WHERE result = 'win')::numeric / "
            "  NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0) * 100, 1) AS win_rate, "
            "ROUND((SUM(profit) / NULLIF(SUM(stake), 0) * 100)::numeric, 2) AS roi, "
            "ROUND(SUM(profit)::numeric, 2) AS total_pnl, "
            "ROUND((AVG(model_prob - market_prob) * 100)::numeric, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != 'void' "
            "GROUP BY system ORDER BY roi DESC"
        )).mappings().all()

        overall = conn.execute(text(
            "SELECT COUNT(*) AS total_bets, "
            "ROUND(COUNT(*) FILTER (WHERE result = 'win')::numeric / "
            "  NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0) * 100, 1) AS win_rate, "
            "ROUND((SUM(profit) / NULLIF(SUM(stake), 0) * 100)::numeric, 2) AS roi, "
            "ROUND((AVG(model_prob - market_prob) * 100)::numeric, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != 'void'"
        )).mappings().first()

    return {
        "overall":  dict(overall) if overall else {},
        "bySystem": [dict(r) for r in by_system],
    }


# ---------------------------------------------------------------------------
# +EV terminal feed + analytics deep dive (added for the beezy.fyi v2 overhaul)
# ---------------------------------------------------------------------------
import json as _json
import re as _re
from datetime import datetime as _datetime, timezone as _timezone

_NFL_PREFIX = "EVSweep/nfl_props"
_NFL_STALE_MINUTES = 180
_EV_BANDS = [("0-3%", 0.0, 0.03), ("3-5%", 0.03, 0.05), ("5-8%", 0.05, 0.08),
             ("8-12%", 0.08, 0.12), ("12%+", 0.12, None)]
_NBOOKS_RE = _re.compile(r"\((\d+) books\)")


def _edge_frac(e):
    """edge is stored as a fraction (0.08) on most rows and already-percent (8) on a few."""
    if e is None:
        return None
    e = float(e)
    return e / 100.0 if abs(e) >= 2 else e


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _strip_book_suffix(bet_type, book):
    bt = bet_type or ""
    tag = "_" + str(book).lower() if book else ""
    return bt[: len(bt) - len(tag)] if tag and bt.lower().endswith(tag) else bt


def _ev_mlb_rows(engine, min_ev, limit, days):
    since = (_date.fromisoformat(_ct_today()) - timedelta(days=int(days))).isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, game_date, game_pk, player, away_team, home_team, bet_type, "
            "model_prob, market_prob, edge, kelly_pct, odds, stake, book, result, profit, "
            "notes, created_at "
            "FROM bets WHERE system = 'EV' AND game_date >= :since AND edge >= :min_ev "
            "ORDER BY game_date DESC, edge DESC LIMIT :limit"
        ), {"since": since, "min_ev": float(min_ev), "limit": int(limit)}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        notes = d.get("notes") or ""
        m = _NBOOKS_RE.search(notes)
        d["sport"] = "mlb"
        d["n_books"] = int(m.group(1)) if m else None
        d["anchored"] = "Pinnacle" in notes
        d["edge"] = _edge_frac(d.get("edge"))
        d["bet_type"] = _strip_book_suffix(d.get("bet_type"), d.get("book"))
        out.append(d)
    return out


def _nfl_feed(min_ev, limit):
    """Shadow NFL prop sweep (ev/sweep_nfl_props.py -> latest.json). Evidence only: the sweep's own
    contract says these signals are unvalidated, so the payload carries status + freshness."""
    from mlb_core import storage as _st
    empty = {"rows": [], "count": 0, "status": "unavailable", "as_of": None, "age_minutes": None,
             "stale": True, "summary": {}, "validated": False}
    try:
        snap = _json.loads(_st.read_bytes(_NFL_PREFIX + "/latest.json"))
    except Exception:  # noqa: BLE001 -- no sweep output yet
        return empty
    now = _datetime.now(_timezone.utc)
    ts = snap.get("ts")
    age = None
    try:
        age = (now - _datetime.fromisoformat(str(ts))).total_seconds() / 60.0
    except Exception:  # noqa: BLE001
        pass
    rows = []
    for r in snap.get("rows") or []:
        try:
            commence = _datetime.fromisoformat(str(r.get("commence")))
        except Exception:  # noqa: BLE001
            continue
        mins_now = (commence - now).total_seconds() / 60.0
        if mins_now <= 0:
            continue  # kicked off -- the quote is dead
        sides = []
        for side in ("yes", "no"):
            ev, ask = _f(r.get("ev_" + side)), _f(r.get("ask_" + side))
            if ev is not None and ask is not None:
                sides.append((ev, side, ask))
        if not sides:
            continue
        ev, side, ask = max(sides)
        flag = bool(r.get("flag"))
        if not flag and ev < min_ev:
            continue
        p_over = _f(r.get("p_over"))
        rows.append({
            "sport": "nfl", "venue": "kalshi", "ticker": r.get("ticker"), "game_key": r.get("game_key"),
            "stat": r.get("stat"), "player": r.get("player"), "team": r.get("team"),
            "strike": _f(r.get("strike")), "side": "OVER" if side == "yes" else "UNDER",
            "ask": ask, "net_ev": round(ev, 4),
            "fair_prob": None if p_over is None else round(p_over if side == "yes" else 1.0 - p_over, 4),
            "n_books": r.get("n_books"), "books": r.get("books"), "disp": _f(r.get("disp")),
            "spread": _f(r.get("spread")), "fee_mult": _f(r.get("fee_mult")),
            "commence": r.get("commence"), "mins_to_kick": round(mins_now, 1),
            "flag": flag, "reason": r.get("reason"),
        })
    rows.sort(key=lambda x: (not x["flag"], -x["net_ev"]))
    rows = rows[: int(limit)]
    stale = age is None or age > _NFL_STALE_MINUTES
    return {"rows": rows, "count": len(rows), "status": "idle" if stale else "shadow_live",
            "as_of": ts, "age_minutes": None if age is None else round(age, 1), "stale": stale,
            "summary": snap.get("summary") or {}, "validated": False}


def get_ev_scanner_feed(engine, sport="all", min_ev=0.02, limit=100, days=2):
    """+EV terminal feed. MLB: tracked system='EV' rows (real graded history, flat stake at the flagged
    price). NFL: latest shadow sweep rows (Kalshi ask vs book consensus), flagged unvalidated."""
    sport = (sport or "all").lower()
    min_ev = float(min_ev)
    limit = max(1, min(int(limit), 200))
    out = {"sport": sport, "min_ev": min_ev, "as_of": _datetime.now(_timezone.utc).isoformat(),
           "mlb": {"rows": [], "count": 0}, "nfl": None}
    if sport in ("all", "mlb"):
        mlb = _ev_mlb_rows(engine, min_ev, limit, days)
        out["mlb"] = {"rows": mlb, "count": len(mlb)}
    if sport in ("all", "nfl"):
        out["nfl"] = _nfl_feed(min_ev, limit)
    return out


def _roi(profit, stake):
    return round(profit / stake * 100.0, 2) if stake else None


def get_analytics_deep_dive(engine, system=None, bankroll=1000.0, kelly_mult=1.0, cap=0.05):
    """Institutional views over settled bets.

    system scopes edge_buckets + book_leaderboard (None = every system). bankroll_curve and ev_headline
    always compare system='EV' (flat vs Kelly) against the MLB model systems. Voids are excluded from
    every ROI denominator (the stake is refunded). Kelly is the non-compounding rescale used by
    mlb.analysis.ev_kelly_bankroll: stake = clip(kelly_pct * kelly_mult, 0, cap) * bankroll, return =
    the REAL profit/stake ratio of the graded bet."""
    bankroll = float(bankroll)
    kelly_mult = max(0.0, float(kelly_mult))
    cap = max(0.0, float(cap))
    cond = "kelly_triggered = true AND stake > 0 AND result IN ('win','loss','push')"
    params = {}
    if system and str(system).strip():
        cond += " AND system = :system"
        params["system"] = str(system).strip().upper()
    sql = ("SELECT id, system, game_date, game_pk, bet_type, player, odds, stake, model_prob, "
           "market_prob, edge, kelly_pct, result, profit, book, clv_pct FROM bets "
           f"WHERE {cond} ORDER BY game_date, id")
    ev_sql = ("SELECT id, system, game_date, game_pk, bet_type, player, odds, stake, model_prob, "
              "market_prob, edge, kelly_pct, result, profit, book, clv_pct FROM bets "
              "WHERE system IN ('EV') AND stake > 0 AND result IN ('win','loss','push') "
              "ORDER BY game_date, id")
    model_sql = ("SELECT game_date, COALESCE(SUM(profit), 0) AS pnl FROM bets WHERE system <> 'EV' "
                 "AND kelly_triggered = true AND stake > 0 AND result IN ('win','loss','push') "
                 "GROUP BY game_date ORDER BY game_date")
    counts_sql = ("SELECT COUNT(*) AS logged, "
                  "COUNT(*) FILTER (WHERE result = 'win') AS wins, "
                  "COUNT(*) FILTER (WHERE result = 'loss') AS losses, "
                  "COUNT(*) FILTER (WHERE result = 'push') AS pushes, "
                  "COUNT(*) FILTER (WHERE result = 'void') AS voids, "
                  "COUNT(*) FILTER (WHERE result IS NULL) AS pending, "
                  "MIN(game_date) AS first_date, MAX(game_date) AS last_date "
                  "FROM bets WHERE system = 'EV'")
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
        ev_rows = [dict(r) for r in conn.execute(text(ev_sql)).mappings().all()]
        model_days = [dict(r) for r in conn.execute(text(model_sql)).mappings().all()]
        counts = dict(conn.execute(text(counts_sql)).mappings().first() or {})

    # a) edge buckets ---------------------------------------------------------
    buckets = []
    for label, lo, hi in _EV_BANDS:
        grp = [r for r in rows
               if (e := _edge_frac(r.get("edge"))) is not None and e >= lo and (hi is None or e < hi)]
        wins = sum(1 for r in grp if r["result"] == "win")
        losses = sum(1 for r in grp if r["result"] == "loss")
        stake = sum(float(r["stake"]) for r in grp)
        profit = sum(float(r["profit"] or 0) for r in grp)
        mp = [float(r["model_prob"]) for r in grp if r.get("model_prob") is not None]
        kp = [float(r["market_prob"]) for r in grp if r.get("market_prob") is not None]
        clv = [float(r["clv_pct"]) for r in grp if r.get("clv_pct") is not None]
        buckets.append({
            "bucket": label, "n": len(grp), "wins": wins, "losses": losses,
            "hit_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
            "avg_model_prob": round(sum(mp) / len(mp) * 100, 1) if mp else None,
            "avg_market_prob": round(sum(kp) / len(kp) * 100, 1) if kp else None,
            "roi_pct": _roi(profit, stake),
            "mean_clv": round(sum(clv) / len(clv), 2) if clv else None,
        })

    # b) book leaderboard -------------------------------------------------------
    by_book = {}
    for r in rows:
        by_book.setdefault((r.get("book") or "unknown").lower(), []).append(r)
    leaderboard = []
    for book, grp in by_book.items():
        wins = sum(1 for r in grp if r["result"] == "win")
        losses = sum(1 for r in grp if r["result"] == "loss")
        stake = sum(float(r["stake"]) for r in grp)
        profit = sum(float(r["profit"] or 0) for r in grp)
        units = sum(float(r["profit"] or 0) / float(r["stake"]) for r in grp)
        clv = [float(r["clv_pct"]) for r in grp if r.get("clv_pct") is not None]
        edges = [e for e in (_edge_frac(r.get("edge")) for r in grp) if e is not None]
        leaderboard.append({
            "book": book, "n": len(grp), "wins": wins,
            "hit_rate": round(wins / (wins + losses) * 100, 1) if wins + losses else None,
            "roi_pct": _roi(profit, stake), "profit_units": round(units, 2),
            "mean_clv": round(sum(clv) / len(clv), 2) if clv else None,
            "avg_edge": round(sum(edges) / len(edges) * 100, 2) if edges else None,
        })
    leaderboard.sort(key=lambda b: -b["n"])

    # c) bankroll curve (EV flat vs EV Kelly vs MLB model systems) -----------------
    days_map = {}
    flat_pnl = flat_stake = kelly_pnl = kelly_stake_total = 0.0
    for r in ev_rows:
        stake, profit = float(r["stake"]), float(r["profit"] or 0)
        ratio = profit / stake
        kp = _f(r.get("kelly_pct"))
        k_stake = min(max((kp or 0.0) * kelly_mult, 0.0), cap) * bankroll
        d = days_map.setdefault(r["game_date"], {"flat": 0.0, "kelly": 0.0, "models": 0.0})
        d["flat"] += ratio * 100.0
        d["kelly"] += ratio * k_stake
        flat_pnl += ratio * 100.0
        flat_stake += 100.0
        kelly_pnl += ratio * k_stake
        kelly_stake_total += k_stake
    for m in model_days:
        days_map.setdefault(m["game_date"], {"flat": 0.0, "kelly": 0.0, "models": 0.0})["models"] += float(m["pnl"])
    curve, cf, ck, cm = [], 0.0, 0.0, 0.0
    for day in sorted(days_map):
        d = days_map[day]
        cf += d["flat"]
        ck += d["kelly"]
        cm += d["models"]
        curve.append({"date": day, "ev_flat_pnl": round(d["flat"], 2), "ev_kelly_pnl": round(d["kelly"], 2),
                      "models_pnl": round(d["models"], 2), "ev_flat_cum": round(cf, 2),
                      "ev_kelly_cum": round(ck, 2), "models_cum": round(cm, 2)})

    # d) EV headline ---------------------------------------------------------------
    unique = {(r["game_date"], r.get("game_pk"), r.get("player"), _strip_book_suffix(r.get("bet_type"), r.get("book")))
              for r in ev_rows}
    graded = len(ev_rows)
    headline = {
        "logged": int(counts.get("logged") or 0), "graded": graded,
        "wins": int(counts.get("wins") or 0), "losses": int(counts.get("losses") or 0),
        "pushes": int(counts.get("pushes") or 0), "voids": int(counts.get("voids") or 0),
        "pending": int(counts.get("pending") or 0),
        "first_date": counts.get("first_date"), "last_date": counts.get("last_date"),
        "flat_stake_unit": 100.0, "flat_roi_pct": _roi(flat_pnl, flat_stake),
        "flat_pnl_dollars": round(flat_pnl, 2),
        "bankroll": bankroll, "kelly_mult": kelly_mult, "kelly_cap": cap,
        "kelly_roi_pct": _roi(kelly_pnl, kelly_stake_total),
        "kelly_pnl_dollars": round(kelly_pnl, 2),
        "kelly_ending_bankroll": round(bankroll + kelly_pnl, 2),
        "unique_props_graded": len(unique),
        "voids_excluded_from_roi": True,
    }
    return {"edge_buckets": buckets, "book_leaderboard": leaderboard, "bankroll_curve": curve,
            "ev_headline": headline, "system": params.get("system")}
