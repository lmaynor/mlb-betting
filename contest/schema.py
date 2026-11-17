"""DDL for the contest tables. Postgres in prod (pg8000), SQLite in tests -- the same dialect switch BetTracker uses.

Money columns are BIGINT cents. Timestamps are ISO-8601 UTC TEXT (repo convention). `contest_ledger` is append-only and is the audit
trail: `contest_entry.balance_cents` is a cached running total that `store.reconcile` checks against the ledger sum.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import text

_PK = "{PK}"

TABLES = [
    f"""CREATE TABLE IF NOT EXISTS contest (
        id            {_PK},
        slug          TEXT NOT NULL UNIQUE,
        name          TEXT NOT NULL,
        sport         TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'open',
        starts_at     TEXT,
        ends_at       TEXT,
        rules_json    TEXT NOT NULL,
        created_at    TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_invite (
        code          TEXT PRIMARY KEY,
        contest_id    INTEGER NOT NULL,
        max_uses      INTEGER NOT NULL DEFAULT 1,
        uses          INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_entry (
        id            {_PK},
        contest_id    INTEGER NOT NULL,
        user_id       TEXT NOT NULL,
        display_name  TEXT NOT NULL,
        name_key      TEXT NOT NULL,
        name_changes  INTEGER NOT NULL DEFAULT 0,
        balance_cents BIGINT NOT NULL DEFAULT 0,
        status        TEXT NOT NULL DEFAULT 'active',
        joined_at     TEXT NOT NULL,
        eliminated_at TEXT,
        UNIQUE (contest_id, user_id),
        UNIQUE (contest_id, name_key)
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_game (
        id            {_PK},
        contest_id    INTEGER NOT NULL,
        sport         TEXT NOT NULL,
        espn_event_id TEXT NOT NULL,
        bp_key        TEXT,
        away_team     TEXT,
        home_team     TEXT,
        commence_time TEXT NOT NULL,
        round_label   TEXT,
        status        TEXT NOT NULL DEFAULT 'scheduled',
        home_score    INTEGER,
        away_score    INTEGER,
        UNIQUE (contest_id, espn_event_id)
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_bet (
        id              {_PK},
        entry_id        INTEGER NOT NULL,
        kind            TEXT NOT NULL,
        stake_cents     BIGINT NOT NULL,
        payout_cents    BIGINT,
        status          TEXT NOT NULL DEFAULT 'pending',
        teaser_points   DOUBLE PRECISION NOT NULL DEFAULT 0,
        idempotency_key TEXT NOT NULL,
        phase           TEXT NOT NULL DEFAULT 'pregame',
        group_id        TEXT,
        placed_at       TEXT NOT NULL,
        settled_at      TEXT,
        UNIQUE (entry_id, idempotency_key)
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_leg (
        id               {_PK},
        bet_id           INTEGER NOT NULL,
        game_id          INTEGER NOT NULL,
        market           TEXT NOT NULL,
        side             TEXT NOT NULL,
        line             DOUBLE PRECISION,
        price            INTEGER NOT NULL,
        quote_fetched_at TEXT,
        result           TEXT,
        graded_at        TEXT
    )""",
    f"""CREATE TABLE IF NOT EXISTS contest_ledger (
        id                  {_PK},
        entry_id            INTEGER NOT NULL,
        ts                  TEXT NOT NULL,
        kind                TEXT NOT NULL,
        amount_cents        BIGINT NOT NULL,
        bet_id              INTEGER,
        balance_after_cents BIGINT NOT NULL,
        note                TEXT
    )""",
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_contest_leg_game ON contest_leg (game_id)",
    "CREATE INDEX IF NOT EXISTS idx_contest_leg_bet ON contest_leg (bet_id)",
    "CREATE INDEX IF NOT EXISTS idx_contest_bet_entry ON contest_bet (entry_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_contest_bet_group ON contest_bet (entry_id, group_id)",
    "CREATE INDEX IF NOT EXISTS idx_contest_ledger_entry ON contest_ledger (entry_id)",
    "CREATE INDEX IF NOT EXISTS idx_contest_game_contest ON contest_game (contest_id, commence_time)",
]


def ddl(dialect: str) -> list[str]:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect == "sqlite" else "SERIAL PRIMARY KEY"
    return [t.replace(_PK, pk) for t in TABLES] + INDEXES


def ensure_schema(engine: sa.engine.Engine) -> None:
    """Create every table and index if missing. Idempotent; safe at every start-up."""
    with engine.begin() as c:
        for stmt in ddl(engine.dialect.name):
            c.execute(text(stmt))
