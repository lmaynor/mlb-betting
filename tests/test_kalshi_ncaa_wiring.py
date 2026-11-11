"""NCAA fair-book wiring: BettingPros-banked NCAAF / NCAAB game lines -> RFQ fair values (ev.kalshi_game_banked),
the Kalshi code census + BettingPros crosswalk (ev.sports.ncaa_teams), and the adapters. Offline; the banked-file
reader is patched at the storage seam. Team tables were built from live sources 2026-11-10 (see ev.sports.ncaa_teams).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ev import kalshi_game_banked as kb
from ev.kalshi_game_legs import GAME_SD, slate_rows, team_code_fn
from ev.kalshi_rfq_pricer import InMemoryFairBook
from ev.sports import ADAPTERS

NOW = 1_800_000_000.0
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)
FETCHED = NOW - 600.0                                  # a banker file fetched 10 minutes ago


def _iso(dt):
    return dt.isoformat()


def _quotes(ml=((150, -170), (145, -165), (155, -175)), spread=(-3.5, -110, -110), total=(47.5, -110, -110),
            books=("fanduel", "draftkings", "betmgm")):
    """Ladder quotes for one game in the bankers' shape: a = AWAY / OVER, b = HOME / UNDER, spread line = HOME handicap."""
    out = []
    for i, book in enumerate(books):
        a, b = ml[i % len(ml)]
        out.append({"market": "ml", "line": None, "book": book, "a": a, "b": b})
        out.append({"market": "spread", "line": spread[0], "book": book, "a": spread[1], "b": spread[2]})
        out.append({"market": "total", "line": total[0], "book": book, "a": total[1], "b": total[2]})
    return out


def _bp_game(away="OHI", home="MOH", start=None, quotes=None, src="bp1"):
    start = start or NOW_DT + timedelta(hours=3)
    return {"src_key": src, "away_team": away, "home_team": home, "away_name": None, "home_name": None,
            "commence_time": _iso(start), "quotes": _quotes() if quotes is None else quotes}


@pytest.fixture
def bp_files(monkeypatch):
    """Patch the storage read behind read_latest_bettingpros; each test sets .files = {sport: obj | None}."""
    from mlb_core import storage

    class F:
        files: dict = {}

    def read(key):
        sport = key.split("/")[-2]
        obj = F.files.get(sport)
        if obj is None:
            raise FileNotFoundError(key)
        import json
        return json.dumps(obj).encode()

    monkeypatch.setattr(storage, "read_bytes", read)
    return F


def _file(games, fetched=FETCHED):
    return {"sport": "ncaaf", "source": "bettingpros", "fetched_at": _iso(datetime.fromtimestamp(fetched, timezone.utc)),
            "games": games, "counts": {"quotes": sum(len(g["quotes"]) for g in games)}}


# ------------------------------------------------------------------------------------------------- adapters / tables
def test_adapters_carry_the_real_series_tickers():
    f, b = ADAPTERS["ncaaf"], ADAPTERS["ncaab"]
    assert (f.series_prefix("ml"), f.series_prefix("spread"), f.series_prefix("total")) == \
        ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAFTOTAL")                       # the total drops an A (live /series listing)
    assert (b.series_prefix("ml"), b.series_prefix("spread"), b.series_prefix("total")) == \
        ("KXNCAAMBGAME", "KXNCAAMBSPREAD", "KXNCAAMBTOTAL")
    assert "OHIO" in f.codes and "DUKE" in b.codes and len(f.codes) >= 250 and len(b.codes) >= 290


def test_crosswalk_is_name_joined_and_fails_closed():
    from ev.sports.ncaa_teams import bp_to_kalshi, name_to_kalshi
    assert bp_to_kalshi("ncaaf", "OHI") == "OHIO" and bp_to_kalshi("ncaaf", "MOH") == "MOH"     # Ohio / Miami (OH)
    assert bp_to_kalshi("ncaab", "MRSH") is None          # Marshall: no Kalshi market that week -> unmapped, not guessed
    assert bp_to_kalshi("ncaaf", "VLB") is None           # Virginia-Lynchburg (D3): never in the census
    assert bp_to_kalshi("ncaab", "DUKE") == "DUKE"
    assert name_to_kalshi("ncaab", "Duke") == "DUKE"      # verbatim Kalshi spelling resolves
    assert name_to_kalshi("ncaab", "Duke Blue Devils") is None   # a mascot spelling does not (fail closed)
    assert team_code_fn("ncaaf")("Ohio") == "OHIO" and team_code_fn("ncaaf")("Ohio Bobcats") is None


