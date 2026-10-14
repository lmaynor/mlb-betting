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


    def test_n_books_counts_only_books_with_a_devigged_fair_prob(self):
        """One-sided / un-devigged quotes carry fair_prob=NaN. n_books must not
        count them, or a 1-real + 3-NaN group clears a min_books=4 gate."""
        books = pd.DataFrame([
            _row(book="draftkings", market="game_ml", game_pk=1, selection="HOME", fair_prob=0.60),
            _row(book="fanduel", market="game_ml", game_pk=1, selection="HOME", fair_prob=None),
            _row(book="betmgm", market="game_ml", game_pk=1, selection="HOME", fair_prob=None),
            _row(book="caesars", market="game_ml", game_pk=1, selection="HOME", fair_prob=None),
        ])
        books["_pid"] = -1
        books["_line"] = -99.0
        assert len(V._kalshi_book_fair(books, min_books=4)) == 0
        out = V._kalshi_book_fair(books, min_books=1)
        assert out.iloc[0]["n_books"] == 1


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


# --------------------------------------------------------------------------- #
# _realized_outcomes() -- ground truth for validate_kalshi_side
# --------------------------------------------------------------------------- #

class TestRealizedOutcomes:
    def _patch_csv(self, monkeypatch, df):
        monkeypatch.setattr("mlb_core.storage.read_bytes", lambda key: df.to_csv(index=False).encode())

    def test_nrfi_yrfi_is_game_level_max_over_both_starter_rows(self, monkeypatch):
        """Regression: NRFI model_features.csv has one row PER STARTER and
        `yrfi` is that pitcher's own 1st-inning result. dict(zip(...)) kept
        whichever row came last, labeling a game NRFI whenever only the OTHER
        half scored. Game-level YRFI = either half scored."""
        df = pd.DataFrame([
            {"game_pk": 1, "yrfi": 1}, {"game_pk": 1, "yrfi": 0},   # one half scored, scoring row first
            {"game_pk": 2, "yrfi": 0}, {"game_pk": 2, "yrfi": 1},   # scoring row last
            {"game_pk": 3, "yrfi": 0}, {"game_pk": 3, "yrfi": 0},   # genuinely NRFI
        ])
        self._patch_csv(monkeypatch, df)
        assert V._realized_outcomes("nrfi_ou") == {1: 1.0, 2: 1.0, 3: 0.0}

    def test_game_ml_label_unchanged(self, monkeypatch):
        df = pd.DataFrame([{"game_pk": 1, "home_win": 1}, {"game_pk": 2, "home_win": 0}])
        self._patch_csv(monkeypatch, df)
        assert V._realized_outcomes("game_ml") == {1: 1.0, 2: 0.0}

    def test_market_without_source_returns_empty(self):
        assert V._realized_outcomes("hr_yn") == {}


# --------------------------------------------------------------------------- #
# game_rl (run line) join -- 2026-10-03 regression.
#
# Kalshi quotes "TEAM wins by MORE THAN N" (selection=TEAM, line=+N). A sportsbook quotes
# HOME -1.5 / AWAY +1.5 per side. The old join keyed on raw (selection, line), so Kalshi's
# AWAY/+1.5 ("away wins by >1.5", ~0.235) met the book's AWAY/+1.5 ("away covers +1.5", ~0.534)
# -- different bets -- and Kalshi's HOME/+1.5 never met the book's equivalent HOME/-1.5.
# Numbers below are the real SD@MIL (game_pk 849830) rows from odds_history, 2026-10-03.
# --------------------------------------------------------------------------- #

def _rl_book(book, selection, line, fair, american):
    dec = round(1 + (american / 100 if american > 0 else 100 / -american), 4)
    return _row(market="game_rl", book=book, selection=selection, line=line, fair_prob=fair,
                american=american, decimal=dec, implied_prob=round(1 / dec, 4), game_pk=849830)


