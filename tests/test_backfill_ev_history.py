"""
tests/test_backfill_ev_history.py -- scripts/backfill_ev_history.py's
_posted_subset(): the join that recovers "what was actually posted" from
log.parquet (every candidate scanned, possibly several times a day) against
notified.parquet (the true posted keys, one row per quote per day).
"""
import pandas as pd
import pytest

import scripts.backfill_ev_history as bfh
from scripts.backfill_ev_history import _posted_subset, recover_fast_alert, recover_kalshi


def _quote(**overrides):
    row = {
        "market": "k_ou", "game_pk": 1, "player_id": 1, "line": 7.5,
        "selection": "OVER", "book": "draftkings", "snapshot_ts": "2026-07-24 10:00:00",
        "american": 120, "consensus_fair": 0.5, "ev": 0.05,
    }
    row.update(overrides)
    return row


class TestPostedSubset:
    def test_one_scan_one_post_recovers_one_row(self):
        log_df = pd.DataFrame([_quote()])
        notified_df = pd.DataFrame([_quote()])[["market", "game_pk", "player_id", "line", "selection", "book"]]
        posted = _posted_subset(log_df, notified_df)
        assert len(posted) == 1

    def test_quote_rescanned_multiple_times_still_recovers_one_row(self):
        """A quote scanned by 4 separate 15-min runs that day (same
        market/game_pk/player_id/line/selection/book, different
        snapshot_ts) but posted only once must recover as ONE row, not
        four -- this was a real bug found on 2026-07-24 (204 naive-join
        rows vs 178 true posted alerts)."""
        log_df = pd.DataFrame([
            _quote(snapshot_ts="2026-07-24 10:00:00", american=110),
            _quote(snapshot_ts="2026-07-24 10:15:00", american=115),
            _quote(snapshot_ts="2026-07-24 10:30:00", american=120),
            _quote(snapshot_ts="2026-07-24 10:45:00", american=125),
        ])
        notified_df = pd.DataFrame([_quote()])[["market", "game_pk", "player_id", "line", "selection", "book"]]
        posted = _posted_subset(log_df, notified_df)
        assert len(posted) == 1

    def test_keeps_the_latest_snapshot_not_an_arbitrary_one(self):
        log_df = pd.DataFrame([
            _quote(snapshot_ts="2026-07-24 10:00:00", american=110),
            _quote(snapshot_ts="2026-07-24 10:45:00", american=125),
        ])
        notified_df = pd.DataFrame([_quote()])[["market", "game_pk", "player_id", "line", "selection", "book"]]
        posted = _posted_subset(log_df, notified_df)
        assert posted.iloc[0]["american"] == 125

    def test_two_distinct_quotes_both_recovered(self):
        log_df = pd.DataFrame([
            _quote(player_id=1),
            _quote(player_id=2, snapshot_ts="2026-07-24 10:05:00"),
        ])
        notified_df = pd.DataFrame([
            _quote(player_id=1), _quote(player_id=2),
        ])[["market", "game_pk", "player_id", "line", "selection", "book"]]
        posted = _posted_subset(log_df, notified_df)
        assert len(posted) == 2

    def test_scanned_but_never_posted_quote_is_excluded(self):
        """A candidate that showed up in log.parquet but was capped out by
        FAL_MAX_POSTS (never actually sent to Discord) must not be
        recovered as if it were a real historical bet."""
        log_df = pd.DataFrame([_quote(player_id=1), _quote(player_id=999)])
        notified_df = pd.DataFrame([_quote(player_id=1)])[["market", "game_pk", "player_id", "line", "selection", "book"]]
        posted = _posted_subset(log_df, notified_df)
        assert len(posted) == 1
        assert posted.iloc[0]["player_id"] == 1

    def test_empty_inputs_return_none(self):
        assert _posted_subset(None, pd.DataFrame([_quote()])) is None
        assert _posted_subset(pd.DataFrame([_quote()]), None) is None
        assert _posted_subset(pd.DataFrame(), pd.DataFrame([_quote()])) is None
        assert _posted_subset(pd.DataFrame([_quote()]), pd.DataFrame()) is None


