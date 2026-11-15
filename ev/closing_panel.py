"""
ev.closing_panel -- bank a daily NHL/NBA player-props CLOSING panel from the DEC-08 accumulator's raw snapshots.

WHY (AUT-01 step c, Nov 15, 2026): the Autopilot execution backtest could not settle same-book price CLV for the
eligible markets because no true closing panel exists for NHL/NBA Novig props -- CLV rested on the last accumulator
snapshot before commence as a proxy, and on consensus closes. This module turns the already-banked raw snapshots
(OddsAccum/{icehockey_nhl,basketball_nba}/raw/{ET day}/props_{HHMM}.json, snapshot time = folder ET date + HHMM ET)
into a named, daily artifact:

    ClosingPanel/{nhl|nba}/{ET day}.json.gz

THE RULE (what "closing" means in this panel). For every event whose commence_time falls on the ET day:
  * close mark = commence_time;  t15 mark = commence_time - 15 minutes (Argon RUL-20261115-autopilot-clv-definition
    defines the same-book measure against Novig's T-15m quote, so the panel carries both marks).
  * At each mark, per (market, player, point) and per book: the book's prices from the LATEST snapshot at or before
    the mark in which that book quoted that market/player/point (per-book last quote, not one snapshot for all
    books), with the snapshot timestamp used.
  * Novig's quote at each mark is lifted out (over/under American prices + snapshot ts + staleness in minutes).
  * Consensus at each mark: per-book two-way devigged over-probability from those last quotes, Novig EXCLUDED;
    cons_p_over = median across books, cons_n_books = the count (mirrors the backtest's Design B definition).

Snapshots are scanned from the ET folders day-1 and day (an event's pre-commence snapshots live in those two
folders). Rows are one per (event, market, player, point). Prices are American odds as banked by the accumulator.

PAPER / MEASUREMENT ONLY: reads and writes GCS objects; no venue calls, no orders, no signal or card path touched.

Usage:  python -m ev.closing_panel [--day YYYY-MM-DD] [--sport nhl|nba|both] [--store gcs|gcloud]
        (default day = yesterday ET; default store gcs = ADC, i.e. the job's service account)
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ev.novig.shadow_bank import make_store

_ET = ZoneInfo("America/New_York")
SPORT_KEYS = {"nhl": "icehockey_nhl", "nba": "basketball_nba"}
PANEL_PREFIX = "ClosingPanel"
SCHEMA = 1
_FILE_RE = re.compile(r"props_(\d{4})\.json$")

RULE = ("close mark = commence_time, t15 mark = commence_time - 15min; per (market, player, point) and book, "
        "prices from the latest OddsAccum snapshot at or before the mark quoting that line, with the snapshot "
        "timestamp; consensus = leave-Novig-out median of per-book devigged over-probabilities at the mark")


def _snap_ts(folder_day: str, hhmm: str) -> datetime:
    et = datetime.strptime(folder_day + hhmm, "%Y-%m-%d%H%M").replace(tzinfo=_ET)
    return et.astimezone(timezone.utc)


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _imp(p: float) -> float:
    p = float(p)
    return (-p) / (-p + 100.0) if p < 0 else 100.0 / (p + 100.0)


def _devig_over(over: float, under: float) -> float:
    io_, iu = _imp(over), _imp(under)
    return io_ / (io_ + iu) if (io_ + iu) > 0 else io_


def _list_snapshots(store, sport_key: str, days: list[str]) -> list[tuple[datetime, str]]:
    out = []
    for d in days:
        for key in store.list(f"OddsAccum/{sport_key}/raw/{d}/"):
            m = _FILE_RE.search(key)
            if m:
                out.append((_snap_ts(d, m.group(1)), key))
    return sorted(out)


def build_panel(store, sport: str, day: str) -> dict:
    sport_key = SPORT_KEYS[sport]
    d0 = datetime.strptime(day, "%Y-%m-%d").date()
    days = [(d0 - timedelta(days=1)).isoformat(), day]
    snaps = _list_snapshots(store, sport_key, days)

    # (event_id) -> event meta; (event_id, market, player, point) -> {book: [(ts, {side: price})]} in ts order
    events: dict[str, dict] = {}
    quotes: dict[tuple, dict[str, list]] = {}
    for ts, key in snaps:
        payload = json.loads(store.read(key))
        for ev in payload if isinstance(payload, list) else []:
            try:
                commence = _parse_ts(ev["commence_time"])
            except Exception:
                continue
            if commence.astimezone(_ET).date().isoformat() != day:
                continue
            eid = ev.get("canonical_event_id") or ev.get("id")
            if not eid:
                continue
            events.setdefault(eid, {"event_id": eid, "commence_time": ev["commence_time"],
                                    "home_team": ev.get("home_team"), "away_team": ev.get("away_team")})
            for bm in ev.get("bookmakers", []):
                book = bm.get("key")
                if not book:
                    continue
                for mk in bm.get("markets", []):
                    grouped: dict[tuple, dict] = {}
                    for oc in mk.get("outcomes", []):
                        player = oc.get("description")
                        if player is None or oc.get("point") is None or oc.get("name") is None:
                            continue
                        grouped.setdefault((player, float(oc["point"])), {})[oc["name"]] = oc["price"]
                    for (player, point), prices in grouped.items():
                        quotes.setdefault((eid, mk["key"], player, point), {}).setdefault(book, []).append((ts, prices))

    def at_mark(series: list, mark: datetime):
        best = None
        for ts, prices in series:
            if ts <= mark:
                best = (ts, prices)
            else:
                break
        return best

    def mark_block(book_series: dict, mark: datetime) -> dict:
        books, fairs = {}, []
        novig = None
        for book, series in sorted(book_series.items()):
            got = at_mark(series, mark)
            if not got:
                continue
            ts, prices = got
            books[book] = {"snap_ts": ts.isoformat(), "prices": prices}
            over = next((v for k, v in prices.items() if k.startswith("Over")), None)
            under = next((v for k, v in prices.items() if k.startswith("Under")), None)
            if book == "novig":
                novig = {"over": over, "under": under, "snap_ts": ts.isoformat(),
                         "stale_min": round((mark - ts).total_seconds() / 60.0, 1)}
            elif over is not None and under is not None:
                fairs.append(_devig_over(over, under))
        return {"books": books, "novig": novig,
                "cons_p_over": round(statistics.median(fairs), 4) if fairs else None,
                "cons_n_books": len(fairs)}

    rows = []
    for (eid, market, player, point), book_series in sorted(quotes.items()):
        commence = _parse_ts(events[eid]["commence_time"])
        rows.append({**events[eid], "market": market, "player": player, "point": point,
                     "close": mark_block(book_series, commence),
                     "t15": mark_block(book_series, commence - timedelta(minutes=15))})
    return {"schema": SCHEMA, "sport": sport, "day": day, "generated_at": datetime.now(timezone.utc).isoformat(),
            "rule": RULE, "snapshots_scanned": len(snaps), "events": len(events), "rows": rows}


def summarise(panel: dict) -> dict:
    rows = panel["rows"]
    return {"sport": panel["sport"], "day": panel["day"], "events": panel["events"], "rows": len(rows),
            "rows_novig_close": sum(1 for r in rows if r["close"]["novig"]),
            "rows_novig_t15": sum(1 for r in rows if r["t15"]["novig"]),
            "rows_cons_close": sum(1 for r in rows if r["close"]["cons_p_over"] is not None),
            "snapshots_scanned": panel["snapshots_scanned"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bank the daily NHL/NBA props closing panel (AUT-01 step c).")
    ap.add_argument("--day", default=None, help="ET day YYYY-MM-DD (default: yesterday ET)")
    ap.add_argument("--sport", default="both", choices=["nhl", "nba", "both"])
    ap.add_argument("--store", default="gcs", help="gcs (ADC) | gcloud (CLI)")
    ap.add_argument("--bucket", default=None)
    a = ap.parse_args(argv)
    day = a.day or (datetime.now(_ET) - timedelta(days=1)).date().isoformat()
    store = make_store(a.store, a.bucket)
    for sport in (["nhl", "nba"] if a.sport == "both" else [a.sport]):
        panel = build_panel(store, sport, day)
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
            gz.write(json.dumps(panel, sort_keys=True).encode())
        key = f"{PANEL_PREFIX}/{sport}/{day}.json.gz"
        store.put(key, buf.getvalue())
        print(json.dumps({"panel": key, **summarise(panel)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