def _rl_kalshi(selection, line, mid, ask):
    return _row(market="game_rl", book="kalshi", source="kalshi", selection=selection, line=line,
                fair_prob=mid, implied_prob=ask, decimal=round(1 / ask, 4), american=0, game_pk=849830)


class TestRunLineJoin:
    def _frame(self):
        rows = []
        for b in ("draftkings", "fanduel", "betmgm", "caesars"):
            rows += [_rl_book(b, "HOME", -1.5, 0.466, 105),     # home covers -1.5  == Kalshi "HOME wins by >1.5"
                     _rl_book(b, "AWAY", 1.5, 0.534, -125)]     # away covers +1.5  == NOT(HOME wins by >1.5)
        rows += [_rl_kalshi("HOME", 1.5, 0.455, 0.46), _rl_kalshi("AWAY", 1.5, 0.235, 0.24)]
        return V._prep(pd.DataFrame(rows))

    def test_prep_maps_each_quote_to_the_proposition_it_prices(self):
        d = self._frame()
        dk = d[d["book"] == "draftkings"].set_index("selection")
        assert (dk.loc["HOME", "_jsel"], dk.loc["HOME", "_jline"], dk.loc["HOME", "_no"]) == ("HOME", 1.5, False)
        # AWAY +1.5 is the COMPLEMENT of HOME winning by more than 1.5
        assert (dk.loc["AWAY", "_jsel"], dk.loc["AWAY", "_jline"], dk.loc["AWAY", "_no"]) == ("HOME", 1.5, True)
        assert dk.loc["AWAY", "line"] == 1.5                    # display line untouched (the book's own +1.5)
        assert "AWAY" in set(d[d["book"] == "draftkings"]["selection"])   # display selection untouched
        ka = d[d["book"] == "kalshi"].set_index("selection")
        assert (ka.loc["AWAY", "_jsel"], ka.loc["AWAY", "_jline"], bool(ka.loc["AWAY", "_no"])) == ("AWAY", 1.5, False)

    def test_alt_lines_map_both_directions(self):
        rows = [_rl_book("b", "AWAY", -1.5, 0.24, 300), _rl_book("b", "HOME", 1.5, 0.76, -400),
                _rl_book("b", "HOME", -2.5, 0.35, 190)]
        d = V._prep(pd.DataFrame(rows)).set_index(["selection", "line"])
        assert (d.loc[("AWAY", -1.5), "_jsel"], d.loc[("AWAY", -1.5), "_no"]) == ("AWAY", False)
        assert (d.loc[("HOME", 1.5), "_jsel"], d.loc[("HOME", 1.5), "_no"]) == ("AWAY", True)   # opp, complement
        assert (d.loc[("HOME", -2.5), "_jline"], d.loc[("HOME", -2.5), "_no"]) == (2.5, False)

    def test_whole_number_and_missing_run_lines_are_dropped_not_guessed(self):
        rows = [_rl_book("b", "HOME", -1.0, 0.5, 100), _rl_book("b", "AWAY", None, 0.5, 100),
                _rl_book("b", "HOME", -1.5, 0.47, 105)]
        d = V._prep(pd.DataFrame(rows))
        assert len(d) == 1 and d.iloc[0]["line"] == -1.5

    def test_book_cover_joins_the_same_proposition_not_the_look_alike(self):
        """THE regression: the book's AWAY +1.5 (0.534) must be matched to Kalshi's HOME-by->1.5
        mid (0.455) as a complement -> true prob 0.545 -- never to Kalshi's AWAY-by->1.5 (0.235)."""
        d = self._frame()
        m = V._match_books(d[d["book"] == "kalshi"], d[d["book"] != "kalshi"], "game_rl")
        away = m[(m["selection"] == "AWAY") & (m["book"] == "draftkings")]
        assert len(away) == 1                                   # one join, not two look-alikes
        assert abs(away.iloc[0]["p_true"] - 0.545) < 1e-9       # 1 - 0.455, NOT 0.235
        assert abs(away.iloc[0]["ev_pct"] - round(0.545 * 1.8 - 1, 4)) < 1e-3
        home = m[(m["selection"] == "HOME") & (m["book"] == "draftkings")]
        assert len(home) == 1 and abs(home.iloc[0]["p_true"] - 0.455) < 1e-9
        assert len(m) == 8                                      # 4 books x 2 quotes; nothing joined to AWAY-by->1.5

    def test_complement_quotes_never_pollute_each_others_consensus(self):
        d = self._frame()
        m = V._match_books(d[d["book"] == "kalshi"], d[d["book"] != "kalshi"], "game_rl")
        # a HOME -1.5 quote's consensus is over HOME -1.5 quotes only (implied ~0.488), not mixed
        # with the complementary AWAY +1.5 quotes (implied ~0.556)
        home = m[m["selection"] == "HOME"].iloc[0]
        away = m[m["selection"] == "AWAY"].iloc[0]
        assert abs(home["cons_impl"] - 0.4878) < 1e-3 and abs(away["cons_impl"] - 0.5556) < 1e-3

    def test_alt_line_away_wins_by_more_than_matches_kalshi_away_contract(self):
        rows = [_rl_book("b", "AWAY", -1.5, 0.24, 300)] + [_rl_kalshi("AWAY", 1.5, 0.235, 0.24)]
        d = V._prep(pd.DataFrame(rows))
        m = V._match_books(d[d["book"] == "kalshi"], d[d["book"] != "kalshi"], "game_rl")
        assert len(m) == 1 and abs(m.iloc[0]["p_true"] - 0.235) < 1e-9 and not m.iloc[0]["_no"]

    def test_non_run_line_markets_are_untouched_by_the_new_keys(self):
        rows = [_row(market="game_ml", book="draftkings", selection="HOME", line=None),
                _row(market="game_total", book="draftkings", selection="OVER", line=8.5)]
        d = V._prep(pd.DataFrame(rows))
        assert (d["_jsel"] == d["selection"]).all() and not d["_no"].any()
        assert d.set_index("market").loc["game_total", "_jline"] == 8.5

    def test_mirror_book_fair_flips_complement_quotes_onto_one_proposition(self):
        d = self._frame()
        cons = V._kalshi_book_fair(d[d["book"] != "kalshi"], min_books=4)
        # HOME -1.5 (fair 0.466) x4 and AWAY +1.5 flipped (1-0.534 = 0.466) x4 vote on one key
        assert len(cons) == 1
        row = cons.iloc[0]
        assert row["_jsel"] == "HOME" and row["_jline"] == 1.5 and row["n_books"] == 8
        assert abs(row["book_fair"] - 0.466) < 1e-9

    def test_mirror_no_longer_matches_kalshi_away_contract_to_the_away_cover(self):
        """Old behavior: Kalshi AWAY/1.5 (ask 0.24) joined book AWAY/+1.5 (fair 0.534) -> a fake
        +122% 'edge'. There is no book quote for 'away wins by >1.5' here, so there is NO match."""
        d = self._frame()
        cons = V._kalshi_book_fair(d[d["book"] != "kalshi"], min_books=4)
        ev = V._kalshi_ev(d[d["book"] == "kalshi"], cons)
        assert list(ev["selection"]) == ["HOME"]                # only the genuine HOME/1.5 match
        assert abs(ev.iloc[0]["book_fair"] - 0.466) < 1e-9
        assert (ev["ev_pct"] < 0.1).all()                       # no manufactured triple-digit EV


