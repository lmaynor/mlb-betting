"""
ev.sweep_props_alerts -- NHL / NBA player-prop +EV ALERTS (Sink A only): a sportsbook quote that beats the
leave-self-out consensus of the OTHER real books at the same (player, stat, point).

No Kalshi leg, no orders, no model. Evidence + admin-channel alerts only: the signals are UNVALIDATED (the only
positive paper evidence behind this idea is MLB soft-book vs Pinnacle-anchored consensus, +7.56%, itself unverified
for execution). Every flagged row goes into a daily thread under the sport's alerts channel (#nhl-alerts / #nba-alerts) via the
bot; the admin DISCORD_WEBHOOK_ALERTS hook is the fallback, log-only if neither. The Beezy-Score-gated subset
(mlb_core.alerts.sport_alerts, labelled paper/unvalidated) is also posted top-level as a highlight. NEVER a +EV channel or
#ops-alerts.

ZERO EXTRA ODDS CREDITS: this reads the newest props snapshot the odds banker already bought
(nba/odds/accumulator.py -> OddsAccum/{sport}/raw/{date}/props_{HHMM}.json, pointer latest_props.json). Run it a few
minutes after each banker run (deploy/setup_props_alerts.sh). A snapshot older than --max-age-min is REFUSED: a stale
quote is exactly the adverse-selection trap (the book already moved), so "no data" beats "old data".

Player identity: quotes are resolved to the league's own player id (NHL roster crosswalk / NBA ESPN crosswalk) so
"Alex Ovechkin" / "Alexander Ovechkin" / "Alex Ovechkin (WSH)" form ONE consensus group; ambiguous names are dropped.
An event whose team is missing from the crosswalk falls back to plain normalized names (never a wrong merge).

Outputs (GCS via mlb_core.storage):
  EVSweep/{sport}_props/{ET day}/book_outliers_{HHMMSS}.csv   every outlier row (the grading evidence)
  EVSweep/{sport}_props/latest.json                           flagged + top rows + summary (public +EV feed)
  EVSweep/{sport}_props/{ET day}/notified.json                alert dedup (never re-ping the same quote today)
Run:  python -m ev.sweep_props_alerts --sport nhl          (one pass, then exit)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from ev.props import parse_event_props, scan_book_outliers
from mlb_core.alerts.discord_routes import post_props_signal, webhook_for, with_deep_links

log = logging.getLogger("ev.sweep_props_alerts")
_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PropSport:
    sport: str                 # 'nhl' | 'nba'
    label: str
    parlay_key: str            # OddsAccum/{parlay_key}/...
    prefix: str                # EVSweep/{sport}_props
    min_books_scan: int        # leave-self-out books needed to LOG a row as evidence
    alert_min_books: int       # ... needed to ALERT (>= min_books_scan)


SPORTS = {
    # NHL has only ~3-4 real two-sided books pre-game, so evidence is logged from 2 others but alerts need 3.
    "nhl": PropSport("nhl", "NHL", "icehockey_nhl", "EVSweep/nhl_props", 2, 3),
    "nba": PropSport("nba", "NBA", "basketball_nba", "EVSweep/nba_props", 3, 3),
}

# alert gates (UNVALIDATED starting points; the evidence CSVs are what get graded)
MIN_EV = 0.03
EV_CAP = 0.15            # an apparent edge this big is far more often reference error than value (repo winner's-curse rule)
MAX_DISP = 0.06
CLOSE_BUFFER_MIN = 20.0
MAX_AGE_MIN = 45.0


# ----------------------------------------------------------------------------- sport plumbing (lazy imports)
def team_fn(spec: PropSport):
    if spec.sport == "nhl":
        from nhl.config import team_abbrev
        return team_abbrev
    from nba.teams import team_code
    return team_code


def load_resolver(spec: PropSport):
    """-> (resolver | None, covered_team_codes | None). A missing/unreadable crosswalk degrades to None (alerts still
    run on normalized names) -- never a crash."""
    from mlb_core import storage
    try:
        if spec.sport == "nhl":
            from nhl.config import PLAYERS_MASTER
            from nhl.data import players
            if not storage.exists(PLAYERS_MASTER):
                return None, None
            rows = storage.read_csv(PLAYERS_MASTER).to_dict("records")
            return players.build_resolver(rows), players.covered_teams(rows)
        from nba.data import espn_players as ep
        if not storage.exists(ep.PLAYERS_XWALK):
            return None, None
        df = storage.read_csv(ep.PLAYERS_XWALK)
        return ep.build_resolver(df), set(df["team"].dropna())
    except Exception:                                                                    # noqa: BLE001
        log.exception("crosswalk load failed for %s; continuing without a resolver", spec.sport)
        return None, None


# ----------------------------------------------------------------------------- bank access
def read_latest_props(spec: PropSport, now: datetime, max_age_min: float = MAX_AGE_MIN):
    """-> (events | None, meta). Refuses a snapshot older than max_age_min."""
    from mlb_core import storage
    from nba.config import oddsaccum_latest_kind_key, oddsaccum_raw_key
    ptr_key = oddsaccum_latest_kind_key(spec.parlay_key, "props")
    if not storage.exists(ptr_key):
        return None, {"reason": "no latest_props.json pointer yet (banker has not run)"}
    ptr = json.loads(storage.read_bytes(ptr_key))
    ts = datetime.fromisoformat(str(ptr["timestamp"]).replace("Z", "+00:00"))
    age = (now - ts).total_seconds() / 60.0
    meta = {"snapshot_ts": ts.isoformat(), "age_min": round(age, 1), "banked_events": ptr.get("events_priced")}
    if age > max_age_min:
        return None, {**meta, "reason": f"snapshot is {age:.0f} min old (> {max_age_min:.0f}); refusing stale quotes"}
    raw_key = oddsaccum_raw_key(spec.parlay_key, ptr["date"], "props", ptr["hhmm"])
    if not storage.exists(raw_key):
        return None, {**meta, "reason": f"raw snapshot missing: {raw_key}"}
    return json.loads(storage.read_bytes(raw_key)), meta


# ----------------------------------------------------------------------------- core
def outlier_rows(spec: PropSport, events: list, now: datetime, resolver=None, covered=None) -> tuple[list, dict]:
    """Scan every banked event. Returns (rows, stats). Each row is one outlier quote with full inputs + a `flag`."""
    tf = team_fn(spec)
    rows, stats = [], {"events": 0, "events_skipped_teams": 0, "events_resolver_off": 0, "ambiguous": 0,
                       "unknown_names": 0, "unmapped_keys": {}}
    for ev in events:
        away, home = tf(ev.get("away_team")), tf(ev.get("home_team"))
        try:
            start = datetime.fromisoformat(str(ev.get("commence_time")).replace("Z", "+00:00"))
        except ValueError:
            stats["events_skipped_teams"] += 1
            continue
        if not away or not home:
            stats["events_skipped_teams"] += 1
            continue
        stats["events"] += 1
        use_resolver = resolver
        if resolver is not None and covered is not None and not ({away, home} <= set(covered)):
            use_resolver = None                    # partial crosswalk: a missing roster could hide a namesake
            stats["events_resolver_off"] += 1
        quotes, meta = parse_event_props(ev, spec.sport, use_resolver, tf)
        stats["ambiguous"] += meta["unresolved"]["ambiguous"]
        stats["unknown_names"] += meta["unresolved"]["unknown"]
        for k, v in meta["unmapped_keys"].items():
            stats["unmapped_keys"][k] = stats["unmapped_keys"].get(k, 0) + v
        mins = (start - now).total_seconds() / 60.0
        gk = f"{spec.sport}:{start.astimezone(_ET).date().isoformat()}:{away}@{home}"
        for r in scan_book_outliers(quotes, min_books=spec.min_books_scan, min_ev=MIN_EV, max_disp=MAX_DISP):
            flag = (r["n_books"] >= spec.alert_min_books and r["ev"] <= EV_CAP and mins >= CLOSE_BUFFER_MIN)
            reason = ("FLAG" if flag else "THIN_BOOKS" if r["n_books"] < spec.alert_min_books
                      else "EV_TOO_HIGH" if r["ev"] > EV_CAP else "NEAR_START")
            rows.append({"sport": spec.sport, "ts": now.isoformat(), "game_key": gk, "event_id": ev.get("id"),
                         "commence": start.isoformat(), "mins_to_start": round(mins, 1), "away": away, "home": home,
                         **r, "flag": flag, "reason": reason})
    return sorted(rows, key=lambda r: (-int(r["flag"]), -r["ev"])), stats


# ----------------------------------------------------------------------------- discord (daily thread under the card channel)
def webhook_url() -> str:
    # Only the bot-down fallback: the shared admin hook (DISCORD_WEBHOOK_ALERTS). Never a member channel and never #ops-alerts,
    # which is for failures.
    return webhook_for("props_alerts") or ""


def post_discord(text: str, embeds=None, sport: str | None = None) -> bool:
    """Alerts go into the sport's daily signals thread under its card channel (post_props_signal); log-only when nothing took it."""
    ok = post_props_signal(sport or "nhl", text, embeds)
    if not ok:
        log.warning("props signal not posted (no bot, no admin hook): %s", text[:120])
    return ok


