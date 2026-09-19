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

    def test_nrfi_book_side_yes_no_normalized_to_yrfi_nrfi(self):
        """2026-09-19: real-book ingestion frames nrfi_ou as a plain YES/NO
        proposition; Kalshi (and everything else in this repo) uses NRFI/YRFI.
        Without normalizing the book side, `selection` never matches between
        the two sources -- confirmed this silently produced ZERO joined rows
        for nrfi_ou in both scan() and scan_kalshi_side(), not "no divergence"."""
        df = pd.DataFrame([
            _row(market="nrfi_ou", book="draftkings", selection="YES", line=None),
            _row(market="nrfi_ou", book="draftkings", selection="NO", line=None),
        ])
        out = V._prep(df)
        assert sorted(out["selection"]) == ["NRFI", "YRFI"]

    def test_nrfi_kalshi_side_selection_untouched(self):
        df = pd.DataFrame([
            _row(market="nrfi_ou", book="kalshi", source="kalshi",
                 selection="YRFI", line=None),
        ])
        out = V._prep(df)
        assert out.iloc[0]["selection"] == "YRFI"

    def test_non_nrfi_market_selection_untouched(self):
        """The normalization must not leak into other markets that happen to
        use YES/NO (e.g. hr_yn's binary props)."""
        df = pd.DataFrame([_row(market="hr_yn", book="draftkings", selection="YES")])
        out = V._prep(df)
        assert out.iloc[0]["selection"] == "YES"

    def test_extreme_kalshi_price_dropped_on_liquid_market(self):
        """2026-09-19: a Kalshi price this extreme on a LIQUID (game-level)
        market is essentially always a stale/thin historical print, not a real
        price -- confirmed via two independent real examples that manufactured
        25-900%+ fake EV before this guard existed."""
        df = pd.DataFrame([
            _row(market="game_ml", book="kalshi", source="kalshi",
                 selection="HOME", implied_prob=0.02, game_pk=1),
        ])
        out = V._prep(df)
        assert len(out) == 0

    def test_extreme_kalshi_price_kept_on_non_liquid_market(self):
        """Props are documented repo-wide as thin/soft evidence already -- a
        genuinely extreme long-shot price is plausible there, so it's not
        filtered (unlike LIQUID game-level markets)."""
        df = pd.DataFrame([
            _row(market="hr_yn", book="kalshi", source="kalshi",
                 selection="OVER", implied_prob=0.02, game_pk=1),
        ])
        out = V._prep(df)
        assert len(out) == 1

    def test_extreme_book_price_never_dropped(self):
        """The sanity filter targets Kalshi's own thin historical prints --
        a real book's genuinely extreme quote must survive untouched."""
        df = pd.DataFrame([
            _row(market="game_ml", book="draftkings", selection="HOME",
                 implied_prob=0.02, game_pk=1),
        ])
        out = V._prep(df)
        assert len(out) == 1

    def test_kalshi_price_within_sane_range_kept(self):
        df = pd.DataFrame([
            _row(market="game_ml", book="kalshi", source="kalshi",
                 selection="HOME", implied_prob=0.40, game_pk=1),
        ])
        out = V._prep(df)
        assert len(out) == 1


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
# _kalshi_book_fair() / _kalshi_ev() -- the mirror direction: is KALSHI's own
# contract +EV vs the real book pack's devigged consensus?
# --------------------------------------------------------------------------- #

class TestKalshiBookFair:
    def test_medians_devigged_fair_prob_across_books(self):
        books = pd.DataFrame([
            _row(book="draftkings", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.60),
            _row(book="fanduel", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.64),
            _row(book="betmgm", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.62),
            _row(book="caesars", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.62),
        ])
        books["_pid"] = -1
        books["_line"] = -99.0
        out = V._kalshi_book_fair(books, min_books=4)
        assert len(out) == 1
        assert out.iloc[0]["book_fair"] == 0.62
        assert out.iloc[0]["n_books"] == 4

    def test_drops_groups_under_min_books(self):
        books = pd.DataFrame([
            _row(book="draftkings", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.60),
            _row(book="fanduel", market="game_ml", game_pk=1, selection="HOME",
                 fair_prob=0.64),
        ])
        books["_pid"] = -1
        books["_line"] = -99.0
        out = V._kalshi_book_fair(books, min_books=4)
        assert len(out) == 0


