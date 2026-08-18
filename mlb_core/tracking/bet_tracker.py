"""
mlb_core.tracking.bet_tracker v3 — Cloud SQL + SQLite fallback.

Changes from v2:
  - Added `kelly_triggered` BOOLEAN column. True when the prediction
    cleared both min_edge and min_kelly_pct and received a non-zero stake.
    False for predictions that were scored but filtered out.
  - Added dedup check in log_bet(): skips insert if a row already exists
    for (system, game_date, game_pk, player, bet_type). Returns -1 on
    duplicate. Morning + evening runs both score; the first one wins.
    (2026-08-18: added `player` to the key -- `bet_type` alone collides
    across different players/pitchers in the same game for every
    per-player market; see the comment above idx_bets_dedup_v3 below.)
  - All runners now call log_bet() for every scored prediction (not just
    qualifying ones), setting kelly_triggered=False and stake=0 for
    filtered predictions. This supports threshold/Kelly post-mortems.
"""
import os
from pathlib import Path
from datetime import datetime

import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text

from mlb_core.config import DB_URL

import logging
logger = logging.getLogger(__name__)


def _safe_int(v):
    """Coerce a value from a pandas row to a native Python int, or None.

    Guards against a classic pandas footgun: a list-of-dicts with an
    int-or-None column (e.g. morning_odds is None for any market not in the
    morning snapshot) silently upcasts the WHOLE column to float64 once it
    goes through pd.DataFrame(...) -- so a clean -149 becomes -149.0 by the
    time a runner's .iterrows() loop reads it back out. Postgres's INTEGER
    columns reject that outright (pg_strtoint32: 'invalid input syntax for
    type integer: "-149.0"'), crashing the entire log_bet() insert -- not
    just the one row. game_pk, odds, and morning_odds are all INTEGER columns
    that pass through this exact list-of-dicts -> DataFrame -> iterrows() ->
    log_bet() pipeline in run_k.py/run_f5.py/run_nrfi.py/run_hr.py, so all
    three are cast here rather than trusting each caller to remember.
    """
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return int(v)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bets (
    id               SERIAL PRIMARY KEY,
    system           TEXT    NOT NULL,
    game_date        TEXT,
    game_pk          INTEGER,
    player           TEXT,
    away_team        TEXT,
    home_team        TEXT,
    bet_type         TEXT,
    model_prob       REAL,
    market_prob      REAL,
    edge             REAL,
    kelly_pct        REAL,
    odds             INTEGER,
    stake            REAL,
    kelly_triggered  BOOLEAN DEFAULT TRUE,
    paper            INTEGER DEFAULT 1,
    result           TEXT,
    profit           REAL,
    settled_at       TEXT,
    notes            TEXT,
    created_at       TEXT,
    lambda_k         REAL,
    proj_k           REAL,
    book             TEXT,
    closing_odds     REAL,
    closing_prob     REAL,
    clv_pct          REAL,
    morning_odds     INTEGER,
    line_move_pct    REAL
)
"""

_SCHEMA_SQL_SQLITE = _SCHEMA_SQL.replace(
    "id               SERIAL PRIMARY KEY",
    "id               INTEGER PRIMARY KEY AUTOINCREMENT",
)

# Migration: add lambda_k / proj_k columns for K/OUTS diagnostics.
_MIGRATE_LAMBDA_SQL = """
ALTER TABLE bets ADD COLUMN lambda_k REAL
"""
_MIGRATE_PROJ_K_SQL = """
ALTER TABLE bets ADD COLUMN proj_k REAL
"""

_MIGRATE_BOOK_SQL = """
ALTER TABLE bets ADD COLUMN book TEXT
"""

# Migration: add CLV columns (T08, 2026-05-19).
# Migration: add line movement columns (E10, 2026-05-21).
_MIGRATE_MORNING_ODDS_SQL = "ALTER TABLE bets ADD COLUMN morning_odds INTEGER"
_MIGRATE_LINE_MOVE_SQL    = "ALTER TABLE bets ADD COLUMN line_move_pct REAL"
_MIGRATE_CLOSING_ODDS_SQL = "ALTER TABLE bets ADD COLUMN closing_odds REAL"
_MIGRATE_CLOSING_PROB_SQL = "ALTER TABLE bets ADD COLUMN closing_prob REAL"
_MIGRATE_CLV_PCT_SQL      = "ALTER TABLE bets ADD COLUMN clv_pct REAL"

# Migration: add kelly_triggered to existing tables that predate v3.
_MIGRATE_SQL = """
ALTER TABLE bets ADD COLUMN kelly_triggered BOOLEAN DEFAULT TRUE
"""

# Composite UNIQUE index for is_duplicate()'s hot path AND -- as of
# 2026-08-17, finding B3.3 -- the actual race-safety net. The old version
# of this index was a plain (non-unique) index; is_duplicate() + log_bet()
# used two separate connections/transactions with a real check-then-insert
# race window, so a double-log under concurrent or manually-retriggered
# runs could double-stake a real bet with no error at all.
#
# v3 (2026-08-18): the original v2 key -- (system, game_date, game_pk,
# bet_type, kelly_triggered), no `player` -- crashed in production the very
# first time it ran: idx_bets_dedup_v2 could not be created because
# pre-existing duplicate rows violated it. Investigating those "duplicates"
# found a second, worse bug hiding behind the first: for every per-player
# market (HR/K/OUTS/BATTER_HITS/BATTER_TB/PITCHER_ER), `bet_type` alone does
# NOT identify a unique bet -- e.g. HR's bet_type is always the literal
# string "HR" regardless of which batter. Without `player` in the key, two
# different players qualifying for a bet in the same game on the same day
# collide on the SAME key. v2 would have made that collision permanent and
# silent (ON CONFLICT DO NOTHING drops the second player's bet with no
# error, logged only as an innocuous "lost a dedup race" line) instead of
# the pre-v2 behavior of merely failing to dedup correctly. v3 adds `player`
# to close this: for game-level systems (F5/NRFI/GAME/F1H) `player` is a
# constant per-game matchup string, so this is a no-op there; for
# player-level systems it's the actual disambiguator. NULL `player` is a
# known gap (Postgres NULLs are never equal, even to each other, so a
# missing player silently bypasses dedup entirely) -- not exercised today
# since every runner populates `player` on every log_bet() call, but worth
# revisiting with COALESCE(player, '') if that ever changes.
#
# Includes kelly_triggered (not just system/game_date/game_pk/player/
# bet_type) so the legitimate case is_duplicate() already permits -- a
# kelly_triggered=False row existing, then a REAL kelly_triggered=True bet
# getting logged later
# the same day -- still works: those are two DIFFERENT 6-tuples, not a
# collision. What this DOES block at the DB level, no matter how racy the
# caller is: two kelly_triggered=TRUE attempts for the same (system,
# game_date, game_pk, player, bet_type) -- the actually dangerous,
# money-doubling case. New index name each time the key shape changes (not
# reusing an old name) so CREATE ... IF NOT EXISTS can never silently no-op
# against a lingering index of that name with the wrong column set;
# _DROP_OLD_DEDUP_IDX_SQL / _DROP_OLD_DEDUP_IDX_V2_SQL below remove both
# predecessors anyway.
_DROP_OLD_DEDUP_IDX_SQL    = "DROP INDEX IF EXISTS idx_bets_dedup"
_DROP_OLD_DEDUP_IDX_V2_SQL = "DROP INDEX IF EXISTS idx_bets_dedup_v2"
_IDX_DEDUP_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_bets_dedup_v3
  ON bets(system, game_date, game_pk, player, bet_type, kelly_triggered)
"""