def alert_embed(spec: PropSport, rows: list) -> list:
    from mlb_core.notify.discord import book_display
    by_game: dict = {}
    for r in rows:
        by_game.setdefault(r["game_key"], []).append(r)
    fields = []
    for gk, rs in list(by_game.items())[:20]:
        lines = []
        for r in sorted(rs, key=lambda x: -x["ev"])[:8]:
            lines.append(f"**{r['player']}** {r['stat']} {r['side'][0]} {r['point']:g} @ {r['american']:+d} "
                         f"({book_display(r['book'])}) | fair {r['p_fair']:.2f} | EV {r['ev'] * 100:+.1f}% | "
                         f"{r['n_books']} bk")
        fields.append({"name": gk.split(":", 1)[1], "value": "\n".join(lines)[:1000]})
    return [with_deep_links({"title": f"{spec.label} props: {len(rows)} soft-line candidate(s) (UNVALIDATED)",
                             "description": "A book quote beating the consensus of the other books. Evidence-tracked and graded; "
                                            "NOT a recommendation.", "fields": fields, "color": 0x38BDF8})]


def _key(r: dict) -> str:
    return f"{r['game_key']}|{r['player_norm']}|{r['stat']}|{r['point']}|{r['side']}|{r['book']}"


def post_new(spec: PropSport, rows: list, day: str, discord: bool = True) -> int:
    from mlb_core import storage
    nkey = f"{spec.prefix}/{day}/notified.json"
    try:
        notified = set(json.loads(storage.read_bytes(nkey))) if storage.exists(nkey) else set()
    except Exception:                                                                    # noqa: BLE001
        notified = set()
    new = [r for r in rows if r["flag"] and _key(r) not in notified]
    if not new or not discord:
        return 0
    sent = 0
    # Member channel (#<sport>-alerts): the Beezy-Score-gated subset, labelled paper/unvalidated. Admin still gets all.
    from mlb_core.alerts.sport_alerts import gate, post_sport_alerts
    member = gate(new)
    if member and post_sport_alerts(spec.sport, member):
        notified.update(_key(r) for r in member)
    for i in range(0, len(new), 20):
        batch = new[i:i + 20]
        if post_discord(f"{spec.label} props sweep: {len(batch)} new candidate(s)", alert_embed(spec, batch), spec.sport):
            notified.update(_key(r) for r in batch)
            sent += len(batch)
    try:
        storage.write_bytes(json.dumps(sorted(notified)).encode(), nkey)
    except Exception as e:                                                               # noqa: BLE001
        log.warning("notified write failed: %r", e)
    return sent


