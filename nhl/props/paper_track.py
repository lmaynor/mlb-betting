"""Forward paper-tracking of the served trained NHL props against REAL lines and closes. No bet is ever placed.

    python -m nhl.props.paper_track snapshot [--no-write]    every ~30 min before games: log served prediction x live line
    python -m nhl.props.paper_track settle [--day YYYY-MM-DD] after games: attach the real result to each logged prop
    python -m nhl.props.paper_track report [--days N]         how the served models did, against the market, on data nobody has seen

WHY: every number published for these models so far (docs/analysis/nhl_trained_serving_2026-11-01.md) comes from one historical
BettingPros archive, analysed repeatedly, with close-ish snapshots and best-price ROI. This is the only test that is not that
history: predictions logged BEFORE the game, against the lines the board really carried, settled on the real box score.

LEDGER (bucket, via mlb_core.storage):
    NHL/paper_track/snapshots/{CT day}.jsonl   one record per (snapshot time x served prediction x posted line)
    NHL/paper_track/settled/{CT day}.jsonl     one record per (game, player, stat, line): FIRST sighting, LAST pre-game sighting
                                               (the closing proxy), the real result, and the scores
Join: the served file `EVSweep/nhl_props/trained_latest.json` (mu / alpha per player x game x stat; read regardless of the
board's NHL_TRAINED_PROPS flag) with the board file `EVSweep/nhl_props/projections_latest.json` (consensus line, de-vigged market
P(over), best prices). A prop is logged only while its game has not started and >= MIN_BOOKS books price it.

REPORT: per stat -- n, model vs market log loss on the closing proxy, blend coefficient c (model information beyond the market),
and disagreement bets (|model - market| >= GAP at the FIRST sighting) with flat-stake ROI at the first-seen best price, the
closing-line value of the side taken (market P(side) at the last sighting minus at the first), and the Under / Over split. Read it
the way the repo reads everything: ROI on a few hundred correlated props is noise; CLV and c need weeks.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from nhl.props.hierarchical import NegBinDist

log = logging.getLogger("nhl.props.paper_track")
_CT = ZoneInfo("America/Chicago")
STATS = ("saves", "goals", "assists", "points")
MIN_BOOKS = 3
GAP = 0.05
SNAP_KEY = "NHL/paper_track/snapshots/{day}.jsonl"
SETTLED_KEY = "NHL/paper_track/settled/{day}.jsonl"
TRAINED_KEY = "EVSweep/nhl_props/trained_latest.json"
BOARD_KEY = "EVSweep/nhl_props/projections_latest.json"


# ----------------------------------------------------------------------------------------------------------- storage
def _read(key: str):
    from mlb_core import storage
    try:
        return storage.read_bytes(key)
    except Exception:                                                                   # noqa: BLE001
        return None


def _write(key: str, data: bytes) -> None:
    from mlb_core import storage
    storage.write_bytes(data, key)


def read_jsonl(key: str, read=_read) -> list:
    raw = read(key)
    return [json.loads(x) for x in raw.decode().splitlines() if x.strip()] if raw else []


def write_jsonl(key: str, rows: list, write=_write) -> None:
    write(key, ("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n").encode())


def ct_day(ts) -> str:
    t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return t.astimezone(_CT).date().isoformat()


# ----------------------------------------------------------------------------------------------------------- snapshot
def snapshot_records(now: datetime, trained: dict, board: dict) -> list:
    """Served predictions x the board's posted lines -> records. Pure. `trained` / `board` = the two JSON payloads."""
    from mlb_core.data.cockpit_form import norm_name
    served = {}
    for r in trained.get("rows", []):
        if r.get("stat") in STATS and r.get("game_key"):
            served[(r["game_key"], norm_name(r["name"]), r["stat"])] = r
    out = []
    for b in board.get("rows", []):
        if b.get("stat") not in STATS or not b.get("has_line") or b.get("line") is None or b.get("p_over_market") is None:
            continue
        if (b.get("n_books") or 0) < MIN_BOOKS or not b.get("commence"):
            continue
        if datetime.fromisoformat(str(b["commence"]).replace("Z", "+00:00")) <= now:
            continue                                                                    # started: a live line is not a bet
        s = served.get((b["game_key"], b.get("player_norm") or norm_name(b.get("player", "")), b["stat"]))
        if s is None:
            continue
        line = float(b["line"])
        out.append({"ts": now.isoformat(), "game_key": b["game_key"], "commence": b["commence"], "player_id": s["player_id"],
                    "player": b.get("player"), "stat": b["stat"], "team": b.get("team"), "opp": b.get("opp"), "line": line,
                    "mu": s["mu"], "alpha": s["alpha"], "p_model": round(NegBinDist(s["mu"], s["alpha"]).p_over(line), 5),
                    "p_mkt": b["p_over_market"], "over_odds": b.get("best_over_odds"), "under_odds": b.get("best_under_odds"),
                    "over_book": b.get("best_over_book"), "under_book": b.get("best_under_book"), "n_books": b["n_books"],
                    "version": trained.get("version")})
    return out