def test_parse_set_drops_are_minimal_and_fail_closed():
    """The full census is not blob-split-unique (Kalshi concatenates codes with no separator), so the adapters
    parse against the census minus a minimum drop set. Dropped schools stay in the census, the name tables and
    the crosswalks (the banked-code path still prices them); only ticker-blob parsing fails closed on them."""
    from ev.kalshi_map import MapFail, parse_event, split_teams
    from ev.sports.ncaa_teams import (NCAAB_KALSHI_CODES, NCAAB_PARSE_DROPPED_CODES, NCAAF_KALSHI_CODES,
                                      NCAAF_PARSE_DROPPED_CODES, bp_to_kalshi, name_to_kalshi)
    assert NCAAF_PARSE_DROPPED_CODES == frozenset({"ME", "ND"})
    assert NCAAB_PARSE_DROPPED_CODES == frozenset({"AMER", "CSB", "EVAN", "ILL", "MASS", "SF"})
    for sport, census, drops in (("ncaaf", NCAAF_KALSHI_CODES, NCAAF_PARSE_DROPPED_CODES),
                                 ("ncaab", NCAAB_KALSHI_CODES, NCAAB_PARSE_DROPPED_CODES)):
        assert drops < census and ADAPTERS[sport].codes == census - drops
    # census completion: AKR / UNCG were found on open markets by the 2026-11-10 completion census
    assert "AKR" in NCAAB_KALSHI_CODES and name_to_kalshi("ncaab", "Akron") == "AKR"
    assert "UNCG" in NCAAB_KALSHI_CODES and name_to_kalshi("ncaab", "UNC Greensboro") == "UNCG"
    assert parse_event(ADAPTERS["ncaab"], "KXNCAAMBGAME-26NOV14TULNAKR") == ("2026-11-14", "TULN", "AKR")
    assert parse_event(ADAPTERS["ncaab"], "KXNCAAMBGAME-26NOV14UNCGCOR") == ("2026-11-14", "UNCG", "COR")
    # a blob whose only split needs a dropped code is ambiguous, never guessed into the longer code
    r = parse_event(ADAPTERS["ncaaf"], "KXNCAAFGAME-26NOV14BCND")
    assert isinstance(r, MapFail) and split_teams("HCUND", NCAAF_KALSHI_CODES) is None
    # dropped schools still resolve by name and by crosswalk (banked path identity)
    assert name_to_kalshi("ncaaf", "Notre Dame") == "ND" and bp_to_kalshi("ncaaf", "ND") == "ND"
    assert name_to_kalshi("ncaab", "Illinois") == "ILL" and bp_to_kalshi("ncaab", "ILL") == "ILL"


def test_game_sd_has_unvalidated_ncaa_entries():
    assert GAME_SD["ncaaf"]["margin"] > GAME_SD["nfl"]["margin"]        # college margins run wider than the pros'
    assert GAME_SD["ncaab"] == {"margin": 11.0, "total": 13.5}


def test_why_unmapped_distinguishes_ncaa_from_womens_and_team_totals():
    book = InMemoryFairBook()
    assert book.why_unmapped("KXNCAAFGAME-26NOV14OHIOMOH-OHIO") == "no_game_line"     # covered league, no line banked
    assert book.why_unmapped("KXNCAFTOTAL-26NOV14OHIOMOH-48") == "no_game_line"
    assert book.why_unmapped("KXNCAAMBGAME-26NOV10DUKEUNC-DUKE") == "no_game_line"
    assert book.why_unmapped("KXNCAAWBGAME-26NOV10DUKEUNC-DUKE") == "sport_not_covered"   # women's: never priced
    assert book.why_unmapped("KXNCAAFTEAMTOTAL-26NOV14OHIOMOH-24") == "sport_not_covered"


# ------------------------------------------------------------------------------------------------- conversion
def test_events_from_bettingpros_translates_codes_and_drops_unmapped():
    # NCAAF: BP's OHI / MOH join to Kalshi's OHIO / MOH by school name; VLB (Virginia-Lynchburg, D3) has no Kalshi
    # market and no census entry, so its whole game drops instead of guessing.
    games = [_bp_game(), _bp_game(away="VLB", home="OHI", src="bp2")]
    events, stats = kb.events_from_bettingpros("ncaaf", games, NOW_DT)
    assert stats["unmapped_teams"] == 1 and len(events) == 1
    ev = events[0]
    assert (ev["away_team"], ev["home_team"]) == ("OHIO", "MOH")


