"""
Read-only query helpers for the public API endpoints in main.py.
All queries use text() wrapper -- required for pg8000 named params (CONTEXT.md ss8).
"""
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


def _require_api_key(request_headers: dict, site_api_key: str) -> bool:
    """Return True if the X-API-Key header matches the configured key."""
    return request_headers.get("X-API-Key", "").strip() == site_api_key


def get_today_picks(engine):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, system, game_date, game_pk, bet_type, player, "
            "away_team, home_team, odds, stake, model_prob, market_prob, "
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
            "FROM bets "
            "WHERE game_date = CURRENT_DATE AND kelly_triggered = true "
            "ORDER BY system, created_at DESC"
        )).mappings().all()
    return [dict(r) for r in rows]


def get_picks(engine, system=None, date=None, status=None, limit=50, offset=0):
    conditions = []
    params = {}

    if system:
        conditions.append("system = :system")
        params["system"] = system

    if date == "today":
        conditions.append("game_date = CURRENT_DATE")
    elif date == "yesterday":
        conditions.append("game_date = CURRENT_DATE - INTERVAL '1 day'")
    elif date == "last7":
        conditions.append("game_date >= CURRENT_DATE - INTERVAL '7 days'")
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
        "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
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
            "edge, kelly_pct, kelly_triggered, result, profit, paper, notes, created_at "
            "FROM bets WHERE result IS NOT NULL "
            "ORDER BY game_date DESC, created_at DESC "
            "LIMIT :limit"
        ), {"limit": int(limit)}).mappings().all()
    return [dict(r) for r in rows]


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
            "ROUND(SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2) AS roi, "
            "ROUND(SUM(profit), 2) AS total_pnl, "
            "ROUND(AVG(model_prob - market_prob) * 100, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != 'void' "
            "GROUP BY system ORDER BY roi DESC"
        )).mappings().all()

        overall = conn.execute(text(
            "SELECT COUNT(*) AS total_bets, "
            "ROUND(COUNT(*) FILTER (WHERE result = 'win')::numeric / "
            "  NULLIF(COUNT(*) FILTER (WHERE result IN ('win','loss')), 0) * 100, 1) AS win_rate, "
            "ROUND(SUM(profit) / NULLIF(SUM(stake), 0) * 100, 2) AS roi, "
            "ROUND(AVG(model_prob - market_prob) * 100, 2) AS avg_edge "
            "FROM bets WHERE result IS NOT NULL AND result != 'void'"
        )).mappings().first()

    return {
        "overall":  dict(overall) if overall else {},
        "bySystem": [dict(r) for r in by_system],
    }
