"""
mlb.analysis.ev_remediate -- one-off cleanup of system="EV" rows in the bets table
after the 2026-10-02 audit (docs/solutions/logic-errors/
ev-bet-type-ignores-side-and-line-misgrades.md).

Why: rows already in `bets` were written/graded under a broken bet_type mapping
(HR "Under 0.5" graded as "1+ HR wins"), a backfill that lost ~53% of player
names (-> "AWAY @ HOME" -> void), and (latent) inverted NRFI/YRFI. The alert
trail in GCS (Alerts/{day}/*.parquet) is independent and complete, so the
cleanest fix is: back up, delete every system='EV' row, rebuild from the trail
with the FIXED code, grade, report.

Runs as a Cloud Run Job (needs MLB_DB_URL + Cloud SQL + the GCS bucket secret --
see deploy/setup_ev_remediate_job.sh). Two modes:

    --stats   READ-ONLY. Persistence check ("do live EV rows actually land in
              Postgres?") + a profile of what's there. Writes nothing.
    --apply   1) CREATE TABLE <backup> AS SELECT ... WHERE system='EV' (skipped if
                 it already exists -- a re-run never overwrites the backup)
              2) DELETE FROM bets WHERE system='EV'
              3) rebuild from the GCS trail (scripts/backfill_ev_history.py),
                 insert via BetTracker (dedup index applies), grade via the
                 real settlers
              4) print the flat + Kelly report (mlb.analysis.ev_kelly_bankroll)
              Safe to re-run: the rebuild is deterministic and dedup-protected.

Run the pagers on the FIXED image before --apply, or an old-code pager can
re-insert a mis-graded row between the delete and the rebuild (the next
--apply would clean it, but avoid the churn).
"""
from __future__ import annotations

import argparse
import logging
import re

import pandas as pd
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ev_remediate")

DEFAULT_BACKUP = "bets_ev_backup_20261003"
LIVE_WIRING_FIXED_ON = "2026-09-18"   # mlb-fast-alert / mlb-kalshi-alert got MLB_DB_URL


def _safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError(f"unsafe table name: {name!r}")
    return name


def _table_exists(conn, name: str) -> bool:
    if conn.dialect.name == "sqlite":
        q = text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=:n")
    else:
        q = text("SELECT count(*) FROM information_schema.tables WHERE table_name=:n")
    return int(conn.execute(q, {"n": name}).scalar() or 0) > 0


def stats(engine) -> dict:
    """Read-only profile of system='EV'. The key number for the persistence check
    is `rows_since_wiring_fix`: >0 means live pager rows really land in Postgres."""
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT id, game_date, bet_type, player, result, kelly_pct, "
                              "created_at FROM bets WHERE system='EV'"), conn)
    out = {"total_rows": len(df)}
    if df.empty:
        return out
    since = df[df["created_at"].astype(str) >= LIVE_WIRING_FIXED_ON]
    out.update({
        "min_created_at": str(df["created_at"].min()),
        "max_created_at": str(df["created_at"].max()),
        "rows_since_wiring_fix": len(since),
        "rows_since_wiring_fix_with_kelly_pct": int(since["kelly_pct"].notna().sum()),
        "rows_with_matchup_as_player": int(df["player"].astype(str).str.contains(" @ ").sum()),
        "hr_rows": int(df["bet_type"].astype(str).str.upper().str.startswith("HR").sum()),
        "by_result": df["result"].fillna("pending").value_counts().to_dict(),
        "by_day_last7": since.groupby(since["created_at"].astype(str).str[:10]).size().tail(7).to_dict(),
    })
    return out


