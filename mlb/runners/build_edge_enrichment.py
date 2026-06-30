"""Precompute per-player enrichment for the beezy.fyi "The Edge" cockpit.

Writes a SMALL JSON (Enrich/edge/{date}.json) so the public API never reads the
300MB Statcast master per request. Runs nightly / on demand. Fail-soft: any field
that can't be built is omitted, never fatal.

Per kelly-triggered pick today (keyed by normalized player name):
  - weather:     temp / wind from Weather/weather_master.csv by game_pk
  - BATTER picks (hits / total_bases / home_runs):
      recent_form  -- last N games of the stat vs the line
      spray        -- batted-ball (hc_x, hc_y) scatter, hit vs out
      ev_la        -- exit-velocity / launch-angle scatter
  - PITCHER picks (strikeouts / outs / earned_runs):
      recent_form  -- last N games of the stat
      velo         -- release_speed distribution by pitch_type
      release      -- release_pos_x / release_pos_z by pitch_type
      zone         -- Gameday zone (1-14) frequency

Statcast convention: `player_name` is the PITCHER; `batter`/`pitcher` are MLBAM
ids. Batters are matched on the `batter` id (resolved via the MLB Stats API);
pitchers on `player_name`.

Run:
  GCS_BUCKET=... MLB_DB_URL=... python3 -m runners.build_edge_enrichment [--date YYYY-MM-DD]
"""
import argparse
import json
import logging
import unicodedata
from datetime import datetime, timezone

import pandas as pd
import requests

from mlb_core import storage
from mlb_core.config import DB_URL
from mlb_core.data.lineups import get_today_schedule, confirmed_lineup_ids, fetch_il_ids

logger = logging.getLogger(__name__)

HIT_EVENTS = {"single", "double", "triple", "home_run"}
TB_MAP = {"single": 1, "double": 2, "triple": 3, "home_run": 4}
BATTED_EVENTS = HIT_EVENTS | {"field_out", "grounded_into_double_play", "force_out",
                              "sac_fly", "sac_bunt", "field_error", "fielders_choice",
                              "double_play", "triple_play", "fielders_choice_out"}

# outs recorded per out-producing event (events is non-null only on the PA-ending
# pitch, so summing over rows is correct). Approximate: covers the common cases.
OUT_EVENTS_MULT = {
    "strikeout": 1, "field_out": 1, "force_out": 1, "sac_fly": 1, "sac_bunt": 1,
    "fielders_choice_out": 1, "caught_stealing_2b": 1, "caught_stealing_3b": 1,
    "caught_stealing_home": 1, "other_out": 1,
    "grounded_into_double_play": 2, "double_play": 2, "sac_fly_double_play": 2,
    "strikeout_double_play": 2, "triple_play": 3,
}

BATTER_STATS = [("TOTAL_BASES", "total_bases"), ("TB", "total_bases"),
                ("HITS", "hits"), ("HR", "home_runs"), ("HOMERUN", "home_runs")]
PITCHER_STATS = [("OUTS", "outs"), ("STRIKEOUT", "strikeouts"), ("_K_", "strikeouts"),
                 ("EARNED_RUN", "earned_runs"), ("ER", "earned_runs")]

_SC_COLS = ["pitcher", "player_name", "batter", "game_pk", "game_date", "events",
            "description", "pitch_type", "release_speed", "release_pos_x", "release_pos_z",
            "zone", "hc_x", "hc_y", "launch_speed", "launch_angle"]


def _norm(name) -> str:
    if not isinstance(name, str):
        return ""
    s = name.strip()
    if "," in s:                       # "Last, First" -> "First Last"
        last, first = [p.strip() for p in s.split(",", 1)]
        s = f"{first} {last}"
    s = unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    return s.lower().strip()


def _classify(bet_type: str):
    bt = (bet_type or "").upper()
    for frag, stat in BATTER_STATS:
        if frag in bt:
            return "batter", stat
    for frag, stat in PITCHER_STATS:
        if frag in bt:
            return "pitcher", stat
    return None, None


