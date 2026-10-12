"""
ev.sweep_nfl_props -- the NFL player-prop sweep: book consensus (ParlayAPI) vs Kalshi prop markets.

Each cycle:  ParlayAPI slate (1 credit) -> per-upcoming-event props (1 credit/market) -> per-book 2-way
de-vig -> median consensus per (player, stat, point)  ->  join to EVERY open Kalshi NFL prop market at the
same point  ->  log every comparable row (the evidence table)  ->  flag net-of-fee EV candidates  ->
Discord alert (new ones only, per day)  ->  write latest.json for the executor.

SHADOW ONLY: this module never places an order. Run (Cloud Run job):
  python -m ev.sweep_nfl_props --loop-minutes 15 --until 2026-10-06T05:00:00Z
Outputs (GCS, via mlb_core.storage):
  EVSweep/nfl_props/{ET day}/cycle_{HHMMSS}.csv    every comparable row
  EVSweep/nfl_props/raw/{ET day}/{event}_{HHMMSS}.json   raw ParlayAPI payload (backtest/survival data)
  EVSweep/nfl_props/latest.json                    rows for the executor
Discord: DISCORD_WEBHOOK_ALERTS, else DISCORD_WEBHOOK_OPS (admin-only). NEVER the member channels:
these signals are unvalidated.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ev.kalshi_map import game_key
from ev.kalshi_props import PROP_SERIES, PropLeg, map_prop_market
from ev.props import build_consensus, parse_event_props
from ev.props_config import props_config
from ev.sports import NFL
from mlb.execution.kalshi.client import PUBLIC_DATA_ENV, KalshiClient
from mlb.execution.kalshi.fees import taker_fee
from mlb.execution.kalshi.mapping import MapFail

log = logging.getLogger("ev.sweep_nfl_props")
SPORT = "americanfootball_nfl"
_ET = ZoneInfo("America/New_York")
PREFIX = "EVSweep/nfl_props"
# markets requested from ParlayAPI per event (1 credit each). Alternate ladders are ONE-SIDED prices (not
# decision-grade without an NFL-calibrated vig) so they are not requested in v1 -- ~45% fewer credits.
PARLAY_MARKETS = ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions",
                  "player_pass_tds"]


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def event_game(ev: dict):
    """ParlayAPI slate event -> (game_key, commence datetime UTC) or None if a team name is unknown."""
    from ev.sports.nfl import TEAM_NAMES
    away, home = TEAM_NAMES.get(ev.get("away_team")), TEAM_NAMES.get(ev.get("home_team"))
    if not away or not home or not ev.get("commence_time"):
        return None
    start = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
    return game_key("nfl", start.astimezone(_ET).date().isoformat(), away, home), start


def side_eval(p_side: float, ask, mult: float, cfg, h: float):
    """(net_ev, p_hat, cost) buying a side at `ask` with shrunk prob; None if no usable ask."""
    if ask is None or not (0.0 < ask < 1.0):
        return None
    fee_pc = taker_fee(ask, 100, mult) / 100.0
    p_hat = p_side - h
    cost = ask + fee_pc
    return (p_hat / cost - 1.0, p_hat, cost)


def classify(row: dict, cfg) -> tuple[bool, str, str | None]:
    """(flag, reason, side) for one comparable row, using cfg gates. Mirrors the executor's early gates."""
    if row["n_books"] < cfg.n_min_books:
        return False, "THIN_CONS", None
    if row["disp"] > cfg.disp_max:
        return False, "DISPERSED", None
    if row["mins_to_kick"] < cfg.close_buffer_min:
        return False, "NEAR_KICK", None
    best = None
    for side in ("yes", "no"):
        ev, ask = row[f"ev_{side}"], row[f"ask_{side}"]
        if ev is None or not (cfg.price_min <= ask <= cfg.price_max):
            continue
        if best is None or ev > best[1]:
            best = (side, ev)
    if best is None:
        return False, "NO_ASK_IN_BAND", None
    side, ev = best
    if row["spread"] is None or row["spread"] > cfg.spread_max:
        return False, "WIDE_SPREAD", side
    if ev > cfg.ev_cap:
        return False, "EV_TOO_HIGH", side
    if ev < cfg.ev_min:
        return False, "EV_LOW", side
    return True, "FLAG", side


