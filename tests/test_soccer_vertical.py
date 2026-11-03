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