def _line(bet_type: str):
    import re
    m = re.search(r"(\d+(?:\.\d+)?)", bet_type or "")
    return float(m.group(1)) if m else None


_ID_CACHE: dict[str, int] = {}


def _person_id(name: str):
    """Resolve a player name to an MLBAM id via the MLB Stats API (cached).
    Works for batters and pitchers."""
    if name in _ID_CACHE:
        return _ID_CACHE[name]
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1/people/search",
                         params={"names": name}, timeout=15)
        people = (r.json() or {}).get("people", []) if r.ok else []
        pid = int(people[0]["id"]) if people else None
    except Exception as exc:
        logger.warning("id lookup failed for %s: %s", name, exc)
        pid = None
    _ID_CACHE[name] = pid
    return pid


_POS_CACHE: dict[int, str] = {}


def _position(pid: int):
    """primaryPosition abbreviation (e.g. 'SS', 'SP') via /people/{id}, cached."""
    if pid in _POS_CACHE:
        return _POS_CACHE[pid]
    pos = None
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1/people/{pid}", timeout=15)
        if r.ok:
            people = (r.json() or {}).get("people", [])
            if people:
                pos = (people[0].get("primaryPosition") or {}).get("abbreviation")
    except Exception as exc:
        logger.warning("position lookup failed for %s: %s", pid, exc)
    _POS_CACHE[pid] = pos
    return pos


def _season_realized(pid: int, kind: str, season: str):
    """Traditional season line via /people/{id}/stats. Batters: AVG/HR/OBP.
    Pitchers: ERA/K/IP. Returns list of {label,value} (possibly empty)."""
    group = "hitting" if kind == "batter" else "pitching"
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                         params={"stats": "season", "group": group, "season": season},
                         timeout=15)
        blocks = (r.json() or {}).get("stats", []) if r.ok else []
        splits = (blocks[0].get("splits") if blocks else []) or []
        st = splits[0].get("stat", {}) if splits else {}
    except Exception as exc:
        logger.warning("season stats failed for %s: %s", pid, exc)
        st = {}
    out = []

    def add(label, key):
        v = st.get(key)
        if v is not None and v != "":
            out.append({"label": label, "value": str(v)})
    if kind == "batter":
        add("AVG", "avg"); add("HR", "homeRuns"); add("OBP", "obp")
    else:
        add("ERA", "era"); add("K", "strikeOuts"); add("IP", "inningsPitched")
    return out


_XSTATS = None   # lazy Savant expected-stats master (indexed by player_id)


def _expected_master():
    global _XSTATS
    if _XSTATS is not None:
        return _XSTATS
    try:
        df = storage.read_csv("Statcast/savant_expected_statistics_master.csv")
        if "year" in df.columns:
            df = df.sort_values("year").groupby("player_id", as_index=False).tail(1)
        _XSTATS = df.set_index("player_id")
    except Exception as exc:
        logger.warning("expected-stats master unreadable: %s", exc)
        _XSTATS = pd.DataFrame()
    return _XSTATS


def _season_expected(pid: int):
    """Statcast expected stats from the Savant master. Best-effort: pulls
    whichever of est_ba/est_slg/est_woba (+ xera) exist. Returns [{label,value}]."""
    m = _expected_master()
    if m.empty or pid not in m.index:
        return []
    row = m.loc[pid]
    if getattr(row, "ndim", 1) > 1:        # duplicate ids -> first
        row = row.iloc[0]
    out = []

    def add(label, col, fmt="{:.3f}"):
        if col in m.columns:
            v = row.get(col)
            if v is not None and not pd.isna(v):
                try:
                    out.append({"label": label, "value": fmt.format(float(v))})
                except Exception:
                    out.append({"label": label, "value": str(v)})
    add("xBA", "est_ba"); add("xSLG", "est_slg"); add("xwOBA", "est_woba")
    add("xERA", "xera", "{:.2f}")
    return out


