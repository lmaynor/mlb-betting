"""
mlb.runners.kalshi_alert -- Discord pager for mlb.analysis.kalshi_vs_books.

Wraps the soft-book-vs-Kalshi-mid +EV scanner (mlb.analysis.kalshi_vs_books)
as a scheduled Cloud Run Job: scan odds_history -> classify -> post only NEW
credible ("check" verdict -- kalshi_vs_books.classify() already screens out
thin_pack/kalshi_off/stale? rows) divergences to Discord, deduped per day so
the same lagging quote never pings twice.

Read-only against odds_history + Alerts/{day}/kalshi_*.parquet state -- this
job does not fetch its own snapshot. Kalshi capture (mlb-kalshi-capture, see
deploy/setup_kalshi_capture.sh) and the soft-book trackers already run on
their own schedules (CONTEXT.md s4/s8), so this only needs to run shortly
AFTER both have landed for a given slot.

Recommended cadence (see deploy/setup_kalshi_alert_job.sh): 6x/day, ~10 min
after each SAME-DAY Kalshi-capture + soft-book-snapshot pair (15:55, 18:55,
20:25, 21:25, 21:55, 23:05 UTC -- the mlb-snapshot-* cadence in CONTEXT.md
s4, which mlb-kalshi-capture is deliberately aligned to). Deliberately skips
the two next-day-opener capture times (01:25/03:25 UTC): tomorrow's lines
are too thin/early for this scanner's book-pack consensus to be meaningful
yet, and there's nothing actionable to strike 20+ hours out.

Config via env:
    KALSHI_ALERT_MARKETS   comma list (default: kalshi_vs_books.DEFAULT_MARKETS)
    KALSHI_ALERT_MIN_EV    min ev_pct to alert on (0.03 = +3%)
    KALSHI_ALERT_MIN_BOOKS min books for a trustworthy book-pack consensus (4)
    KALSHI_ALERT_KDEV_MAX  max |p_true - book consensus| for a trusted row (0.04)
    KALSHI_ALERT_STALE_GAP flag a book this far below consensus as stale (0.15)
    KALSHI_ALERT_SOFT_ONLY 1=only alert on soft (>=8% hold) books (default 1) --
                           the strategy is soft-book mispricing; a "sharp" book
                           diverging this much from Kalshi is a weaker, more
                           suspicious signal than the classify() verdict alone
                           catches. Set 0 to also see sharp-book divergences.
    KALSHI_ALERT_MAX_POSTS max alerts posted per run (10)

Local:
  PYTHONPATH=. python3 -m mlb.runners.kalshi_alert
"""
from __future__ import annotations

import logging
import os
from datetime import date as _date

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kalshi_alert")

# identity of a bettable quote for notify-dedup (no snapshot_ts: one ping per
# quote per day, even as later runs re-confirm it) -- same shape as
# fast_alert_loop's _QUOTE_KEYS, deliberately, for a consistent convention.
_QUOTE_KEYS = ["market", "game_pk", "player_id", "line", "selection", "book"]
_LOG_KEYS = _QUOTE_KEYS + ["snapshot_ts"]


def _fmt_american(a) -> str:
    try:
        a = int(a)
    except (TypeError, ValueError):
        return "?"
    return f"+{a}" if a > 0 else str(a)


_SEL_WORDS = {"OVER": "Over", "UNDER": "Under", "YES": "Yes", "NO": "No"}


def _alert_webhook() -> str | None:
    # Same fallback convention as fast_alert_loop: a dedicated ALERTS webhook
    # if one's ever provisioned, otherwise the main picks webhook. Neither
    # deploy script sets DISCORD_WEBHOOK_ALERTS today (see CONTEXT.md s14) --
    # this just means both live alert jobs land in #daily-picks until/unless
    # a dedicated channel+secret gets provisioned.
    return os.getenv("DISCORD_WEBHOOK_ALERTS") or os.getenv("DISCORD_WEBHOOK_URL")


