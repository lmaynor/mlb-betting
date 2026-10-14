"""
ev.grade_props -- grade logged NHL/NBA soft-line alerts against REAL results (flat $100 stake at the flagged price).

This is the instrument that answers "are these alerts actually +EV?" -- without it the sweep is only a pager. It reads
the evidence the sweep logged (EVSweep/{sport}_props/{day}/book_outliers_*.csv), takes the FIRST sighting of each
distinct quote (same dedup key the pager uses, so a quote seen in six passes counts once), fetches the final stats and
grades by the league's own PLAYER ID -- NHL: official boxscore `playerId`; NBA: ESPN summary athlete id -- the same id
spaces as the roster crosswalks, so there is no name matching at grade time. Rows with no resolved id are 'ungradable'
(never guessed).

Grading rules (documented because books differ):
  * Over wins if actual > point, Under if actual < point, equal = push (half-point lines make that rare).
  * A one-sided "Yes" (anytime goal etc.) is an Over at its point (0.5 = 1+).
  * Player did not play (not in the box score, TOI 0, ESPN didNotPlay, goalie dressed but 0:00): VOID (stake refunded).
    Sportsbook rules for DNPs vary (NHL: most void a scratched skater; goalie props void if the goalie does not start
    -- a backup who enters mid-game is NOT modelled here) so voids are reported, never counted as losses.
  * Game not final yet: 'pending'.
  * NHL stats that need play-by-play (power-play POINTS) are 'ungradable' for now.
Verdict discipline (repo rule): ROI on a few hundred correlated quotes is NOISE -- the summary reports n, voids and a
t-stat, and the verdict stays UNDETERMINED until CLV vs the closing line is added. This never changes a stake.

Run:  python -m ev.grade_props --sport nhl --date 2026-10-14        (writes {prefix}/{day}/graded.csv, prints summary)
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
from datetime import date as _date

import pandas as pd
import requests

from mlb_core.odds.utils import american_to_decimal

log = logging.getLogger("ev.grade_props")
STAKE = 100.0
_UA = {"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-data/1.0)"}

NHL_SCHEDULE = "https://api-web.nhle.com/v1/schedule/{day}"
NHL_BOX = "https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={ymd}"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}"

NHL_SKATER = {"goals": "goals", "assists": "assists", "points": "points", "sog": "sog", "hits": "hits",
              "blocked_shots": "blockedShots", "pim": "pim"}


def _http_json(url: str):
    r = requests.get(url, headers=_UA, timeout=30)
    r.raise_for_status()
    return r.json()


def _toi_sec(s) -> int:
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except (ValueError, AttributeError):
        return 0


# ----------------------------------------------------------------------------- actuals providers
def nhl_actuals(day: str, away: str, home: str, get=_http_json):
    """-> {'final': bool, 'players': {player_id: {stat: value, '_played': bool}}} for the NHL game away@home on `day`."""
    sched = get(NHL_SCHEDULE.format(day=day)) or {}
    gid, final = None, False
    for wk in sched.get("gameWeek") or []:
        if wk.get("date") != day:
            continue
        for g in wk.get("games") or []:
            if (g.get("awayTeam") or {}).get("abbrev") == away and (g.get("homeTeam") or {}).get("abbrev") == home:
                gid, final = g["id"], g.get("gameState") in ("OFF", "FINAL")
    if gid is None:
        return {"final": False, "players": {}, "reason": "game not found in schedule"}
    if not final:
        return {"final": False, "players": {}, "reason": "not final"}
    box = get(NHL_BOX.format(gid=gid)) or {}
    out = {}
    for side in ("awayTeam", "homeTeam"):
        grp = (box.get("playerByGameStats") or {}).get(side) or {}
        for p in (grp.get("forwards") or []) + (grp.get("defense") or []):
            rec = {stat: p.get(f, 0) or 0 for stat, f in NHL_SKATER.items()}
            rec["_played"] = _toi_sec(p.get("toi")) > 0
            out[int(p["playerId"])] = rec
        for p in grp.get("goalies") or []:
            out[int(p["playerId"])] = {"saves": p.get("saves", 0) or 0, "_played": _toi_sec(p.get("toi")) > 0}
    return {"final": True, "players": out, "game_id": gid}


def _espn_val(keys, stats, key):
    try:
        v = stats[keys.index(key)]
    except (ValueError, IndexError):
        return None
    try:
        return float(str(v).split("-")[0])           # '3-7' (made-attempted) -> 3
    except ValueError:
        return None


def nba_actuals(day: str, away: str, home: str, get=_http_json):
    """-> same shape as nhl_actuals, keyed by ESPN athlete id (int). away/home are NBA tricodes."""
    from nba.teams import team_code
    sb = get(ESPN_SCOREBOARD.format(ymd=day.replace("-", ""))) or {}
    eid, final = None, False
    for ev in sb.get("events") or []:
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c["homeAway"]: team_code((c.get("team") or {}).get("abbreviation")) for c in comp.get("competitors") or []}
        if teams.get("away") == away and teams.get("home") == home:
            eid, final = ev["id"], (ev.get("status", {}).get("type", {}).get("name") == "STATUS_FINAL")
    if eid is None:
        return {"final": False, "players": {}, "reason": "game not found on scoreboard"}
    if not final:
        return {"final": False, "players": {}, "reason": "not final"}
    out = {}
    for t in (get(ESPN_SUMMARY.format(eid=eid)) or {}).get("boxscore", {}).get("players", []):
        for blk in t.get("statistics") or []:
            keys = blk.get("keys") or []
            for a in blk.get("athletes") or []:
                st = a.get("stats") or []
                v = lambda k: _espn_val(keys, st, k)                                     # noqa: E731
                pts, reb, ast = v("points"), v("rebounds"), v("assists")
                stl, blk_, tov = v("steals"), v("blocks"), v("turnovers")
                combo = lambda *xs: None if any(x is None for x in xs) else float(sum(xs))   # noqa: E731
                out[int(a["athlete"]["id"])] = {
                    "points": pts, "rebounds": reb, "assists": ast, "steals": stl, "blocks": blk_, "turnovers": tov,
                    "threes": v("threePointFieldGoalsMade-threePointFieldGoalsAttempted"), "minutes": v("minutes"),
                    "pra": combo(pts, reb, ast), "pr": combo(pts, reb), "pa": combo(pts, ast), "ra": combo(reb, ast),
                    "stocks": combo(stl, blk_),
                    "_played": not a.get("didNotPlay") and (v("minutes") or 0) > 0}
    return {"final": True, "players": out, "game_id": eid}


PROVIDERS = {"nhl": nhl_actuals, "nba": nba_actuals}


# ----------------------------------------------------------------------------- grading
def grade_row(row: dict, game: dict) -> tuple[str, float]:
    """-> (result, profit) for one logged quote against its game's actuals."""
    if not game or not game.get("final"):
        return "pending", 0.0
    pid = row.get("player_id")
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return "ungradable", 0.0
    p = game["players"].get(pid)
    if p is None or not p.get("_played"):
        return "void", 0.0
    actual = p.get(row["stat"])
    if actual is None:
        return "ungradable", 0.0
    point = float(row["point"])
    if actual == point:
        return "push", 0.0
    won = actual > point if str(row["side"]).upper() == "OVER" else actual < point
    return ("win", STAKE * (american_to_decimal(int(row["american"])) - 1.0)) if won else ("loss", -STAKE)