def _today_picks(engine, date: str):
    from sqlalchemy import text
    q = text("SELECT player, game_pk, bet_type FROM bets "
             "WHERE game_date = :d AND kelly_triggered = true AND player IS NOT NULL")
    with engine.connect() as c:
        return [(r[0], int(r[1]), r[2]) for r in c.execute(q, {"d": date}).fetchall()]


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
            "wind_dir": None if pd.isna(r.get("wind_direction")) else str(r["wind_direction"]),
        }
    return out


def _batter_stat(g: pd.DataFrame, stat: str) -> int:
    ev = g["events"].dropna()
    if stat == "hits":
        return int(ev.isin(HIT_EVENTS).sum())
    if stat == "home_runs":
        return int((ev == "home_run").sum())
    if stat == "total_bases":
        return int(ev.map(TB_MAP).fillna(0).sum())
    return 0


def _recent(g: pd.DataFrame, valfn, line, n=12):
    out = []
    for (d, _gp), gg in g.dropna(subset=["game_date"]).groupby(["game_date", "game_pk"]):
        out.append((str(d)[5:10], int(valfn(gg))))
    out.sort(key=lambda t: t[0])
    out = out[-n:]
    return {"line": line, "games": [{"date": d, "value": v,
            "over": (None if line is None else bool(v > line))} for d, v in out]}


def _batter_block(g: pd.DataFrame, stat: str, line):
    rec = {}
    try:
        rf = _recent(g, lambda gg: _batter_stat(gg, stat), line)
        rf["stat"] = stat
        rec["recent_form"] = rf
    except Exception as exc:
        logger.warning("batter recent_form failed: %s", exc)
    try:
        bb = g[g["events"].isin(BATTED_EVENTS) & g["hc_x"].notna() & g["hc_y"].notna()].tail(160)
        rec["spray"] = [{"x": round(float(x), 1), "y": round(float(y), 1),
                         "hit": bool(e in HIT_EVENTS)} for x, y, e in
                        zip(bb["hc_x"], bb["hc_y"], bb["events"])]
    except Exception as exc:
        logger.warning("spray failed: %s", exc)
    try:
        ev = g[g["launch_speed"].notna() & g["launch_angle"].notna()].tail(200)
        rec["ev_la"] = [{"ev": round(float(s), 1), "la": round(float(a), 1),
                         "hit": bool(e in HIT_EVENTS)} for s, a, e in
                        zip(ev["launch_speed"], ev["launch_angle"], ev["events"])]
    except Exception as exc:
        logger.warning("ev_la failed: %s", exc)
    return rec


def _pitcher_block(g: pd.DataFrame, stat: str, line):
    rec = {}
    try:
        def val(gg):
            if stat == "strikeouts":
                return int((gg["events"] == "strikeout").sum())
            if stat == "outs":
                return int(gg["events"].dropna().map(OUT_EVENTS_MULT).fillna(0).sum())
            return 0
        if stat in ("strikeouts", "outs"):
            rf = _recent(g, val, line)
            rf["stat"] = stat
            rec["recent_form"] = rf
    except Exception as exc:
        logger.warning("pitcher recent_form failed: %s", exc)
    try:
        v = g[g["release_speed"].notna() & g["pitch_type"].notna()].tail(800)
        by = v.groupby("pitch_type")["release_speed"].agg(["mean", "count"])
        rec["velo"] = [{"pitch": p, "mph": round(float(m), 1), "n": int(c)}
                       for p, (m, c) in by.iterrows() if c >= 3]
    except Exception as exc:
        logger.warning("velo failed: %s", exc)
    try:
        r = g[g["release_pos_x"].notna() & g["release_pos_z"].notna() & g["pitch_type"].notna()].tail(600)
        rec["release"] = [{"x": round(float(x), 2), "z": round(float(z), 2), "pitch": p}
                          for x, z, p in zip(r["release_pos_x"], r["release_pos_z"], r["pitch_type"])]
    except Exception as exc:
        logger.warning("release failed: %s", exc)
    try:
        z = g[g["zone"].notna()]["zone"].astype(int).value_counts()
        rec["zone"] = {int(k): int(v) for k, v in z.items()}
    except Exception as exc:
        logger.warning("zone failed: %s", exc)
    return rec


