"""
tests/test_kalshi_client.py -- mlb_core.odds.kalshi pure parsing/math helpers.

No network: everything under test here is regex/string parsing or arithmetic.
fetch_active_markets() (the one networked function) is exercised indirectly via
test_kalshi_to_history.py with requests monkeypatched out.
"""
from mlb_core.odds import kalshi as K


# --------------------------------------------------------------------------- #
# ff() -- Kalshi's stringly-typed numeric fields
# --------------------------------------------------------------------------- #

class TestFf:
    def test_parses_numeric_string(self):
        assert K.ff("0.4700") == 0.47

    def test_none_is_zero(self):
        assert K.ff(None) == 0.0

    def test_empty_string_is_zero(self):
        assert K.ff("") == 0.0

    def test_garbage_is_zero(self):
        assert K.ff("not-a-number") == 0.0

    def test_already_numeric_passes_through(self):
        assert K.ff(3) == 3.0


# --------------------------------------------------------------------------- #
# _split_teams() -- concatenated team-abbrev blob from an event ticker
# --------------------------------------------------------------------------- #

class TestSplitTeams:
    def test_two_two_split(self):
        assert K._split_teams("TBTOR") == ("TB", "TOR")

    def test_three_two_split(self):
        # away=ARI (3 chars), home=TB (2 chars)
        assert K._split_teams("ARITB") == ("ARI", "TB")

    def test_two_three_split(self):
        # away=TB (2 chars), home=ARI (3 chars)
        assert K._split_teams("TBARI") == ("TB", "ARI")

    def test_unknown_abbrev_is_ambiguous(self):
        assert K._split_teams("ZZTOR") == (None, None)

    def test_no_valid_split_is_ambiguous(self):
        # Neither the 2- nor 3-char away split lands on a known abbrev ->
        # zero hits -> (None, None). (A genuine dual-hit collision would need
        # two real abbrevs that overlap at both split points; none exist in
        # KALSHI_ABBREVS today, so this is the reachable "ambiguous" case.)
        assert K._split_teams("XXXXX") == (None, None)


# --------------------------------------------------------------------------- #
# parse_event_ticker() -- 'KXMLBHR-26JUL231507TBTOR' -> (date, away, home)
# --------------------------------------------------------------------------- #

class TestParseEventTicker:
    def test_full_parse(self):
        date, away, home = K.parse_event_ticker("KXMLBHR-26JUL231507TBTOR")
        assert date == "2026-07-23"
        assert away == "TB"
        assert home == "TOR"

    def test_doubleheader_suffix_still_parses(self):
        date, away, home = K.parse_event_ticker("KXMLBHR-26JUL231507TBTORG2")
        assert date == "2026-07-23"
        assert (away, home) == ("TB", "TOR")

    def test_no_dash_returns_all_none(self):
        assert K.parse_event_ticker("GARBAGE") == (None, None, None)

    def test_empty_string_returns_all_none(self):
        assert K.parse_event_ticker("") == (None, None, None)

    def test_bad_month_returns_all_none(self):
        # "XXX" is not in _MONTHS
        assert K.parse_event_ticker("KXMLBHR-26XXX231507TBTOR") == (None, None, None)

    def test_unmatched_tail_returns_all_none(self):
        assert K.parse_event_ticker("KXMLBHR-not-the-expected-shape") == (None, None, None)

    def test_ambiguous_teams_still_returns_good_date(self):
        # bad team blob -> date should still parse, teams come back None
        date, away, home = K.parse_event_ticker("KXMLBHR-26JUL231507ZZQQQ")
        assert date == "2026-07-23"
        assert (away, home) == (None, None)


# --------------------------------------------------------------------------- #
# market_outcome() -- trailing ticker segment -> team/side token
# --------------------------------------------------------------------------- #

class TestMarketOutcome:
    def test_team_ml_outcome(self):
        assert K.market_outcome({"ticker": "KXMLBGAME-26JUL23TBTOR-ATL"}) == "ATL"

    def test_spread_strips_trailing_digits(self):
        assert K.market_outcome({"ticker": "KXMLBSPREAD-26JUL23TBTOR-ATL2"}) == "ATL"

    def test_tie_outcome(self):
        assert K.market_outcome({"ticker": "KXMLBGAME-26JUL23TBTOR-TIE"}) == "TIE"

    def test_missing_ticker_is_empty(self):
        assert K.market_outcome({}) == ""


# --------------------------------------------------------------------------- #
# player_from_title()
# --------------------------------------------------------------------------- #

class TestPlayerFromTitle:
    def test_splits_on_first_colon(self):
        mk = {"yes_sub_title": "Vladimir Guerrero Jr.: 2+ home runs?"}
        assert K.player_from_title(mk) == "Vladimir Guerrero Jr."

    def test_falls_back_to_title(self):
        mk = {"title": "Shohei Ohtani: 1+ strikeouts allowed?"}
        assert K.player_from_title(mk) == "Shohei Ohtani"

    def test_no_colon_returns_whole_stripped_string(self):
        mk = {"title": "no colon here"}
        assert K.player_from_title(mk) == "no colon here"

    def test_missing_fields_returns_empty(self):
        assert K.player_from_title({}) == ""


# --------------------------------------------------------------------------- #
# prices() -- top-of-book mid/ask extraction
# --------------------------------------------------------------------------- #

class TestPrices:
    def test_normal_two_sided_book(self):
        mk = {"yes_bid_dollars": "0.4500", "yes_ask_dollars": "0.4900",
              "no_bid_dollars": "0.5100", "no_ask_dollars": "0.5500",
              "yes_bid_size_fp": "100", "yes_ask_size_fp": "80",
              "volume_fp": "500", "open_interest_fp": "200"}
        p = K.prices(mk)
        assert abs(p["yes_mid"] - 0.47) < 1e-9
        assert abs(p["no_mid"] - 0.53) < 1e-9
        assert p["volume"] == 500.0
        assert p["open_interest"] == 200.0

    def test_missing_no_side_derives_from_yes_mid(self):
        # no_bid/no_ask both absent (0.0) -> no_mid falls back to 1 - yes_mid
        mk = {"yes_bid_dollars": "0.40", "yes_ask_dollars": "0.44"}
        p = K.prices(mk)
        assert abs(p["yes_mid"] - 0.42) < 1e-9
        assert abs(p["no_mid"] - 0.58) < 1e-9

    def test_completely_empty_market_has_no_mids(self):
        p = K.prices({})
        assert p["yes_mid"] is None
        assert p["no_mid"] is None
        assert p["volume"] == 0.0


# --------------------------------------------------------------------------- #
# prob_to_american() -- probability -> American odds
# --------------------------------------------------------------------------- #

class TestProbToAmerican:
    def test_favorite(self):
        # p=0.6667 -> ~-200
        assert K.prob_to_american(2 / 3) == -200

    def test_underdog(self):
        # p=0.3333 -> ~+200
        assert K.prob_to_american(1 / 3) == 200

    def test_even_money_is_boundary_favorite_branch(self):
        # p == 0.5 takes the p < 0.5 == False branch -> -100
        assert K.prob_to_american(0.5) == -100

    def test_degenerate_zero_is_none(self):
        assert K.prob_to_american(0) is None

    def test_degenerate_one_is_none(self):
        assert K.prob_to_american(1) is None

    def test_none_input_is_none(self):
        assert K.prob_to_american(None) is None
