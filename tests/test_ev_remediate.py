"""
tests/test_ev_remediate.py -- mlb.analysis.ev_remediate against a real
(sqlite-backed) BetTracker schema: the destructive step must back up first, only
touch system='EV', never overwrite an existing backup, and refuse on a bad backup.
"""
import os

os.environ.pop("MLB_DB_URL", None)

import pytest
from sqlalchemy import text

from mlb.analysis import ev_remediate as er
from mlb_core.tracking.bet_tracker import BetTracker


def _log(tracker, n, bet_type="K_OVER_7.5_draftkings", **kw):
    for i in range(n):
        tracker.log_bet(
            game_date="2026-09-20", game_pk=1000 + i, player=kw.get("player", f"P{i}"),
            away_team="NYY", home_team="BOS", bet_type=bet_type, model_prob=0.5,
            market_prob=0.45, edge=0.05, odds=120, stake=100.0, kelly_triggered=True,
            paper=True, book="draftkings", notes="",
        )


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "bets.db")
    ev = BetTracker(path, system="EV")
    other = BetTracker(path, system="K")
    _log(ev, 3)
    _log(ev, 1, bet_type="HR_draftkings", player="NYY @ BOS")
    _log(other, 2, bet_type="K_OVER_5.5")
    return ev.engine


def _count(engine, where):
    with engine.connect() as c:
        return int(c.execute(text(f"SELECT count(*) FROM bets WHERE {where}")).scalar())


class TestBackupAndDelete:
    def test_backs_up_then_deletes_only_ev(self, db):
        res = er.backup_and_delete(db, "bets_ev_backup_test")
        assert res["rows_before"] == 4 and res["deleted"] == 4 and res["backup_rows"] == 4
        assert res["backup_created_now"] is True
        assert _count(db, "system='EV'") == 0
        assert _count(db, "system='K'") == 2          # untouched
        with db.connect() as c:                         # backup really holds the EV rows
            assert int(c.execute(text("SELECT count(*) FROM bets_ev_backup_test")).scalar()) == 4

    def test_existing_backup_is_never_overwritten(self, db):
        er.backup_and_delete(db, "bets_ev_backup_test")
        # rows get rebuilt, then the job is re-run: the ORIGINAL backup must survive
        ev = BetTracker(str(db.url.database), system="EV")
        _log(ev, 2, bet_type="K_OVER_9.5_fanduel")
        res = er.backup_and_delete(db, "bets_ev_backup_test")
        assert res["backup_created_now"] is False
        assert res["backup_rows"] == 4                  # still the original 4, not 2
        assert _count(db, "system='EV'") == 0

    def test_unsafe_table_name_rejected_before_any_sql(self, db):
        with pytest.raises(ValueError):
            er.backup_and_delete(db, "x; DROP TABLE bets")
        assert _count(db, "system='EV'") == 4

    def test_refuses_to_delete_when_backup_is_empty(self, db):
        with db.begin() as c:                           # a pre-existing but EMPTY backup table
            c.execute(text("CREATE TABLE bets_ev_backup_test AS SELECT * FROM bets WHERE 1=0"))
        with pytest.raises(RuntimeError):
            er.backup_and_delete(db, "bets_ev_backup_test")
        assert _count(db, "system='EV'") == 4           # nothing deleted


class TestStats:
    def test_profile(self, db):
        s = er.stats(db)
        assert s["total_rows"] == 4
        assert s["hr_rows"] == 1
        assert s["rows_with_matchup_as_player"] == 1     # the "NYY @ BOS" fallback row
        assert s["by_result"] == {"pending": 4}

    def test_empty_table(self, db):
        er.backup_and_delete(db, "bets_ev_backup_test")
        assert er.stats(db) == {"total_rows": 0}

    def test_stats_is_read_only(self, db):
        er.stats(db)
        assert _count(db, "system='EV'") == 4
