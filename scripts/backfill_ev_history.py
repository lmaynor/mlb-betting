#!/usr/bin/env python3
"""
scripts/backfill_ev_history.py -- recover historical +EV alerts (system="EV")
that were successfully posted to Discord but never durably logged, because
mlb-fast-alert and mlb-kalshi-alert had no MLB_DB_URL / Cloud SQL wiring from
the day the EV bet tracking feature shipped (2026-08-20) until it was fixed
2026-09-18 (see deploy/setup_fast_alert.sh, deploy/setup_kalshi_alert_job.sh,
and docs/solutions/runtime-errors/ev-alert-jobs-missing-db-wiring.md).

BetTracker's DB_URL-empty fallback (mlb_core/tracking/bet_tracker.py's
_make_engine) silently wrote every "logged" row to an ephemeral sqlite file
inside each Cloud Run Job's own container -- gone the instant the job
exited, even though the job's own logs showed a convincing "N/N posted
alerts logged to bets table" success message every run.

The underlying alert data survives independently in GCS, unaffected by that
bug:
    Alerts/{day}/log.parquet             every candidate fast_alert_loop scanned
    Alerts/{day}/notified.parquet        the TRUE posted subset (quote keys only)
    Alerts/{day}/kalshi_log.parquet      kalshi_alert's equivalent of log.parquet
    Alerts/{day}/kalshi_notified.parquet kalshi_alert's equivalent of notified.parquet

This script inner-joins each day's log -> notified on the shared quote-
identity columns to recover exactly what was actually posted (not every
candidate merely scanned), reconstructs each row exactly as
fast_alert_loop._log_ev_bets() / kalshi_alert._log_ev_bets() would have
(same bet_type + kelly_pct computation), inserts via the real
BetTracker.log_bet() (safe to re-run: hits the same
(system, game_date, game_pk, player, bet_type, kelly_triggered) dedup index
every live bet does, so it can never double-insert), then grades the
recovered rows via the same _settle_ev logic settle_bets.py uses in
production -- WITHOUT settle_bets.run()'s unconditional Discord recap post,
which would otherwise spam #daily-recap with a misleading "yesterday" recap
covering two months of recovered history.

Requires MLB_GCS_BUCKET and MLB_DB_URL in the environment (the latter
pointing at a real, writable Postgres -- e.g. a local Cloud SQL Auth Proxy
tunnel) since this reuses mlb_core.storage / mlb_core.tracking.BetTracker
verbatim, same as every other runner in this repo.

Usage:
    PYTHONPATH=. python3 scripts/backfill_ev_history.py --dry-run
    PYTHONPATH=. python3 scripts/backfill_ev_history.py
    PYTHONPATH=. python3 scripts/backfill_ev_history.py --skip-settle
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime

import pandas as pd

from mlb_core import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_ev_history")

_QUOTE_KEYS = ["market", "game_pk", "player_id", "line", "selection", "book"]

# Real, historical data-quality bug (finding C4.1, fixed 2026-08-17): Kalshi
# (a no-vig SHARP REFERENCE feed, not a real sportsbook) was pooled into the
# same odds_history `book` column as real tradeable prices, so outlier_scan
# could -- and did -- flag "Kalshi's own reference price diverges from
# consensus" as if it were a real soft-book mispricing. mlb.analysis.
# backtest_market.OFFSHORE is the already-established, live-pager-consuming
# denylist for this (its own comment: "inherited by every consumer of
# OFFSHORE, including outlier_scan.py's live fast_alert_loop.py pager").
# Confirmed empirically while building this recovery: 2026-08-12 (380/559 =
# 68%) and 2026-08-16 (661/809 = 82%) of that day's "posted alerts" had
# book=="kalshi" at absurd prices (e.g. +9900 American on a total-bases
# prop) -- not real, placeable bets. Zero such rows appear from 2026-08-17
# onward (the fix date), confirmed on a clean day (2026-09-05: 0/119).
# Recovering these pre-fix rows into real bets-table history would corrupt
# both the flat-stake ROI and the Kelly-bankroll report with impossible
# outcomes -- skip them, matching what the live pager itself does today.
from mlb.analysis.backtest_market import OFFSHORE as _NOT_REAL_BOOKS


def _read_parquet_safe(key: str):
    try:
        return storage.read_parquet(key)
    except Exception:  # noqa: BLE001 -- most days simply won't have this file
        return None


def _discover_alert_days() -> list[str]:
    """Every YYYY-MM-DD day with an Alerts/ prefix in GCS, sorted."""
    keys = storage.list_keys("Alerts/")
    days = sorted({
        parts[1] for k in keys
        if (parts := k.split("/")) and len(parts) > 1 and len(parts[1]) == 10
    })
    return days


def _posted_subset(log_df, notified_df):
    """Inner-join log (full detail) -> notified (true posted keys) so only
    what was ACTUALLY posted to Discord is recovered, not every candidate
    that was merely scanned that day.

    log.parquet's own dedup key includes snapshot_ts (fast_alert_loop._LOG_KEYS),
    so a quote re-scanned by multiple runs that day (every 15 min in the
    strike window) legitimately has several rows there even though it was
    only POSTED once -- confirmed on a real day (2026-07-24: 178 unique
    posted quotes, 204 log rows referencing them, up to 4 snapshot copies of
    a single quote). Collapse to one row per quote identity (latest
    snapshot_ts -- freshest recorded price) before joining, so the
    recovered count matches the true posted count. Not a correctness risk
    either way -- BetTracker's own dedup index would collapse this at
    insert time regardless, since bet_type doesn't encode snapshot_ts -- but
    this keeps the dry-run report and Kelly computation from doing
    redundant/misleading multi-counting."""
    if log_df is None or notified_df is None or log_df.empty or notified_df.empty:
        return None
    keys = [k for k in _QUOTE_KEYS if k in log_df.columns and k in notified_df.columns]
    if not keys:
        return None
    log_latest = (log_df.sort_values("snapshot_ts") if "snapshot_ts" in log_df.columns else log_df) \
        .drop_duplicates(subset=keys, keep="last")
    return log_latest.merge(notified_df[keys].drop_duplicates(), on=keys, how="inner")


# -- recovery: fast_alert_loop --------------------------------------------------

def recover_fast_alert(days: list[str]) -> pd.DataFrame:
    from mlb.runners.fast_alert_loop import _ev_bet_type, _EV_KELLY_FRACTION
    from mlb_core.odds.utils import kelly_pct as kpct

    rows = []
    skipped_not_real_book = 0
    for day in days:
        posted = _posted_subset(
            _read_parquet_safe(f"Alerts/{day}/log.parquet"),
            _read_parquet_safe(f"Alerts/{day}/notified.parquet"),
        )
        if posted is None or posted.empty:
            continue
        for _, r in posted.iterrows():
            if str(r.get("book", "")).lower() in _NOT_REAL_BOOKS:
                skipped_not_real_book += 1
                continue
            bet_type = _ev_bet_type(r.get("market"), r.get("selection"), r.get("line"), r.get("book"))
            if bet_type is None:
                continue
            model_prob = float(r["consensus_fair"]) if pd.notna(r.get("consensus_fair")) else None
            odds = r.get("american")
            decimal = r.get("decimal")
            pname = r.get("player_name")
            player = pname if isinstance(pname, str) and pname else f"{r.get('away_team')} @ {r.get('home_team')}"
            n_books = r.get("n_books")
            rows.append(dict(
                source="fast_alert", day=day,
                game_date=str(r.get("game_date") or day),
                game_pk=int(r["game_pk"]) if pd.notna(r.get("game_pk")) else None,
                player=player, away_team=r.get("away_team"), home_team=r.get("home_team"),
                bet_type=bet_type, model_prob=model_prob,
                market_prob=round(1.0 / decimal, 4) if pd.notna(decimal) and decimal else None,
                edge=float(r["ev"]) if pd.notna(r.get("ev")) else None,
                kelly_pct=round(kpct(model_prob, odds, _EV_KELLY_FRACTION), 4),
                odds=odds, book=r.get("book"),
                notes=(f"soft-book +EV alert vs "
                       f"{'Pinnacle' if r.get('anchored') else 'consensus'} "
                       f"({int(n_books)} books)") if pd.notna(n_books) else "",
            ))
        logger.info("fast_alert %s: %d posted alerts recovered", day, len(posted))
    if skipped_not_real_book:
        logger.info("fast_alert: skipped %d pre-2026-08-17 alert(s) with a non-bettable "
                     "OFFSHORE book (kalshi/pinnacle/consensus/...) -- see backtest_market.OFFSHORE",
                     skipped_not_real_book)
    return pd.DataFrame(rows)


# -- recovery: kalshi_alert -------------------------------------------------------

def recover_kalshi(days: list[str]) -> pd.DataFrame:
    from mlb.runners.fast_alert_loop import _ev_bet_type, _EV_KELLY_FRACTION, resolve_player_names
    from mlb_core.odds.utils import kelly_pct as kpct

    day_frames = []
    for day in days:
        posted = _posted_subset(
            _read_parquet_safe(f"Alerts/{day}/kalshi_log.parquet"),
            _read_parquet_safe(f"Alerts/{day}/kalshi_notified.parquet"),
        )
        if posted is None or posted.empty:
            continue
        posted = posted.copy()
        posted["_day"] = day
        day_frames.append(posted)
        logger.info("kalshi %s: %d posted alerts recovered", day, len(posted))
    if not day_frames:
        return pd.DataFrame()
    combined = pd.concat(day_frames, ignore_index=True)

    # kalshi_alert resolves player_id -> name via a per-run in-memory dict
    # that isn't persisted anywhere; player_id itself IS in kalshi_log.parquet,
    # so re-resolve names now the same way the live pager does (names don't
    # change) rather than losing them.
    names = (resolve_player_names(combined["player_id"].dropna().unique())
             if "player_id" in combined.columns else {})

    rows = []
    skipped_not_real_book = 0
    for _, r in combined.iterrows():
        if str(r.get("book", "")).lower() in _NOT_REAL_BOOKS:
            skipped_not_real_book += 1
            continue
        bet_type = _ev_bet_type(r.get("market"), r.get("selection"), r.get("line"), r.get("book"))
        if bet_type is None:
            continue
        model_prob = float(r["p_true"]) if pd.notna(r.get("p_true")) else None
        odds = r.get("american")
        pid = r.get("player_id")
        pname = names.get(int(pid)) if pd.notna(pid) else None
        player = pname or f"{r.get('away_team')} @ {r.get('home_team')}"
        n_books = r.get("n_books")
        rows.append(dict(
            source="kalshi", day=r["_day"],
            game_date=str(r.get("game_date") or r["_day"]),
            game_pk=int(r["game_pk"]) if pd.notna(r.get("game_pk")) else None,
            player=player, away_team=r.get("away_team"), home_team=r.get("home_team"),
            bet_type=bet_type, model_prob=model_prob,
            market_prob=float(r["cons_impl"]) if pd.notna(r.get("cons_impl")) else None,
            edge=float(r["ev_pct"]) if pd.notna(r.get("ev_pct")) else None,
            kelly_pct=round(kpct(model_prob, odds, _EV_KELLY_FRACTION), 4),
            odds=odds, book=r.get("book"),
            notes=(f"soft-book +EV alert vs Kalshi mid ({int(n_books)} books)"
                   if pd.notna(n_books) else ""),
        ))
    if skipped_not_real_book:
        logger.info("kalshi: skipped %d alert(s) with a non-bettable OFFSHORE book "
                     "-- see backtest_market.OFFSHORE", skipped_not_real_book)
    return pd.DataFrame(rows)


# -- insert + settle --------------------------------------------------------------

def insert_rows(df: pd.DataFrame) -> tuple[int, int]:
    """Insert recovered rows via the real BetTracker.log_bet() -- hits the
    same dedup index every live bet does, so this can never double-insert
    even if re-run, or if a handful of rows somehow already made it into
    Postgres some other way. Returns (inserted, duplicate)."""
    from mlb_core.tracking import BetTracker
    from mlb.runners.fast_alert_loop import _EV_BET_DB, _EV_STAKE_UNIT

    tracker = BetTracker(_EV_BET_DB, system="EV")
    inserted = duplicate = 0
    for _, r in df.iterrows():
        bet_id = tracker.log_bet(
            game_date=r["game_date"], game_pk=r["game_pk"], player=r["player"],
            away_team=r["away_team"], home_team=r["home_team"], bet_type=r["bet_type"],
            model_prob=r["model_prob"], market_prob=r["market_prob"], edge=r["edge"],
            kelly_pct=r["kelly_pct"], odds=r["odds"], stake=_EV_STAKE_UNIT,
            kelly_triggered=True, paper=True, book=r["book"], notes=r["notes"],
        )
        if bet_id == -1:
            duplicate += 1
        else:
            inserted += 1
    return inserted, duplicate


def settle_recovered() -> dict:
    """Grade every still-pending system='EV' row via the SAME _settle_ev
    logic settle_bets.py uses in production -- deliberately NOT
    settle_bets.run() itself, which unconditionally posts a Discord recap
    (#daily-recap) that would misleadingly summarize two months of recovered
    history as "yesterday".

    Deliberately does NOT call settle_bets._void_stale_nonfinal_bets(). That
    function's age-based catch-all ("still pending after
    UNSETTLEABLE_VOID_DAYS=7 -> void it") is designed for the real daily
    /settle loop, where a bet's age reflects how many DAILY RETRY attempts
    have already failed to grade it -- a legitimate "something is wrong"
    signal there. On a one-time bulk backfill, every row's age instead just
    reflects how long ago the real game was played, and this is each row's
    FIRST-EVER settlement attempt -- so the catch-all fires on nearly
    everything before the real per-market settlers below ever get a chance.
    Confirmed live: an earlier run of this function wrongly voided 5,295 of
    6,090 rows this way (0 were ever genuinely non-Final -- game_cache
    showed 848/858 Final on that same run); those were reverted back to
    pending and this function fixed same-session. A genuinely non-Final
    game_pk (a real, still-outstanding postponement even now) simply stays
    pending here -- rare enough after 2+ months to be worth a manual look
    rather than an automatic void."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from sqlalchemy import text
    from mlb_core.tracking.bet_tracker import _make_engine
    from mlb_core.data.game_result import fetch_game_result
    from mlb.runners.settle_bets import _settle_ev

    engine = _make_engine(db_path="unused")
    with engine.connect() as conn:
        pending = pd.read_sql(text("SELECT * FROM bets WHERE system='EV' AND result IS NULL"), conn)
    if pending.empty:
        logger.info("settle_recovered: nothing pending")
        return {"settled": 0, "still_pending": 0}

    game_pks = sorted(set(pd.to_numeric(pending["game_pk"], errors="coerce").dropna().astype(int)))
    game_cache: dict = {}
    with ThreadPoolExecutor(max_workers=min(8, len(game_pks) or 1)) as pool:
        futs = {pool.submit(fetch_game_result, gpk): gpk for gpk in game_pks}
        for fut in as_completed(futs):
            gpk = futs[fut]
            try:
                game_cache[gpk] = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.warning("fetch_game_result(%s) raised: %s", gpk, e)
                game_cache[gpk] = None
    logger.info("settle_recovered: fetched results for %d game_pks (%d Final)",
                len(game_pks), sum(1 for v in game_cache.values() if v is not None))

    outcomes = _settle_ev(pending, game_cache)
    if outcomes:
        settled_at = datetime.now().isoformat()
        with engine.begin() as conn:
            for o in outcomes:
                conn.execute(
                    text("UPDATE bets SET result=:r, profit=:p, settled_at=:s WHERE id=:id"),
                    {"r": o["result"], "p": o["profit"], "s": settled_at, "id": o["id"]},
                )
    still_pending = len(pending) - len(outcomes)
    logger.info("settle_recovered: settled %d, still pending %d (non-Final games)",
                len(outcomes), still_pending)
    return {"settled": len(outcomes), "still_pending": still_pending}


# -- main -------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Recover and print, but do not write to the DB")
    parser.add_argument("--skip-settle", action="store_true", help="Insert recovered rows but don't grade them")
    args = parser.parse_args()

    days = _discover_alert_days()
    logger.info("discovered %d Alerts/ day(s) in GCS: %s .. %s",
                len(days), days[0] if days else "-", days[-1] if days else "-")

    fal_df = recover_fast_alert(days)
    kal_df = recover_kalshi(days)
    combined = pd.concat([fal_df, kal_df], ignore_index=True) if len(fal_df) or len(kal_df) else pd.DataFrame()

    logger.info("=== Recovery summary ===")
    logger.info("fast_alert: %d posted alerts across %d day(s)",
                len(fal_df), fal_df["day"].nunique() if len(fal_df) else 0)
    logger.info("kalshi:     %d posted alerts across %d day(s)",
                len(kal_df), kal_df["day"].nunique() if len(kal_df) else 0)
    logger.info("combined:   %d rows recovered (BetTracker's own dedup index applies at insert time)",
                len(combined))
    if len(combined):
        logger.info("by source:\n%s", combined["source"].value_counts().to_string())
        sample_cols = ["source", "game_date", "bet_type", "player", "odds", "model_prob", "edge", "kelly_pct"]
        logger.info("sample rows:\n%s", combined.sample(min(5, len(combined)))[sample_cols].to_string())

    if args.dry_run:
        logger.info("--dry-run: not writing to the DB. Re-run without --dry-run to insert for real.")
        return

    if not len(combined):
        logger.info("nothing to insert.")
        return

    inserted, duplicate = insert_rows(combined)
    logger.info("insert: inserted=%d duplicate(already-present)=%d", inserted, duplicate)

    if not args.skip_settle:
        stats = settle_recovered()
        logger.info("settlement: %s", stats)


if __name__ == "__main__":
    main()