def _alert_fields(new: pd.DataFrame, names: dict) -> list[dict]:
    from mlb_core.notify.discord import book_display, market_label, ev_alert_emoji, TEAM_NICKNAME

    fields = []
    for _, r in new.iterrows():
        sel = str(r.get("selection", "")).upper()
        sel_word = _SEL_WORDS.get(sel, sel.title())
        line = r.get("line")
        label = market_label(r["market"])

        away, home = r.get("away_team"), r.get("home_team")
        matchup = (f"{TEAM_NICKNAME.get(away, away)} @ {TEAM_NICKNAME.get(home, home)}"
                   if away else f"game {r.get('game_pk')}")
        pid = r.get("player_id")
        pname = names.get(int(pid)) if pd.notna(pid) else None

        if sel in ("HOME", "AWAY") and not pname:
            # team-level bet (moneyline/run line) -- name the team, not the
            # literal side word, matching _format_bet_headline's convention.
            team_full = TEAM_NICKNAME.get(home, home) if sel == "HOME" \
                else TEAM_NICKNAME.get(away, away)
            what = f"{team_full} {label}"
        elif sel in ("YES", "NO"):
            what = f"{label} ({sel_word})"
        elif pd.notna(line):
            what = f"{sel_word} {line:g} {label}"
        else:
            what = f"{sel_word} {label}"

        # Header names the bet (+ the player, if there is one -- `what` already
        # names the team for team-level bets, so no "-- Yankees" repeat there).
        # The matchup always goes in the value line, since it's the only place
        # the opponent shows up for a team-level bet.
        soft_tag = " (soft book)" if r.get("soft") else ""
        name = (f"{ev_alert_emoji(r['ev_pct'])} {what}" + (f" -- {pname}" if pname else ""))[:256]
        value = (
            f"{matchup}\n"
            f"**{book_display(r.get('book'))} {_fmt_american(r.get('american'))}**{soft_tag} -> "
            f"EV **{r['ev_pct']:+.1%}**\n"
            f"vs Kalshi mid **{r['p_true']:.1%}** (book pack **{r['cons_impl']:.1%}**, "
            f"{int(r['n_books'])} books)"
        )
        fields.append({"name": name, "value": value[:1024], "inline": False})
    return fields


def notify(new: pd.DataFrame, names: dict, min_ev: float, min_books: int) -> None:
    from mlb_core.notify.discord import _post

    url = _alert_webhook()
    fields = _alert_fields(new, names)

    if not url:
        log.warning("no Discord webhook -- printing alerts only")
        for f in fields:
            print(f"{f['name']}\n  {f['value']}")
        return

    embed = {
        "title": f"📡 Kalshi +EV Alerts -- {len(new)} new",
        "description": (
            f"Soft-book price diverges from Kalshi's no-vig mid by >= {min_ev:.0%}, "
            f"corroborated by the book pack (not a lone-wolf Kalshi mid)."
        ),
        "color": 0x00D2A0,  # Kalshi-adjacent teal, distinct from fast_alert_loop's amber
        "fields": fields[:25],
        "footer": {"text": f"min EV {min_ev:.0%} | min {min_books} books | "
                           f"verdict=check only | kalshi_alert"},
    }
    _post(url, {"embeds": [embed]})


