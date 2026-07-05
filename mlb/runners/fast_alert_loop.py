"""
mlb.runners.fast_alert_loop -- the pager: snapshot -> scan -> notify, every 15 min.

The soft-line strategy only pays if a lagging quote is struck BEFORE it
corrects (typical prop staleness: 10-60 min). The 3x/day odds_alert cadence
misses that window; this loop closes it. Scheduled */15 in the 19:00-23:45 UTC
strike window PLUS every 2 hours overnight -- openers for TOMORROW's games
post overnight and are the softest prices of the day (one book posts, the
others copy lazily, nothing corrects until liquidity arrives). The scan
covers today AND tomorrow (FAL_DAYS=2); next-day alerts are tagged
[TMRW opener]. Each execution:

  1. LINEUP EVENTS: diff today's posted batting orders against the last-seen
     state (Alerts/{day}/lineup_state.json). A newly posted lineup or a
     scratch/substitution marks that game HOT -- those are exactly the moments
     soft books lag.
  2. SNAPSHOT: bank a fresh (free) BettingPros snapshot into odds_history
     (mlb.runners.track_bettingpros, today only).
  3. SCAN: +EV outliers vs the Pinnacle-anchored consensus
     (mlb.analysis.outlier_scan) at the latest snapshot.
  4. NOTIFY: post only NEW alerts (never re-ping the same quote; dedup state in
     Alerts/{day}/notified.parquet) to Discord, hot games flagged. Alerts are
     also appended to Alerts/{day}/log.parquet so the nightly odds_alert
     resolve/scorecard pass covers them.

Config via env: FAL_MARKETS (hr_yn,outs_ou,btb_ou,bhits_ou,k_ou), FAL_MIN_EV
(0.03), FAL_MIN_BOOKS (4), FAL_MAX_POSTS (10 per run), FAL_ANCHOR (pinnacle;
"none" disables), FAL_SKIP_SNAPSHOT=1 (scan-only, for tests).

Local:
  PYTHONPATH=. FAL_SKIP_SNAPSHOT=1 python3 -m mlb.runners.fast_alert_loop
"""

from __future__ import annotations

import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from mlb_core import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fast_alert_loop")

# identity of a bettable quote for notify-dedup (no snapshot_ts: one ping per
# quote per day, even as later snapshots re-confirm it)
_QUOTE_KEYS = ["market", "game_pk", "player_id", "line", "selection", "book"]
_LOG_KEYS = _QUOTE_KEYS + ["snapshot_ts"]


def _read_parquet(key: str):
    try:
        return pd.read_parquet(io.BytesIO(storage.read_bytes(key)))
    except Exception:  # noqa: BLE001
        return None


def _write_parquet(df: pd.DataFrame, key: str) -> None:
    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    storage.write_bytes(buf.getvalue(), key)


# -- 1. lineup events ---------------------------------------------------------

def lineup_events(day: str) -> tuple[set, list]:
    """Diff posted lineups vs last-seen state. Returns (hot_game_pks, notes)."""
    from mlb_core.data.lineups import _get_games_for_date, confirmed_lineup_ids

    state_key = f"Alerts/{day}/lineup_state.json"
    try:
        prev = json.loads(storage.read_bytes(state_key).decode())
    except Exception:  # noqa: BLE001
        prev = {}

    hot: set = set()
    notes: list = []
    cur: dict = {}
    try:
        games = _get_games_for_date(day)
    except Exception as e:  # noqa: BLE001
        log.warning("lineup fetch failed: %s", e)
        return hot, notes
    for g in games:
        gpk = int(g["game_pk"])
        try:
            ids = sorted(confirmed_lineup_ids(gpk))
        except Exception:  # noqa: BLE001
            ids = []
        cur[str(gpk)] = ids
        before = prev.get(str(gpk), [])
        if ids and not before:
            hot.add(gpk)
            notes.append(f"lineup POSTED game {gpk}")
        elif ids and before and ids != before:
            gone = set(before) - set(ids)
            hot.add(gpk)
            notes.append(f"lineup CHANGED game {gpk}"
                         + (f" (out: {sorted(gone)})" if gone else ""))
    try:
        storage.write_bytes(json.dumps(cur).encode(), state_key)
    except Exception as e:  # noqa: BLE001
        log.warning("lineup state write failed: %s", e)
    return hot, notes


# -- 4. notify ----------------------------------------------------------------

def _alert_webhook() -> str | None:
    return (os.getenv("DISCORD_WEBHOOK_ALERTS")
            or os.getenv("DISCORD_WEBHOOK_URL"))


def _fmt_american(a) -> str:
    try:
        a = int(a)
    except (TypeError, ValueError):
        return "?"
    return f"+{a}" if a > 0 else str(a)