def build(date: str) -> dict:
    players: dict[str, dict] = {}
    weather = _weather_by_gamepk()
    if not DB_URL:
        logger.warning("MLB_DB_URL not set -- empty enrichment")
        return {"date": date, "players": {}, "generated_at": _now_iso()}

    from sqlalchemy import create_engine
    engine = create_engine(DB_URL)
    picks = _today_picks(engine, date)
    typed = [(p, gp, bt, *_classify(bt)) for (p, gp, bt) in picks]
    typed = [t for t in typed if t[3]]   # keep only batter/pitcher picks
    if not typed:
        return {"date": date, "players": {}, "generated_at": _now_iso()}

    try:
        sc = storage.read_csv("Statcast/statcast_master.csv", usecols=lambda c: c in _SC_COLS)
    except Exception as exc:
        logger.warning("statcast master unreadable: %s -- weather-only", exc)
        sc = None
    if sc is not None:
        sc["_pname"] = sc["player_name"].map(_norm)

    # --- status precompute (bounded network) --------------------------------
    season = date[:4]
    try:
        il_ids = fetch_il_ids()
    except Exception as exc:
        logger.warning("IL fetch failed: %s", exc)
        il_ids = set()
    lineup_sets: dict[int, set] = {}
    for gp in {gp for (_p, gp, _bt, _k, _s) in typed}:
        try:
            lineup_sets[gp] = confirmed_lineup_ids(gp)
        except Exception:
            lineup_sets[gp] = set()
    probable: dict[int, set] = {}
    try:
        sched = get_today_schedule(date)
        for _, r in sched.iterrows():
            ids = set()
            for col in ("home_pitcher_id", "away_pitcher_id"):
                v = r.get(col)
                if pd.notna(v):
                    ids.add(int(v))
            probable[int(r["game_pk"])] = ids
    except Exception as exc:
        logger.warning("schedule fetch failed: %s", exc)

    def _status(pid, kind, game_pk) -> str:
        if pid is None:
            return "unknown"
        if pid in il_ids:
            return "out"
        if kind == "pitcher":
            return "confirmed" if pid in probable.get(game_pk, set()) else "expected"
        ls = lineup_sets.get(game_pk, set())
        if ls:
            return "confirmed" if pid in ls else "out"
        return "expected"

    for player, game_pk, bet_type, kind, stat in typed:
        key = _norm(player)
        rec = players.setdefault(key, {})
        if game_pk in weather:
            rec["weather"] = weather[game_pk]

        pid = _person_id(player)
        rec["status"] = _status(pid, kind, game_pk)
        if pid is not None:
            try:
                pos = _position(pid)
                if pos:
                    rec["position"] = pos
            except Exception as exc:
                logger.warning("position failed for %s: %s", player, exc)
            try:
                realized = _season_realized(pid, kind, season)
                expected = _season_expected(pid)
                if realized or expected:
                    rec["season"] = {"realized": realized, "expected": expected}
            except Exception as exc:
                logger.warning("season failed for %s: %s", player, exc)

        if sc is None:
            continue
        line = _line(bet_type)
        try:
            if kind == "batter":
                g = sc[sc["batter"] == pid] if pid is not None else sc.iloc[0:0]
                if not g.empty:
                    rec.update(_batter_block(g, stat, line))
            else:  # pitcher -- statcast player_name IS the pitcher
                g = sc[sc["_pname"] == key]
                if not g.empty:
                    rec.update(_pitcher_block(g, stat, line))
        except Exception as exc:
            logger.warning("enrich failed for %s (%s): %s", player, kind, exc)

    players = {k: v for k, v in players.items() if v}
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
    logger.info("done: %s", run(ap.parse_args().date))


if __name__ == "__main__":
    main()