_QUOTE_COLS = ["market", "game_pk", "player_id", "line", "selection", "book"]


def _fake_reader(files: dict):
    """files: {gcs_key: DataFrame} -- returns None for any key not present,
    matching _read_parquet_safe's real behavior for a missing GCS object."""
    def _read(key):
        return files.get(key)
    return _read


class TestOffshoreBookExcluded:
    """Real, historical data-quality bug (finding C4.1, fixed 2026-08-17):
    Kalshi (a no-vig reference feed, not a real sportsbook) was pooled into
    the same odds_history `book` column as real tradeable prices, so
    pre-fix days have "posted alerts" with book=="kalshi" at impossible
    prices (e.g. +9900 American). Confirmed empirically on real data:
    2026-08-16 had 661/809 (82%) such rows; 2026-09-05 (post-fix) had 0/119.
    Recovering these would corrupt both the flat-stake ROI and the Kelly
    bankroll report with fake outcomes."""

    def test_kalshi_book_row_excluded_from_fast_alert_recovery(self, monkeypatch):
        log_df = pd.DataFrame([
            _quote(book="kalshi", american=9900, player_id=1),
            _quote(book="draftkings", american=120, player_id=2, snapshot_ts="2026-08-12 10:05:00"),
        ])
        notified_df = log_df[_QUOTE_COLS]
        monkeypatch.setattr(bfh, "_read_parquet_safe", _fake_reader({
            "Alerts/2026-08-12/log.parquet": log_df,
            "Alerts/2026-08-12/notified.parquet": notified_df,
        }))
        df = recover_fast_alert(["2026-08-12"])
        assert len(df) == 1
        assert df.iloc[0]["book"] == "draftkings"

    @pytest.mark.parametrize("book", ["kalshi", "pinnacle", "bovada", "betfair",
                                       "consensus", "average", "KALSHI", "Pinnacle"])
    def test_every_offshore_book_excluded_case_insensitive(self, monkeypatch, book):
        log_df = pd.DataFrame([_quote(book=book)])
        notified_df = log_df[_QUOTE_COLS]
        monkeypatch.setattr(bfh, "_read_parquet_safe", _fake_reader({
            "Alerts/2026-08-12/log.parquet": log_df,
            "Alerts/2026-08-12/notified.parquet": notified_df,
        }))
        assert len(recover_fast_alert(["2026-08-12"])) == 0

    def test_real_book_not_excluded(self, monkeypatch):
        log_df = pd.DataFrame([_quote(book="fanatics")])
        notified_df = log_df[_QUOTE_COLS]
        monkeypatch.setattr(bfh, "_read_parquet_safe", _fake_reader({
            "Alerts/2026-08-12/log.parquet": log_df,
            "Alerts/2026-08-12/notified.parquet": notified_df,
        }))
        assert len(recover_fast_alert(["2026-08-12"])) == 1

    def test_kalshi_book_row_excluded_from_kalshi_recovery(self, monkeypatch):
        import mlb.runners.fast_alert_loop as fal
        monkeypatch.setattr(fal, "resolve_player_names", lambda ids: {})

        def _krow(**overrides):
            row = {
                "market": "hr_yn", "game_pk": 1, "game_date": "2026-08-12",
                "away_team": "NYY", "home_team": "BOS", "player_id": 1,
                "selection": "OVER", "line": 0.5, "book": "fliff", "american": 450,
                "p_true": 0.22, "cons_impl": 0.16, "n_books": 6,
            }
            row.update(overrides)
            return row

        log_df = pd.DataFrame([_krow(book="kalshi", american=9900), _krow(book="fliff", player_id=2)])
        notified_df = log_df[_QUOTE_COLS]
        monkeypatch.setattr(bfh, "_read_parquet_safe", _fake_reader({
            "Alerts/2026-08-12/kalshi_log.parquet": log_df,
            "Alerts/2026-08-12/kalshi_notified.parquet": notified_df,
        }))
        df = recover_kalshi(["2026-08-12"])
        assert len(df) == 1
        assert df.iloc[0]["book"] == "fliff"