def notify(new: pd.DataFrame, hot: set, notes: list, today_str: str = '') -> None:
    from mlb_core.notify.discord import _post
    url = _alert_webhook()
    lines = []
    for _, r in new.iterrows():
        game = f"{r.get('away_team', '?')}@{r.get('home_team', '?')}" \
            if r.get("away_team") else f"game {r.get('game_pk')}"
        pid = r.get("player_id")
        who = f"player {int(pid)}" if pd.notna(pid) else "team"
        flame = " **HOT**" if r.get("game_pk") in hot else ""
        anchor = " [pinn]" if r.get("anchored") else ""
        gd = str(r.get("game_date", ""))
        nextday = " [TMRW opener]" if today_str and gd and gd > today_str else ""
        lines.append(
            f"`{r['market']}` {game} {who} {r['selection']} {r.get('line', '')} "
            f"@ **{r['book']}** {_fmt_american(r.get('american'))} -> "
            f"EV **{r['ev']*100:+.1f}%**{anchor}{nextday} (fair {r['consensus_fair']:.3f}, "
            f"{int(r['n_books'])} books){flame}")
    body = "\n".join(lines)
    if notes:
        body = "**Lineup events:** " + "; ".join(notes[:6]) + "\n\n" + body
    if not url:
        log.warning("no Discord webhook -- printing alerts only")
        print(body)
        return
    _post(url, {"embeds": [{"title": f"+EV alerts ({len(new)})",
                            "description": body[:3900], "color": 0xE3B261}]})


# -- main ---------------------------------------------------------------------

def run(run_date: str | None = None) -> dict:
    now = datetime.now(timezone.utc)
    day = run_date or now.date().isoformat()
    # scan window: today + tomorrow. Openers for tomorrow's games post
    # overnight and are the softest prices of the day -- one book posts, the
    # others copy lazily, and nothing corrects until liquidity arrives. The
    # trackers already bank next-day quotes (BP_DAYS=2, parlay day_offset=1);
    # this makes the scanner actually look at them.
    n_days = int(os.environ.get("FAL_DAYS", "2"))
    until = (now.date() + timedelta(days=n_days - 1)).isoformat()
    markets = os.environ.get("FAL_MARKETS", "hr_yn,outs_ou,btb_ou,bhits_ou,k_ou").split(",")
    min_ev = float(os.environ.get("FAL_MIN_EV", "0.03"))
    min_books = int(os.environ.get("FAL_MIN_BOOKS", "4"))
    max_posts = int(os.environ.get("FAL_MAX_POSTS", "10"))
    anchor = os.environ.get("FAL_ANCHOR", "pinnacle")
    anchor = None if anchor.lower() in ("", "none", "0") else anchor

    # 1) lineup events
    hot, notes = lineup_events(day)
    if notes:
        log.info("lineup events: %s", "; ".join(notes))

    # 2) fresh snapshot (free)
    if os.environ.get("FAL_SKIP_SNAPSHOT") != "1":
        from mlb.runners import track_bettingpros
        os.environ.setdefault("BP_DAYS", str(n_days))  # bank tomorrow's openers too
        try:
            snap = track_bettingpros.run(run_date=day)
            log.info("snapshot: %s rows @ %s", snap.get("rows"), snap.get("snapshot_ts"))
        except Exception as e:  # noqa: BLE001
            log.warning("snapshot failed (scanning last banked data): %s", e)

    # 3) scan
    from mlb.analysis import outlier_scan as osc
    found = osc.scan_markets(markets, since=day, until=until, min_ev=min_ev,
                             min_books=min_books, latest_only=True,
                             anchor_book=anchor)
    if not len(found):
        log.info("scan: no +EV outliers")
        return {"status": "ok", "day": day, "new_alerts": 0, "hot_games": len(hot)}

    # 4) dedup vs already-notified, notify, persist
    notified_key = f"Alerts/{day}/notified.parquet"
    seen = _read_parquet(notified_key)
    if seen is not None and len(seen):
        keys = [k for k in _QUOTE_KEYS if k in found.columns and k in seen.columns]
        merged = found.merge(seen[keys].drop_duplicates(), on=keys,
                             how="left", indicator=True)
        new = found[(merged["_merge"] == "left_only").values].copy()
    else:
        new = found.copy()

    if len(new):
        # hottest first: hot-game alerts, then by EV
        new["_hot"] = new["game_pk"].isin(hot)
        new = new.sort_values(["_hot", "ev"], ascending=[False, False]).drop(columns="_hot")
        posted = new.head(max_posts)
        notify(posted, hot, notes, today_str=day)
        if len(new) > max_posts:
            log.info("capped: %d further alerts not posted this run", len(new) - max_posts)
        # persist notify-state and the shared alert log (for odds_alert resolve)
        seen_new = pd.concat([seen, new[[c for c in _QUOTE_KEYS if c in new.columns]]],
                             ignore_index=True) if seen is not None else \
            new[[c for c in _QUOTE_KEYS if c in new.columns]]
        _write_parquet(seen_new.drop_duplicates(), notified_key)

        logkey = f"Alerts/{day}/log.parquet"
        prior = _read_parquet(logkey)
        merged_log = pd.concat([prior, new], ignore_index=True) if prior is not None else new
        merged_log = merged_log.drop_duplicates(
            subset=[c for c in _LOG_KEYS if c in merged_log.columns], keep="last")
        _write_parquet(merged_log, logkey)
        log.info("notified %d new alerts (%d found, %d hot games)",
                 len(posted), len(found), len(hot))
    else:
        log.info("scan: %d outliers, all already notified today", len(found))

    return {"status": "ok", "day": day, "new_alerts": int(len(new)),
            "hot_games": len(hot)}


def main() -> int:
    res = run()
    print(f"fast_alert_loop {res['day']}: {res['new_alerts']} new alerts, "
          f"{res['hot_games']} hot games")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
