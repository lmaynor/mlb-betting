"""
tests/test_kalshi_vs_books.py -- mlb.analysis.kalshi_vs_books pure DataFrame logic.

Covers _prep (dedup to latest snapshot per quote+book), _kalshi_truth
(normalize complementary-pair mids to sum to 1; passthrough for non-
complementary markets like the run line), and classify (the verdict
heuristic that separates credible edges from thin/stale/kalshi-outlier
artifacts). scan() itself (GCS reads + book_vig lookups) is intentionally
left to manual/live validation -- these three functions are where the
actual math lives.
"""
import pandas as pd

from mlb.analysis import kalshi_vs_books as V


def _row(**kw):
    base = {"market": "hr_yn", "game_pk": 1, "game_date": "2026-07-23",
            "event_id": "e1", "away_team": "TB", "home_team": "TOR",
            "player_id": None, "selection": "OVER", "line": None,
            "book": "draftkings", "american": -110, "decimal": 1.909,
            "implied_prob": 0.524, "fair_prob": None,
            "snapshot_ts": "2026-07-23 15:55:00", "is_open": False,
            "is_closing": False, "source": "parlayapi", "ingested_at": "x"}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# _prep() -- latest snapshot per (market, game_pk, player, line, selection, book)
# --------------------------------------------------------------------------- #

class TestPrep:
    def test_keeps_latest_snapshot_per_quote_book(self):
        df = pd.DataFrame([
            _row(book="draftkings", implied_prob=0.50, snapshot_ts="2026-07-23 15:00:00"),
            _row(book="draftkings", implied_prob=0.55, snapshot_ts="2026-07-23 21:00:00"),
        ])
        out = V._prep(df)
        assert len(out) == 1
        assert out.iloc[0]["implied_prob"] == 0.55

    def test_drops_rows_with_no_implied_prob(self):
        df = pd.DataFrame([_row(implied_prob=None)])
        out = V._prep(df)
        assert len(out) == 0

    def test_distinct_books_both_survive(self):
        df = pd.DataFrame([
            _row(book="draftkings", implied_prob=0.50),
            _row(book="fanduel", implied_prob=0.52),
        ])
        out = V._prep(df)
        assert len(out) == 2

    def test_null_player_and_line_get_sentinel_fill(self):
        df = pd.DataFrame([_row(player_id=None, line=None)])
        out = V._prep(df)
        assert out.iloc[0]["_pid"] == -1
        assert out.iloc[0]["_line"] == -99.0


# --------------------------------------------------------------------------- #
# _kalshi_truth() -- normalize complementary pairs; passthrough for run line
# --------------------------------------------------------------------------- #

def _kalshi_row(selection, fair_prob, **kw):
    return _row(book="kalshi", source="kalshi", selection=selection,
                fair_prob=fair_prob, implied_prob=fair_prob, **kw)


class TestKalshiTruth:
    def test_normalizes_two_sided_pair_to_sum_one(self):
        # Kalshi's half-spread means OVER+UNDER mids don't sum to exactly 1.
        k = pd.DataFrame([
            _kalshi_row("OVER", 0.48, market="hr_yn", game_pk=1),
            _kalshi_row("UNDER", 0.50, market="hr_yn", game_pk=1),
        ])
        k = pd.concat([k], ignore_index=True)
        k["_pid"] = -1
        k["_line"] = -99.0
        out = V._kalshi_truth(k, normalize=True)
        pair_sum = out["p_true"].sum()
        assert abs(pair_sum - 1.0) < 1e-9

    def test_single_sided_pair_passes_through_unscaled(self):
        # Only one side quoted -- n_sides<2 -> use the raw mid, don't divide by itself.
        k = pd.DataFrame([_kalshi_row("OVER", 0.48, market="hr_yn", game_pk=1)])
        k["_pid"] = -1
        k["_line"] = -99.0
        out = V._kalshi_truth(k, normalize=True)
        assert out.iloc[0]["p_true"] == 0.48

    def test_non_complementary_market_uses_raw_mid(self):
        # Run line: HOME-by-N and AWAY-by-N are not complementary -- normalize=False
        # must return the raw mid even when two "sides" happen to be present.
        k = pd.DataFrame([
            _kalshi_row("HOME", 0.30, market="game_rl", game_pk=1),
            _kalshi_row("AWAY", 0.25, market="game_rl", game_pk=1),
        ])
        k["_pid"] = -1
        k["_line"] = -99.0
        out = V._kalshi_truth(k, normalize=False)
        assert sorted(out["p_true"]) == [0.25, 0.30]

    def test_renames_fair_prob_and_snapshot_columns(self):
        k = pd.DataFrame([_kalshi_row("OVER", 0.48, market="hr_yn", game_pk=1)])
        k["_pid"] = -1
        k["_line"] = -99.0
        out = V._kalshi_truth(k, normalize=True)
        assert "k_mid" in out.columns and "k_ts" in out.columns
        assert "fair_prob" not in out.columns


# --------------------------------------------------------------------------- #
# classify() -- verdict heuristic
# --------------------------------------------------------------------------- #

def _scanned_row(**kw):
    base = {"n_books": 5, "k_dev": 0.0, "bk_dev": 0.0}
    base.update(kw)
    return base


class TestClassify:
    def test_empty_frame_passes_through(self):
        out = V.classify(pd.DataFrame())
        assert len(out) == 0

    def test_thin_pack_wins_when_too_few_books(self):
        df = pd.DataFrame([_scanned_row(n_books=2)])
        out = V.classify(df, min_books=4)
        assert out.iloc[0]["verdict"] == "thin_pack"

    def test_kalshi_off_when_kalshi_disagrees_with_pack(self):
        df = pd.DataFrame([_scanned_row(n_books=5, k_dev=0.10)])
        out = V.classify(df, kdev_max=0.04)
        assert out.iloc[0]["verdict"] == "kalshi_off"

    def test_stale_when_book_far_below_consensus(self):
        df = pd.DataFrame([_scanned_row(n_books=5, k_dev=0.0, bk_dev=-0.20)])
        out = V.classify(df, stale_gap=0.15)
        assert out.iloc[0]["verdict"] == "stale?"

    def test_check_when_nothing_trips(self):
        df = pd.DataFrame([_scanned_row(n_books=5, k_dev=0.01, bk_dev=-0.02)])
        out = V.classify(df, min_books=4, kdev_max=0.04, stale_gap=0.15)
        assert out.iloc[0]["verdict"] == "check"

    def test_priority_thin_pack_beats_other_flags(self):
        # Too few books AND kalshi disagrees AND stale -- thin_pack wins (first
        # in np.select's condition list = the highest-priority disqualifier).
        df = pd.DataFrame([_scanned_row(n_books=1, k_dev=0.5, bk_dev=-0.5)])
        out = V.classify(df, min_books=4, kdev_max=0.04, stale_gap=0.15)
        assert out.iloc[0]["verdict"] == "thin_pack"