def consensus_snapshot(parlay_events: list):
    """Book side only (no Kalshi needed): -> (snapshot, stats). snapshot = JSON-safe list, one entry per
    book event with every (player, stat, point) consensus. This is what the cloud job publishes, so the join to
    Kalshi can happen anywhere Kalshi is reachable (Cloud Run egress IPs are blocked by Kalshi's CDN)."""
    snap, stats = [], {"events": 0, "unmapped_keys": {}}
    for pe in parlay_events:
        eg = event_game(pe["event"])
        if eg is None:
            stats.setdefault("unknown_teams", []).append((pe["event"].get("away_team"), pe["event"].get("home_team")))
            continue
        gk, start = eg
        stats["events"] += 1
        quotes, meta = parse_event_props(pe["props"])
        for k, v in meta["unmapped_keys"].items():
            stats["unmapped_keys"][k] = stats["unmapped_keys"].get(k, 0) + v
        points = [{"player_norm": pn, "stat": st, "point": pt, "p_over": c.p_over, "n_books": c.n_books,
                   "disp": c.disp, "books": list(c.books), "n_onesided": c.n_onesided, "n_dfs": c.n_dfs,
                   "team_hints": list(c.team_hints), "book_fair": dict(c.book_fair)} for (pn, st, pt), c in build_consensus(quotes).items()]
        snap.append({"game_key": gk, "commence": start.isoformat(), "points": points})
    return snap, stats


def rows_from_snapshot(snap: list, legs_by_game: dict, now: datetime, cfg, fee_mult: float = 1.0):
    """Join a consensus snapshot to Kalshi prop markets. One row per Kalshi market that has a book consensus at
    the same point; (rows, stats)."""
    rows, stats = [], {"kalshi_legs_seen": 0, "matched": 0, "unmatched_no_book": 0, "team_mismatch": 0,
                       "books_per_match": []}
    for ev in snap:
        gk, start = ev["game_key"], datetime.fromisoformat(ev["commence"])
        idx = {(p["player_norm"], p["stat"], p["point"]): p for p in ev["points"]}
        mins = (start - now).total_seconds() / 60.0
        for leg, mk in legs_by_game.get(gk, []):
            stats["kalshi_legs_seen"] += 1
            c = idx.get((leg.player_norm, leg.stat, leg.strike))
            if c is None:
                stats["unmatched_no_book"] += 1
                continue
            if c["team_hints"] and leg.team not in c["team_hints"]:
                stats["team_mismatch"] += 1
                continue
            stats["matched"] += 1
            stats["books_per_match"].append(c["n_books"])
            ya, na = _f(mk.get("yes_ask_dollars")), _f(mk.get("no_ask_dollars"))
            yb = _f(mk.get("yes_bid_dollars"))
            spread = round(ya - yb, 4) if ya is not None and yb is not None else None
            h = cfg.h0 + cfg.h_disp * c["disp"]
            ey, ny = side_eval(c["p_over"], ya, fee_mult, cfg, h), side_eval(1.0 - c["p_over"], na, fee_mult, cfg, h)
            row = {
                "ts": now.isoformat(), "ticker": leg.ticker, "game_key": gk, "stat": leg.stat,
                "player": leg.player, "player_norm": leg.player_norm, "team": leg.team, "strike": leg.strike,
                "commence": start.isoformat(), "mins_to_kick": round(mins, 1),
                "p_over": round(c["p_over"], 5), "n_books": c["n_books"], "disp": round(c["disp"], 5),
                "books": ",".join(c["books"]), "n_onesided": c["n_onesided"], "n_dfs": c["n_dfs"],
                "ask_yes": ya, "ask_no": na, "bid_yes": yb, "spread": spread,
                "ask_yes_size": _f(mk.get("yes_ask_size_fp")), "oi": _f(mk.get("open_interest_fp")),
                "vol24": _f(mk.get("volume_24h_fp")),
                "ev_yes": None if ey is None else round(ey[0], 5), "ev_no": None if ny is None else round(ny[0], 5),
                "fee_mult": fee_mult,
                "ranges": json.dumps([[r.start, r.end, r.step] for r in leg.ranges]),
                "exchange_index": leg.exchange_index,
            }
            flag, reason, side = classify(row, cfg)
            row.update(flag=flag, reason=reason, side=side)
            rows.append(row)
    return rows, stats


