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


def get_today_picks(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
            "FROM bets "
            "WHERE game_date = :_today AND kelly_triggered = true "
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
    params["limit"] = int(limit)
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


def get_pnl_sparkline(engine, days=30):
    """Daily cumulative P&L for the last N days, kelly_triggered=true only.
    Returns list of {date, daily_pnl, cum_pnl} dicts sorted ascending by date.
    """
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT game_date, COALESCE(SUM(profit), 0) AS daily_pnl "
            "FROM bets "
            "WHERE result IS NOT NULL "
            "  AND kelly_triggered = true "
            "  AND stake > 0 "
            "  AND game_date >= :cutoff "
            "GROUP BY game_date "
            "ORDER BY game_date ASC"
        ), {"cutoff": cutoff}).mappings().all()
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
