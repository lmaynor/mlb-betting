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
     Alerts/{day}/notified.parquet) to Discord as a structured embed -- one
     field per alert, human market/book/team names (mlb_core.notify.discord
     market_label/book_display), EV-tiered emoji, and the fair-price anchor
     spelled out (vs Pinnacle / vs consensus) instead of packed into a single
     cryptic text blob. Hot games and next-day openers are flagged. Alerts are
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


# -- player-id -> name resolution (alerts must be ACTIONABLE: names, not ids) --

def resolve_player_names(ids) -> dict:
    """Batch MLBAM id -> full name via the MLB Stats API. Best-effort."""
    import requests
    ids = sorted({int(i) for i in ids if pd.notna(i)})
    out: dict = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            r = requests.get(
                "https://statsapi.mlb.com/api/v1/people",
                params={"personIds": ",".join(map(str, chunk))}, timeout=15)
            r.raise_for_status()
            for pers in r.json().get("people", []):
                out[int(pers["id"])] = pers.get("fullName", "")
        except Exception as e:  # noqa: BLE001
            log.warning("player name resolution failed for %d ids: %s", len(chunk), e)
    return out


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


_SEL_WORDS = {"OVER": "Over", "UNDER": "Under", "YES": "Yes", "NO": "No"}


def _alert_parts(r: pd.Series, hot: set, today_str: str, anchor_book: str | None,
                 book_display, market_label, ev_alert_emoji, team_nickname: dict) -> dict:
    """Compute the human-readable pieces of one +EV alert row. Shared by the
    Discord embed fields and the no-webhook print fallback so both stay in sync."""
    sel = str(r.get("selection", "")).upper()
    sel_word = _SEL_WORDS.get(sel, sel.title())
    line = r.get("line")
    label = market_label(r["market"])
    if sel in ("YES", "NO"):
        what = f"{label} ({sel_word})"
    elif pd.notna(line):
        what = f"{sel_word} {line:g} {label}"
    else:
        what = f"{sel_word} {label}"

    away, home = r.get("away_team"), r.get("home_team")
    matchup = (f"{team_nickname.get(away, away)} @ {team_nickname.get(home, home)}"
               if away else f"game {r.get('game_pk')}")
    pname = r.get("player_name")
    has_player = isinstance(pname, str) and bool(pname)
    who = pname if has_player else matchup

    gd = str(r.get("game_date", ""))
    badge = ("🔥 " if r.get("game_pk") in hot else "") + \
            ("🌙 " if today_str and gd and gd > today_str else "")

    fair = r.get("consensus_fair")
    n_books = r.get("n_books")
    return {
        "emoji":        ev_alert_emoji(r["ev"]),
        "badge":        badge,
        "who":          who,
        "what":         what,
        "matchup_tag":  f" | {matchup}" if has_player else "",
        "book":         book_display(r.get("book")),
        "american":     _fmt_american(r.get("american")),
        "ev":           r["ev"],
        "anchor_label": book_display(anchor_book) if r.get("anchored") and anchor_book else "consensus",
        "fair":         fair if pd.notna(fair) else None,
        "n_books":      int(n_books) if pd.notna(n_books) else 0,
    }


def notify(new: pd.DataFrame, hot: set, notes: list, today_str: str = '',
           min_ev: float = 0.03, min_books: int = 4, anchor: str | None = None) -> None:
    from mlb_core.notify.discord import (
        _post, book_display, market_label, ev_alert_emoji, TEAM_NICKNAME,
    )
    url = _alert_webhook()

    fields = []
    if notes:
        fields.append({"name": "📋 Lineup events",
                       "value": "; ".join(notes[:6])[:1024], "inline": False})
    for _, r in new.iterrows():
        p = _alert_parts(r, hot, today_str, anchor, book_display, market_label,
                         ev_alert_emoji, TEAM_NICKNAME)
        fair_str = f"{p['fair']:.1%}" if p["fair"] is not None else "N/A"
        name = f"{p['emoji']} {p['badge']}{p['who']}"[:256]
        value = (
            f"{p['what']}{p['matchup_tag']}\n"
            f"**{p['book']} {p['american']}** -> EV **{p['ev']:+.1%}** "
            f"vs {p['anchor_label']} fair **{fair_str}** ({p['n_books']} books)"
        )[:1024]
        fields.append({"name": name, "value": value, "inline": False})

    if not url:
        log.warning("no Discord webhook -- printing alerts only")
        for f in fields:
            print(f"{f['name']}\n  {f['value']}")
        return

    anchor_disp = book_display(anchor) if anchor else "consensus only"
    embed = {
        "title": f"📡 +EV Alerts -- {len(new)} new",
        "description": (
            f"Soft-book price lagging the sharp reference by >= {min_ev:.0%} -- "
            f"a line worth striking before it corrects."
        ),
        "color": 0xE3B261,
        "fields": fields[:25],
        "footer": {"text": f"min EV {min_ev:.0%} | min {min_books} books | "
                           f"anchor: {anchor_disp} | fast_alert_loop"},
    }
    _post(url, {"embeds": [embed]})


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
        # names, not ids -- Discord pings and the site must be actionable
        if "player_id" in new.columns:
            names = resolve_player_names(new["player_id"].dropna().unique())
            new["player_name"] = new["player_id"].map(
                lambda x: names.get(int(x), "") if pd.notna(x) else "")
        # hottest first: hot-game alerts, then by EV
        new["_hot"] = new["game_pk"].isin(hot)
        new = new.sort_values(["_hot", "ev"], ascending=[False, False]).drop(columns="_hot")
        posted = new.head(max_posts)
        notify(posted, hot, notes, today_str=day,
               min_ev=min_ev, min_books=min_books, anchor=anchor)
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
