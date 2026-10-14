"""ev.kalshi_map: strict Kalshi -> proposition mapping for NFL / NBA / NHL.

Fixtures are real Kalshi public-API payloads captured 2026-10-04 (trimmed to the fields the mapper
reads). Every failure case here is a wrong-BET risk, so each must fail CLOSED."""
import itertools

import pytest

from ev.kalshi_map import Leg, game_key, map_market, parse_event, split_teams
from ev.sports import ADAPTERS, NBA, NFL, NHL, get_adapter
from mlb.execution.kalshi.mapping import MapFail

NFL_TIE = ("The following market refers to the team who wins the BUF Bills vs LA Rams Pro Football game "
           "originally scheduled for Oct 12, 2026. If the game ends in a tie, the market will resolve to "
           "$0.50 for each team.")


def _mk(**kw):
    base = {"market_type": "binary", "exchange_index": 0,
            "price_ranges": [{"start": "0.0000", "end": "1.0000", "step": "0.0100"}]}
    base.update(kw)
    return base


NFL_ML = _mk(
    ticker="KXNFLGAME-26OCT12BUFLAR-LAR", event_ticker="KXNFLGAME-26OCT12BUFLAR",
    title="Los Angeles R wins", yes_sub_title="Los Angeles R", strike_type="structured",
    rules_primary="If Los Angeles R wins the BUF Bills vs LA Rams Pro Football game originally "
                  "scheduled for Oct 12, 2026, then the market resolves to Yes.",
    rules_secondary=NFL_TIE)
NBA_ML = _mk(
    ticker="KXNBAGAME-26OCT09HOUDAL-HOU", event_ticker="KXNBAGAME-26OCT09HOUDAL", exchange_index=3,
    title="Houston wins", yes_sub_title="Houston",
    rules_primary="If Houston wins the Houston vs Dallas Pro Basketball game originally scheduled for "
                  "Oct 9, 2026, then the market resolves to Yes.", rules_secondary="")
NHL_ML = _mk(
    ticker="KXNHLGAME-26OCT10LAVGK-VGK", event_ticker="KXNHLGAME-26OCT10LAVGK",
    title="Vegas wins", yes_sub_title="Vegas",
    rules_primary="If Vegas wins the Los Angeles vs Vegas NHL game originally scheduled for Oct 10, 2026, "
                  "then the market resolves to Yes.", rules_secondary="")
NFL_SPREAD = _mk(
    ticker="KXNFLSPREAD-26OCT08TBDAL-TB8", event_ticker="KXNFLSPREAD-26OCT08TBDAL",
    title="TB Buccaneers wins by over 7.5 points?", yes_sub_title="TB Buccaneers wins by over 7.5 points",
    strike_type="greater", floor_strike=7.5,
    rules_primary="If TB Buccaneers wins by more than 7.5 points in the TB Buccaneers vs DAL Cowboys Pro "
                  "Football game originally scheduled for Oct 8, 2026, then the market resolves to Yes.")
NHL_SPREAD = _mk(
    ticker="KXNHLSPREAD-26OCT06FLALA-LA3", event_ticker="KXNHLSPREAD-26OCT06FLALA",
    title="Los Angeles wins by over 2.5 goals", yes_sub_title="Los Angeles wins by over 2.5 goals",
    strike_type="greater", floor_strike=2.5,
    rules_primary="If Los Angeles wins by over 2.5 goals in the Florida vs Los Angeles NHL game "
                  "originally scheduled for Oct 6, 2026, then the market resolves to Yes.")
NBA_TOTAL = _mk(
    ticker="KXNBATOTAL-26OCT04UTADEN-258", event_ticker="KXNBATOTAL-26OCT04UTADEN", exchange_index=3,
    title="Full Game: Over 257.5 points scored", yes_sub_title="Over 257.5 points",
    strike_type="greater", floor_strike=257.5,
    rules_primary="If over 257.5 total points are scored in the Utah vs Denver Pro Basketball game "
                  "originally scheduled for Oct 4, 2026, then the market resolves to Yes.")