def test_ncaaf_game_prices_ml_spread_total_with_real_tickers(bp_files):
    bp_files.files = {"ncaaf": _file([_bp_game()])}
    rows, stats = kb.banked_game_rows(["ncaaf"], NOW)
    tickers = {r["ticker"] for r in rows}
    assert stats["ncaaf"]["source"] == "bettingpros" and stats["ncaaf"]["games_priced"] == 1
    assert "KXNCAAFGAME-27JAN16OHIOMOH-MOH" in tickers or any(t.startswith("KXNCAAFGAME-") and t.endswith("-MOH") for t in tickers)
    assert any(t.startswith("KXNCAFTOTAL-") for t in tickers)                       # one-A total series on the rows
    assert any(t.startswith("KXNCAAFSPREAD-") and t.endswith("-MOH4") and not r.get("pooled")
               for t, r in ((r["ticker"], r) for r in rows))
    ml = next(r for r in rows if r["market_class"] == "game_ml" and r["ticker"].endswith("-MOH"))
    assert ml["n_books"] == 3 and 0.55 < ml["p_yes"] < 0.65
    assert all(r["updated_ts"] == pytest.approx(FETCHED) for r in rows)             # the file's stamp, not the read time


def test_unmapped_school_drops_the_whole_game(bp_files):
    bp_files.files = {"ncaaf": _file([_bp_game(), _bp_game(away="VLB", home="OHI", src="bp2")])}
    rows, stats = kb.banked_game_rows(["ncaaf"], NOW)
    assert stats["ncaaf"]["games_in"] == 2 and stats["ncaaf"]["unmapped_teams"] == 1
    assert {r["game_key"].split(":")[2] for r in rows} == {"OHIO@MOH"}


def test_kalshi_and_dfs_rows_inside_the_bp_file_never_enter_the_consensus(bp_files):
    quotes = _quotes() + [{"market": "ml", "line": None, "book": "kalshi", "a": 500, "b": -700},
                          {"market": "ml", "line": None, "book": "prizepicks", "a": 900, "b": -900}]
    bp_files.files = {"ncaaf": _file([_bp_game(quotes=quotes)])}
    rows, _ = kb.banked_game_rows(["ncaaf"], NOW)
    ml = next(r for r in rows if r["market_class"] == "game_ml")
    assert ml["n_books"] == 3                                                       # only the three real books


def test_stale_bettingpros_file_is_unavailable_and_rows_age_out(bp_files):
    bp_files.files = {"ncaaf": _file([_bp_game()], fetched=NOW - 4 * 3600.0)}
    rows, stats = kb.banked_game_rows(["ncaaf"], NOW)
    assert rows == [] and "min old" in stats["ncaaf"]["unavailable"]
    bp_files.files = {"ncaaf": _file([_bp_game()], fetched=NOW - 1200.0)}           # 20 min old: served...
    rows, _ = kb.banked_game_rows(["ncaaf"], NOW)
    book = InMemoryFairBook(clock=lambda: NOW)
    book.load_rows(rows, NOW)
    from ev.kalshi_rfq_pricer import REJ_STALE_CONSENSUS, KalshiRFQPricer
    pricer = KalshiRFQPricer(book)
    t = next(r["ticker"] for r in rows if r["market_class"] == "game_ml")
    assert pricer.leg_gate(book.get(t, NOW), NOW) is None                            # ...inside the 1500 s gate
    assert pricer.leg_gate(book.get(t, NOW + 400.0), NOW + 400.0) == REJ_STALE_CONSENSUS


def test_started_ncaa_game_is_dropped(bp_files):
    bp_files.files = {"ncaab": _file([_bp_game(away="DUKE", home="UNC", start=NOW_DT - timedelta(minutes=5))])}
    rows, stats = kb.banked_game_rows(["ncaab"], NOW)
    assert rows == [] and stats["ncaab"]["started"] == 1


def test_ncaab_mapped_game_prices_with_mb_series(bp_files):
    bp_files.files = {"ncaab": _file([_bp_game(away="DUKE", home="UNC", quotes=_quotes(total=(145.5, -110, -110)))])}
    rows, stats = kb.banked_game_rows(["ncaab"], NOW)
    assert stats["ncaab"]["source"] == "bettingpros"
    assert any(t.startswith("KXNCAAMBGAME-") and t.endswith("-UNC") for t in (r["ticker"] for r in rows))
    assert any(t.startswith("KXNCAAMBTOTAL-") for t in (r["ticker"] for r in rows))


def test_slate_rows_round_trip_for_ncaa_via_verbatim_names():
    ev = [{"home_team": "Miami (OH)", "away_team": "Ohio", "commence_time": _iso(NOW_DT + timedelta(hours=5)),
           "bookmakers": [{"key": "fanduel", "markets": [
               {"key": "h2h", "outcomes": [{"name": "Miami (OH)", "price": -170}, {"name": "Ohio", "price": 150}]}]},
              {"key": "draftkings", "markets": [
               {"key": "h2h", "outcomes": [{"name": "Miami (OH)", "price": -165}, {"name": "Ohio", "price": 145}]}]}]}]
    rows, stats = slate_rows(ev, "ncaaf", NOW)
    assert stats["games_priced"] == 1 and any(r["ticker"].endswith("-MOH") for r in rows)