# ----------------------------------------------------------------------------- one pass
def run_once(spec: PropSport, now: datetime = None, max_age_min: float = MAX_AGE_MIN, discord: bool = True) -> dict:
    from mlb_core import storage
    now = now or datetime.now(timezone.utc)
    events, meta = read_latest_props(spec, now, max_age_min)
    if events is None:
        log.warning("%s sweep skipped: %s", spec.label, meta.get("reason"))
        return {"status": "skipped", **meta}
    resolver, covered = load_resolver(spec)
    rows, stats = outlier_rows(spec, events, now, resolver, covered)
    day = now.astimezone(_ET).date().isoformat()
    hhmmss = now.strftime("%H%M%S")
    sent = post_new(spec, rows, day, discord)
    summary = {"status": "ok", "ts": now.isoformat(), "sport": spec.sport, **meta, **stats,
               "rows": len(rows), "flagged": sum(1 for r in rows if r["flag"]), "alerts_sent": sent,
               "resolver": resolver is not None, "validated": False}
    log.info("SWEEP %s", json.dumps(summary, default=str))
    if rows:
        # all_books (the per-book strip) is nested JSON: it rides latest.json for the UI, not the flat evidence CSV
        storage.write_bytes(pd.DataFrame(rows).drop(columns=["all_books"], errors="ignore").to_csv(index=False).encode(),
                            f"{spec.prefix}/{day}/book_outliers_{hhmmss}.csv")
    storage.write_bytes(json.dumps({"ts": now.isoformat(), "summary": summary, "rows": rows[:300]}, default=str).encode(),
                        f"{spec.prefix}/latest.json")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", choices=sorted(SPORTS), required=True)
    ap.add_argument("--max-age-min", type=float, default=MAX_AGE_MIN)
    ap.add_argument("--no-discord", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_once(SPORTS[a.sport], max_age_min=a.max_age_min, discord=not a.no_discord)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
