"""
Unified BetTracker for all systems (NRFI, HR, F5, K).
One schema, one class. System column differentiates records.
"""
import sqlite3
import pandas as pd
from pathlib import Path
from datetime import date, datetime, timedelta


class BetTracker:
    """
    Unified bet tracker. All systems use this class.

    Usage:
        tracker = BetTracker("C:/path/to/nrfi_bets.db", system="NRFI")
        bet_id = tracker.log_bet(
            game_date="2026-04-07",
            away_team="CHC", home_team="STL",
            bet_type="NRFI",
            model_prob=0.58, market_prob=0.52,
            edge=0.06, kelly_pct=0.018,
            odds=-115, stake=18.00,
            paper=True,
        )
        tracker.settle(bet_id, result="win", profit=15.65)
    """

    SCHEMA = """
        CREATE TABLE IF NOT EXISTS bets (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            system         TEXT    NOT NULL,
            game_date      TEXT,
            game_pk        INTEGER,
            player         TEXT,
            away_team      TEXT,
            home_team      TEXT,
            bet_type       TEXT,
            model_prob     REAL,
            market_prob    REAL,
            edge           REAL,
            kelly_pct      REAL,
            odds           INTEGER,
            stake          REAL,
            paper          INTEGER DEFAULT 1,
            result         TEXT,
            profit         REAL,
            settled_at     TEXT,
            notes          TEXT,
            created_at     TEXT
        )
    """

    def __init__(self, db_path: str | Path, system: str):
        """
        Args:
            db_path: Path to SQLite file. Created if it does not exist.
            system:  One of 'NRFI', 'HR', 'F5', 'K'.
        """
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(db_path)
        self.system = system.upper()
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self.SCHEMA)
            conn.commit()

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
        paper: bool = True,
        notes: str = "",
    ) -> int:
        """Log a bet. Returns bet_id."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO bets
                   (system, game_date, game_pk, player, away_team, home_team,
                    bet_type, model_prob, market_prob, edge, kelly_pct,
                    odds, stake, paper, notes, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.system, game_date, game_pk, player,
                    away_team, home_team, bet_type,
                    model_prob, market_prob, edge, kelly_pct,
                    odds, stake, int(paper), notes,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        bet_id = cur.lastrowid
        label = player if player else f"{away_team} @ {home_team}"
        print(
            f"  [{self.system}] Bet #{bet_id} logged: {bet_type} {label}"
            f" | edge: {edge:+.1%}" if edge else f"  [{self.system}] Bet #{bet_id} logged"
        )
        return bet_id

    def settle(self, bet_id: int, result: str, profit: float) -> None:
        """
        Settle a bet.

        Args:
            bet_id: ID returned by log_bet.
            result: 'win' or 'loss'.
            profit: Dollar P&L (positive for win, negative for loss).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE bets SET result=?, profit=?, settled_at=? WHERE id=?",
                (result, round(profit, 2), datetime.now().isoformat(), bet_id),
            )
            conn.commit()
        print(f"  [{self.system}] Bet #{bet_id} settled: {result} (P&L: ${profit:+.2f})")

    def pending(self) -> pd.DataFrame:
        """Return all unsettled bets."""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql(
                "SELECT * FROM bets WHERE result IS NULL ORDER BY game_date DESC", conn
            )

    def summary(self, last_n: int = None, season: str = None) -> dict:
        """
        Print and return performance summary.

        Args:
            last_n:  Limit to last N settled bets.
            season:  Filter by season year string e.g. '2026'.
        """
        with sqlite3.connect(self.db_path) as conn:
            df = pd.read_sql("SELECT * FROM bets", conn)

        if df.empty:
            print(f"[{self.system}] No bets logged.")
            return {}

        if season:
            df = df[df["game_date"].str.startswith(season)]

        resolved = df[df["result"].notna()].copy()
        pending_df = df[df["result"].isna()]

        if last_n:
            resolved = resolved.tail(last_n)

        print(f"\n{'='*52}")
        print(f"  {self.system} BET TRACKER")
        print(f"{'='*52}")
        print(f"  Total logged : {len(df)} | Resolved: {len(resolved)} | Pending: {len(pending_df)}")

        stats = {}
        if not resolved.empty:
            wins = (resolved["result"] == "win").sum()
            total_staked = resolved["stake"].sum()
            pnl = resolved["profit"].sum()
            roi = pnl / total_staked * 100 if total_staked > 0 else 0
            avg_edge = resolved["edge"].mean()
            hit_rate = wins / len(resolved)

            print(f"  Win rate     : {hit_rate:.1%} ({wins}/{len(resolved)})")
            print(f"  Avg edge     : {avg_edge:+.1%}" if avg_edge == avg_edge else "  Avg edge     : N/A")
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
        """Return all bets as DataFrame."""
        with sqlite3.connect(self.db_path) as conn:
            return pd.read_sql("SELECT * FROM bets ORDER BY game_date DESC", conn)