# Partial index for pending-bet queries (Postgres only -- SQLite ignores WHERE clause).
_IDX_PENDING_SQL = """
CREATE INDEX IF NOT EXISTS idx_bets_pending
  ON bets(result, game_date)
  WHERE result IS NULL
"""


def _make_engine(db_path: str) -> sa.Engine:
    url = DB_URL or ""
    if url:
        return sa.create_engine(url)
    sqlite_path = Path(db_path)
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return sa.create_engine(f"sqlite:///{sqlite_path}")


class BetTracker:
    """
    Unified bet tracker. All systems use this class.

    Usage:
        tracker = BetTracker("path/to/bets.db", system="1IOU")

        # Log every scored prediction (qualifying or not):
        bet_id = tracker.log_bet(
            game_date="2026-05-14",
            game_pk=12345,
            player="CLE @ LAA",
            away_team="CLE", home_team="LAA",
            bet_type="NRFI",
            model_prob=0.58, market_prob=0.52,
            edge=0.06, kelly_pct=0.018,
            odds=-115, stake=18.00,
            kelly_triggered=True,
            paper=True,
        )
        # Returns -1 if (system, game_date, game_pk, player, bet_type) already exists.

        tracker.settle(bet_id, result="win", profit=15.65)
    """

    def __init__(self, db_path: str | Path, system: str):
        self.db_path = str(db_path)
        self.system  = system.upper()
        self.engine  = _make_engine(self.db_path)
        self._init_db()

    def _init_db(self):
        schema = (
            _SCHEMA_SQL_SQLITE
            if self.engine.dialect.name == "sqlite"
            else _SCHEMA_SQL
        )
        with self.engine.begin() as conn:
            conn.execute(text(schema))
        # Migrate in a separate transaction so a "column already exists"
        # error doesn't poison the connection used for schema creation.
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_MIGRATE_SQL))
        except Exception:
            pass  # Column already exists — expected after first migration.
        # Deleted 2026-08-17 (finding B3.2): a one-shot OUTS-system
        # reclassification UPDATE used to run here unconditionally on
        # EVERY BetTracker construction -- every runner invocation, every
        # system, 4-8x/day, forever. It did its job in 2026-05-14 and
        # matches 0 rows in steady state, but the query cost grows with the
        # bets table's size regardless of matching nothing.
        # Migrate lambda_k / proj_k columns.
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_MIGRATE_LAMBDA_SQL))
        except Exception:
            pass
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_MIGRATE_PROJ_K_SQL))
        except Exception:
            pass
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_MIGRATE_BOOK_SQL))
        except Exception:
            pass
        # Migrate CLV columns (T08, 2026-05-19).
        for clv_sql in (_MIGRATE_CLOSING_ODDS_SQL, _MIGRATE_CLOSING_PROB_SQL, _MIGRATE_CLV_PCT_SQL,
                       _MIGRATE_MORNING_ODDS_SQL, _MIGRATE_LINE_MOVE_SQL):
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(clv_sql))
            except Exception:
                pass  # Column already exists.
        # Composite UNIQUE index for is_duplicate() + the log_bet() race
        # safety net (finding B3.3) -- idempotent, but a real (not
        # silently-swallowed) warning on failure: unlike every other
        # migration above, CREATE UNIQUE INDEX can genuinely fail if
        # pre-existing duplicate rows already violate it (this project has
        # a history of manual re-triggers and backfills). A silent `pass`
        # here would mean the safety net never gets installed with zero
        # visibility -- the OLD non-unique index (if DROP also failed)
        # keeps working, just without the new protection.
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_DROP_OLD_DEDUP_IDX_SQL))
                conn.execute(text(_DROP_OLD_DEDUP_IDX_V2_SQL))
                conn.execute(text(_IDX_DEDUP_SQL))
        except Exception as e:
            logger.warning(
                f"bet_tracker: unique dedup index migration failed (likely "
                f"pre-existing duplicate (system,game_date,game_pk,player,"
                f"bet_type,kelly_triggered) rows) -- {e}"
            )
        # Partial index for pending queries (Postgres only).
        if self.engine.dialect.name != "sqlite":
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(_IDX_PENDING_SQL))
            except Exception:
                pass


    def is_duplicate(self, game_date: str, game_pk: int, bet_type: str,
                     player: str, kelly_triggered: bool = False) -> bool:
        """Return True if this prediction should be skipped.

        Two modes:
        - kelly_triggered=False: check any existing row (scored but not bet).
          Prevents logging the same prediction twice in one run.
        - kelly_triggered=True: check for an EXISTING kelly_triggered=TRUE row.
          Prevents placing a second bet on the same market side between the
          morning and evening runs. A non-triggered row does NOT block a
          triggered bet later in the day (edge can cross the gate in the PM).

        Keyed on (system, game_date, game_pk, player, bet_type). `player` is
        required (2026-08-18, v3): for per-player markets (HR/K/OUTS/
        BATTER_HITS/BATTER_TB/PITCHER_ER), `bet_type` alone does not identify
        a unique bet -- e.g. every HR bet has bet_type="HR" regardless of
        batter. Without `player` in the key, two different players
        qualifying in the same game on the same day were wrongly treated as
        duplicates of each other. For game-level systems (F5/NRFI/GAME/F1H)
        `player` is a constant per-game matchup string, so including it here
        changes nothing for them.
        """
        if kelly_triggered:
            # Only block if a triggered bet already exists for this market side.
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT 1 FROM bets
                        WHERE system=:s AND game_date=:d AND game_pk=:g
                          AND player=:p AND bet_type=:t AND kelly_triggered=TRUE
                        LIMIT 1
                    """),
                    {"s": self.system, "d": game_date, "g": game_pk,
                     "p": player, "t": bet_type},
                ).fetchone()
            return row is not None
        else:
            # Block any duplicate regardless of triggered status.
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT 1 FROM bets
                        WHERE system=:s AND game_date=:d AND game_pk=:g
                          AND player=:p AND bet_type=:t
                        LIMIT 1
                    """),
                    {"s": self.system, "d": game_date, "g": game_pk,
                     "p": player, "t": bet_type},
                ).fetchone()
            return row is not None

    def log_bet(
        self,
        game_date: str = None,
        game_pk: int = None,
        player: str = None,
        away_team: str = None,
        home_team: str = None,
        bet_type: str = None,
        model_prob: float = None,
        market_prob: float = None,
        edge: float = None,
        kelly_pct: float = None,
        odds: int = None,
        stake: float = None,
        kelly_triggered: bool = True,
        paper: bool = True,
        notes: str = "",
        lambda_k: float = None,
        proj_k: float = None,
        book: str = None,
        morning_odds: int = None,
    ) -> int:
        """Log a prediction. Returns bet_id, or -1 if duplicate."""
        # Sanitize INTEGER-column values before EITHER the dedup SELECT or the
        # INSERT touches them -- see _safe_int()'s docstring for why a caller's
        # pandas row can hand these in as float64.
        game_pk = _safe_int(game_pk)
        odds = _safe_int(odds)
        morning_odds = _safe_int(morning_odds)

        if (game_pk is not None and bet_type is not None
                and game_date is not None and player is not None):
            if self.is_duplicate(game_date, game_pk, bet_type, player,
                                 kelly_triggered=kelly_triggered):
                return -1

        with self.engine.begin() as conn:
            # ON CONFLICT ... DO NOTHING + RETURNING id (finding B3.3): the
            # is_duplicate() check above is a separate SELECT on a separate
            # transaction from this INSERT -- a real check-then-insert race
            # window under concurrent or manually-retriggered runs. The
            # idx_bets_dedup_v3 UNIQUE index (system, game_date, game_pk,
            # player, bet_type, kelly_triggered) is what actually closes it:
            # if another call wins the race and inserts the identical
            # 6-tuple first, this INSERT silently matches zero rows instead
            # of raising an IntegrityError, and RETURNING gives back nothing
            # to fetch -- checked below.
            result = conn.execute(
                text("""
                    INSERT INTO bets
                        (system, game_date, game_pk, player, away_team, home_team,
                         bet_type, model_prob, market_prob, edge, kelly_pct,
                         odds, stake, kelly_triggered, paper, notes, created_at, lambda_k, proj_k, book, morning_odds)
                    VALUES
                        (:system, :game_date, :game_pk, :player, :away_team, :home_team,
                         :bet_type, :model_prob, :market_prob, :edge, :kelly_pct,
                         :odds, :stake, :kelly_triggered, :paper, :notes, :created_at, :lambda_k, :proj_k, :book, :morning_odds)
                    ON CONFLICT (system, game_date, game_pk, player, bet_type, kelly_triggered)
                    DO NOTHING
                    RETURNING id
                """),
                {
                    "system": self.system, "game_date": game_date,
                    "game_pk": game_pk, "player": player,
                    "away_team": away_team, "home_team": home_team,
                    "bet_type": bet_type, "model_prob": model_prob,
                    "market_prob": market_prob, "edge": edge,
                    "kelly_pct": kelly_pct, "odds": odds,
                    "stake": stake,
                    "kelly_triggered": kelly_triggered,
                    "paper": int(paper),
                    "notes": notes, "created_at": datetime.now().isoformat(),
                    "lambda_k": lambda_k,
                    "proj_k": proj_k,
                    "book": book,
                    "morning_odds": morning_odds,
                },
            )
            row = result.fetchone()
            if row is None:
                # Lost the race: another call already inserted this exact
                # (system, game_date, game_pk, player, bet_type,
                # kelly_triggered) tuple between our is_duplicate() check
                # and this INSERT.
                logger.info(
                    f"[{self.system}] log_bet: lost a dedup race for "
                    f"{bet_type} player={player} game_pk={game_pk} "
                    f"kelly_triggered={kelly_triggered} -- another call "
                    f"already inserted it"
                )
                return -1
            bet_id = row[0]

        if kelly_triggered:
            label = player if player else f"{away_team} @ {home_team}"
            edge_str = f" | edge: {edge:+.1%}" if edge is not None else ""
            print(f"  [{self.system}] Bet #{bet_id} logged: {bet_type} {label}{edge_str}")
        return bet_id

    def settle(self, bet_id: int, result: str, profit: float) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE bets SET result=:r, profit=:p, settled_at=:s WHERE id=:id"),
                {"r": result, "p": round(profit, 2),
                 "s": datetime.now().isoformat(), "id": bet_id},
            )
        print(f"  [{self.system}] Bet #{bet_id} settled: {result} (P&L: ${profit:+.2f})")

    def write_closing_line(self, bet_id: int, closing_odds: float,
                           complement_odds: float = None) -> None:
        """Record the closing line for a bet and compute CLV (price-based).

        CLV% = (decimal_entry_odds / decimal_closing_odds - 1) * 100

        Positive => we got a better price than the close (the line moved our
        way). This is the industry-standard CLV: bounded, correctly signed, and
        computed on the SAME side's prices so book vig largely cancels.

        Prior implementation used a probability-RELATIVE CLV
        ((entry_fair - closing_fair)/closing_fair) which (a) had the sign
        inverted and (b) divided by the closing probability, so a small or
        mismatched closing_fair (e.g. a cross-line complement, s15.3) blew the
        value up to +-35-68%. The /edge-analysis run (2026-06-11) surfaced
        exactly that. Price-based CLV does not depend on the devig/complement
        path at all, so the cross-line risk no longer affects CLV.

        closing_prob (devigged) is still stored for reference, using the
        complement when available; it no longer feeds clv_pct.
        """
        from mlb_core.odds.utils import (
            american_to_implied_prob, devig_unilateral, clv_pct_from_prices,
        )

        closing_implied = american_to_implied_prob(closing_odds)
        if pd.isna(closing_implied):
            logger.warning(f"write_closing_line: cannot parse closing_odds={closing_odds}")
            return

        with self.engine.begin() as conn:
            row = conn.execute(
                text("SELECT odds, market_prob, bet_type FROM bets WHERE id=:id"),
                {"id": bet_id},
            ).fetchone()
        if row is None:
            logger.warning(f"write_closing_line: bet_id={bet_id} not found")
            return
        entry_odds, entry_fair_prob, bet_type = row

        # closing_prob (devigged) -- reference only, no longer feeds CLV.
        bt_upper = (bet_type or "").upper()
        is_prop = bt_upper.startswith(("K_", "OUTS_", "HR", "BATTER_", "PITCHER_"))
        if complement_odds is not None and not is_prop:
            from mlb_core.odds.utils import remove_vig
            complement_implied = american_to_implied_prob(complement_odds)
            closing_fair_prob, _ = remove_vig(closing_implied, complement_implied)
        elif is_prop:
            closing_fair_prob = devig_unilateral(closing_implied, vig_pct=0.07)
        else:
            closing_fair_prob = closing_implied

        # Price-based CLV from the raw entry vs closing American odds.
        clv_pct = clv_pct_from_prices(entry_odds, closing_odds)
        if pd.isna(clv_pct):
            clv_pct = None

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bets
                       SET closing_odds=:co, closing_prob=:cp, clv_pct=:clv
                     WHERE id=:id
                """),
                {
                    "co":  closing_odds,
                    "cp":  round(closing_fair_prob, 6) if closing_fair_prob and not pd.isna(closing_fair_prob) else None,
                    "clv": clv_pct,
                    "id":  bet_id,
                },
            )
        logger.debug(
            f"  [{self.system}] Bet #{bet_id} closing: entry_odds={entry_odds} "
            f"closing_odds={closing_odds} "
            + (f"CLV={clv_pct:+.2f}%" if clv_pct is not None else "CLV=n/a")
        )

    def pending(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(
                text("SELECT * FROM bets WHERE system = :sys AND result IS NULL ORDER BY game_date DESC"),
                conn, params={"sys": self.system},
            )

    def summary(self, last_n: int = None, season: str = None) -> dict:
        with self.engine.connect() as conn:
            df = pd.read_sql(text("SELECT * FROM bets WHERE system = :sys"), conn, params={"sys": self.system})

        if df.empty:
            print(f"[{self.system}] No bets logged.")
            return {}

        if season:
            df = df[df["game_date"].str.startswith(season)]

        # Summary counts only kelly_triggered=True rows (actual bets).
        bet_df     = df[df["kelly_triggered"].fillna(True).astype(bool)]
        resolved   = bet_df[bet_df["result"].notna()].copy()
        pending_df = bet_df[bet_df["result"].isna()]
        if last_n:
            resolved = resolved.tail(last_n)

        print(f"\n{'='*52}")
        print(f"  {self.system} BET TRACKER")
        print(f"{'='*52}")
        print(f"  Total logged : {len(bet_df)} bets | {len(df)-len(bet_df)} non-triggered")
        print(f"  Resolved: {len(resolved)} | Pending: {len(pending_df)}")

        stats = {}
        if not resolved.empty:
            wins         = (resolved["result"] == "win").sum()
            total_staked = resolved["stake"].sum()
            pnl          = resolved["profit"].sum()
            roi          = pnl / total_staked * 100 if total_staked > 0 else 0
            avg_edge     = resolved["edge"].mean()
            hit_rate     = wins / len(resolved)

            print(f"  Win rate     : {hit_rate:.1%} ({wins}/{len(resolved)})")
            if avg_edge == avg_edge:
                print(f"  Avg edge     : {avg_edge:+.1%}")
            print(f"  Total staked : ${total_staked:.2f}")
            print(f"  P&L          : ${pnl:+.2f}")
            print(f"  ROI          : {roi:+.1f}%")

            paper_mask = resolved["paper"] == 1
            if paper_mask.any():
                p_pnl = resolved[paper_mask]["profit"].sum()
                print(f"  Paper P&L    : ${p_pnl:+.2f} ({paper_mask.sum()} bets)")

            stats = {
                "bets": len(resolved), "wins": int(wins),
                "hit_rate": hit_rate, "pnl": pnl,
                "roi": roi, "avg_edge": avg_edge,
            }

        print(f"{'='*52}\n")
        return stats

    def all_bets(self) -> pd.DataFrame:
        with self.engine.connect() as conn:
            return pd.read_sql(
                text("SELECT * FROM bets WHERE system = :sys ORDER BY game_date DESC"),
                conn, params={"sys": self.system},
            )