def cmd_snapshot(a) -> int:
    now = datetime.now(timezone.utc)
    trained, board = (json.loads(_read(k) or b"{}") for k in (TRAINED_KEY, BOARD_KEY))
    if not trained.get("rows") or not board.get("rows"):
        log.warning("nothing to snapshot (served predictions: %d, board rows: %d)", len(trained.get("rows", [])), len(board.get("rows", [])))
        return 0
    age = (now - datetime.fromisoformat(str(trained["ts"]).replace("Z", "+00:00"))).total_seconds() / 3600.0
    if age > 36.0:
        log.warning("served predictions are %.1f h old: not logged", age)
        return 0
    recs = snapshot_records(now, trained, board)
    log.info("%d props logged (%d served rows, %d board rows)", len(recs), len(trained["rows"]), len(board["rows"]))
    if recs and not a.no_write:
        day = ct_day(now)
        key = SNAP_KEY.format(day=day)
        write_jsonl(key, read_jsonl(key) + recs)
    return 0


# ------------------------------------------------------------------------------------------------------------ settle
def settle_records(snaps: list, actuals_for, now: datetime) -> list:
    """snapshot records -> settled records. `actuals_for(day, away, home) -> {'final': bool, 'players': {id: {stat: v, '_played': bool}}}`
    (ev.grade_props.nhl_actuals shape). A prop whose game is not final yet is skipped; a player who did not dress is VOID."""
    groups = defaultdict(list)
    for s in snaps:
        groups[(s["game_key"], s["player_id"], s["stat"], s["line"])].append(s)
    cache, out = {}, []
    for (gk, pid, stat, line), rows in groups.items():
        _, day, teams = gk.split(":")
        away, home = teams.split("@")
        if (day, away, home) not in cache:
            try:
                cache[(day, away, home)] = actuals_for(day, away, home)
            except Exception as exc:                                                    # noqa: BLE001
                log.warning("actuals failed for %s: %r", gk, exc)
                cache[(day, away, home)] = {"final": False, "players": {}}
        g = cache[(day, away, home)]
        if not g.get("final"):
            continue
        rows.sort(key=lambda r: r["ts"])
        pre = [r for r in rows if r["ts"] < r["commence"]] or rows
        first, last = pre[0], pre[-1]
        p = g["players"].get(int(pid))
        actual = None if p is None or not p.get("_played") else p.get(stat)
        out.append({"game_key": gk, "player_id": pid, "player": first["player"], "stat": stat, "line": line, "version": first["version"],
                    "n_snaps": len(pre), "first": {k: first[k] for k in ("ts", "p_model", "p_mkt", "over_odds", "under_odds", "mu")},
                    "last": {k: last[k] for k in ("ts", "p_model", "p_mkt", "over_odds", "under_odds", "mu")},
                    "actual": actual, "void": actual is None,
                    "won_over": None if actual is None else (None if actual == line else float(actual > line)),
                    "settled_ts": now.isoformat()})
    return out


