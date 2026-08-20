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
     soft books lag. (Feeds the per-alert hot-game badge/sort priority only --
     as of 2026-08-20 no longer posted as its own Discord field; a bare list of
     raw game_pks wasn't actionable on its own.)
  2. SNAPSHOT: bank a fresh (free) BettingPros snapshot into odds_history
     (mlb.runners.track_bettingpros, today only).
  3. SCAN: +EV outliers vs the Pinnacle-anchored consensus
     (mlb.analysis.outlier_scan) at the latest snapshot.
  4. NOTIFY: post only NEW alerts (never re-ping the same quote; dedup state in
     Alerts/{day}/notified.parquet) to Discord as a structured embed -- ONE
     FIELD PER SPORTSBOOK (2026-08-20: double group-by -- book groups ordered
     by that book's best EV, alerts within a group ordered by EV), human
     market/team names (mlb_core.notify.discord market_label/book_display),
     EV-tiered emoji, and the fair-price anchor spelled out (vs Pinnacle / vs
     consensus) instead of packed into a single cryptic text blob. Hot games
     and next-day openers are flagged inline. Alerts are also appended to
     Alerts/{day}/log.parquet so the nightly odds_alert resolve/scorecard pass
     covers them.
  5. EV TRACKING: every alert actually posted this run is ALSO logged to the
     `bets` table (system="EV", flat-stake) so profitability can be queried
     the same way as any model system -- see _log_ev_bets below and
     settle_bets._settle_ev.

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


def _grouped_fields(new: pd.DataFrame, hot: set, today_str: str, anchor_book: str | None,
                    book_display, market_label, ev_alert_emoji, team_nickname: dict) -> list[dict]:
    """Double group-by for the Discord embed: one field PER SPORTSBOOK
    (book groups ordered by that book's own best EV, descending), each
    field listing its alerts sorted by EV descending -- "in order of EV" is
    preserved as the within-book sort, "grouped by sportsbook" is the
    field-level split. Replaces the old one-field-per-alert layout, which
    made cross-book scanning (e.g. "what's DraftKings got today") a matter
    of reading every field's value text."""
    rows = [_alert_parts(r, hot, today_str, anchor_book, book_display, market_label,
                         ev_alert_emoji, team_nickname)
            for _, r in new.iterrows()]

    groups: dict[str, list[dict]] = {}
    for p in rows:
        groups.setdefault(p["book"] or "Unknown", []).append(p)

    ordered_books = sorted(groups, key=lambda b: max(p["ev"] for p in groups[b]), reverse=True)

    fields = []
    for book in ordered_books:
        book_rows = sorted(groups[book], key=lambda p: p["ev"], reverse=True)
        lines = []
        for p in book_rows:
            fair_str = f"{p['fair']:.1%}" if p["fair"] is not None else "N/A"
            lines.append(
                f"{p['emoji']} {p['badge']}**{p['who']}** {p['what']}{p['matchup_tag']} "
                f"@ **{p['american']}** -> EV **{p['ev']:+.1%}** (fair {fair_str}, {p['n_books']} bks)"
            )
        n = len(book_rows)
        name = f"🏦 {book} -- {n} alert{'s' if n != 1 else ''}"[:256]
        value = "\n".join(lines)[:1024]
        fields.append({"name": name, "value": value, "inline": False})
    return fields


def notify(new: pd.DataFrame, hot: set, today_str: str = '',
           min_ev: float = 0.03, min_books: int = 4, anchor: str | None = None) -> None:
    from mlb_core.notify.discord import (
        _post, book_display, market_label, ev_alert_emoji, TEAM_NICKNAME,
    )
    url = _alert_webhook()

    fields = _grouped_fields(new, hot, today_str, anchor, book_display, market_label,
                             ev_alert_emoji, TEAM_NICKNAME)

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


# -- 5. EV bet tracking (profitability) ---------------------------------------
#
# Every alert this pager actually posts is ALSO logged to the same `bets`
# table every model system uses, under system="EV" -- so "is this a
# profitable avenue" can be answered with BetTracker(db, system="EV")
# .summary() / settle_bets.py's nightly settlement, the same way it's
# answered for HR/K/OUTS/etc., instead of only the CLV-style
# lag-vs-informed proxy odds_alert.py already computes into
# Alerts/{day}/resolved.parquet (which resolves against a LATER quote, not
# a real settled outcome).
#
# Deliberately NOT registered in mlb_core.registry.SYSTEMS / CANONICAL_ORDER:
# monitor_performance.py's CANONICAL_ORDER loop drives the live
# suppression-gate + Discord performance-alert machinery, calibrated for
# model systems (AUC, calibration, expected_hit_rate) -- none of which
# apply to a book-vs-consensus outlier feed. Keeping EV out of that loop
# means it can never trip a false suppression-gate alert for every OTHER
# system. It settles via the normal nightly /settle job (added to
# settle_bets.SYSTEM_MAP / ALL_SYSTEMS) and is queryable directly; it just
# doesn't render in the cross-system Discord recap embed (yet -- see
# CONTEXT.md).
#
# mlb.runners.kalshi_alert (the sibling pager, same soft-line strategy,
# Kalshi mid instead of Pinnacle-consensus as the fair-price anchor) pools
# its OWN posted alerts into this SAME system="EV" table (its own
# _log_ev_bets, since its rows carry a different shape -- ev_pct/p_true/
# cons_impl, not ev/consensus_fair/decimal -- but reuses _ev_bet_type and
# _EV_BET_DB/_EV_STAKE_UNIT from here). Deliberately pooled, not kept in a
# separate system="EV_KALSHI": when both pagers independently flag the
# identical real-world quote (same market/game_pk/player/line/book -- they
# scan overlapping prop markets), it's the SAME bet either way, and the
# shared (system, game_date, game_pk, player, bet_type) dedup key correctly
# collapses it to one row instead of double-counting it.

_EV_BET_DB = "EV_Alerts/data/ev_bets.db"  # local/offline fallback only; prod uses DB_URL (Cloud SQL)
_EV_STAKE_UNIT = float(os.environ.get("EV_STAKE_UNIT", "100"))

# odds_history market code -> the underlying system's OWN bet_type
# construction, exactly as settle_bets.py already grades it. Reusing this
# means an EV alert on e.g. K_OVER_7.5 settles IDENTICALLY to a real K-system
# bet on that same line -- same market, same selection, same line, just a
# different source (soft-book-vs-consensus scan instead of the model).
_EV_MARKET_PREFIX = {
    "k_ou":     "K",
    "outs_ou":  "OUTS",
    "btb_ou":   "BATTER_TB",
    "bhits_ou": "BATTER_HITS",
    "per_ou":   "PITCHER_ER",
}

# Markets bettingpros_to_parquet.BP_TO_HISTORY carries into odds_history with
# system="" (no production settler exists for them at all yet -- run_line and
# total_runs are tracked for coverage/analysis but nothing grades them). Do
# NOT log these under system="EV"; there is nothing to settle them against.
# mlb.analysis.kalshi_vs_books.DEFAULT_MARKETS includes both (it scans every
# LIQUID Kalshi market) -- fast_alert_loop itself never scans either.
_EV_UNSETTLEABLE_MARKETS = {"game_total", "game_rl"}


def _ev_bet_type(market: str, selection: str, line, book: str | None) -> str | None:
    """(market, selection, line) -> a bet_type settle_bets.py already knows
    how to grade, suffixed with "_{book}" so two different books flagging
    the same prop don't collide on BetTracker's (system, game_date, game_pk,
    player, bet_type) dedup key. Every settler parses bet_type by a
    fixed-position prefix or split, so a trailing book suffix is inert to
    THEIR parsing -- but NRFI's/F5's bare-string bet_types ("NRFI"/"YRFI",
    "HOME"/"AWAY") and the innings-window settler's "GAME_{SIDE}" need the
    suffix stripped back off before dispatch (settle_bets._settle_ev does
    that; this function just needs to produce it consistently).
    Returns None for a market with no settler (do not log the unsettleable)
    -- either genuinely uncovered (_EV_UNSETTLEABLE_MARKETS) or unrecognised.
    """
    book_tag = (book or "unknown").lower()
    sel = str(selection).upper()

    if market == "hr_yn":
        base = "HR"
    elif market == "nrfi_ou":
        # NRFI/YRFI's own bet_type is the bare word (settle_bets._settle_nrfi
        # matches it exactly) -- there's no line, the side IS the whole bet.
        # odds_history's O/U convention for this market: OVER 0.5 = a run
        # scored = YRFI, UNDER 0.5 = no run = NRFI (bettingpros_to_parquet's
        # "run_in_1st_inning" entry is kind="total", i.e. OVER/UNDER, not
        # yes/no).
        base = "YRFI" if sel == "OVER" else "NRFI"
    elif market == "game_ml":
        base = f"GAME_{sel}"   # matches settle_bets._settle_innings_window's "GAME_{SIDE}"
    elif market == "f5_ml":
        base = sel             # F5's own bet_type IS the bare side string "HOME"/"AWAY"
    elif market in _EV_UNSETTLEABLE_MARKETS:
        return None
    else:
        prefix = _EV_MARKET_PREFIX.get(market)
        if prefix is None or pd.isna(line):
            return None
        base = f"{prefix}_{sel}_{float(line):g}"
    return f"{base}_{book_tag}"


def _log_ev_bets(posted: pd.DataFrame, run_date: str) -> int:
    """Log every alert actually posted to Discord this run into the `bets`
    table (system="EV") at a flat unit stake -- there's no model
    probability to Kelly-size by here, the whole question is "would
    striking this specific price have won," so a flat stake makes the ROI
    directly comparable across alerts. kelly_triggered=True always: a
    posted alert already cleared FAL_MIN_EV/FAL_MIN_BOOKS, so by
    construction every row here IS the signal, not a logged-but-filtered
    prediction (unlike the model systems' log-every-scored-row contract)."""
    from mlb_core.tracking import BetTracker

    if not len(posted):
        return 0
    tracker = BetTracker(_EV_BET_DB, system="EV")
    logged = 0
    for _, r in posted.iterrows():
        bet_type = _ev_bet_type(r.get("market"), r.get("selection"), r.get("line"), r.get("book"))
        if bet_type is None:
            continue
        pname = r.get("player_name")
        player = pname if isinstance(pname, str) and pname else f"{r.get('away_team')} @ {r.get('home_team')}"
        n_books = r.get("n_books")
        decimal = r.get("decimal")
        bet_id = tracker.log_bet(
            game_date       = str(r.get("game_date") or run_date),
            game_pk         = int(r["game_pk"]) if pd.notna(r.get("game_pk")) else None,
            player          = player,
            away_team       = r.get("away_team"),
            home_team       = r.get("home_team"),
            bet_type        = bet_type,
            model_prob      = float(r["consensus_fair"]) if pd.notna(r.get("consensus_fair")) else None,
            market_prob     = round(1.0 / decimal, 4) if pd.notna(decimal) and decimal else None,
            edge            = float(r["ev"]) if pd.notna(r.get("ev")) else None,
            odds            = r.get("american"),
            stake           = _EV_STAKE_UNIT,
            kelly_triggered = True,
            paper           = True,
            book            = r.get("book"),
            notes           = (f"soft-book +EV alert vs "
                               f"{'Pinnacle' if r.get('anchored') else 'consensus'} "
                               f"({int(n_books)} books)") if pd.notna(n_books) else "",
        )
        if bet_id != -1:
            logged += 1
    return logged


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
    deferred_key = f"Alerts/{day}/deferred.parquet"
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
        # Fixed 2026-08-17 (finding C6.3): rows deferred by a PRIOR run's cap
        # get first priority this run -- otherwise a persistently >max_posts
        # scan (plausible right after a lineup-news cascade, exactly the
        # highest-value moment for this pager) could keep bumping the same
        # overflow rows indefinitely, never actually posting them even
        # though they're correctly not blacklisted anymore.
        deferred = _read_parquet(deferred_key)
        if deferred is not None and len(deferred):
            dkeys = [k for k in _QUOTE_KEYS if k in new.columns and k in deferred.columns]
            dmerged = new.merge(deferred[dkeys].drop_duplicates(), on=dkeys,
                                how="left", indicator="_defer_ind")
            new["_deferred"] = (dmerged["_defer_ind"] == "both").values
        else:
            new["_deferred"] = False
        # hottest first: hot-game alerts, then by EV
        new["_hot"] = new["game_pk"].isin(hot)
        new = new.sort_values(["_deferred", "_hot", "ev"], ascending=[False, False, False]) \
                 .drop(columns=["_deferred", "_hot"])
        posted = new.head(max_posts)
        overflow = new.iloc[max_posts:]
        notify(posted, hot, today_str=day,
               min_ev=min_ev, min_books=min_books, anchor=anchor)
        try:
            _ev_logged = _log_ev_bets(posted, day)
            log.info("EV: %d/%d posted alerts logged to bets table (system=EV)",
                     _ev_logged, len(posted))
        except Exception as e:  # noqa: BLE001 -- never let bet-logging break the pager itself
            log.warning("EV bet logging failed: %s", e)
        if len(overflow):
            log.info("capped: %d further alerts not posted this run -- deferred for retry",
                     len(overflow))
        # Persist deferred state from `overflow`, not `new` -- this run's
        # cap-list supersedes whatever was deferred before (anything from
        # the old deferred set either got boosted into `posted` above, or
        # is still correctly present in this run's own overflow).
        _write_parquet(overflow[[c for c in _QUOTE_KEYS if c in overflow.columns]], deferred_key)
        # Persist notify-state from `posted`, NOT `new` -- this was the
        # actual bug (finding C6.3): dedup state used to come from the full
        # uncapped `new` set, so any row beyond max_posts got marked
        # "notified" even though it was never actually sent to Discord,
        # permanently blacklisting it from ever being reconsidered.
        seen_new = pd.concat([seen, posted[[c for c in _QUOTE_KEYS if c in posted.columns]]],
                             ignore_index=True) if seen is not None else \
            posted[[c for c in _QUOTE_KEYS if c in posted.columns]]
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