def backup_and_delete(engine, backup_table: str = DEFAULT_BACKUP) -> dict:
    """Back up then delete every system='EV' row. Returns counts. The backup is
    created only if absent so a re-run can never overwrite the original rows."""
    backup_table = _safe_ident(backup_table)
    with engine.begin() as conn:
        n_before = int(conn.execute(text("SELECT count(*) FROM bets WHERE system='EV'")).scalar() or 0)
        created = False
        if not _table_exists(conn, backup_table):
            conn.execute(text(f"CREATE TABLE {backup_table} AS SELECT * FROM bets WHERE system='EV'"))
            created = True
        n_backup = int(conn.execute(text(f"SELECT count(*) FROM {backup_table}")).scalar() or 0)
        if created and n_backup != n_before:
            raise RuntimeError(f"backup row count {n_backup} != source {n_before}; refusing to delete")
        if n_before and n_backup == 0:
            raise RuntimeError("backup table is empty; refusing to delete")
        res = conn.execute(text("DELETE FROM bets WHERE system='EV'"))
    return {"rows_before": n_before, "backup_table": backup_table,
            "backup_created_now": created, "backup_rows": n_backup, "deleted": res.rowcount}


def rebuild() -> dict:
    """Recover posted alerts from the GCS trail with the fixed code, insert, grade."""
    import scripts.backfill_ev_history as bf

    days = bf._discover_alert_days()
    log.info("rebuild: %d Alerts/ day(s): %s .. %s", len(days),
             days[0] if days else "-", days[-1] if days else "-")
    fal_df = bf.recover_fast_alert(days)
    kal_df = bf.recover_kalshi(days)
    combined = pd.concat([fal_df, kal_df], ignore_index=True) if len(fal_df) or len(kal_df) else pd.DataFrame()
    unnamed = int(combined["player"].astype(str).str.contains(" @ ").sum()) if len(combined) else 0
    log.info("rebuild: recovered fast_alert=%d kalshi=%d (still unnamed: %d)",
             len(fal_df), len(kal_df), unnamed)
    inserted, duplicate = bf.insert_rows(combined) if len(combined) else (0, 0)
    log.info("rebuild: inserted=%d duplicate=%d", inserted, duplicate)
    settled = bf.settle_recovered()
    return {"recovered": len(combined), "unnamed": unnamed,
            "inserted": inserted, "duplicate": duplicate, **settled}


def report(engine, bankroll: float = 1000.0, max_pct: float = 0.05) -> dict:
    from mlb.analysis.ev_kelly_bankroll import load_settled_ev, kelly_bankroll_report
    df = load_settled_ev(engine)
    if df.empty:
        return {"n_bets": 0}
    r = kelly_bankroll_report(df, bankroll=bankroll, max_pct=max_pct)
    r.pop("detail", None)
    with engine.connect() as conn:
        by = pd.read_sql(text("SELECT result, count(*) AS n FROM bets WHERE system='EV' "
                              "GROUP BY result"), conn)
    r["by_result"] = {str(k): int(v) for k, v in zip(by["result"].fillna("pending"), by["n"])}
    return r


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stats", action="store_true", help="read-only persistence check + profile")
    g.add_argument("--apply", action="store_true", help="backup, delete, rebuild, grade, report")
    p.add_argument("--backup-table", default=DEFAULT_BACKUP)
    p.add_argument("--bankroll", type=float, default=1000.0)
    p.add_argument("--max-pct", type=float, default=0.05)
    args = p.parse_args(argv)

    from mlb_core.tracking.bet_tracker import _make_engine
    engine = _make_engine(db_path="unused")
    log.info("dialect=%s", engine.dialect.name)

    if args.stats:
        for k, v in stats(engine).items():
            log.info("STATS %s = %s", k, v)
        return 0

    log.info("APPLY step 1/4: stats BEFORE")
    for k, v in stats(engine).items():
        log.info("BEFORE %s = %s", k, v)
    log.info("APPLY step 2/4: backup + delete")
    log.info("DELETE %s", backup_and_delete(engine, args.backup_table))
    log.info("APPLY step 3/4: rebuild from the GCS alert trail")
    log.info("REBUILD %s", rebuild())
    log.info("APPLY step 4/4: stats AFTER + report")
    for k, v in stats(engine).items():
        log.info("AFTER %s = %s", k, v)
    for k, v in report(engine, args.bankroll, args.max_pct).items():
        log.info("REPORT %s = %s", k, v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