def cmd_settle(a) -> int:
    from ev import grade_props as gp
    now = datetime.now(timezone.utc)
    days = [a.day] if a.day else [(now.astimezone(_CT).date() - timedelta(days=d)).isoformat() for d in (1, 0)]
    for day in days:
        snaps = read_jsonl(SNAP_KEY.format(day=day))
        if not snaps:
            continue
        done = {(r["game_key"], r["player_id"], r["stat"], r["line"]) for r in read_jsonl(SETTLED_KEY.format(day=day))}
        todo = [s for s in snaps if (s["game_key"], s["player_id"], s["stat"], s["line"]) not in done]
        new = settle_records(todo, gp.nhl_actuals, now)
        log.info("%s: %d snapshots, %d newly settled props", day, len(snaps), len(new))
        if new and not a.no_write:
            key = SETTLED_KEY.format(day=day)
            write_jsonl(key, read_jsonl(key) + new)
    return 0


# ------------------------------------------------------------------------------------------------------------ report
def _ret(odds, win: bool) -> float:
    from mlb_core.odds.utils import american_to_decimal
    return american_to_decimal(int(odds)) - 1.0 if win else -1.0


def report_from(settled: list) -> dict:
    from mlb_core.odds import model_vs_market as MVM
    out = {}
    for stat in STATS:
        rs = [r for r in settled if r["stat"] == stat and r["won_over"] is not None]
        if not rs:
            continue
        y = np.array([r["won_over"] for r in rs])
        pm = np.array([r["last"]["p_model"] for r in rs])
        pk = np.array([r["last"]["p_mkt"] for r in rs])
        cl = [r["game_key"] for r in rs]
        sc = MVM.score_model_vs_market(y, np.clip(pm, 0.005, 0.995), np.clip(pk, 0.005, 0.995), cl)
        bets = []
        for r in rs:
            diff = r["first"]["p_model"] - r["first"]["p_mkt"]
            if abs(diff) < GAP:
                continue
            over = diff > 0
            odds = r["first"]["over_odds"] if over else r["first"]["under_odds"]
            if odds is None:
                continue
            win = (r["won_over"] == 1.0) if over else (r["won_over"] == 0.0)
            side_first = r["first"]["p_mkt"] if over else 1 - r["first"]["p_mkt"]
            side_last = r["last"]["p_mkt"] if over else 1 - r["last"]["p_mkt"]
            bets.append({"over": over, "ret": _ret(odds, win), "win": win, "clv": side_last - side_first, "game": r["game_key"]})
        b = {"n": len(bets)}
        if bets:
            rets = np.array([x["ret"] for x in bets])
            g = defaultdict(list)
            for x in bets:
                g[x["game"]].append(x["ret"])
            gm = np.array([np.sum(v) for v in g.values()])
            se = float(np.sqrt(np.sum((gm - gm.mean()) ** 2) / max(len(gm) - 1, 1)) * len(gm) / len(bets)) if len(gm) > 1 else float("nan")
            b.update(roi=float(rets.mean()), roi_se=se, win=float(np.mean([x["win"] for x in bets])),
                     clv_mean=float(np.mean([x["clv"] for x in bets])),
                     clv_pos=float(np.mean([x["clv"] > 0 for x in bets])),
                     under_share=float(np.mean([not x["over"] for x in bets])))
        out[stat] = {"n_props": len(rs), "n_games": int(sc["n_clusters"]), "over_rate": sc["over_rate"],
                     "logloss_model": sc["logloss_model"], "logloss_market": sc["logloss_market"],
                     "blend_c": (sc.get("blend") or {}).get("c"), "blend_t": (sc.get("blend") or {}).get("t_c"), "bets": b}
    return out


def cmd_report(a) -> int:
    now = datetime.now(timezone.utc)
    days = [(now.astimezone(_CT).date() - timedelta(days=d)).isoformat() for d in range(a.days)]
    settled = [r for d in days for r in read_jsonl(SETTLED_KEY.format(day=d))]
    rep = report_from(settled)
    print(json.dumps({"days": a.days, "settled_props": len(settled), "stats": rep}, indent=1, default=lambda o: float(o)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--no-write", action="store_true")
    t = sub.add_parser("settle")
    t.add_argument("--day", default=None, help="Central game day (default: yesterday and today)")
    t.add_argument("--no-write", action="store_true")
    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return {"snapshot": cmd_snapshot, "settle": cmd_settle, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
