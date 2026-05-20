"""
mlb_core.tracking.bet_tracker v3 — Cloud SQL + SQLite fallback.

Changes from v2:
  - Added `kelly_triggered` BOOLEAN column. True when the prediction
    cleared both min_edge and min_kelly_pct and received a non-zero stake.
    False for predictions that were scored but filtered out.
  - Added dedup check in log_bet(): skips insert if a row already exists
    for (system, game_date, game_pk, bet_type). Returns -1 on duplicate.
    Morning + evening runs both score; the first one wins.
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
    clv_pct          REAL
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
_MIGRATE_CLOSING_ODDS_SQL = "ALTER TABLE bets ADD COLUMN closing_odds REAL"
_MIGRATE_CLOSING_PROB_SQL = "ALTER TABLE bets ADD COLUMN closing_prob REAL"
_MIGRATE_CLV_PCT_SQL      = "ALTER TABLE bets ADD COLUMN clv_pct REAL"

# Migration: add kelly_triggered to existing tables that predate v3.
_MIGRATE_SQL = """
ALTER TABLE bets ADD COLUMN kelly_triggered BOOLEAN DEFAULT TRUE
"""

# One-shot: reclassify OUTS bets logged before the OUTS system split (2026-05-14).
# Prior to the split, OUTS bets were logged under system="K". Safe to re-run.
_MIGRATE_OUTS_SQL = """
UPDATE bets SET system = 'OUTS' WHERE bet_type LIKE 'OUTS_%' AND system = 'K'
"""

# Composite index for is_duplicate() hot path.
_IDX_DEDUP_SQL = """
CREATE INDEX IF NOT EXISTS idx_bets_dedup
  ON bets(system, game_date, game_pk, bet_type)
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
        tracker = BetTracker("path/to/bets.db", system="NRFI")

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
        # Returns -1 if (system, game_date, game_pk, bet_type) already exists.

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
        # One-shot reclassification — idempotent, safe to re-run.
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_MIGRATE_OUTS_SQL))
        except Exception as e:
            logger.warning(f"bet_tracker: OUTS migration failed: {e}")
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
        for clv_sql in (_MIGRATE_CLOSING_ODDS_SQL, _MIGRATE_CLOSING_PROB_SQL, _MIGRATE_CLV_PCT_SQL):
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(clv_sql))
            except Exception:
                pass  # Column already exists.
        # Composite index for is_duplicate() -- idempotent.
        try:
            with self.engine.begin() as conn:
                conn.execute(text(_IDX_DEDUP_SQL))
        except Exception:
            pass
        # Partial index for pending queries (Postgres only).
        if self.engine.dialect.name != "sqlite":
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(_IDX_PENDING_SQL))
            except Exception:
                pass


    def is_duplicate(self, game_date: str, game_pk: int, bet_type: str,
                     kelly_triggered: bool = False) -> bool:
        """Return True if this prediction should be skipped.

        Two modes:
        - kelly_triggered=False: check any existing row (scored but not bet).
          Prevents logging the same prediction twice in one run.
        - kelly_triggered=True: check for an EXISTING kelly_triggered=TRUE row.
          Prevents placing a second bet on the same market side between the
          morning and evening runs. A non-triggered row does NOT block a
          triggered bet later in the day (edge can cross the gate in the PM).

        Keyed on (system, game_date, game_pk, bet_type).
        """
        if kelly_triggered:
            # Only block if a triggered bet already exists for this market side.
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT 1 FROM bets
                        WHERE system=:s AND game_date=:d AND game_pk=:g
                          AND bet_type=:t AND kelly_triggered=TRUE
                        LIMIT 1
                    """),
                    {"s": self.system, "d": game_date, "g": game_pk, "t": bet_type},
                ).fetchone()
            return row is not None
        else:
            # Block any duplicate regardless of triggered status.
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("""
                        SELECT 1 FROM bets
                        WHERE system=:s AND game_date=:d AND game_pk=:g AND bet_type=:t
                        LIMIT 1
                    """),
                    {"s": self.system, "d": game_date, "g": game_pk, "t": bet_type},
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
    ) -> int:
        """Log a prediction. Returns bet_id, or -1 if duplicate."""
        if game_pk is not None and bet_type is not None and game_date is not None:
            if self.is_duplicate(game_date, game_pk, bet_type,
                                 kelly_triggered=kelly_triggered):
                return -1

        with self.engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO bets
                        (system, game_date, game_pk, player, away_team, home_team,
                         bet_type, model_prob, market_prob, edge, kelly_pct,
                         odds, stake, kelly_triggered, paper, notes, created_at, lambda_k, proj_k, book)
                    VALUES
                        (:system, :game_date, :game_pk, :player, :away_team, :home_team,
                         :bet_type, :model_prob, :market_prob, :edge, :kelly_pct,
                         :odds, :stake, :kelly_triggered, :paper, :notes, :created_at, :lambda_k, :proj_k, :book)
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
                },
            )
            bet_id = result.lastrowid if self.engine.dialect.name == "sqlite" \
                     else conn.execute(text("SELECT lastval()")).scalar()

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
        """Record the closing line for a bet and compute CLV.

        CLV = (entry_fair_prob - closing_fair_prob) / closing_fair_prob * 100

        Both sides must use NO-VIG (fair) probabilities for CLV to be meaningful.
        Using raw vig-inclusive implied probs produces a number that conflates
        line movement with vig changes — incorrect.

        For two-sided markets (NRFI, F5): we use the market_prob stored at
        bet time, which is already the no-vig fair prob (runners call remove_vig
        before storing). For one-sided props (HR, K): market_prob stored is the
        devigged fair prob from devig_unilateral. Either way, market_prob in the
        DB is already the fair prob.

        The closing_odds are vig-inclusive (raw American odds from SGO). We
        apply the same devig method used at bet time:
          - Two-sided markets: need both sides; approximate by assuming
            symmetric vig → closing_fair ≈ closing_implied / (closing_implied
            + (1 - closing_implied)) = closing_implied (simplifies to raw for
            50/50 markets; for asymmetric markets this is an approximation).
            The correct fix is to store the complementary side at closing time.
          - One-sided props: use devig_unilateral with the same vig_pct.

        TODO: For two-sided markets, capture_closing_lines.py should also
        capture the complementary side's closing odds so remove_vig can be
        applied properly. For now we use raw implied as a proxy and log a note.
        """
        from mlb_core.odds.utils import american_to_implied_prob, devig_unilateral

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

        # Determine closing fair prob using the same devig method as at entry.
        # HR/K props: use devig_unilateral (vig_pct=0.07).
        # All others: use raw implied as approximation (symmetric vig assumed).
        bt_upper = (bet_type or "").upper()
        is_prop = bt_upper.startswith(("K_", "OUTS_", "HR", "BATTER_", "PITCHER_"))
        if complement_odds is not None and not is_prop:
            # C06: devig two-sided market with complement for accurate CLV.
            # Raw implied causes vig changes to appear as fake CLV movement.
            from mlb_core.odds.utils import remove_vig
            complement_implied = american_to_implied_prob(complement_odds)
            closing_fair_prob, _ = remove_vig(closing_implied, complement_implied)
        elif is_prop:
            closing_fair_prob = devig_unilateral(closing_implied, vig_pct=0.07)
        else:
            # No complement captured -- raw implied as fallback.
            closing_fair_prob = closing_implied

        clv_pct = None
        if (entry_fair_prob and entry_fair_prob > 0
                and closing_fair_prob and closing_fair_prob > 0):
            clv_pct = round(
                (entry_fair_prob - closing_fair_prob) / closing_fair_prob * 100, 4
            )

        with self.engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE bets
                       SET closing_odds=:co, closing_prob=:cp, clv_pct=:clv
                     WHERE id=:id
                """),
                {
                    "co":  closing_odds,
                    "cp":  round(closing_fair_prob, 6),
                    "clv": clv_pct,
                    "id":  bet_id,
                },
            )
        logger.debug(
            f"  [{self.system}] Bet #{bet_id} closing: "
            f"entry_fair={entry_fair_prob:.4f} "
            f"closing_fair={closing_fair_prob:.4f} "
            f"CLV={clv_pct:+.2f}%" if clv_pct is not None else
            f"  [{self.system}] Bet #{bet_id} closing line recorded: {closing_odds}"
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