def run(run_date: str | None = None) -> dict:
    from mlb.analysis.kalshi_vs_books import scan, classify, _player_names, DEFAULT_MARKETS
    from mlb.runners.fast_alert_loop import _read_parquet, _write_parquet

    day = run_date or _date.today().isoformat()

    markets = os.environ.get("KALSHI_ALERT_MARKETS", ",".join(DEFAULT_MARKETS)).split(",")
    min_ev = float(os.environ.get("KALSHI_ALERT_MIN_EV", "0.03"))
    min_books = int(os.environ.get("KALSHI_ALERT_MIN_BOOKS", "4"))
    kdev_max = float(os.environ.get("KALSHI_ALERT_KDEV_MAX", "0.04"))
    stale_gap = float(os.environ.get("KALSHI_ALERT_STALE_GAP", "0.15"))
    soft_only = os.environ.get("KALSHI_ALERT_SOFT_ONLY", "1") not in ("0", "", "false", "False")
    max_posts = int(os.environ.get("KALSHI_ALERT_MAX_POSTS", "10"))

    found = scan(markets, date=day, min_ev=min_ev)
    if not len(found):
        log.info("scan: no kalshi vs book divergences >= %.0f%%", min_ev * 100)
        return {"status": "ok", "day": day, "new_alerts": 0}

    found = classify(found, kdev_max=kdev_max, stale_gap=stale_gap, min_books=min_books)
    counts = found["verdict"].value_counts().to_dict()
    log.info("verdict mix: %s", counts)

    credible = found[found["verdict"] == "check"]
    if soft_only:
        credible = credible[credible["soft"]]
    if not len(credible):
        log.info("scan: %d divergences, none credible (verdict=check%s)",
                 len(found), " + soft" if soft_only else "")
        return {"status": "ok", "day": day, "new_alerts": 0}

    # dedup vs already-notified quotes for today
    notified_key = f"Alerts/{day}/kalshi_notified.parquet"
    deferred_key = f"Alerts/{day}/kalshi_deferred.parquet"
    seen = _read_parquet(notified_key)
    if seen is not None and len(seen):
        keys = [k for k in _QUOTE_KEYS if k in credible.columns and k in seen.columns]
        merged = credible.merge(seen[keys].drop_duplicates(), on=keys,
                                how="left", indicator=True)
        new = credible[(merged["_merge"] == "left_only").values].copy()
    else:
        new = credible.copy()

    if not len(new):
        log.info("scan: %d credible divergences, all already notified today", len(credible))
        return {"status": "ok", "day": day, "new_alerts": 0}

    # Fixed 2026-08-17 (finding C6.3): rows deferred by a PRIOR run's cap get
    # first priority this run -- otherwise a persistently >max_posts scan
    # could keep bumping the same overflow rows indefinitely. Mirrors the
    # identical fix in fast_alert_loop.py.
    deferred = _read_parquet(deferred_key)
    if deferred is not None and len(deferred):
        dkeys = [k for k in _QUOTE_KEYS if k in new.columns and k in deferred.columns]
        dmerged = new.merge(deferred[dkeys].drop_duplicates(), on=dkeys,
                            how="left", indicator="_defer_ind")
        new["_deferred"] = (dmerged["_defer_ind"] == "both").values
    else:
        new["_deferred"] = False
    new = new.sort_values(["_deferred", "ev_pct"], ascending=[False, False]) \
             .drop(columns="_deferred")
    posted = new.head(max_posts)
    overflow = new.iloc[max_posts:]

    names_raw = _player_names(day)
    names = {int(pid): pname for pid, pname in names_raw.items()} if names_raw else {}

    notify(posted, names, min_ev, min_books)
    if len(overflow):
        log.info("capped: %d further alerts not posted this run -- deferred for retry",
                 len(overflow))

    # Persist deferred state from `overflow`, not `new` (see comment above).
    _write_parquet(overflow[[c for c in _QUOTE_KEYS if c in overflow.columns]], deferred_key)
    # Persist notify-state from `posted`, NOT `new` -- this was the actual
    # bug (finding C6.3): dedup state used to come from the full uncapped
    # `new` set, so any row beyond max_posts got marked "notified" even
    # though it was never actually sent to Discord, permanently
    # blacklisting it from ever being reconsidered.
    seen_new = pd.concat([seen, posted[[c for c in _QUOTE_KEYS if c in posted.columns]]],
                        ignore_index=True) if seen is not None else \
        posted[[c for c in _QUOTE_KEYS if c in posted.columns]]
    _write_parquet(seen_new.drop_duplicates(), notified_key)

    logkey = f"Alerts/{day}/kalshi_log.parquet"
    prior = _read_parquet(logkey)
    merged_log = pd.concat([prior, new], ignore_index=True) if prior is not None else new
    merged_log = merged_log.drop_duplicates(
        subset=[c for c in _LOG_KEYS if c in merged_log.columns], keep="last")
    _write_parquet(merged_log, logkey)

    log.info("notified %d new alerts (%d credible, %d total divergences)",
             len(posted), len(credible), len(found))
    return {"status": "ok", "day": day, "new_alerts": int(len(new))}


def main() -> int:
    res = run()
    print(f"kalshi_alert {res['day']}: {res['new_alerts']} new alerts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