def compute_rows(parlay_events: list, legs_by_game: dict, now: datetime, cfg, fee_mult: float = 1.0):
    """parlay_events: [{'event': slate_event, 'props': single-event odds obj}] -> (rows, stats)."""
    snap, s1 = consensus_snapshot(parlay_events)
    rows, s2 = rows_from_snapshot(snap, legs_by_game, now, cfg, fee_mult)
    return rows, {**s1, **s2}


# ----------------------------------------------------------------------------- IO
class KalshiBlocked(RuntimeError):
    """Kalshi's CDN refused every series from this egress IP (Cloud Run IPs are blocked per-IP, 2026-10-04)."""


def fetch_kalshi_legs(client: KalshiClient):
    """-> ({game_key: [(PropLeg, market_dict)]}, fee_multiplier, {reason: n}). Raises KalshiBlocked if
    every series fetch failed (checked BEFORE any ParlayAPI credit is spent)."""
    legs, fails, mult = {}, {}, 1.0
    errors = 0
    for series in PROP_SERIES:
        try:
            ser = client.get_series(series) or {}
            mult = float((ser.get("series") or ser).get("fee_multiplier", 1.0) or 1.0)
            markets = client.get_markets(series)
        except Exception as e:                                                      # noqa: BLE001
            log.error("kalshi fetch %s failed: %r", series, str(e)[:160])
            errors += 1
            continue
        for mk in markets:
            leg = map_prop_market(mk)
            if isinstance(leg, MapFail):
                fails[leg.reason] = fails.get(leg.reason, 0) + 1
                continue
            legs.setdefault(leg.game_key, []).append((leg, mk))
    if errors == len(PROP_SERIES):
        raise KalshiBlocked("all Kalshi series fetches failed from this egress IP")
    return legs, mult, fails


def webhook_url():
    return os.environ.get("DISCORD_WEBHOOK_ALERTS") or os.environ.get("DISCORD_WEBHOOK_OPS") or ""


def post_discord(text: str, embeds=None) -> bool:
    url = webhook_url()
    if not url:
        log.warning("no Discord webhook configured; alert not posted")
        return False
    try:
        r = requests.post(url, json={"content": text[:1900], "embeds": embeds or []}, timeout=20)
        return r.status_code < 300
    except requests.RequestException as e:
        log.warning("discord post failed: %r", e)
        return False