NFL_TOTAL = _mk(
    ticker="KXNFLTOTAL-26OCT08TBDAL-69", event_ticker="KXNFLTOTAL-26OCT08TBDAL",
    title="Full Game: over 68.5 points scored?", yes_sub_title="Over 68.5 points",
    strike_type="greater", floor_strike=68.5,
    rules_primary="If TB Buccaneers and DAL Cowboys collectively score more than 68.5 points in the TB "
                  "Buccaneers vs DAL Cowboys Pro Football game originally scheduled for Oct 8, 2026, "
                  "then the market resolves to Yes.")
NHL_TOTAL = _mk(
    ticker="KXNHLTOTAL-26OCT06FLALA-9", event_ticker="KXNHLTOTAL-26OCT06FLALA",
    title="Full Game: Over 8.5 goals scored", yes_sub_title="Over 8.5 goals scored",
    strike_type="greater", floor_strike=8.5,
    rules_primary="If the teams collectively score more than 8.5 goals in the Florida vs Los Angeles NHL "
                  "game originally scheduled for Oct 6, 2026, then the market resolves to Yes.")


# ---------------- event parsing ----------------
def test_parse_event_all_leagues():
    assert parse_event(NFL, "KXNFLGAME-26OCT12BUFLAR") == ("2026-10-12", "BUF", "LAR")
    assert parse_event(NBA, "KXNBAGAME-26OCT09HOUDAL") == ("2026-10-09", "HOU", "DAL")
    assert parse_event(NHL, "KXNHLGAME-26OCT10LAVGK") == ("2026-10-10", "LA", "VGK")


@pytest.mark.parametrize("adapter", list(ADAPTERS.values()), ids=list(ADAPTERS))
def test_every_ordered_team_pair_splits_uniquely(adapter):
    """No two valid splits for ANY matchup -- otherwise the ticker is silently mis-attributed."""
    for a, b in itertools.permutations(sorted(adapter.codes), 2):
        assert split_teams(a + b, adapter.codes) == (a, b), (a, b)


def test_unknown_code_is_ambiguous_not_guessed():
    r = parse_event(NFL, "KXNFLGAME-26OCT12BUFXXX")
    assert isinstance(r, MapFail) and r.reason == "REJ_AMBIGUOUS"


def test_wrong_series_prefix_rejected():
    r = parse_event(NFL, "KXNHLGAME-26OCT10LAVGK")
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


@pytest.mark.parametrize("ev", ["", "KXNFLGAME", "KXNFLGAME-26OCT12", "KXNFLGAME-26XXX12BUFLAR",
                                "KXNFLGAME-26OCT151507BUFLAR"])
def test_malformed_event_tails_rejected(ev):
    assert isinstance(parse_event(NFL, ev), MapFail)


# ---------------- moneyline ----------------
def test_nfl_ml_home_and_buy_no_is_away():
    leg = map_market(NFL, "ml", NFL_ML)
    assert isinstance(leg, Leg)
    assert (leg.away, leg.home, leg.game_date) == ("BUF", "LAR", "2026-10-12")
    assert leg.game_key == "nfl:2026-10-12:BUF@LAR" == game_key("nfl", "2026-10-12", "BUF", "LAR")
    assert leg.yes_sel == ("HOME", None) and leg.no_sel == ("AWAY", None)
    assert leg.market == "game_ml" and leg.series == "KXNFLGAME"


def test_ml_away_outcome():
    mk = dict(NFL_ML, ticker="KXNFLGAME-26OCT12BUFLAR-BUF", title="Buffalo wins", yes_sub_title="Buffalo",
              rules_primary=NFL_ML["rules_primary"].replace("Los Angeles R", "Buffalo", 1))
    leg = map_market(NFL, "ml", mk)
    assert leg.yes_sel == ("AWAY", None) and leg.no_sel == ("HOME", None)


def test_nba_and_nhl_ml_and_exchange_index():
    nba = map_market(NBA, "ml", NBA_ML)
    assert nba.yes_sel == ("AWAY", None) and nba.exchange_index == 3
    nhl = map_market(NHL, "ml", NHL_ML)
    assert (nhl.away, nhl.home) == ("LA", "VGK") and nhl.yes_sel == ("HOME", None)


def test_nfl_ml_requires_the_half_dollar_tie_rule():
    r = map_market(NFL, "ml", dict(NFL_ML, rules_secondary="If the game ends in a tie, resolves No."))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_ml_outcome_code_not_in_event():
    r = map_market(NFL, "ml", dict(NFL_ML, ticker="KXNFLGAME-26OCT12BUFLAR-DAL"))
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


