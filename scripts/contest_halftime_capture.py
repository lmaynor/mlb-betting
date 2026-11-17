"""contest_halftime_capture -- record ESPN game state plus Kalshi college quotes around REAL halftimes (research tool, M0/M5 of
docs/designs/bowl-bankroll-contest-scope.md).

WHY: the contest's halftime betting window needs (1) ESPN's real halftime status shape and (2) Kalshi prices that are live,
stable and tight enough to quote from. The 2026-11-17 probe showed BettingPros lines freeze at tip-off and Kalshi college markets
are live but thin; it never saw an actual halftime. This script captures one, so the go/no-go rests on data.

WHAT: stdlib only (runs under plain python3 on the laptop, no repo imports, no keys, no GCS). Every --interval seconds it polls the
public ESPN scoreboard; for any game inside the capture window (last 4 min of the first half, halftime, first 4 min of the second
half) it also pulls the open Kalshi full-game series (moneyline / spread / total) and appends the quotes of the matched game to a
JSONL file. Nothing is written anywhere else; nothing is traded.

  python3 scripts/contest_halftime_capture.py --sport ncaab --hours 6          # capture (Ctrl-C prints the summary)
  python3 scripts/contest_halftime_capture.py --sport ncaaf --hours 8
  python3 scripts/contest_halftime_capture.py --summarize ~/halftime_captures/ncaab_20261117_1900.jsonl
  python3 scripts/contest_halftime_capture.py --sport ncaab --once              # one cycle, for a smoke test

Rows (one JSON object per line):
  {"t": iso-utc, "k": "espn",   "id", "name", "status", "detail", "period", "clock", "phase", "score": [away, home]}
  {"t": iso-utc, "k": "kalshi", "id", "series": "game|spread|total", "ticker", "title", "bid", "ask", "last"}

Politeness: 1 ESPN request per cycle, 3 Kalshi requests per cycle ONLY while a game is in the window, 429/5xx back-off.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = "beezy-research-probe"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
_ET = ZoneInfo("America/New_York")

SPORTS = {
    "ncaab": {"path": "basketball/mens-college-basketball", "groups": 50, "half_s": 1200.0, "late_period": 1, "early_period": 2,
              "series": {"game": "KXNCAAMBGAME", "spread": "KXNCAAMBSPREAD", "total": "KXNCAAMBTOTAL"}},
    # football: the half ends after period 2; ESPN clock counts seconds left in the 15-minute quarter
    "ncaaf": {"path": "football/college-football", "groups": 80, "half_s": 900.0, "late_period": 2, "early_period": 3,
              "series": {"game": "KXNCAAFGAME", "spread": "KXNCAAFSPREAD", "total": "KXNCAFTOTAL"}},
}
WINDOW_S = 240.0            # 4 minutes either side of halftime
WINDOW_PHASES = ("H1_LATE", "HALFTIME", "H2_EARLY")


# ------------------------------------------------------------------------------------------------------------ pure helpers

def norm(s: str | None) -> str:
    s = (s or "").lower().replace("&", " and ").replace("'", "").replace(".", " ")
    s = re.sub(r"\bst\b", "state", s)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s).split())


def is_halftime(status: dict) -> bool:
    t = status.get("type") or {}
    return t.get("name") == "STATUS_HALFTIME" or "halftime" in str(t.get("detail", "")).lower() \
        or "halftime" in str(t.get("shortDetail", "")).lower()


def phase(sport: str, status: dict) -> str:
    """PRE | H1 | H1_LATE | HALFTIME | H2_EARLY | H2 | FINAL | OTHER from an ESPN `status` object."""
    cfg = SPORTS[sport]
    t = status.get("type") or {}
    name, state = t.get("name", ""), t.get("state", "")
    if name == "STATUS_FINAL" or t.get("completed"):
        return "FINAL"
    if state == "pre" or name == "STATUS_SCHEDULED":
        return "PRE"
    if is_halftime(status):
        return "HALFTIME"
    if state != "in":
        return "OTHER"
    period, clock = int(status.get("period") or 0), float(status.get("clock") or 0.0)
    if period < cfg["late_period"]:
        return "H1"
    if period == cfg["late_period"]:
        return "H1_LATE" if clock <= WINDOW_S else "H1"
    if period == cfg["early_period"]:
        return "H2_EARLY" if clock >= cfg["half_s"] - WINDOW_S else "H2"
    return "H2"


def espn_rows(sport: str, scoreboard: dict, ts: str) -> list[dict]:
    rows = []
    for e in scoreboard.get("events") or []:
        st = e.get("status") or {}
        c = (e.get("competitions") or [{}])[0].get("competitors") or []
        side = {x.get("homeAway"): x for x in c}
        if "home" not in side or "away" not in side:
            continue
        team = lambda x: sorted({norm(x["team"].get(k)) for k in ("location", "shortDisplayName", "displayName") if x["team"].get(k)})
        rows.append({"t": ts, "k": "espn", "id": e["id"], "name": e.get("name"), "status": (st.get("type") or {}).get("name"),
                     "detail": (st.get("type") or {}).get("detail"), "period": st.get("period"), "clock": st.get("clock"),
                     "phase": phase(sport, st), "score": [side["away"].get("score"), side["home"].get("score")],
                     "away": team(side["away"]), "home": team(side["home"])})
    return rows


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def kalshi_events(markets_by_series: dict[str, list[dict]]) -> dict[str, dict]:
    """{game key (event ticker minus series prefix): {"teams": {norm names}, "markets": [(series, market)]}}.
    Team names come from the moneyline titles ('<School> wins')."""
    out: dict[str, dict] = {}
    for series, ms in markets_by_series.items():
        for m in ms:
            key = str(m.get("event_ticker", "")).split("-", 1)[-1]
            g = out.setdefault(key, {"teams": set(), "markets": []})
            g["markets"].append((series, m))
            if series == "game":
                title = str(m.get("title") or "")
                if title.endswith(" wins"):
                    g["teams"].add(norm(title[:-5]))
    return out


def match_game(espn_row: dict, events: dict[str, dict]) -> str | None:
    """Kalshi game key whose two moneyline team names equal ESPN's home and away names (any of ESPN's spellings); None if not
    exactly one match (fail closed, never a guess)."""
    hits = []
    for k, g in events.items():
        if len(g["teams"]) != 2:
            continue
        t1, t2 = sorted(g["teams"])
        if (t1 in espn_row["home"] and t2 in espn_row["away"]) or (t2 in espn_row["home"] and t1 in espn_row["away"]):
            hits.append(k)
    return hits[0] if len(hits) == 1 else None


def kalshi_rows(game_id: str, game: dict, ts: str) -> list[dict]:
    rows = []
    for series, m in game["markets"]:
        bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        if bid is None or ask is None or not 0.03 <= (bid + ask) / 2 <= 0.97:
            continue
        rows.append({"t": ts, "k": "kalshi", "id": game_id, "series": series, "ticker": m.get("ticker"), "title": m.get("title"),
                     "bid": bid, "ask": ask, "last": _f(m.get("last_price_dollars"))})
    return rows


# ------------------------------------------------------------------------------------------------------------ summary

def summarize(path: str) -> str:
    """Per-game verdict from a capture file (offline): status names seen, halftime length, score stability, ML price stability."""
    games: dict[str, dict] = {}
    statuses: dict[str, int] = {}
    for line in open(path):
        r = json.loads(line)
        g = games.setdefault(r["id"], {"name": None, "espn": [], "kalshi": []})
        if r["k"] == "espn":
            g["name"] = r.get("name")
            g["espn"].append(r)
            statuses[str(r["status"])] = statuses.get(str(r["status"]), 0) + 1
        else:
            g["kalshi"].append(r)
    lines = [f"file: {path}", f"ESPN status names seen: {dict(sorted(statuses.items()))}", ""]
    for gid, g in games.items():
        half = [r for r in g["espn"] if r["phase"] == "HALFTIME"]
        lines.append(f"game {gid} {g['name']}")
        if not half:
            lines.append("  no HALFTIME row captured (window not reached, or ESPN never reported halftime)")
            continue
        t0, t1 = half[0]["t"], half[-1]["t"]
        mins = (datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds() / 60
        scores = {tuple(r["score"]) for r in g["espn"] if r["phase"] in ("H1_LATE", "HALFTIME", "H2_EARLY") and r["t"] >= t0}
        lines.append(f"  halftime rows {len(half)}, first {t0}, last {t1} ({mins:.1f} min seen); detail strings: "
                     f"{sorted({str(r['detail']) for r in half})}")
        lines.append(f"  distinct scores from halftime start through window end: {len(scores)} (1 = frozen at the break)")
        ml = [r for r in g["kalshi"] if r["series"] == "game"]
        by_t: dict[str, list[dict]] = {}
        for r in ml:
            by_t.setdefault(r["t"], []).append(r)
        ht = sorted(t for t in by_t if t0 <= t <= t1)
        lines.append(f"  Kalshi snapshots: {len(by_t)} total, {len(ht)} during halftime")
        if ht:
            first, last = by_t[ht[0]], by_t[ht[-1]]
            fav = max(first, key=lambda r: (r["bid"] + r["ask"]) / 2)["ticker"]
            mid = lambda rows: next(((r["bid"] + r["ask"]) / 2, r["ask"] - r["bid"]) for r in rows if r["ticker"] == fav)
            (m0, w0), (m1, w1) = mid(first), mid(last)
            widths = [r["ask"] - r["bid"] for t in ht for r in by_t[t] if r["ticker"] == fav]
            lines.append(f"  favourite {fav}: mid {m0:.3f} -> {m1:.3f} over the break, width first {w0:.3f}, last {w1:.3f}, "
                         f"median width {statistics.median(widths):.3f}, share of snapshots within 6c: "
                         f"{100 * sum(w <= 0.06 for w in widths) / len(widths):.0f}%")
        for series in ("spread", "total"):
            rows = [r for r in g["kalshi"] if r["series"] == series and half and half[0]["t"] <= r["t"] <= half[-1]["t"]]
            tight = [r for r in rows if r["ask"] - r["bid"] <= 0.06]
            lines.append(f"  {series}: {len(rows)} rung-quotes during halftime, {len(tight)} within 6c")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------ network

def http_json(url: str, params: dict | None = None, attempts: int = 4) -> dict:
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for i in range(attempts):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
        except urllib.error.URLError:
            if i < attempts - 1:
                time.sleep(2 ** (i + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def fetch_scoreboard(sport: str, now: datetime) -> dict:
    cfg = SPORTS[sport]
    days = {(now.astimezone(_ET) - timedelta(days=d)).strftime("%Y%m%d") for d in (0, 1)}
    events: dict[str, dict] = {}
    for ds in sorted(days):
        for e in http_json(ESPN.format(path=cfg["path"]), {"dates": ds, "limit": 400, "groups": cfg["groups"]}).get("events") or []:
            events[e["id"]] = e
    return {"events": list(events.values())}


def fetch_kalshi(sport: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for key, series in SPORTS[sport]["series"].items():
        ms: list[dict] = []
        cursor = None
        while True:
            d = http_json(KALSHI, {"series_ticker": series, "status": "open", "limit": 1000, **({"cursor": cursor} if cursor else {})})
            ms += d.get("markets") or []
            cursor = d.get("cursor")
            if not cursor:
                break
        out[key] = ms
        time.sleep(0.5)
    return out


# ------------------------------------------------------------------------------------------------------------ main loop

def cycle(sport: str, sink, now: datetime) -> dict:
    ts = now.isoformat(timespec="seconds")
    rows = [r for r in espn_rows(sport, fetch_scoreboard(sport, now), ts) if r["phase"] not in ("PRE", "FINAL", "OTHER")]
    window = [r for r in rows if r["phase"] in WINDOW_PHASES]
    for r in rows:
        sink.write(json.dumps({k: v for k, v in r.items() if k not in ("away", "home")}) + "\n")
    matched = 0
    if window:
        events = kalshi_events(fetch_kalshi(sport))
        for r in window:
            key = match_game(r, events)
            if key is None:
                sink.write(json.dumps({"t": ts, "k": "note", "id": r["id"], "msg": "no unique Kalshi match", "name": r["name"]}) + "\n")
                continue
            matched += 1
            for kr in kalshi_rows(r["id"], events[key], ts):
                sink.write(json.dumps(kr) + "\n")
    sink.flush()
    return {"live": len(rows), "window": len(window), "kalshi_matched": matched, "phases": sorted({r["phase"] for r in rows})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport", choices=sorted(SPORTS), default="ncaab")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between cycles (default 30)")
    ap.add_argument("--hours", type=float, default=6.0, help="stop after this long (default 6)")
    ap.add_argument("--out", default=os.path.expanduser("~/halftime_captures"), help="output directory (default ~/halftime_captures)")
    ap.add_argument("--once", action="store_true", help="run one cycle and exit (smoke test)")
    ap.add_argument("--summarize", metavar="FILE", help="print the per-game summary of a capture file and exit (no network)")
    a = ap.parse_args(argv)
    if a.summarize:
        print(summarize(a.summarize))
        return 0
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"{a.sport}_{datetime.now().strftime('%Y%m%d_%H%M')}.jsonl")
    stop = {"flag": False}
    signal.signal(signal.SIGINT, lambda *_: stop.update(flag=True))
    deadline = time.time() + a.hours * 3600
    print(f"capturing {a.sport} -> {path} (every {a.interval:.0f}s, {a.hours:g} h; Ctrl-C to stop and summarize)", flush=True)
    with open(path, "a") as sink:
        while not stop["flag"] and time.time() < deadline:
            t_start = time.time()
            try:
                info = cycle(a.sport, sink, datetime.now(timezone.utc))
                print(f"{datetime.now().strftime('%H:%M:%S')} live={info['live']} window={info['window']} "
                      f"kalshi_matched={info['kalshi_matched']} phases={info['phases']}", flush=True)
            except Exception as exc:  # noqa: BLE001 - a transient network error must not end an evening's capture
                print(f"cycle failed: {exc!r}", file=sys.stderr, flush=True)
            if a.once:
                break
            time.sleep(max(0.0, a.interval - (time.time() - t_start)))
    print("\n" + summarize(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