def alert_embed(rows: list) -> list:
    fields = []
    by_game: dict = {}
    for r in sorted(rows, key=lambda r: -(r["ev_yes"] if r["side"] == "yes" else r["ev_no"])):
        by_game.setdefault(r["game_key"], []).append(r)
    for gk, rs in list(by_game.items())[:20]:
        lines = []
        for r in rs[:8]:
            side = r["side"]
            ask = r[f"ask_{side}"]
            fair = r["p_over"] if side == "yes" else 1 - r["p_over"]
            venue = f"[{r['venue'].upper()}] " if r.get("venue") else ""
            lines.append(f"{venue}**{r['player']}** {r['stat']} {int(r['strike'] + 0.5)}+  BUY {side.upper()} @ {ask:.2f} | "
                         f"fair {fair:.2f} | net EV {r['ev_' + side] * 100:+.1f}% | {r['n_books']} bk | "
                         f"spr {r['spread']} | OI {int(r['oi'] or 0)}" + (" | ZERO pre-game fee" if r.get("venue") == "novig" else ""))
        fields.append({"name": gk.replace("nfl:", ""), "value": "\n".join(lines)[:1000]})
    return [{"title": f"NFL props: {len(rows)} Kalshi candidate(s) (SHADOW / unvalidated)",
             "description": "Net of Kalshi fee and shrinkage. Paper-tracked on a $1,000 notional bankroll; "
                            "NOT a recommendation.", "fields": fields, "color": 0xE6915D}]


def _write(data: bytes, key: str):
    from mlb_core.storage import write_bytes
    write_bytes(data, key)


def _read(key: str):
    from mlb_core.storage import exists, read_bytes
    return read_bytes(key) if exists(key) else None


def notified_set(state: dict, day: str) -> set:
    if day not in state:
        try:
            raw = _read(f"{PREFIX}/{day}/notified.json")
            state[day] = set(json.loads(raw)) if raw else set()
        except Exception:                                                                # noqa: BLE001
            state[day] = set()
    return state[day]


def post_new(rows: list, state: dict, day: str) -> int:
    """Post rows not yet notified today (dedup persisted in GCS so task restarts never re-alert)."""
    notified = notified_set(state, day)
    new = [r for r in rows if f"{r['ticker']}|{r['side']}" not in notified]
    if not new:
        return 0
    ok = True
    for i in range(0, len(new), 20):
        batch = new[i:i + 20]
        if post_discord(f"NFL props sweep: {len(batch)} new candidate(s)", alert_embed(batch)):
            notified.update(f"{r['ticker']}|{r['side']}" for r in batch)
        else:
            ok = False
    try:
        _write(json.dumps(sorted(notified)).encode(), f"{PREFIX}/{day}/notified.json")
    except Exception as e:                                                               # noqa: BLE001
        log.warning("notified write failed: %r", e)
    return len(new) if ok else 0


def process_pending_alerts(state: dict) -> int:
    """Candidates flagged by the LOCAL executor (which can reach Kalshi) are posted from here, because this
    job already holds the Discord webhook -- no webhook secret ever leaves Cloud Run."""
    raw = _read(f"{PREFIX}/alerts_pending.json")
    if not raw:
        return 0
    rows = json.loads(raw)
    if not rows:
        return 0
    day = datetime.now(timezone.utc).astimezone(_ET).date().isoformat()
    n = post_new(rows, state, day)
    _write(b"[]", f"{PREFIX}/alerts_pending.json")
    return n