# --------------------------------------------------------------------------- #
# Time alignment + synthetic rows (2026-10-03). Both sources keep capturing after first pitch, and
# 'latest snapshot per quote' was chosen per source independently, so a Kalshi IN-PLAY price could be
# paired with the books' last PRE-GAME price. 'consensus' (a synthetic aggregate row) was also
# treated as a real, bettable book.
# --------------------------------------------------------------------------- #

# SD@MIL 2026-10-03, first pitch 8:30pm EDT == 2026-10-04 00:30 UTC
_EV = "KXMLBGAME-26OCT032030SDMIL"


class TestPregameAlignment:
    def _odds(self):
        rows = [
            _row(market="game_ml", game_pk=1, book="draftkings", selection="HOME", snapshot_ts="2026-10-03 23:32:33",
                 implied_prob=0.60, fair_prob=0.62),
            # Kalshi: a normal pre-game print, then an IN-PLAY print after first pitch (team leads big)
            _row(market="game_ml", game_pk=1, book="kalshi", source="kalshi", selection="HOME", event_id=_EV,
                 snapshot_ts="2026-10-03 23:07:45", implied_prob=0.55, fair_prob=0.545),
            _row(market="game_ml", game_pk=1, book="kalshi", source="kalshi", selection="HOME", event_id=_EV,
                 snapshot_ts="2026-10-04 02:10:00", implied_prob=0.96, fair_prob=0.955),
        ]
        return pd.DataFrame(rows)

    def test_inplay_snapshots_dropped_before_latest_is_chosen(self):
        kept = V._drop_inplay(self._odds())
        assert sorted(kept["snapshot_ts"]) == ["2026-10-03 23:07:45", "2026-10-03 23:32:33"]
        d = V._prep(kept)
        k = d[d["book"] == "kalshi"]
        assert len(k) == 1 and k.iloc[0]["implied_prob"] == 0.55     # the PRE-GAME print, not 0.96

    def test_without_the_filter_the_old_pairing_is_the_inplay_price(self):
        """Documents the bug: unfiltered, 'latest' is the in-play 0.96."""
        d = V._prep(self._odds())
        assert d[d["book"] == "kalshi"].iloc[0]["implied_prob"] == 0.96

    def test_first_pitch_is_eastern_time_from_event_ticker(self):
        from mlb_core.odds import kalshi as K
        st = K.event_start_utc(_EV)
        assert (st.year, st.month, st.day, st.hour, st.minute) == (2026, 10, 4, 0, 30)
        assert K.event_start_utc("garbage") is None and K.event_start_utc("") is None

    def test_game_without_kalshi_event_is_left_alone(self):
        odds = self._odds()
        odds = odds[odds["book"] != "kalshi"]
        assert len(V._drop_inplay(odds)) == len(odds)

    def test_other_games_unaffected_by_one_games_start(self):
        odds = self._odds()
        other = _row(market="game_ml", game_pk=2, book="draftkings", selection="HOME",
                     snapshot_ts="2026-10-04 03:00:00", implied_prob=0.5)       # different game, no Kalshi row
        both = pd.concat([odds, pd.DataFrame([other])], ignore_index=True)
        kept = V._drop_inplay(both)
        assert (kept["game_pk"] == 2).sum() == 1 and ((kept["game_pk"] == 1) & (kept["snapshot_ts"] > "2026-10-04")).sum() == 0

    def test_gap_filter_drops_far_apart_pairs_and_none_disables(self):
        m = pd.DataFrame({"k_ts": ["2026-10-03 23:07:00", "2026-10-03 18:00:00"],
                          "snapshot_ts": ["2026-10-03 23:32:00", "2026-10-03 23:32:00"]})
        assert len(V._gap_filter(m, "k_ts", "snapshot_ts", 60)) == 1       # 25 min kept, 5.5 h dropped
        assert len(V._gap_filter(m, "k_ts", "snapshot_ts", None)) == 2
        assert len(V._gap_filter(pd.DataFrame(), "k_ts", "snapshot_ts", 60)) == 0

    def test_real_books_excludes_kalshi_consensus_and_open(self):
        df = pd.DataFrame([_row(book=b) for b in ("draftkings", "consensus", "open", "kalshi", "fanduel")])
        assert sorted(V._real_books(df)["book"]) == ["draftkings", "fanduel"]

    def test_consensus_row_no_longer_inflates_book_fair_or_n_books(self):
        rows = [_row(book=b, market="game_ml", game_pk=1, selection="HOME", fair_prob=f)
                for b, f in (("draftkings", 0.60), ("fanduel", 0.62), ("betmgm", 0.64), ("caesars", 0.62),
                             ("consensus", 0.99))]
        books = V._real_books(pd.DataFrame(rows))
        books["_pid"], books["_line"] = -1, -99.0
        out = V._kalshi_book_fair(books, min_books=4)
        assert out.iloc[0]["n_books"] == 4 and abs(out.iloc[0]["book_fair"] - 0.62) < 1e-9
        assert "cons_ts" in out.columns

    def test_mirror_cons_ts_is_the_latest_contributing_snapshot(self):
        rows = [_row(book=b, market="game_ml", game_pk=1, selection="HOME", fair_prob=0.6, snapshot_ts=t)
                for b, t in (("a", "2026-10-03 21:00:00"), ("b", "2026-10-03 23:00:00"),
                             ("c", "2026-10-03 22:00:00"), ("d", "2026-10-03 22:30:00"))]
        books = pd.DataFrame(rows)
        books["_pid"], books["_line"] = -1, -99.0
        assert V._kalshi_book_fair(books, min_books=4).iloc[0]["cons_ts"] == "2026-10-03 23:00:00"