def test_ml_title_mismatch():
    r = map_market(NFL, "ml", dict(NFL_ML, title="Los Angeles R wins outright"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_rules_date_must_match_ticker_date():
    mk = dict(NFL_ML, rules_primary=NFL_ML["rules_primary"].replace("Oct 12", "Oct 13"))
    r = map_market(NFL, "ml", mk)
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_wrong_league_phrase_in_rules():
    r = map_market(NFL, "ml", dict(NFL_ML, rules_primary=NFL_ML["rules_primary"].replace("Pro Football", "NHL")))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_non_binary_market_rejected():
    r = map_market(NFL, "ml", dict(NFL_ML, market_type="scalar"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


# ---------------- spread ----------------
def test_nfl_spread_yes_is_team_minus_line_no_is_opponent_plus_line():
    leg = map_market(NFL, "spread", NFL_SPREAD)
    assert (leg.away, leg.home, leg.line) == ("TB", "DAL", 7.5)
    # "TB (away) wins by over 7.5" == book TB -7.5 ; buying NO == DAL +7.5
    assert leg.yes_sel == ("AWAY", -7.5) and leg.no_sel == ("HOME", 7.5)


def test_nhl_spread_uses_goals_and_over_wording():
    leg = map_market(NHL, "spread", NHL_SPREAD)
    assert (leg.away, leg.home, leg.line) == ("FLA", "LA", 2.5)
    assert leg.yes_sel == ("HOME", -2.5) and leg.no_sel == ("AWAY", 2.5)


def test_spread_ticker_strike_must_match_floor_strike():
    r = map_market(NFL, "spread", dict(NFL_SPREAD, ticker="KXNFLSPREAD-26OCT08TBDAL-TB9"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_whole_number_spread_has_a_push_and_is_rejected():
    mk = dict(NFL_SPREAD, floor_strike=7.0, ticker="KXNFLSPREAD-26OCT08TBDAL-TB7",
              title="TB Buccaneers wins by over 7 points?",
              rules_primary=NFL_SPREAD["rules_primary"].replace("7.5", "7"))
    r = map_market(NFL, "spread", mk)
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_spread_wrong_unit_rejected():
    r = map_market(NFL, "spread", dict(NFL_SPREAD, title="TB Buccaneers wins by over 7.5 goals?"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_spread_team_not_in_event():
    r = map_market(NFL, "spread", dict(NFL_SPREAD, ticker="KXNFLSPREAD-26OCT08TBDAL-NE8"))
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


# ---------------- total ----------------
def test_nba_and_nfl_totals():
    nba = map_market(NBA, "total", NBA_TOTAL)
    assert nba.yes_sel == ("OVER", 257.5) and nba.no_sel == ("UNDER", 257.5) and nba.market == "game_total"
    nfl = map_market(NFL, "total", NFL_TOTAL)
    assert nfl.line == 68.5


def test_total_ticker_strike_mismatch():
    r = map_market(NBA, "total", dict(NBA_TOTAL, ticker="KXNBATOTAL-26OCT04UTADEN-257"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_total_wrong_wording():
    r = map_market(NBA, "total", dict(NBA_TOTAL, title="First Half: Over 257.5 points scored"))
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER"


def test_nhl_total_is_refused_shootout_goal_difference():
    """Kalshi counts a shootout as one goal; the book total typically does not -> different proposition."""
    r = map_market(NHL, "total", NHL_TOTAL)
    assert isinstance(r, MapFail) and r.reason == "REJ_RULES_DIFFER" and "shootout" in r.detail


def test_series_mismatch_for_kind():
    r = map_market(NFL, "total", NFL_SPREAD)
    assert isinstance(r, MapFail) and r.reason == "REJ_UNMAPPED"


def test_unknown_kind_and_sport():
    assert isinstance(map_market(NFL, "prop", NFL_ML), MapFail)
    with pytest.raises(ValueError):
        get_adapter("mlb")
    assert get_adapter("NFL") is NFL


def test_price_grid_carried_through():
    leg = map_market(NFL, "ml", NFL_ML)
    assert leg.ranges and leg.ranges[0].step == pytest.approx(0.01)