def _kalshi_ask_row(selection, ask, **kw):
    """A kalshi quote row as it's actually stored: implied_prob=ask, decimal=1/ask."""
    return _row(book="kalshi", source="kalshi", selection=selection,
                implied_prob=ask, decimal=round(1.0 / ask, 4), fair_prob=None, **kw)


class TestKalshiEv:
    def test_positive_ev_when_kalshi_ask_below_book_fair(self):
        # Books think HOME is worth 0.62; Kalshi will sell it for 0.55 -- a real
        # discount, so buying Kalshi's contract should show positive EV.
        k = pd.DataFrame([_kalshi_ask_row("HOME", 0.55, market="game_ml", game_pk=1)])
        k["_pid"] = -1
        k["_line"] = -99.0
        cons = pd.DataFrame([{"market": "game_ml", "game_pk": 1, "_pid": -1,
                              "_line": -99.0, "selection": "HOME",
                              "book_fair": 0.62, "n_books": 5}])
        out = V._kalshi_ev(k, cons)
        assert len(out) == 1
        row = out.iloc[0]
        assert row["edge"] == round(0.62 - 0.55, 4)
        assert row["ev_pct"] == round(0.62 * (1.0 / 0.55) - 1.0, 4)
        assert row["ev_pct"] > 0

    def test_negative_ev_when_kalshi_ask_above_book_fair(self):
        # Kalshi wants 0.70 for something the books think is worth 0.62 -- a bad buy.
        k = pd.DataFrame([_kalshi_ask_row("HOME", 0.70, market="game_ml", game_pk=1)])
        k["_pid"] = -1
        k["_line"] = -99.0
        cons = pd.DataFrame([{"market": "game_ml", "game_pk": 1, "_pid": -1,
                              "_line": -99.0, "selection": "HOME",
                              "book_fair": 0.62, "n_books": 5}])
        out = V._kalshi_ev(k, cons)
        assert out.iloc[0]["ev_pct"] < 0

    def test_no_match_when_consensus_has_no_matching_group(self):
        k = pd.DataFrame([_kalshi_ask_row("HOME", 0.55, market="game_ml", game_pk=1)])
        k["_pid"] = -1
        k["_line"] = -99.0
        cons = pd.DataFrame([{"market": "game_ml", "game_pk": 2, "_pid": -1,
                              "_line": -99.0, "selection": "HOME",
                              "book_fair": 0.62, "n_books": 5}])
        out = V._kalshi_ev(k, cons)
        assert len(out) == 0


# --------------------------------------------------------------------------- #
# _kalshi_won() / _tstat() -- realized-outcome settlement for validate_kalshi_side
# --------------------------------------------------------------------------- #

class TestKalshiWon:
    def test_game_ml_home_selection_wins_when_home_won(self):
        assert V._kalshi_won("game_ml", "HOME", 1.0) == 1.0

    def test_game_ml_home_selection_loses_when_away_won(self):
        assert V._kalshi_won("game_ml", "HOME", 0.0) == 0.0

    def test_game_ml_away_selection_wins_when_away_won(self):
        assert V._kalshi_won("game_ml", "AWAY", 0.0) == 1.0

    def test_nrfi_ou_yrfi_selection_wins_when_run_scored(self):
        assert V._kalshi_won("nrfi_ou", "YRFI", 1.0) == 1.0

    def test_nrfi_ou_nrfi_selection_wins_when_no_run(self):
        assert V._kalshi_won("nrfi_ou", "NRFI", 0.0) == 1.0

    def test_unknown_market_returns_none(self):
        assert V._kalshi_won("game_total", "OVER", 8.0) is None

    def test_nan_realized_returns_none(self):
        assert V._kalshi_won("game_ml", "HOME", float("nan")) is None


class TestTstat:
    def test_none_with_fewer_than_two_values(self):
        assert V._tstat(pd.Series([0.5])) is None

    def test_none_when_zero_variance(self):
        assert V._tstat(pd.Series([0.1, 0.1, 0.1])) is None

    def test_positive_tstat_for_consistently_positive_roi(self):
        t = V._tstat(pd.Series([0.1, 0.12, 0.09, 0.11, 0.10]))
        assert t is not None and t > 0


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
