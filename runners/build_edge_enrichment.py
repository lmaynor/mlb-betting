"""Precompute per-player enrichment for the beezy.fyi "The Edge" dashboard.

Writes a SMALL JSON (Enrich/edge/{date}.json) so the public API never reads the
300MB Statcast master per request. Runs nightly / on demand -- NOT wired into the
daily build chain yet. Fail-soft: any field that can't be built is omitted, never
fatal, so a partial result still serves.

For each batter with a kelly-triggered pick today it computes:
  - weather:     temp / wind from Weather/weather_master.csv by game_pk
  - recent_form: last N games of the prop stat (hits / total_bases / home_runs)
                 derived from Statcast, with the line for over/under context
  - spray:       recent batted-ball (hc_x, hc_y) scatter, classified hit vs out

Run:
  GCS_BUCKET=... MLB_DB_URL=... python3 -m runners.build_edge_enrichment
  python3 -m runners.build_edge_enrichment --date 2026-06-15

VALIDATION NOTE: the Statcast-derived fields (recent_form, spray) need one live
run to confirm column/name matching against the real master before the dashboard
shows them; the endpoint + UI already degrade gracefully until then.
"""
import argparse
import json
import logging
from datetime import datetime, timezone

import pandas as pd

from mlb_core import storage
from mlb_core.config import DB_URL

logger = logging.getLogger(__name__)

HIT_EVENTS = {"single", "double", "triple", "home_run"}
TB_MAP = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
BATTED_EVENTS = HIT_EVENTS | {"field_out", "grounded_into_double_play", "force_out",
                              "sac_fly", "sac_bunt", "field_error", "fielders_choice",
                              "double_play", "triple_play", "fielders_choice_out"}

# bet_type fragment -> stat key
STAT_FROM_BETTYPE = [
    ("TOTAL_BASES", "total_bases"), ("TB", "total_bases"),
    ("HITS", "hits"), ("HR", "home_runs"), ("HOMERUN", "home_runs"),
]


def _norm_name(name) -> str:
    if not isinstance(name, str):
        return ""
    s = name.strip()
    if "," in s:                       # statcast "Last, First" -> "First Last"
        last, first = [p.strip() for p in s.split(",", 1)]
        s = f"{first} {last}"
    import unicodedata
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def _stat_for(bet_type: str) -> str | None:
    bt = (bet_type or "").upper()
    for frag, stat in STAT_FROM_BETTYPE:
        if frag in bt:
            return stat
    return None


def _line_from(bet_type: str):
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", bet_type or "")
    return float(m.group(1)) if m else None


def _today_batter_picks(engine, date: str):
    """Return [(player, game_pk, bet_type)] for today's kelly-triggered batter picks."""
    from sqlalchemy import text
    q = text("SELECT player, game_pk, bet_type FROM bets "
             "WHERE game_date = :d AND kelly_triggered = true AND player IS NOT NULL")
    with engine.connect() as c:
        rows = c.execute(q, {"d": date}).fetchall()
    return [(r[0], int(r[1]), r[2]) for r in rows if _stat_for(r[2])]


def _weather_by_gamepk():
    try:
        w = storage.read_csv("Weather/weather_master.csv")
    except Exception as exc:
        logger.warning("weather master unreadable: %s", exc)
        return {}
    out = {}
    for _, r in w.iterrows():
        gp = r.get("game_pk")
        if pd.isna(gp):
            continue
        out[int(gp)] = {
            "temp_f": None if pd.isna(r.get("temperature_f")) else round(float(r["temperature_f"])),
            "wind_mph": None if pd.isna(r.get("wind_speed_mph")) else round(float(r["wind_speed_mph"])),
            "wind_dir": (None if pd.isna(r.get("wind_direction")) else str(r["wind_direction"])),
        }
    return out


def _stat_value(group: pd.DataFrame, stat: str) -> int:
    ev = group["events"].dropna()
    if stat == "hits":
        return int(ev.isin(HIT_EVENTS).sum())
    if stat == "home_runs":
        return int((ev == "home_run").sum())
    if stat == "total_bases":
        return int(ev.map(TB_MAP).fillna(0).sum())
    return 0


def build(date: str) -> dict:
    players: dict[str, dict] = {}
    weather = _weather_by_gamepk()

    if not DB_URL:
        logger.warning("MLB_DB_URL not set -- skipping picks (weather-only enrichment unavailable)")
        return {"date": date, "players": {}, "generated_at": _now_iso()}
    from sqlalchemy import create_engine
    engine = create_engine(DB_URL)
    picks = _today_batter_picks(engine, date)
    if not picks:
        return {"date": date, "players": {}, "generated_at": _now_iso()}

    # Load only the statcast columns we need (keeps memory bounded).
    cols = ["batter", "player_name", "game_pk", "game_date", "events", "hc_x", "hc_y", "launch_speed"]
    try:
        sc = storage.read_csv("Statcast/statcast_master.csv", usecols=lambda c: c in cols)
    except Exception as exc:
        logger.warning("statcast master unreadable: %s -- weather-only", exc)
        sc = None

    sc_by_name = {}
    if sc is not None and "player_name" in sc.columns:
        sc["_norm"] = sc["player_name"].map(_norm_name)
        sc_by_name = {n: g for n, g in sc.groupby("_norm")}

    for player, game_pk, bet_type in picks:
        key = _norm_name(player)
        stat = _stat_for(bet_type)
        rec: dict = {}
        if game_pk in weather:
            rec["weather"] = weather[game_pk]
        g = sc_by_name.get(key)
        if g is not None and not g.empty and stat:
            try:
                # recent form: per-game stat over last 12 games
                per_game = (g.dropna(subset=["game_date"])
                            .groupby(["game_date", "game_pk"], as_index=False)
                            .apply(lambda gg: pd.Series({"value": _stat_value(gg, stat)}))
                            .sort_values("game_date").tail(12))
                line = _line_from(bet_type)
                rec["recent_form"] = {
                    "stat": stat, "line": line,
                    "games": [{"date": str(d)[5:10], "value": int(v),
                               "over": (None if line is None else bool(v > line))}
                              for d, v in zip(per_game["game_date"], per_game["value"])],
                }
            except Exception as exc:
                logger.warning("recent_form failed for %s: %s", player, exc)
            try:
                bb = g[g["events"].isin(BATTED_EVENTS) & g["hc_x"].notna() & g["hc_y"].notna()].tail(160)
                rec["spray"] = [
                    {"x": round(float(x), 1), "y": round(float(y), 1),
                     "hit": bool(e in HIT_EVENTS),
                     "ev": (None if pd.isna(s) else round(float(s)))}
                    for x, y, e, s in zip(bb["hc_x"], bb["hc_y"], bb["events"], bb.get("launch_speed", []))
                ]
            except Exception as exc:
                logger.warning("spray failed for %s: %s", player, exc)
        if rec:
            players[key] = rec

    return {"date": date, "players": players, "generated_at": _now_iso()}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def run(date: str = None) -> dict:
    if not date:
        from zoneinfo import ZoneInfo
        date = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    payload = build(date)
    storage.write_bytes(json.dumps(payload).encode(), f"Enrich/edge/{date}.json")
    logger.info("edge enrichment %s: %d players", date, len(payload.get("players", {})))
    return {"date": date, "players": len(payload.get("players", {}))}


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    logger.info("done: %s", run(args.date))


if __name__ == "__main__":
    main()