def run_cycle(pc, kc, cfg, cycle: int, horizon_h: float, state: dict) -> dict:
    now = datetime.now(timezone.utc)
    day = now.astimezone(_ET).date().isoformat()
    hhmmss = now.strftime("%H%M%S")
    slate = pc.get_slate(SPORT, markets=["h2h"]) or []
    start_cutoff = now + timedelta(minutes=cfg.close_buffer_min)
    upcoming = []
    for ev in slate:
        eg = event_game(ev)
        if eg is None:
            continue
        _, start = eg
        mins = (start - now).total_seconds() / 60.0
        if start <= start_cutoff or mins > horizon_h * 60:
            continue
        if mins <= 360 or cycle % 4 == 0:                     # near games every cycle, far games every 4th
            upcoming.append(ev)
    parlay_events = []
    for ev in upcoming:
        props = pc.get_event_props(SPORT, ev["id"], PARLAY_MARKETS)
        if props:
            parlay_events.append({"event": ev, "props": props})
            try:
                _write(json.dumps(props).encode(), f"{PREFIX}/raw/{day}/{ev['id']}_{hhmmss}.json")
            except Exception as e:                                                      # noqa: BLE001
                log.warning("raw write failed: %r", e)
    snap, sstats = consensus_snapshot(parlay_events)
    # Book consensus is ALWAYS published: the join to Kalshi can then happen anywhere Kalshi is reachable.
    _write(json.dumps({"ts": now.isoformat(), "cycle": cycle, "events": snap}).encode(), f"{PREFIX}/consensus.json")
    n_pts = [len([p for p in e["points"] if p["n_books"] >= cfg.n_min_books]) for e in snap]
    rows, jstats, kfails, kalshi_ok = [], {}, {}, True
    try:
        legs, mult, kfails = fetch_kalshi_legs(kc)
        rows, jstats = rows_from_snapshot(snap, legs, now, cfg, mult)
    except KalshiBlocked:
        kalshi_ok = False                                  # expected on blocked egress IPs; local executor joins
    flagged = [r for r in rows if r["flag"]]
    new = post_new(flagged, state, day) if flagged else 0
    bpm = jstats.pop("books_per_match", [])
    summary = {"cycle": cycle, "ts": now.isoformat(), "events_pulled": len(parlay_events),
               "slate_events": len(slate), "kalshi_reachable": kalshi_ok, "points_ge_min_books": sum(n_pts),
               "rows": len(rows), "flagged": len(flagged), "new_alerts": new,
               "credits_remaining": pc.credits_remaining,
               "books_per_match_hist": {str(n): bpm.count(n) for n in sorted(set(bpm))},
               "kalshi_map_fails": kfails, **sstats, **jstats}
    log.info("CYCLE %s", json.dumps(summary, default=str))
    if rows:
        _write(pd.DataFrame(rows).to_csv(index=False).encode(), f"{PREFIX}/{day}/cycle_{hhmmss}.csv")
        _write(json.dumps({"ts": now.isoformat(), "cycle": cycle, "summary": summary, "rows": rows},
                          default=str).encode(), f"{PREFIX}/latest.json")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop-minutes", type=float, default=0, help="0 = run one cycle and exit")
    ap.add_argument("--until", default="", help="ISO UTC stop time for the loop")
    ap.add_argument("--horizon-hours", type=float, default=40.0)
    ap.add_argument("--max-credits", type=int, default=9000, help="stop when this many credits are spent")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from nba.odds.parlayapi import ParlayApiClient
    pc, kc, cfg = ParlayApiClient(), KalshiClient(PUBLIC_DATA_ENV, None, None), props_config()
    kc.max_retries = 1                       # a blocked egress IP stays blocked; do not burn minutes retrying
    until = datetime.fromisoformat(a.until.replace("Z", "+00:00")) if a.until else None
    state: dict = {}
    first_rem, cycle, next_cycle = None, 0, 0.0
    post_discord("NFL props SHADOW sweep running (unvalidated signals; alerts go to this admin channel only).")
    while True:
        if time.time() >= next_cycle:
            try:
                run_cycle(pc, kc, cfg, cycle, a.horizon_hours, state)
                first_rem = first_rem or (int(pc.credits_remaining) if pc.credits_remaining else None)
            except Exception:                                                            # noqa: BLE001
                log.exception("cycle %s failed", cycle)
            cycle += 1
            next_cycle = time.time() + a.loop_minutes * 60
            if first_rem and pc.credits_remaining and first_rem - int(pc.credits_remaining) >= a.max_credits:
                log.error("credit budget for this run exhausted (%s spent); stopping", a.max_credits)
                post_discord("NFL props sweep stopped: run credit budget exhausted.")
                return 0
        if a.loop_minutes <= 0 or (until and datetime.now(timezone.utc) >= until):
            return 0
        try:
            n = process_pending_alerts(state)
            if n:
                log.info("posted %s pending alert(s) from the local executor", n)
        except Exception:                                                                # noqa: BLE001
            log.exception("pending-alert processing failed")
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
