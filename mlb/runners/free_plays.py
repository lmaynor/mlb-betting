"""
mlb.runners.free_plays -- the daily FREE PLAYS (#daily-free-plays, visible to everyone).

Rule (decided 2026-10-17): the day's highest Beezy Score plays across every sport and source, but only if STRONG
(score >= FREE_PLAY_MIN_SCORE, default 70), at most FREE_PLAYS_PER_DAY (default 2) per ET day in total. Fewer than 2
qualifying plays means fewer posts: the floor is the claim, the cap is the product boundary.

Candidates (nothing here places or sizes a bet):
  * today's `bets` rows, any sport -- model picks that cleared Kelly plus the +EV alerts logged as system="EV";
    both are paper-tracked and settle into the public ledger.
  * the newest NHL / NBA / NFL prop-sweep outliers (EVSweep/{sport}_props/latest.json), today's ET date only,
    games that have not started. Paper / unvalidated and labelled so.

A play is posted at most once and never replaced: posted keys persist in GCS (FreePlays/{ET day}/posted.json), so
re-running the job later in the day only tops up to the cap when the first run found fewer than the cap.

Delivery: bot-user message first, DISCORD_WEBHOOK_FREE_PLAYS as the fallback; with neither set it prints (dry run).
Run:  python -m mlb.runners.free_plays [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mlb_core.alerts.beezy_score import beezy_score, books_from_notes, tier
from mlb_core.alerts.discord_routes import (
    bot_configured, post_via_bot, slip_text, webhook_for, with_deep_links,
)

log = logging.getLogger("free_plays")
_ET = ZoneInfo("America/New_York")

MIN_SCORE = int(os.environ.get("FREE_PLAY_MIN_SCORE", "70"))
PER_DAY = int(os.environ.get("FREE_PLAYS_PER_DAY", "2"))
SWEEP_SPORTS = ("nhl", "nba", "nfl")
PICKS_URL = "https://beezy.fyi/picks"


def et_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_ET).date().isoformat()


# ----------------------------------------------------------------------------- candidates
def _bet_label(b: dict) -> str:
    """Readable pick text for a `bets` row. Model systems reuse the Discord card's headline; +EV rows (bet_type carries
    a trailing book tag) get a plain humanised label."""
    system = str(b.get("system") or "")
    if system != "EV":
        try:
            from mlb_core.notify.discord import _format_bet_headline
            return _format_bet_headline(b, system)
        except Exception:  # noqa: BLE001 -- label only; never block a free play on formatting
            pass
    bt = str(b.get("bet_type") or "")
    book = str(b.get("book") or "").lower()
    toks = bt.split("_")
    if book and toks and toks[-1].lower() == book:
        toks = toks[:-1]
    what = " ".join(toks).title()
    who = b.get("player") or ""
    return f"{who} - {what}".strip(" -")


def candidates_from_bets(df, day: str) -> list[dict]:
    """`bets` rows for `day` that are live (ungraded, kelly_triggered, with an edge) -> candidate dicts."""
    out = []
    for r in df.to_dict("records"):
        r = {k: (None if isinstance(v, float) and v != v else v) for k, v in r.items()}   # pandas NaN -> None
        if r.get("result") not in (None, ""):
            continue
        if not r.get("kelly_triggered") or r.get("edge") is None or r.get("odds") is None:
            continue
        system = str(r.get("system") or "")
        score = beezy_score(r.get("edge"), books_from_notes(r.get("notes")), r.get("model_prob"), system)
        out.append({
            "key": f"bet|{r.get('sport', 'mlb')}|{r.get('id')}",
            "sport": str(r.get("sport") or "mlb"),
            "text": _bet_label(r),
            "game": f"{r.get('away_team') or ''} @ {r.get('home_team') or ''}".strip(" @"),
            "book": r.get("book") or "",
            "odds": int(r["odds"]),
            "edge": float(r["edge"]),
            "n_books": books_from_notes(r.get("notes")),
            "score": score,
            "basis": "ev" if system == "EV" else "model",
        })
    return out


def candidates_from_sweep(sport: str, payload: dict, day: str, now: datetime) -> list[dict]:
    """Outlier rows of one sport's latest.json -> candidate dicts. Stale files (another ET day), non-flagged rows and
    games already under way are skipped."""
    ts = payload.get("ts")
    try:
        if et_day(datetime.fromisoformat(ts)) != day:
            return []
    except (TypeError, ValueError):
        return []
    rows = payload.get("rows") or payload.get("book_outliers") or []
    out = []
    for r in rows:
        if r.get("flag") is False:
            continue
        if not all(k in r and r[k] is not None for k in ("player", "stat", "side", "point", "american", "book", "ev")):
            continue
        commence = r.get("commence")
        if commence:
            try:
                if datetime.fromisoformat(str(commence)) <= now:
                    continue
            except ValueError:
                pass
        score = beezy_score(r["ev"], r.get("n_books"), r.get("p_fair"))
        out.append({
            "key": f"sweep|{sport}|{r.get('game_key')}|{r.get('player_norm') or r['player']}|{r['stat']}|{r['point']}|{r['side']}|{r['book']}",
            "sport": sport,
            "text": f"{r['player']} {str(r['side']).capitalize()} {r['point']:g} {str(r['stat']).replace('_', ' ')}",
            "game": str(r.get("game_key") or "").split(":", 1)[-1],
            "book": r["book"], "odds": int(r["american"]), "edge": float(r["ev"]),
            "n_books": r.get("n_books"), "score": score, "basis": "sweep",
        })
    return out


def load_candidates(day: str, now: datetime) -> list[dict]:
    import pandas as pd
    from sqlalchemy import text
    from mlb_core import storage
    from mlb_core.tracking.bet_tracker import _make_engine

    cands: list[dict] = []
    try:
        with _make_engine(db_path="unused").connect() as conn:
            df = pd.read_sql(text("SELECT * FROM bets WHERE game_date = :d"), conn, params={"d": day})
        cands += candidates_from_bets(df, day)
    except Exception as e:  # noqa: BLE001 -- the sweeps below can still supply plays
        log.warning("bets load failed: %r", e)
    for sport in SWEEP_SPORTS:
        key = f"EVSweep/{sport}_props/latest.json"
        try:
            if storage.exists(key):
                cands += candidates_from_sweep(sport, json.loads(storage.read_bytes(key)), day, now)
        except Exception as e:  # noqa: BLE001
            log.warning("%s sweep load failed: %r", sport, e)
    return cands


# ----------------------------------------------------------------------------- selection + posting
def pick(cands: list[dict], already: set[str], slots: int, floor: int = MIN_SCORE) -> list[dict]:
    """Best `slots` unposted candidates at/above the floor; ties break on edge. One play per proposition."""
    pool = sorted((c for c in cands if c["score"] >= floor and c["key"] not in already),
                  key=lambda c: (-c["score"], -c["edge"]))
    seen, out = set(), []          # one play per proposition: not the same prop at 2 books
    for c in pool:
        prop = (c["sport"], c["text"])
        if prop in seen:
            continue
        seen.add(prop)
        out.append(c)
    return out[:max(slots, 0)]


def _label(c: dict) -> str:
    return "paper / unvalidated" if c["basis"] == "sweep" else "paper-tracked on the public ledger"


def embed_for(c: dict, day: str) -> dict:
    from mlb_core.notify.discord import book_display, _odds_str
    bits = [f"**{c['text']}**", f"@ **{book_display(c['book'])} {_odds_str(c['odds'])}**"]
    fields = [{"name": "Edge", "value": f"{c['edge'] * 100:+.1f}%", "inline": True}]
    if c.get("n_books"):
        fields.append({"name": "Books", "value": str(c["n_books"]), "inline": True})
    if c.get("game"):
        fields.append({"name": "Game", "value": c["game"], "inline": True})
    e = {"title": f"FREE PLAY | {c['sport'].upper()} | Beezy {c['score']} {tier(c['score']).upper()}",
         "description": " ".join(bits), "color": 0x57F287, "fields": fields,
         "footer": {"text": f"{day} | {_label(c)} | graded in #daily-recap"}, "url": PICKS_URL}
    return e


def post_play(c: dict, day: str) -> bool:
    """Bot-user message first, webhook fallback. True only if delivered. Never raises."""
    from mlb_core.notify.discord import book_display, _odds_str
    embed = embed_for(c, day)
    slip = slip_text([f"{c['text']} @ {book_display(c['book'])} {_odds_str(c['odds'])}"])
    if bot_configured() and post_via_bot("free_plays", slip, [embed]):
        return True
    url = webhook_for("free_plays")
    if not url:
        log.warning("no bot and no DISCORD_WEBHOOK_FREE_PLAYS set; free play not posted: %s", c["text"])
        return False
    try:
        import requests
        return requests.post(url, json={"embeds": [embed]}, timeout=20).status_code < 300
    except Exception as e:  # noqa: BLE001
        log.warning("free play webhook failed: %r", e)
        return False


def _state_key(day: str) -> str:
    return f"FreePlays/{day}/posted.json"


def load_posted(day: str) -> list[dict]:
    from mlb_core import storage
    try:
        return json.loads(storage.read_bytes(_state_key(day))) if storage.exists(_state_key(day)) else []
    except Exception:  # noqa: BLE001
        return []


def run(now: datetime | None = None, dry_run: bool = False) -> dict:
    from mlb_core import storage
    now = now or datetime.now(timezone.utc)
    day = et_day(now)
    posted = load_posted(day)
    slots = PER_DAY - len(posted)
    if slots <= 0:
        return {"status": "full", "day": day, "posted": len(posted)}
    cands = load_candidates(day, now)
    chosen = pick(cands, {p["key"] for p in posted}, slots)
    sent = []
    for c in chosen:
        if dry_run:
            print(f"[dry-run] {c['score']} {c['sport']} {c['text']} @ {c['book']} {c['odds']:+d} (edge {c['edge']:+.1%})")
            continue
        if post_play(c, day):
            sent.append({"key": c["key"], "text": c["text"], "score": c["score"], "sport": c["sport"],
                         "ts": now.isoformat()})
    if sent:
        storage.write_bytes(json.dumps(posted + sent).encode(), _state_key(day))
    summary = {"status": "ok", "day": day, "candidates": len(cands), "qualifying": sum(c["score"] >= MIN_SCORE for c in cands),
               "chosen": len(chosen), "posted": len(sent), "dry_run": dry_run}
    log.info("FREE_PLAYS %s", json.dumps(summary))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the plays that would post; post and persist nothing")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