class TestNetOfFee:
    def test_fee_per_contract_matches_schedule(self):
        # 0.07 * 0.5 * 0.5 = 0.0175 at M=1; M=0.5 halves it
        assert abs(V.kalshi_taker_fee_per_contract(0.5, 1.0) - 0.0175) < 1e-12
        assert abs(V.kalshi_taker_fee_per_contract(0.5, 0.5) - 0.00875) < 1e-12

    def test_validate_reports_net_roi_below_gross(self, monkeypatch, capsys):
        monkeypatch.setattr(V, "_realized_outcomes", lambda m: {1: 1.0, 2: 0.0, 3: 1.0, 4: 1.0})
        df = pd.DataFrame([
            {"market": "game_ml", "game_pk": g, "selection": "HOME", "implied_prob": 0.40,
             "decimal": 2.5, "book_fair": 0.46, "ev_pct": 0.15, "line": None} for g in (1, 2, 3, 4)])
        V.validate_kalshi_side(df, fee_mult={"game_ml": 0.5})
        out = capsys.readouterr().out
        assert "NET of Kalshi taker fee" in out and "all flagged, net" in out and "executor gate" in out
        # gross ROI: 3 wins x +1.5 and 1 loss -> (4.5 - 1)/4 = +87.5%
        assert "ROI=+87.5%" in out
        # net: cost = 0.40 + 0.5*0.07*0.4*0.6 = 0.4084; win roi = 1.4486, loss -1 -> mean (3*1.4486-1)/4
        net = (3 * (1 - 0.4084) / 0.4084 - 1) / 4 * 100
        assert f"ROI={net:+.1f}%" in out and net < 87.5

    def test_gate_subset_excludes_edges_outside_the_ev_band(self, monkeypatch, capsys):
        monkeypatch.setattr(V, "_realized_outcomes", lambda m: {1: 1.0, 2: 1.0})
        df = pd.DataFrame([   # g1: tiny edge (below gate), g2: in-band edge
            {"market": "game_ml", "game_pk": 1, "selection": "HOME", "implied_prob": 0.40, "decimal": 2.5,
             "book_fair": 0.41, "ev_pct": 0.02, "line": None},
            {"market": "game_ml", "game_pk": 2, "selection": "HOME", "implied_prob": 0.40, "decimal": 2.5,
             "book_fair": 0.45, "ev_pct": 0.12, "line": None}])
        V.validate_kalshi_side(df, fee_mult={"game_ml": 1.0})
        out = capsys.readouterr().out
        assert "after 0.5% haircut), net: n=1" in out


def test_cli_help_builds_without_error():
    """Regression: a bare '%' in an argparse help string raised ValueError at
    parser-build time on py3.14 (and on --help for every version)."""
    import pytest as _pytest
    with _pytest.raises(SystemExit) as e:
        V.main(["--help"])
    assert e.value.code == 0
