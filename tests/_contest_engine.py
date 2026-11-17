"""One place that builds the contest test engine: SQLite in a temp file by default; set CONTEST_TEST_PG_URL (e.g.
postgresql+pg8000://postgres@127.0.0.1:55432/contest_test) to run the SAME tests on a throwaway Postgres, which exercises SERIAL, RETURNING,
FOR UPDATE and the partial unique index through pg8000 (not run in CI)."""
import os

import sqlalchemy as sa
from sqlalchemy import text

TABLES = ("contest_ledger", "contest_blackjack", "contest_quote", "contest_leg", "contest_bet", "contest_game", "contest_entry", "contest_invite", "contest")


def make_engine(tmp_path, name="contest.db"):
    url = os.environ.get("CONTEST_TEST_PG_URL")
    if not url:
        return sa.create_engine(f"sqlite:///{tmp_path / name}")
    engine = sa.create_engine(url)
    with engine.begin() as c:
        for t in TABLES:
            c.execute(text(f"DROP TABLE IF EXISTS {t}"))
    return engine
