"""Unit tests for ev.kalshi_nba_ticker_mapper (RUL-20261125 Half 2 / MAK-01)."""

from __future__ import annotations

from datetime import date

import pytest

from ev.kalshi_nba_ticker_mapper import MappedMarket, parse_market, parse_ticker


REAL_MARKETS = [
    (
        {
            "ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15",
            "title": "Ayo Dosunmu: 15+ points",
            "floor_strike": 14.5,
        },
        MappedMarket(
            ticker="KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15",
            series="KXNBAPTS",
            stat="points",
            event_date=date(2026, 11, 21),
            away="MEM",
            home="MIN",
            team="MIN",
            participant_key="ADOSUNMU13",
            player_name="Ayo Dosunmu",
            player_norm="ayo dosunmu",
            player_id=None,
            threshold_n=15,
            consensus_line=14.5,
        ),
    ),
    (
        {
            "ticker": "KXNBAREB-26NOV21BOSSAC-SACDSABONIS11-10",
            "title": "Domantas Sabonis: 10+ rebounds",
            "floor_strike": 9.5,
        },
        MappedMarket(
            ticker="KXNBAREB-26NOV21BOSSAC-SACDSABONIS11-10",
            series="KXNBAREB",
            stat="rebounds",
            event_date=date(2026, 11, 21),
            away="BOS",
            home="SAC",
            team="SAC",
            participant_key="DSABONIS11",
            player_name="Domantas Sabonis",
            player_norm="domantas sabonis",
            player_id=None,
            threshold_n=10,
            consensus_line=9.5,
        ),
    ),
    (
        {
            "ticker": "KXNBAREB-26NOV21PORNYK-PORDCLINGAN23-12",
            "title": "Donovan Clingan: 12+ rebounds",
            "floor_strike": 11.5,
        },
        MappedMarket(
            ticker="KXNBAREB-26NOV21PORNYK-PORDCLINGAN23-12",
            series="KXNBAREB",
            stat="rebounds",
            event_date=date(2026, 11, 21),
            away="POR",
            home="NYK",
            team="POR",
            participant_key="DCLINGAN23",
            player_name="Donovan Clingan",
            player_norm="donovan clingan",
            player_id=None,
            threshold_n=12,
            consensus_line=11.5,
        ),
    ),
    (
        {
            "ticker": "KXNBA3PT-26NOV21LACDAL-LACDGARLAND10-3",
            "title": "Darius Garland: 3+ threes",
            "floor_strike": 2.5,
        },
        MappedMarket(
            ticker="KXNBA3PT-26NOV21LACDAL-LACDGARLAND10-3",
            series="KXNBA3PT",
            stat="threes",
            event_date=date(2026, 11, 21),
            away="LAC",
            home="DAL",
            team="LAC",
            participant_key="DGARLAND10",
            player_name="Darius Garland",
            player_norm="darius garland",
            player_id=None,
            threshold_n=3,
            consensus_line=2.5,
        ),
    ),
]


@pytest.mark.parametrize("market,expected", REAL_MARKETS)
def test_real_tickers_exact_match(market, expected):
    mapped = parse_market(market)
    assert mapped == expected
    assert mapped.identity_key == expected.participant_key


def test_resolver_injection_and_fallback():
    market = REAL_MARKETS[0][0]
    mapped_resolved = parse_market(market, resolver=lambda name, team: f"{team}:{name}:123")
    assert mapped_resolved.player_id == "MIN:ayo dosunmu:123"
    assert mapped_resolved.identity_key == "MIN:ayo dosunmu:123"

    mapped_unresolved = parse_market(market, resolver=lambda name, team: None)
    assert mapped_unresolved.player_id is None
    assert mapped_unresolved.identity_key == "ADOSUNMU13"


def test_parse_ticker_without_market_dict():
    mapped = parse_ticker("KXNBA3PT-26NOV21LACDAL-LACDGARLAND10-3")
    assert mapped.series == "KXNBA3PT"
    assert mapped.stat == "threes"
    assert mapped.event_date == date(2026, 11, 21)
    assert mapped.away == "LAC"
    assert mapped.home == "DAL"
    assert mapped.team == "LAC"
    assert mapped.participant_key == "DGARLAND10"
    assert mapped.threshold_n == 3
    assert mapped.consensus_line == 2.5
    assert mapped.player_id is None


@pytest.mark.parametrize(
    "bad_ticker",
    [
        "",
        "KXNBAGAME-26NOV21MEMMIN-MIN",
        "KXNBAAST-26NOV21MEMMIN-MINADOSUNMU13-5",
        "KXNBAPTSPROT-26NOV21MEMMIN-MINADOSUNMU13-15",
        "KXNFLPASSYDS-26NOV21KCBUF-KCPMAHOMES15-250",
        "KXNBAPTS-26FEB30MEMMIN-MINADOSUNMU13-15",
        "KXNBAPTS-26NOV21MEMMEM-MINADOSUNMU13-15",
        "KXNBAPTS-26NOV21ZZZMIN-MINADOSUNMU13-15",
        "KXNBAPTS-26NOV21MEMMIN-BOSADOSUNMU13-15",
        "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-0",
        "KXNBAPTS-26NOV21MEMMIN-MINA-15",
        "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-abc",
    ],
)
def test_reject_invalid_or_ineligible_tickers(bad_ticker):
    with pytest.raises(ValueError):
        parse_ticker(bad_ticker)


@pytest.mark.parametrize(
    "bad_market",
    [
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "Ayo Dosunmu: 16+ points", "floor_strike": 14.5},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "Ayo Dosunmu: 15+ rebounds", "floor_strike": 14.5},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "LeBron James: 15+ points", "floor_strike": 14.5},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "Ayo Dosunmu: 15+ points", "floor_strike": 15.5},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "Ayo Dosunmu: 15+ points", "floor_strike": None},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "", "floor_strike": 14.5},
        {"ticker": "KXNBAPTS-26NOV21MEMMIN-MINADOSUNMU13-15", "title": "Ayo Dosunmu: 15+ points"},
    ],
)
def test_reject_market_cross_check_disagreements(bad_market):
    with pytest.raises(ValueError):
        parse_market(bad_market)