def first_sightings(df: pd.DataFrame, flagged_only: bool = False) -> pd.DataFrame:
    """One row per distinct quote -- the earliest sighting (what a person alerted at that moment would have faced)."""
    if df.empty:
        return df
    if flagged_only:
        df = df[df["flag"].astype(bool)]
    key = ["game_key", "player_norm", "stat", "point", "side", "book"]
    return df.sort_values("ts").drop_duplicates(key, keep="first").reset_index(drop=True)


def grade_frame(df: pd.DataFrame, sport: str, provider=None) -> pd.DataFrame:
    provider = provider or PROVIDERS[sport]
    df = df.copy()
    games: dict = {}
    res, prof = [], []
    for r in df.to_dict("records"):
        _, day, teams = str(r["game_key"]).split(":")
        away, home = teams.split("@")
        gk = (day, away, home)
        if gk not in games:
            try:
                games[gk] = provider(day, away, home)
            except Exception as exc:                                                     # noqa: BLE001
                log.warning("actuals failed for %s: %r", gk, exc)
                games[gk] = {"final": False, "players": {}}
        result, profit = grade_row(r, games[gk])
        res.append(result)
        prof.append(round(profit, 2))
    df["result"], df["profit"] = res, prof
    return df


def summarize(g: pd.DataFrame, by: str = None) -> dict | list:
    def one(x: pd.DataFrame) -> dict:
        dec = x[x["result"].isin(["win", "loss", "push"])]
        n = len(dec)
        wins, losses = int((dec["result"] == "win").sum()), int((dec["result"] == "loss").sum())
        staked = STAKE * n
        pnl = float(dec["profit"].sum())
        per = (dec["profit"] / STAKE).to_numpy() if n else []
        se = (float(pd.Series(per).std(ddof=1)) / math.sqrt(n)) if n > 1 else 0.0
        t = (float(pd.Series(per).mean()) / se) if se > 1e-9 else None
        return {"quotes": len(x), "graded": n, "wins": wins, "losses": losses, "pushes": n - wins - losses,
                "voids": int((x["result"] == "void").sum()), "pending": int((x["result"] == "pending").sum()),
                "ungradable": int((x["result"] == "ungradable").sum()),
                "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
                "flat_pnl": round(pnl, 2), "flat_roi_pct": round(pnl / staked * 100, 2) if staked else None,
                "avg_claimed_ev_pct": round(float(x["ev"].mean()) * 100, 2) if len(x) else None,
                "roi_t_stat": None if t is None else round(t, 2),
                "verdict": "UNDETERMINED (ROI on correlated alerts is noise; needs CLV vs close)"}
    if by is None:
        return one(g)
    return [{by: k, **one(sub)} for k, sub in g.groupby(by)]


def load_day(prefix: str, day: str) -> pd.DataFrame:
    from mlb_core import storage
    keys = [k for k in storage.list_keys(f"{prefix}/{day}/") if "/book_outliers_" in k and k.endswith(".csv")]
    frames = [pd.read_csv(io.BytesIO(storage.read_bytes(k))) for k in sorted(keys)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(sport: str, day: str, flagged_only: bool = False) -> dict:
    from ev.sweep_props_alerts import SPORTS
    from mlb_core import storage
    spec = SPORTS[sport]
    raw = load_day(spec.prefix, day)
    if raw.empty:
        return {"status": "no_evidence", "day": day}
    graded = grade_frame(first_sightings(raw, flagged_only), sport)
    storage.write_bytes(graded.to_csv(index=False).encode(), f"{spec.prefix}/{day}/graded.csv")
    return {"status": "ok", "day": day, "overall": summarize(graded), "by_stat": summarize(graded, "stat"),
            "by_book": summarize(graded, "book")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=sorted(PROVIDERS), required=True)
    ap.add_argument("--date", default="yesterday",
                    help="ET game day to grade: YYYY-MM-DD or 'yesterday' (default; what the scheduled job uses)")
    ap.add_argument("--flagged-only", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    day = a.date
    if day == "yesterday":
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        day = (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()
    print(json.dumps(run(a.sport, day, a.flagged_only), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
