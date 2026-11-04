"""Soccer vertical (Phase 1) contract tests -- 100% offline.

Covers the spec's Part 6 laws: three-way devig (all methods sum to 1, ordering preserved, power/shin push more
of the overround onto the longshot than proportional), Dixon-Coles matrix coherence (unit mass, the 1X2 and
double-chance identities, the BTTS identity, rho < 0 inflating the low-scoring draws), the Reep bridge's
fail-closed identity, zero time-travel leakage in the ratings fit, and the Phase 1 plumbing around them
(ingest parsing, Kalshi three-way legs, the odds board's draw leg, the backtest harness, the Phase 2 gate).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from mlb_core.odds.utils import devig_three_way

# ---------------------------------------------------------------- three-way devig laws

BOOKS = [
    (0.55, 0.28, 0.24),     # a normal 1X2 book (~7% overround)
    (0.40, 0.30, 0.37),
    (0.70, 0.20, 0.17),     # a heavy favorite
    (0.36, 0.35, 0.36),     # near pick'em
    (0.90, 0.08, 0.05),     # extreme: huge favorite, longshot away
]


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
@pytest.mark.parametrize("book", BOOKS)
def test_devig_three_way_sums_to_one(method, book):
    f = devig_three_way(*book, method=method)
    assert abs(sum(f) - 1.0) < 1e-9
    assert all(0.0 < p < 1.0 for p in f)


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
@pytest.mark.parametrize("book", BOOKS)
def test_devig_three_way_preserves_ordering(method, book):
    f = devig_three_way(*book, method=method)
    order_in = sorted(range(3), key=lambda i: book[i])
    order_out = sorted(range(3), key=lambda i: f[i])
    assert order_in == order_out


def test_devig_three_way_power_and_shin_shade_longshot_harder_than_proportional():
    # the favorite-longshot law: vs proportional, power/shin take MORE off the longshot leg and less off the favorite
    book = (0.55, 0.28, 0.24)
    prop = devig_three_way(*book, method="proportional")
    for method in ("power", "shin"):
        f = devig_three_way(*book, method=method)
        assert f[2] < prop[2]           # away longshot: lower fair prob (more overround allocated to it)
        assert f[0] > prop[0]           # home favorite: higher fair prob


def test_devig_three_way_rejects_bad_books():
    with pytest.raises(ValueError):
        devig_three_way(0.30, 0.30, 0.30)                 # sums below 1: not a book
    with pytest.raises(ValueError):
        devig_three_way(0.0, 0.50, 0.55)                  # a zero leg
    with pytest.raises(ValueError):
        devig_three_way(0.50, 0.30, 0.27, method="magic")


def test_devig_three_way_no_vig_is_plain_normalization():
    f = devig_three_way(0.50, 0.25, 0.25, method="power")
    assert f == pytest.approx((0.50, 0.25, 0.25))


# ---------------------------------------------------------------- Dixon-Coles matrix coherence

from soccer.game.matrix_pricer import build_matrix, price_match, price_matrix  # noqa: E402


@pytest.mark.parametrize("lh,la,rho", [(1.45, 1.05, -0.08), (1.90, 0.70, -0.12), (1.10, 1.30, 0.0), (2.60, 2.20, 0.05)])
def test_matrix_sums_to_one_and_1x2_is_complete(lh, la, rho):
    matrix, probs = price_match(lh, la, rho)
    assert abs(sum(sum(r) for r in matrix) - 1.0) < 1e-12
    assert abs(probs["p_home"] + probs["p_draw"] + probs["p_away"] - 1.0) < 1e-12


def test_matrix_double_chance_and_dnb_identities():
    _, probs = price_match(1.45, 1.05, -0.08)
    assert probs["p_1x"] == pytest.approx(probs["p_home"] + probs["p_draw"])
    assert probs["p_x2"] == pytest.approx(probs["p_away"] + probs["p_draw"])
    assert probs["p_12"] == pytest.approx(probs["p_home"] + probs["p_away"])
    assert probs["p_home_dnb"] == pytest.approx(probs["p_home"] / (probs["p_home"] + probs["p_away"]))


def test_matrix_btts_identity():
    matrix, probs = price_match(1.45, 1.05, -0.08)
    col0 = sum(matrix[x][0] for x in range(11))       # away scores 0
    row0 = sum(matrix[0][y] for y in range(11))       # home scores 0
    expect = 1.0 - row0 - col0 + matrix[0][0]
    assert probs["p_btts_yes"] == pytest.approx(expect)
    assert probs["p_btts_no"] == pytest.approx(1.0 - expect)


def test_matrix_totals_whole_goal_push_mass():
    _, probs = price_match(1.45, 1.05, -0.08)
    t2 = probs["totals"]["2"]
    assert t2["p_push"] > 0                            # exactly-2-goals mass exists at a whole-goal line
    assert t2["p_over_raw"] + t2["p_under_raw"] + t2["p_push"] == pytest.approx(1.0)
    assert t2["p_over"] + t2["p_under"] == pytest.approx(1.0)   # no-push pair renormalizes
    assert probs["totals"]["2.5"]["p_push"] == 0.0


def test_matrix_negative_rho_inflates_low_scoring_draws():
    m_neg, _ = price_match(1.35, 1.15, -0.15)
    m_zero, _ = price_match(1.35, 1.15, 0.0)
    assert m_neg[0][0] > m_zero[0][0]
    assert m_neg[1][1] > m_zero[1][1]


# ---------------------------------------------------------------- Reep register: fail-closed identity

from soccer.data.reep_register import resolve_club  # noqa: E402


@pytest.mark.parametrize("spelling,provider", [
    ("Manchester United", "parlayapi"), ("Manchester United", "understat"), ("Man United", "football_data"),
    ("Man United", "clubelo"), ("Manchester United", "kalshi"), ("MUN", None), ("Manchester United", None),
])
def test_reep_aliases_resolve_to_one_code(spelling, provider):
    assert resolve_club(spelling, provider=provider) == "MUN"


@pytest.mark.parametrize("spelling,provider,code", [
    ("Wolves", "football_data", "WOL"), ("Wolverhampton Wanderers", "parlayapi", "WOL"),
    ("Man City", "football_data", "MCI"), ("Tottenham", "understat", "TOT"),
    ("Paris SG", "football_data", "PSG"), ("Ath Madrid", "football_data", "ATM"),
    ("Nott'm Forest", "football_data", "NFO"), ("Brighton", "understat", "BHA"),
])
def test_reep_cross_provider_spellings(spelling, provider, code):
    assert resolve_club(spelling, provider=provider) == code


def test_reep_unknown_club_fails_closed():
    assert resolve_club("FC Nonexistent") is None
    assert resolve_club("Manchester") is None          # a substring is not an identity
    assert resolve_club("") is None
    assert resolve_club(None) is None


# ---------------------------------------------------------------- ratings fit + zero time-travel leakage

from soccer.game import dixon_coles  # noqa: E402

_TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
_STRENGTH = {"AAA": 0.9, "BBB": 0.4, "CCC": 0.1, "DDD": -0.1, "EEE": -0.4, "FFF": -0.9}


def _synthetic_matches(start: date, rounds: int = 3) -> list[dict]:
    """Deterministic pseudo-results: stronger teams score more. No randomness, so fits are reproducible."""
    out = []
    day = start
    for r in range(rounds):
        for i, home in enumerate(_TEAMS):
            for j, away in enumerate(_TEAMS):
                if i == j:
                    continue
                diff = _STRENGTH[home] - _STRENGTH[away]
                hg = max(0, round(1.4 + diff + (0.3 if (i + j + r) % 3 == 0 else 0.0)))
                ag = max(0, round(1.1 - diff + (0.2 if (i * j + r) % 4 == 0 else 0.0)))
                out.append({"date": day, "home": home, "away": away, "home_goals": hg, "away_goals": ag,
                            "xg_home": hg + 0.15, "xg_away": ag + 0.1, "hst": hg + 2, "ast": ag + 2})
                day += timedelta(days=1)
    return out


def test_fit_ratings_recovers_strength_order():
    matches = _synthetic_matches(date(2025, 8, 1))
    ratings = dixon_coles.fit_ratings(matches, date(2026, 1, 1), iterations=1200)
    lam_strong, _ = ratings.expected_goals("AAA", "FFF")
    lam_weak, _ = ratings.expected_goals("FFF", "AAA")
    assert lam_strong > lam_weak
    assert abs(sum(ratings.alpha.values())) < 1e-6     # identifiability: sum alpha == 0
    assert -0.25 <= ratings.rho <= 0.25
    assert ratings.expected_goals("AAA", "ZZZ") is None    # unrated club: fail closed


def test_zero_time_travel_leakage():
    as_of = date(2026, 1, 1)
    base = _synthetic_matches(date(2025, 8, 1))
    future = _synthetic_matches(date(2026, 2, 1))                       # entirely after as_of
    for m in future:                                                    # poison them: absurd scores + xG
        m.update(home_goals=9, away_goals=9, xg_home=9.9, xg_away=9.9)
    on_the_day = [{"date": as_of, "home": "AAA", "away": "FFF", "home_goals": 8, "away_goals": 0,
                   "xg_home": 8.5, "xg_away": 0.1, "hst": 12, "ast": 1}]
    r1 = dixon_coles.fit_ratings(base, as_of, iterations=800)
    r2 = dixon_coles.fit_ratings(base + future + on_the_day, as_of, iterations=800)
    assert r1.alpha == r2.alpha and r1.beta == r2.beta
    assert r1.mu0 == r2.mu0 and r1.gamma == r2.gamma and r1.rho == r2.rho
    assert r1.expected_goals("AAA", "FFF") == r2.expected_goals("AAA", "FFF")


def test_elo_sanity_guard():
    model = (0.60, 0.22, 0.18)
    elo = (0.35, 0.28, 0.37)
    market = (0.38, 0.27, 0.35)
    guarded, warn = dixon_coles.elo_sanity(model, elo, market)
    assert warn == "ELO_DIVERGENCE"
    assert guarded[0] == pytest.approx(0.5 * 0.60 + 0.5 * 0.35)
    assert abs(sum(guarded) - 1.0) < 1e-12
    # only one reference disagrees -> no trip; agreeing model -> no trip
    assert dixon_coles.elo_sanity(model, elo, None) == (model, None)
    assert dixon_coles.elo_sanity(market, elo, market) == (market, None)


# ---------------------------------------------------------------- ingest (offline fixtures)

from soccer.data import ingest  # noqa: E402

_FD_CSV = """Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HS,AS,HST,AST,PSH,PSD,PSA,PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,P>2.5,P<2.5,PC>2.5,PC<2.5
E0,16/08/2025,Arsenal,Man United,2,0,14,9,6,3,1.80,3.60,4.50,1.75,3.70,4.80,1.85,3.80,5.00,1.78,3.65,4.60,2.10,1.75,2.05,1.80
E0,17/08/2025,Liverpool,Chelsea,1,1,11,12,4,5,2.00,3.40,3.10,1.95,3.50,3.30,2.05,3.60,3.45,1.97,3.45,3.20,1.95,1.90,1.90,1.95
"""


def test_ingest_football_data_csv_parsing():
    matches = ingest.fetch_football_data_csv("epl", 2025, fetch=lambda url: _FD_CSV)
    assert len(matches) == 2
    m = matches[0]
    assert (m["home"], m["away"]) == ("ARS", "MUN")
    assert (m["home_goals"], m["away_goals"]) == (2, 0)
    assert m["odds"]["psch"] == 1.75 and m["odds"]["pc_over25"] == 2.05 and m["odds"]["maxca"] == 5.00
    assert ingest.season_code(2021) == "2122" and ingest.season_code("2425") == "2425"


_UNDERSTAT_HTML = r"""<html><script>
var matchesData = JSON.parse('[{"id":"1","isResult":true,"h":{"id":"1","title":"Arsenal"},"a":{"id":"2","title":"Chelsea"},"goals":{"h":"2","a":"1"},"xG":{"h":"1.85","a":"0.92"},"datetime":"2025-08-16 17:30:00"},{"id":"2","isResult":false,"h":{"id":"3","title":"Liverpool"},"a":{"id":"4","title":"Everton"},"goals":{"h":null,"a":null},"xG":{"h":null,"a":null},"datetime":"2025-08-23 15:00:00"}]');
</script></html>"""


def test_ingest_understat_matches_parsing():
    matches = ingest.fetch_understat_matches("epl", 2025, fetch=lambda url: _UNDERSTAT_HTML)
    assert len(matches) == 2
    played, fixture = matches
    assert (played["home"], played["away"], played["xg_home"]) == ("ARS", "CHE", 1.85)
    assert played["is_result"] is True
    assert fixture["is_result"] is False and fixture["xg_home"] is None and fixture["home"] == "LIV"


def test_ingest_elo_probs():
    p = ingest.elo_probs(1600.0, 1600.0)
    assert abs(sum(p) - 1.0) < 1e-12 and p[0] > p[2]     # home edge from HFA alone
    strong = ingest.elo_probs(1900.0, 1500.0)
    assert strong[0] > p[0] and strong[1] < p[1]          # bigger gap: more home wins, fewer draws


# ---------------------------------------------------------------- Kalshi three-way legs + odds board

from ev.kalshi_game_legs import _p_three_way, soccer_slate_rows  # noqa: E402


def _book(h, d, a):
    return {"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Arsenal", "price": h}, {"name": "Draw", "price": d}, {"name": "Chelsea", "price": a}]}]}


def test_p_three_way_devigs_all_three_legs():
    t = _p_three_way(_book(-140, 260, 300), "Arsenal", "Chelsea")
    assert t is not None and abs(sum(t) - 1.0) < 1e-9
    assert t[0] > t[1] > t[2]
    two_outcome = {"key": "x", "markets": [{"key": "h2h", "outcomes": [
        {"name": "Arsenal", "price": -140}, {"name": "Chelsea", "price": 120}]}]}
    assert _p_three_way(two_outcome, "Arsenal", "Chelsea") is None


def test_soccer_slate_rows_emit_tie_contract():
    ev = {"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2026-11-07T17:30:00Z",
          "bookmakers": [_book(-140, 260, 300),
                         {**_book(-135, 250, 290), "key": "fanduel"}]}
    rows, stats = soccer_slate_rows([ev], "epl", now=1762000000.0)
    tickers = {r["ticker"] for r in rows}
    assert any(t.endswith("-ARS") for t in tickers)
    assert any(t.endswith("-TIE") for t in tickers)          # the draw is its own Kalshi contract
    assert any(t.endswith("-CHE") for t in tickers)
    tie = next(r for r in rows if r["ticker"].endswith("-TIE"))
    assert 0.0 < tie["p_yes"] < 1.0 and tie["n_books"] == 2
    assert stats["tie_rows"] == 1 and stats["games_priced"] == 1


def test_soccer_slate_rows_drop_unresolved_club():
    ev = {"home_team": "Arsenal", "away_team": "FC Nowhere", "commence_time": "2026-11-07T17:30:00Z",
          "bookmakers": [_book(-140, 260, 300)]}
    rows, stats = soccer_slate_rows([ev], "epl", now=1762000000.0)
    assert rows == [] and stats["dropped_unknown_team"] == 1


def test_game_odds_board_soccer_three_way_rows():
    from ev.game_odds_board import build_game_board
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    ev = {"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2026-11-07T17:30:00Z",
          "bookmakers": [
              {"key": "draftkings", "markets": [
                  {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": -140}, {"name": "Draw", "price": 260},
                                              {"name": "Chelsea", "price": 300}]},
                  {"key": "btts", "outcomes": [{"name": "Yes", "price": -130}, {"name": "No", "price": 110}]}]},
              {"key": "fanduel", "markets": [
                  {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": -135}, {"name": "Draw", "price": 250},
                                              {"name": "Chelsea", "price": 290}]},
                  {"key": "btts", "outcomes": [{"name": "Yes", "price": -125}, {"name": "No", "price": 105}]}]},
          ]}
    board = build_game_board("soccer", [ev], now)
    assert board["validated"] is False
    ml = next(r for r in board["rows"] if r["market"] == "ml")
    dk = next(b for b in ml["books"] if b["book"] == "draftkings")
    assert dk["draw"] == 260 and dk["a"] == 300 and dk["b"] == -140     # a = away, b = home, draw carried
    assert any(r["market"] == "btts" for r in board["rows"])


# ---------------------------------------------------------------- run_daily payload + backtest harness + Phase 2 gate

from soccer.game import run_daily  # noqa: E402


def test_build_predictions_stamps_unvalidated():
    matches = _synthetic_matches(date(2025, 8, 1))
    # rename synthetic codes onto real clubs so the register-priced payload path is exercised end to end
    real = {"AAA": "ARS", "BBB": "CHE", "CCC": "LIV", "DDD": "MCI", "EEE": "TOT", "FFF": "NEW"}
    matches = [{**m, "home": real[m["home"]], "away": real[m["away"]]} for m in matches]
    ratings = dixon_coles.fit_ratings(matches, date(2026, 1, 1), iterations=800)
    fixtures = [{"league": "epl", "date": date(2026, 1, 3), "datetime": datetime(2026, 1, 3, 15, 0),
                 "home": "ARS", "away": "CHE"}]
    payload = run_daily.build_predictions(fixtures, {"epl": ratings}, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert payload["validated"] is False and payload["xg_variant"] == "understat_shot_v1"
    g = payload["priced_games"][0]
    assert g["validated"] is False and g["game_key"] == "soccer:2026-01-03:CHE@ARS"
    assert abs(g["p_home"] + g["p_draw"] + g["p_away"] - 1.0) < 1e-9
    assert len(g["matrix"]) == 11 and abs(sum(sum(r) for r in g["matrix"]) - 1.0) < 1e-3


def test_backtest_walk_forward_runs_on_synthetic_csv(tmp_path):
    from soccer.game import backtest
    teams = ["Arsenal", "Chelsea", "Liverpool", "Man City", "Tottenham", "Everton", "Fulham", "Brentford"]
    lines = ["Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,HS,AS,HST,AST,PSH,PSD,PSA,PSCH,PSCD,PSCA,"
             "MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,P>2.5,P<2.5,PC>2.5,PC<2.5"]
    day = date(2021, 8, 14)
    k = 0
    for rnd in range(9):
        for i in range(0, 8, 2):
            h, a = teams[(i + rnd) % 8], teams[(i + 1 + rnd) % 8]
            hg, ag = (k + rnd) % 3, (k + 1) % 2
            lines.append(f"E0,{day:%d/%m/%Y},{h},{a},{hg},{ag},10,9,4,3,2.10,3.30,2.90,2.05,3.35,3.00,"
                         f"2.20,3.45,3.10,2.08,3.32,2.95,2.00,1.85,1.95,1.90")
            k += 1
        day += timedelta(days=7)
    (tmp_path / "epl_2122.csv").write_text("\n".join(lines), encoding="utf-8")
    report = backtest.run_backtest(["epl"], [2021], data_dir=str(tmp_path), min_train=20)
    s = report["leagues"]["epl"]
    assert report["validated"] is False
    assert s["matches_scored"] > 0
    assert s["brier_1x2_model"] is not None and s["brier_1x2_pinnacle_close"] is not None
    assert s["brier_totals25_model"] is not None


def test_phase2_props_are_gated_off():
    from soccer import props
    assert props.PHASE2_ENABLED is False
    with pytest.raises(RuntimeError):
        props.build_prop_projections([])
    assert props.PROP_MARKET_MAP["player_goals"] == "anytime_goal"
    assert "player_shots_on_goal" not in props.PROP_MARKET_MAP     # ambiguous book definition: not guessed


def test_soccer_adapter_registered_fail_closed_spread():
    from ev.sports import ADAPTERS
    adapter = ADAPTERS["soccer"]
    assert "ARS" in adapter.codes and "RMA" in adapter.codes
    assert "spread" in adapter.disabled


# ---------------------------------------------------------------- pre-merge fixes: UCL pool, sanity guard, board

import json  # noqa: E402

from soccer.config import DOMESTIC_UNDERSTAT_LEAGUES, SOCCER_LEAGUES  # noqa: E402


def test_ucl_has_no_understat_page_and_domestic_pool_is_the_big5():
    # Understat publishes domestic leagues only; /league/UCL/{year} is a 404, so the config must not claim one.
    assert SOCCER_LEAGUES["ucl"]["understat_league"] is None
    assert DOMESTIC_UNDERSTAT_LEAGUES == ("epl", "laliga", "bundesliga", "seriea", "ligue1")
    assert all(SOCCER_LEAGUES[l]["understat_league"] for l in DOMESTIC_UNDERSTAT_LEAGUES)
    with pytest.raises(ValueError):
        ingest.fetch_understat_matches("ucl", 2026, fetch=lambda url: pytest.fail("must not fetch a UCL page"))


def test_hfa_scale_damps_home_lambda():
    base = dixon_coles.Ratings(mu0=0.3, gamma=0.30, rho=0.0, alpha={"AAA": 0.2, "BBB": 0.0},
                               beta={"AAA": 0.0, "BBB": 0.1}, as_of=date(2026, 1, 1), n_matches=10,
                               teams=("AAA", "BBB"))
    european = dixon_coles.Ratings(mu0=0.3, gamma=0.30, rho=0.0, alpha=dict(base.alpha), beta=dict(base.beta),
                                   as_of=date(2026, 1, 1), n_matches=10, teams=("AAA", "BBB"), hfa_scale=0.85)
    assert european.expected_goals("AAA", "BBB")[0] < base.expected_goals("AAA", "BBB")[0]
    assert european.expected_goals("AAA", "BBB")[1] == base.expected_goals("AAA", "BBB")[1]   # away lambda unscaled


def test_ratings_with_elo_fallback_covers_unseen_club_but_not_unrated_one():
    matches = _synthetic_matches(date(2025, 8, 1))
    ratings = dixon_coles.fit_ratings(matches, date(2026, 1, 1), iterations=800)
    elo = {"PSV": 1750.0, "AAA": 1900.0, "BBB": 1700.0}          # ZZZ has no ClubElo entry anywhere
    extended = dixon_coles.ratings_with_elo_fallback(ratings, ("PSV", "ZZZ"), elo)
    assert extended.expected_goals("PSV", "AAA") is not None     # Elo fallback: priced, not dropped
    assert extended.expected_goals("ZZZ", "AAA") is None         # no Elo either: still fail closed
    assert ratings.expected_goals("PSV", "AAA") is None          # the input ratings are not mutated
    # a stronger Elo fallback club prices stronger than a weaker one (same anchors, only PSV's Elo moves:
    # priors are relative to the pool mean, so the comparison must hold the rest of the dict fixed)
    weaker = dixon_coles.ratings_with_elo_fallback(ratings, ("PSV",), {**elo, "PSV": 1500.0})
    assert extended.expected_goals("PSV", "FFF")[0] > weaker.expected_goals("PSV", "FFF")[0]


def test_elo_only_ratings_from_priors():
    ratings = dixon_coles.elo_only_ratings(("AAA", "BBB"), {"AAA": 1900.0, "BBB": 1600.0}, date(2026, 1, 1))
    assert ratings.n_matches == 0
    assert ratings.expected_goals("AAA", "BBB")[0] > ratings.expected_goals("BBB", "AAA")[0]
    with pytest.raises(ValueError):
        dixon_coles.elo_only_ratings(("AAA",), {}, date(2026, 1, 1))


def _us_page(matches: list[dict]) -> str:
    return "<html><script>var matchesData = JSON.parse('" + json.dumps(matches) + "');</script></html>"


def _us_match(home: str, away: str, dt: str, hg=None, ag=None, xh=None, xa=None) -> dict:
    played = hg is not None
    return {"id": "1", "isResult": played, "h": {"id": "1", "title": home}, "a": {"id": "2", "title": away},
            "goals": {"h": hg, "a": ag}, "xG": {"h": xh, "a": xa}, "datetime": dt}


_CLUBELO_CSV = """Rank,Club,Country,Level,Elo,From,To
1,Man City,ENG,1,1950.0,2026-01-01,2026-12-31
2,Real Madrid,ESP,1,2000.0,2026-01-01,2026-12-31
3,Barcelona,ESP,1,1980.0,2026-01-01,2026-12-31
4,Liverpool,ENG,1,1920.0,2026-01-01,2026-12-31
5,Arsenal,ENG,1,1900.0,2026-01-01,2026-12-31
6,Bayern,GER,1,1960.0,2026-01-01,2026-12-31
7,Chelsea,ENG,1,1850.0,2026-01-01,2026-12-31
8,Tottenham,ENG,1,1800.0,2026-01-01,2026-12-31
9,Newcastle,ENG,1,1780.0,2026-01-01,2026-12-31
10,Dortmund,GER,1,1820.0,2026-01-01,2026-12-31
11,Atletico,ESP,1,1880.0,2026-01-01,2026-12-31
12,PSV,NED,1,1720.0,2026-01-01,2026-12-31
13,Celtic,SCO,1,1650.0,2026-01-01,2026-12-31
"""


def _run_daily_fetch(url: str) -> str:
    """Offline fetch for the full scorer run: ClubElo + Understat domestic pages (EPL carries a future fixture;
    La Liga / Bundesliga carry played matches for the UCL pool; every other page is an empty season)."""
    if "api.clubelo.com" in url:
        return _CLUBELO_CSV
    if "/EPL/2026" in url:
        return _us_page([
            _us_match("Arsenal", "Chelsea", "2026-08-15 17:30:00", 2, 1, 1.9, 0.9),
            _us_match("Liverpool", "Manchester City", "2026-08-16 16:30:00", 1, 1, 1.4, 1.5),
            _us_match("Tottenham", "Newcastle United", "2026-08-22 15:00:00", 0, 2, 0.8, 1.7),
            _us_match("Chelsea", "Liverpool", "2026-09-12 17:30:00", 1, 2, 1.1, 1.8),
            _us_match("Manchester City", "Arsenal", "2026-09-19 17:30:00", 2, 2, 2.0, 1.9),
            _us_match("Newcastle United", "Tottenham", "2026-10-03 15:00:00", 1, 0, 1.3, 0.7),
            _us_match("Arsenal", "Chelsea", "2026-11-08 16:30:00"),          # the EPL fixture to price
        ])
    if "/La_liga/2026" in url:
        return _us_page([
            _us_match("Real Madrid", "Barcelona", "2026-08-23 20:00:00", 2, 1, 2.1, 1.2),
            _us_match("Atletico Madrid", "Real Madrid", "2026-09-26 20:00:00", 1, 1, 1.0, 1.4),
            _us_match("Barcelona", "Atletico Madrid", "2026-10-17 20:00:00", 3, 1, 2.4, 0.9),
        ])
    if "/Bundesliga/2026" in url:
        return _us_page([
            _us_match("Bayern Munich", "Borussia Dortmund", "2026-08-28 19:30:00", 3, 1, 2.6, 1.0),
            _us_match("Borussia Dortmund", "Bayern Munich", "2026-10-24 17:30:00", 1, 2, 1.2, 1.9),
        ])
    return _us_page([])


def test_run_soccer_predictions_prices_epl_and_ucl(monkeypatch):
    """The blocker regression: UCL has no Understat page, so its ratings must come from the pooled domestic fit
    + ClubElo fallback and its fixtures from the banked odds slate -- and both competitions must price."""
    from ev import game_odds_board
    from nba.game import odds_feed

    board = {"status": "live", "rows": [
        {"key": "soccer:2026-11-08:BAY@RMA|ml|ml", "sport": "soccer", "game_key": "soccer:2026-11-08:BAY@RMA",
         "market": "ml", "line": None, "books": [
             {"book": "draftkings", "a": 150, "b": 130, "draw": 240},
             {"book": "fanduel", "a": 145, "b": 135, "draw": 235}]},
    ], "games": [], "books": ["draftkings", "fanduel"]}
    monkeypatch.setattr(game_odds_board, "run", lambda sport, now=None, write=True: board)

    def fake_banked(key, now=None, **kw):
        if key == "soccer_uefa_champs_league":
            return ([
                {"commence_time": "2026-11-08T20:00:00Z", "home_team": "Real Madrid", "away_team": "Bayern Munich",
                 "bookmakers": []},
                {"commence_time": "2026-11-08T20:00:00Z", "home_team": "Celtic", "away_team": "PSV Eindhoven",
                 "bookmakers": []},
            ], {"ok": True})
        return None, {"ok": False, "reason": "no snapshot in test"}

    monkeypatch.setattr(odds_feed, "read_banked_game_lines", fake_banked)
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    payload = run_daily.run_soccer_predictions(now=now, write=False, fetch=_run_daily_fetch)
    by_league: dict[str, list[dict]] = {}
    for g in payload["priced_games"]:
        by_league.setdefault(g["league"], []).append(g)
    assert {g["home"] for g in by_league["epl"]} == {"ARS"}                       # Understat fixture, priced
    ucl_pairs = {(g["away"], g["home"]) for g in by_league["ucl"]}
    assert ("BAY", "RMA") in ucl_pairs                                           # pooled-fit clubs, priced
    assert ("PSV", "CEL") in ucl_pairs                                           # NEITHER club has a domestic
    # Understat row in this run: both were rated by the ClubElo fallback alone, and the match still priced.
    assert all(g["validated"] is False for g in payload["priced_games"])
    assert payload["count"] == len(payload["priced_games"]) >= 3


def test_elo_sanity_trips_on_away_and_draw_divergence():
    # home agrees everywhere; the AWAY leg is blown up vs both references -> must trip (the old guard missed it)
    model = (0.40, 0.25, 0.35)
    guarded, warn = dixon_coles.elo_sanity(model, (0.40, 0.27, 0.10), (0.42, 0.26, 0.12))
    assert warn == "ELO_DIVERGENCE" and abs(sum(guarded) - 1.0) < 1e-12
    # the DRAW leg blown up vs both references -> trips too
    _, warn = dixon_coles.elo_sanity((0.45, 0.45, 0.10), (0.50, 0.24, 0.26), (0.48, 0.25, 0.27))
    assert warn == "ELO_DIVERGENCE"
    # only ONE reference diverges on the away leg -> no trip (a model may disagree with a single source)
    assert dixon_coles.elo_sanity(model, (0.40, 0.27, 0.10), (0.41, 0.25, 0.34)) == (model, None)


def test_build_predictions_guard_trip_updates_draw_no_bet():
    from soccer.game.matrix_pricer import price_match
    matches = _synthetic_matches(date(2025, 8, 1))
    real = {"AAA": "ARS", "BBB": "CHE", "CCC": "LIV", "DDD": "MCI", "EEE": "TOT", "FFF": "NEW"}
    matches = [{**m, "home": real[m["home"]], "away": real[m["away"]]} for m in matches]
    ratings = dixon_coles.fit_ratings(matches, date(2026, 1, 1), iterations=800)
    fixtures = [{"league": "epl", "date": date(2026, 1, 3), "datetime": datetime(2026, 1, 3, 15, 0),
                 "home": "ARS", "away": "CHE"}]
    gk = run_daily.game_key(date(2026, 1, 3), "CHE", "ARS")
    # Elo and the market both say ARS is weak here; the fitted model rates ARS strongest -> the guard trips.
    payload = run_daily.build_predictions(fixtures, {"epl": ratings}, elo={"ARS": 1500.0, "CHE": 2050.0},
                                          market_probs={gk: (0.20, 0.30, 0.50)},
                                          now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    g = payload["priced_games"][0]
    assert g["sanity_warn"] == "ELO_DIVERGENCE"
    # DNB must be re-derived from the GUARDED triple, not left at the raw matrix values
    lam = ratings.expected_goals("ARS", "CHE")
    _, raw = price_match(lam[0], lam[1], ratings.rho)
    assert g["p_home_dnb"] == pytest.approx(g["p_home"] / (g["p_home"] + g["p_away"]))
    assert g["p_home_dnb"] != pytest.approx(raw["p_home_dnb"])


def test_game_odds_board_two_leg_quote_never_overwrites_three_way():
    from ev.game_odds_board import build_game_board
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    ev = {"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2026-11-07T17:30:00Z",
          "bookmakers": [
              {"key": "draftkings", "markets": [
                  {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": -140}, {"name": "Draw", "price": 260},
                                              {"name": "Chelsea", "price": 300}]}]},
              {"key": "fanduel", "markets": [
                  {"key": "h2h", "outcomes": [{"name": "Arsenal", "price": -135}, {"name": "Draw", "price": 250},
                                              {"name": "Chelsea", "price": 290}]}]},
          ]}
    # the BestBook crawl is FRESHER but carries no draw leg: under the old two-leg completeness count it would
    # replace the whole quote and erase the draw price.
    bb = [{"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2026-11-07T17:30:00Z",
           "quotes": [{"book": "draftkings", "market": "ml", "a": 310, "b": -150, "line": None}]}]
    board = build_game_board("soccer", [ev], now, meta={"snapshot_ts": "2026-11-03T10:00:00+00:00"},
                             bb_games=bb, bb_meta={"fetched_at": "2026-11-03T11:00:00+00:00"})
    ml = next(r for r in board["rows"] if r["market"] == "ml")
    dk = next(b for b in ml["books"] if b["book"] == "draftkings")
    assert dk["draw"] == 260 and dk["a"] == 300 and dk["b"] == -140     # the complete 3-way quote survived


def test_drop_flipped_books_skips_soccer_ml_only():
    from ev.game_odds_board import _drop_flipped_books
    flipped = {"book": "flip", "a": 130, "b": -150, "d": 260}
    normal = {"book": "norm", "a": -150, "b": 130, "d": 250}
    soccer_row = {"key": "g|ml|ml", "market": "ml",
                  "books": {"flip": dict(flipped), "n1": dict(normal), "n2": dict(normal), "n3": dict(normal)}}
    assert _drop_flipped_books("soccer", soccer_row) == 0
    assert soccer_row["books"]["flip"]["a"] == 130                       # plus-money both sides is normal in 1X2
    nba_row = {"key": "g|ml|ml", "market": "ml",
               "books": {"flip": {"book": "flip", "a": 130, "b": -150},
                         "n1": {"book": "n1", "a": -150, "b": 130},
                         "n2": {"book": "n2", "a": -150, "b": 130},
                         "n3": {"book": "n3", "a": -150, "b": 130}}}
    assert _drop_flipped_books("nba", nba_row) == 1                      # the two-way guard itself still works
    assert nba_row["books"]["flip"]["a"] is None


def test_daily_projections_soccer_hook_is_wired_and_fail_safe(monkeypatch):
    import inspect

    from ev import daily_projections as dp
    from soccer.game import run_daily as srd

    real_hook = inspect.unwrap(dp._run_soccer_game_predictions)
    calls: list = []
    monkeypatch.setattr(srd, "run_soccer_predictions",
                        lambda now=None, write=False: calls.append((now, write)))
    now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
    assert real_hook(now) is True and calls == [(now, True)]

    def boom(now=None, write=False):
        raise RuntimeError("understat down")

    monkeypatch.setattr(srd, "run_soccer_predictions", boom)
    assert real_hook(now) is False                                       # a soccer failure never propagates


def test_soccer_game_lines_banking_is_configured(monkeypatch):
    # the guard refuses any sport with no budget: both soccer keys need one, sized in nba/config.py like the rest
    for env in ("ODDS_BANK_BUDGET_SOCCER_EPL", "ODDS_BANK_BUDGET_SOCCER_UEFA_CHAMPS_LEAGUE"):
        monkeypatch.delenv(env, raising=False)
    from nba.odds.credit_ledger import monthly_budget
    assert monthly_budget("soccer_epl") > 0
    assert monthly_budget("soccer_uefa_champs_league") > 0
    from nba.config import parlay_budget_total, PARLAY_MONTHLY_QUOTA, PARLAY_RESERVE_FLOOR
    assert parlay_budget_total() + PARLAY_RESERVE_FLOOR <= PARLAY_MONTHLY_QUOTA   # the pool still fits the plan
    script = open("deploy/setup_odds_banking.sh", encoding="utf-8").read()
    assert '"epl|soccer_epl|game_lines|' in script
    assert '"ucl|soccer_uefa_champs_league|game_lines|' in script
    assert "ODDS_BANK_BUDGET_SOCCER_UEFA_CHAMPS_LEAGUE=${BUDGET_SOCCER_UCL}" in script


def test_reep_register_resolves_parlayapi_live_spellings_and_skips_corners_events():
    """ParlayAPI's live club names carry 'FC' / 'AFC' / 'SK' (probed 2026-11-04) and it lists 'X (Corners)' pseudo-events; before the
    aliases 53 of 54 events were unmapped and the soccer board stayed empty."""
    from soccer.data.reep_register import resolve_club
    live = {"Arsenal FC": "ARS", "AFC Bournemouth": "BOU", "Brighton & Hove Albion FC": "BHA", "Manchester City FC": "MCI",
            "BV Borussia Dortmund": "DOR", "SSC Napoli": "NAP", "Real Madrid CF": "RMA", "Hull City AFC": "HUL", "Como 1907": "COM"}
    for name, code in live.items():
        assert resolve_club(name, provider="parlayapi") == code, name
    assert resolve_club("Arsenal (Corners)", provider="parlayapi") is None
    assert resolve_club("Totally Unknown FC", provider="parlayapi") is None      # still fail closed


def test_understat_maintenance_page_is_detected_not_cached_and_falls_back_to_the_last_good_copy(monkeypatch):
    """2026-11-04: Understat served a 'We will come back soon' page for every league page; the loader cached it over the last good copy
    and the scorer silently reported 'awaiting_fixtures'."""
    import pytest
    from soccer.data import ingest
    maintenance = "<html><body>We will come back soon</body></html>"
    puts: dict = {}
    monkeypatch.setattr(ingest, "_cache_put", lambda key, text: puts.__setitem__(key, text))
    monkeypatch.setattr(ingest, "_cache_get", lambda key: None)
    with pytest.raises(ingest.UnderstatUnavailable, match="come back soon"):
        ingest.fetch_understat_matches("epl", 2025, fetch=lambda url: maintenance)
    assert puts == {}                                                       # the junk page is never cached
    monkeypatch.setattr(ingest, "_cache_get", lambda key: _UNDERSTAT_HTML)  # a good cached copy exists -> served, with a warning
    assert ingest.fetch_understat_matches("epl", 2025, fetch=lambda url: maintenance)
    ingest.fetch_understat_matches("epl", 2025, fetch=lambda url: _UNDERSTAT_HTML)
    assert list(puts) == ["Soccer/understat/epl_2025.html"]                 # a valid page IS cached


def test_run_soccer_predictions_says_awaiting_data_when_understat_is_down(monkeypatch):
    from datetime import datetime, timezone
    from soccer.data import ingest
    from soccer.game import run_daily
    monkeypatch.setattr(ingest, "_cache_get", lambda key: None)                 # no last-good copy: Understat is the only source
    monkeypatch.setattr(run_daily, "_refresh_board", lambda now, write: None)
    monkeypatch.setattr(run_daily, "_banked_fixtures", lambda now: [])
    p = run_daily.run_soccer_predictions(now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc), write=False,
                                         fetch=lambda url: "<html>We will come back soon</html>")
    assert p["status"] == "awaiting_data" and "Understat is not serving match data" in p["reason"] and p["count"] == 0


def test_goals_fallback_prices_with_football_data_and_labels_the_variant(monkeypatch):
    """Understat down -> the same Dixon-Coles fit on football-data rows (shots-on-target proxy), labeled as a different variant."""
    from datetime import datetime, timezone
    from soccer.data import ingest
    from soccer.game import run_daily
    monkeypatch.setattr(ingest, "_cache_get", lambda key: None)
    monkeypatch.setattr(ingest, "_cache_put", lambda key, text: None)
    monkeypatch.setattr(run_daily, "_refresh_board", lambda now, write: None)

    def fetch(url):
        return _FD_CSV if "football-data" in url else "<html>We will come back soon</html>"
    fixtures = [{"league": "epl", "date": datetime(2026, 11, 8).date(), "home": "ARS", "away": "CHE",
                 "datetime": datetime(2026, 11, 8, 15, 0)}]
    monkeypatch.setattr(run_daily, "_banked_fixtures", lambda now: fixtures)
    p = run_daily.run_soccer_predictions(now=datetime(2026, 11, 4, 12, tzinfo=timezone.utc), write=False, fetch=fetch)
    assert p["fallback"]["source"] == "football-data.co.uk" and "epl" in p["fallback"]["leagues"]
    assert p["engine"] == "dixon_coles_fd_v1" and "no xG source" in p["label"]
    assert p["xg_variant"] in ("football_data_sot_proxy_v1", "understat_shot_v1+football_data_sot_proxy_v1")
    assert p["status"] in ("live", "awaiting_fixtures", "awaiting_data")        # priced when the tiny fixture CSV rates both clubs
    for row in p["priced_games"]:
        assert row["xg_variant"] == p["xg_variant"] and row["validated"] is False
