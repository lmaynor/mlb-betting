"""
Unit tests for ev.sgp_book_pricer.
"""

from __future__ import annotations

import math
import tempfile
import numpy as np
import pytest

from ev.sgp_book_pricer import (
    MIN_QUOTES_PER_GROUP,
    SGP_ARCHETYPES,
    build_sgp_deeplink,
    copula_joint,
    fit_book_sgp_params,
    link_allowed_for_book,
    load_book_sgp_params,
    predict_book_sgp_odds,
)


def test_copula_bivariate_properties():
    p1, p2 = 0.25, 0.30

    # Independence (rho = 0)
    p_indep = copula_joint([p1, p2], rho_or_corr=0.0)
    assert math.isclose(p_indep, p1 * p2, rel_tol=1e-7, abs_tol=1e-7)

    # Positive correlation (rho > 0)
    rho = 0.22
    p_pos = copula_joint([p1, p2], rho_or_corr=rho)
    assert p_pos > (p1 * p2)
    assert p_pos <= min(p1, p2)

    # Negative correlation (rho < 0)
    rho_neg = -0.15
    p_neg = copula_joint([p1, p2], rho_or_corr=rho_neg)
    assert p_neg < (p1 * p2)
    assert p_neg >= max(0.0, p1 + p2 - 1.0)


def test_copula_trivariate_properties():
    p1, p2, p3 = 0.20, 0.30, 0.40
    p_prod = p1 * p2 * p3

    # Independence (rho = 0)
    p_indep = copula_joint([p1, p2, p3], rho_or_corr=0.0)
    assert math.isclose(p_indep, p_prod, rel_tol=1e-7, abs_tol=1e-7)

    # Positive correlation (scalar rho > 0)
    rho = 0.18
    p_pos = copula_joint([p1, p2, p3], rho_or_corr=rho)
    assert p_pos > p_prod
    assert p_pos <= min(p1, p2, p3)

    # Correlation matrix input
    R = np.array([
        [1.0, 0.20, 0.15],
        [0.20, 1.0, 0.18],
        [0.15, 0.18, 1.0],
    ])
    p_mat = copula_joint([p1, p2, p3], rho_or_corr=R)
    assert p_mat > p_prod
    assert p_mat <= min(p1, p2, p3)


def _synth_quotes(book, arch, true_rho, true_hold, pairs, **extra):
    quotes = []
    for l1, l2 in pairs:
        q1 = 100.0 / (l1 + 100.0)
        q2 = 100.0 / (l2 + 100.0)
        cj = copula_joint([q1, q2], true_rho)
        p_sgp = min(0.99, (1.0 + true_hold) * cj)
        dec_sgp = 1.0 / p_sgp
        quotes.append({
            "book": book, "archetype": arch, "leg1_odds": l1, "leg2_odds": l2,
            "sgp_odds": round((dec_sgp - 1.0) * 100.0), "sgp_dec": dec_sgp, **extra,
        })
    return quotes


RAW_PAIRS = [
    (+260, +300), (+280, +320), (+300, +350), (+320, +400), (+350, +420),
    (+240, +280), (+270, +310), (+290, +330), (+310, +360), (+330, +390),
    (+250, +290), (+340, +410), (+360, +430), (+380, +450), (+400, +480),
]


def test_fit_book_sgp_params_recovery():
    """Known (rho_book = 0.10, hold_book = 0.14) is recovered within 0.01 and predicts held-out odds within 1%."""
    true_rho, true_hold = 0.10, 0.14
    book, arch = "fanduel", "nba_reb_ast_10_10"
    quotes = _synth_quotes(book, arch, true_rho, true_hold, RAW_PAIRS)

    with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
        rejections = {}
        fitted = fit_book_sgp_params(quotes, out_path=tmp.name, rejections=rejections)
        key = f"{book}|{arch}"
        assert key in fitted, rejections
        res = fitted[key]
        assert abs(res["rho_book"] - true_rho) < 0.01
        assert abs(res["hold_book"] - true_hold) < 0.01
        assert res["n"] == len(RAW_PAIRS)
        assert res["holdout_mape"] < 0.10

        test_l1, test_l2 = +320, +410
        pred = predict_book_sgp_odds(book, arch, [test_l1, test_l2], custom_params=fitted)
        q1_test = 100.0 / (test_l1 + 100.0)
        q2_test = 100.0 / (test_l2 + 100.0)
        true_dec = 1.0 / ((1.0 + true_hold) * copula_joint([q1_test, q2_test], true_rho))
        assert abs(pred.decimal - true_dec) / true_dec < 0.01
        assert pred.rho_used == pytest.approx(res["rho_book"], abs=1e-3)  # the validated fit is actually applied


def test_fit_rejects_too_few_quotes_and_does_not_persist():
    quotes = _synth_quotes("fanduel", "nba_reb_ast_10_10", 0.10, 0.14, RAW_PAIRS[: MIN_QUOTES_PER_GROUP - 1])
    with tempfile.TemporaryDirectory() as d:
        path = f"{d}/params.json"
        rejections = {}
        assert fit_book_sgp_params(quotes, out_path=path, rejections=rejections) == {}
        assert "fanduel|nba_reb_ast_10_10" in rejections and "valid 2-leg quotes" in rejections["fanduel|nba_reb_ast_10_10"]
        assert load_book_sgp_params(path) == {}  # nothing written


def test_fit_rejects_fit_pinned_at_bound(tmp_path):
    # hold 0.60 is outside HOLD_BOUNDS (max 0.45): the optimiser can only pin at the bound -> rejected
    quotes = _synth_quotes("draftkings", "nba_reb_ast_10_10", 0.10, 0.60, RAW_PAIRS)
    rejections = {}
    fitted = fit_book_sgp_params(quotes, out_path=str(tmp_path / "p.json"), rejections=rejections)
    assert fitted == {}
    assert rejections  # bound or holdout reason, never accepted


def test_fit_rejects_negative_rho_data(tmp_path):
    quotes = _synth_quotes("draftkings", "nba_pts_reb_vs_pr", -0.40, 0.10, RAW_PAIRS)
    rejections = {}
    assert fit_book_sgp_params(quotes, out_path=str(tmp_path / "p.json"), rejections=rejections) == {}
    assert rejections


def test_fit_skips_placeholder_sources_and_three_leg_rows(tmp_path):
    seeded = _synth_quotes("fanduel", "nba_reb_ast_10_10", 0.10, 0.14, RAW_PAIRS, source="initial_seed")
    assert fit_book_sgp_params(seeded, out_path=str(tmp_path / "p.json")) == {}
    three = _synth_quotes("fanduel", "nba_reb_ast_10_10", 0.10, 0.14, RAW_PAIRS, leg3_odds=-150)
    assert fit_book_sgp_params(three, out_path=str(tmp_path / "p.json")) == {}
    real = _synth_quotes("fanduel", "nba_reb_ast_10_10", 0.10, 0.14, RAW_PAIRS, source="harvest", leg3_odds="")
    assert "fanduel|nba_reb_ast_10_10" in fit_book_sgp_params(real, out_path=str(tmp_path / "p.json"))


def test_predict_ignores_params_pinned_at_bound_and_falls_back_to_defaults():
    arch = SGP_ARCHETYPES["nba_reb_ast_10_10"]
    bad = {"fanduel|nba_reb_ast_10_10": {"rho_book": -0.46, "hold_book": 0.45}}
    pred = predict_book_sgp_odds("fanduel", "nba_reb_ast_10_10", [+300, +350], custom_params=bad)
    assert pred.rho_used == arch.rho_book_default and pred.hold_used == arch.hold_book_default


def test_predict_uses_defaults_when_no_params_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no data/sgp_book_params.json here
    assert load_book_sgp_params() == {}
    arch = SGP_ARCHETYPES["nba_pts_reb_vs_pr"]
    pred = predict_book_sgp_odds("draftkings", "nba_pts_reb_vs_pr", [-110, -115])
    assert (pred.rho_used, pred.hold_used) == (arch.rho_book_default, arch.hold_book_default)


def test_predict_clamps_between_independent_product_and_min_leg():
    q = lambda am: 100.0 / (am + 100.0)
    # hold so large the unclamped price would exceed the weakest leg probability
    hi = predict_book_sgp_odds("x", "nba_reb_ast_10_10", [-200, -200], custom_params={"x|nba_reb_ast_10_10": {"rho_book": 0.8, "hold_book": 0.45}})
    min_leg = 200.0 / 300.0
    assert hi.implied_prob <= min_leg + 1e-9
    # a negative-rho / zero-hold parameter set can never be priced below the independent product
    lo = predict_book_sgp_odds("x", "nba_reb_ast_10_10", [+300, +350], custom_params={"x|nba_reb_ast_10_10": {"rho_book": -0.4, "hold_book": 0.0}})
    assert lo.implied_prob >= q(300) * q(350) - 1e-6
    for pred in (hi, lo):
        assert 0.0 < pred.implied_prob < 1.0


def test_three_leg_prediction_uses_defaults_not_the_two_leg_fit():
    arch = SGP_ARCHETYPES["nba_reb_ast_10_10"]
    fit = {"fanduel|nba_reb_ast_10_10": {"rho_book": 0.5, "hold_book": 0.3}}
    pred = predict_book_sgp_odds("fanduel", "nba_reb_ast_10_10", [-200, +300, +350], custom_params=fit)
    assert (pred.rho_used, pred.hold_used) == (arch.rho_book_default, arch.hold_book_default)


def test_dead_archetypes_removed():
    assert "nba_dd_proxy" not in SGP_ARCHETYPES and "nfl_td_yds_stack" not in SGP_ARCHETYPES


def test_build_sgp_deeplink():
    # DraftKings multi-outcome deep link
    dk_legs = [
        {"sid": "12345", "stat": "rebounds"},
        {"sid": "67890", "stat": "assists"},
    ]
    dk_url = build_sgp_deeplink("draftkings", "event_999", dk_legs)
    assert dk_url == "https://sportsbook.draftkings.com/event/event_999?outcomes=12345,67890"

    # DraftKings fallback without sids
    dk_fallback = build_sgp_deeplink("draftkings", "event_999", [])
    assert dk_fallback == "https://sportsbook.draftkings.com/event/event_999"

    # DraftKings internal game_key routing to sport league page (no 404 event URL)
    dk_league = build_sgp_deeplink("draftkings", "nba:2026-10-30:BOS@MIL", [], sport="nba")
    assert "/event/nba:" not in dk_league
    assert dk_league == "https://sportsbook.draftkings.com/leagues/basketball/nba"

    # FanDuel multi-outcome deep link
    fd_legs = [
        {"marketId": "1.1001", "selectionId": "2001"},
        {"marketId": "1.1002", "selectionId": "2002"},
    ]
    fd_url = build_sgp_deeplink("fanduel", "event_888", fd_legs)
    assert fd_url == "https://sportsbook.fanduel.com/addToBetslip?marketId[0]=1.1001&selectionId[0]=2001&marketId[1]=1.1002&selectionId[1]=2002"

    # FanDuel fallback without selectionIds
    fd_fallback = build_sgp_deeplink("fanduel", "event_888", [])
    assert fd_fallback == "https://sportsbook.fanduel.com/event/event_888"

    # FanDuel internal game_key routing to sport navigation page
    fd_nav = build_sgp_deeplink("fanduel", "nba:2026-10-30:BOS@MIL", [], sport="nba")
    assert "/event/nba:" not in fd_nav
    assert fd_nav == "https://sportsbook.fanduel.com/navigation/nba"


def test_deeplink_http_evil_host_with_book_domain_in_query_is_rejected():
    # substring host checks used to accept this
    legs = [{"link": "http://evil.com/?fanduel.com"}, {"link": "https://evil.com/?fanduel.com"},
            {"link": "https://fanduel.com.evil.com/x"}, {"link": "https://user@evil.com/fanduel.com"}]
    url = build_sgp_deeplink("fanduel", "", legs, sport="nba")
    assert url == "https://sportsbook.fanduel.com/navigation/nba"
    dk = build_sgp_deeplink("draftkings", "", [{"link": "http://evil.com/?sportsbook.draftkings.com/event/1"}], sport="nhl")
    assert dk == "https://sportsbook.draftkings.com/leagues/hockey/nhl"
    # a genuine https link on the book's own domain (or a subdomain) is still used
    ok = build_sgp_deeplink("fanduel", "", [{"link": "https://sportsbook.fanduel.com/event/12345"}], sport="nba")
    assert ok == "https://sportsbook.fanduel.com/event/12345"
    assert not link_allowed_for_book("fanduel", "http://sportsbook.fanduel.com/x")      # http
    assert not link_allowed_for_book("fanduel", "https://notfanduel.com/x")             # suffix without a dot boundary
    assert link_allowed_for_book("draftkings", "https://sportsbook.draftkings.com/x")


def test_deeplink_event_id_is_encoded_not_interpolated():
    url = build_sgp_deeplink("draftkings", "123/../x?y=1#frag", [], sport="nba")
    assert url == "https://sportsbook.draftkings.com/event/123%2F..%2Fx%3Fy%3D1%23frag"
    assert "#" not in url and "?" not in url
    for bad in ("..", "."):
        assert build_sgp_deeplink("fanduel", bad, [], sport="nba") == "https://sportsbook.fanduel.com/navigation/nba"
    assert build_sgp_deeplink("betmgm", "a b&c", [], sport="nba") == "https://sports.betmgm.com/en/sports/events/a%20b%26c"
    assert build_sgp_deeplink("caesars", "x?y", [], sport="nba").endswith("/event/x%3Fy")


def test_deeplink_book_name_never_becomes_a_host():
    for evil in ("evil.com/x#", "evil.com", "a.b", "../x", "evil.com\\x"):
        assert build_sgp_deeplink(evil, "event_1", [], sport="nba") is None
    # known books without an event page still get their fixed landing page
    assert build_sgp_deeplink("kalshi", "event_1", []) == "https://kalshi.com"
    assert build_sgp_deeplink("bet365", "event_1", []).startswith("https://www.bet365.com/")


def test_deeplink_dead_sid_and_market_id_branches_stay_allowlisted():
    # The odds board carries no outcome ids today, so these branches never run in production. They must keep
    # working AND keep their hard-coded hosts, with ids percent-encoded rather than interpolated.
    legs = [{"sid": "1&evil=1", "stat": "a"}, {"sid": "2,3", "stat": "b"}]
    dk = build_sgp_deeplink("draftkings", "event_9", legs)
    assert dk.startswith("https://sportsbook.draftkings.com/event/event_9?outcomes=")
    assert "&evil" not in dk
    fd = build_sgp_deeplink("fanduel", "e", [{"marketId": "1.1", "selectionId": "a&b"}, {"marketId": "1.2", "selectionId": "2"}])
    assert fd.startswith("https://sportsbook.fanduel.com/addToBetslip?")
    assert "a%26b" in fd and "&b" not in fd


def test_predict_book_sgp_odds_structure():
    pred = predict_book_sgp_odds("draftkings", "nba_pts_reb_vs_pr", [-110, -115])
    assert pred.book == "draftkings"
    assert pred.archetype == "nba_pts_reb_vs_pr"
    assert pred.decimal > 1.0
    assert pred.american != 0
    assert 0.0 < pred.implied_prob < 1.0


def test_build_sgp_proxy_candidates():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    # Simulated board rows for an NBA player
    nba_rows = [
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "rebounds",
            "line": 9.5,
            "curve_fair_over": 0.25,
            "books": [
                {"book": "fanduel", "over": 320, "under": -450},
                {"book": "draftkings", "over": 310, "under": -430},
            ],
        },
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "assists",
            "line": 9.5,
            "curve_fair_over": 0.22,
            "books": [
                {"book": "fanduel", "over": 410, "under": -600},
                {"book": "draftkings", "over": 400, "under": -580},
            ],
        },
        {
            "game_key": "nba_20261030_lal_gsw",
            "commence": "2026-10-30T23:00:00Z",
            "away": "LAL",
            "home": "GSW",
            "player": "Josh Hart",
            "player_norm": "josh hart",
            "stat": "triple_double",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": 1300, "under": None},
                {"book": "draftkings", "over": 1250, "under": None},
            ],
        },
    ]

    cands = build_sgp_proxy_candidates(nba_rows, min_ev_pct=-50.0)
    assert len(cands) >= 1
    top = cands[0]
    assert top.player == "Josh Hart"
    assert top.archetype == "nba_reb_ast_10_10"
    assert top.est_sgp_american > 1000
    # Reb + Ast alone is NOT a Triple-Double: no like-for-like direct comparison may be made
    assert len(top.legs) == 2
    assert top.direct_best_american is None and top.direct_market_label is None and top.vs_direct_pct is None
    # honesty labels on every row
    assert top.unvalidated is True and "UNVALIDATED" in top.label
    d = top.to_dict()
    assert d["unvalidated"] is True and d["model_artifact"] in (True, False)
    if len(top.runner_up_books or []) > 0:
        assert top.runner_up_books[0]["book"] in ("fanduel", "draftkings")


def _td_rows(extra_books=True):
    base = {
        "game_key": "nba_20261030_lal_gsw", "commence": "2026-10-30T23:00:00Z", "away": "LAL", "home": "GSW",
        "player": "Nikola Jokic", "player_norm": "nikola jokic",
    }
    def row(stat, line, books):
        return {**base, "stat": stat, "line": line, "books": books}
    def bk(over, under):
        out = [{"book": "fanduel", "over": over, "under": under}, {"book": "draftkings", "over": over + 10, "under": under}]
        if extra_books:
            out += [{"book": "betmgm", "over": over - 5, "under": under}, {"book": "caesars", "over": over, "under": under}]
        return out
    return [
        row("points", 9.5, bk(-300, 230)),
        row("rebounds", 9.5, bk(+110, -140)),
        row("assists", 9.5, bk(+150, -190)),
        row("triple_double", 0.5, [{"book": "fanduel", "over": 300, "under": None}, {"book": "draftkings", "over": 320, "under": None}]),
    ]


def test_td_direct_comparison_only_for_three_leg_same_event():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    cands = build_sgp_proxy_candidates(_td_rows(), min_ev_pct=-100.0)
    three = [c for c in cands if c.archetype == "nba_reb_ast_10_10" and len(c.legs) == 3]
    assert three, "3-leg Pts+Reb+Ast (all 9.5) should be generated"
    for c in three:
        assert c.direct_market_label == "Triple-Double"
        assert c.direct_best_american == 320 and c.vs_direct_pct is not None
    for c in cands:
        if len(c.legs) == 2 and c.archetype == "nba_reb_ast_10_10":
            assert c.vs_direct_pct is None and c.direct_best_american is None


def test_combo_and_nhl_candidates_carry_no_direct_claim():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    rows = _td_rows()
    # add a P+R combo market as a tempting (non-comparable) direct row
    rows.append({**rows[0], "stat": "pr", "line": 19.0, "books": [{"book": "fanduel", "over": 110, "under": -140}]})
    cands = build_sgp_proxy_candidates(rows, min_ev_pct=-100.0)
    combos = [c for c in cands if "_vs_" in c.archetype]
    assert combos
    assert all(c.vs_direct_pct is None and c.direct_best_american is None and c.direct_market_label is None for c in combos)


def test_pricing_book_is_excluded_from_its_own_fair_value():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    # Only two books quote the legs. Leaving the pricing book out leaves ONE other book and no curve: no candidate.
    rows = [r for r in _td_rows(extra_books=False) if r["stat"] in ("rebounds", "assists")]
    assert build_sgp_proxy_candidates(rows, min_ev_pct=-100.0) == []
    # Moving the pricing book's own quotes must not move the fair value it is priced against.
    from ev.sgp_proxy_builder import _fair_excl

    row = next(r for r in _td_rows() if r["stat"] == "rebounds")
    f_before = _fair_excl(row, "fanduel")
    for b in row["books"]:
        if b["book"] == "fanduel":
            b["over"] += 150
    assert f_before is not None and _fair_excl(row, "fanduel") == f_before
    # ...whereas another book's quote does move it
    for b in row["books"]:
        if b["book"] == "draftkings":
            b["over"] += 150
    assert _fair_excl(row, "fanduel") != f_before


def test_extreme_ev_rows_are_flagged_not_dropped_and_sorted_last():
    from ev.sgp_proxy_builder import MAX_CREDIBLE_EV_PCT, build_sgp_proxy_candidates

    rows = _td_rows()
    # a hugely stale long price at one book on the rebounds + assists legs creates an absurd modelled EV
    for r in rows:
        if r["stat"] in ("rebounds", "assists"):
            for b in r["books"]:
                if b["book"] == "betmgm":
                    b["over"] = 2500
    cands = build_sgp_proxy_candidates(rows, min_ev_pct=-100.0)
    flagged = [c for c in cands if c.model_artifact]
    assert flagged, "the extreme row must be kept and flagged"
    assert all(c.ev_pct > MAX_CREDIBLE_EV_PCT for c in flagged)
    assert all(c.ev_pct <= MAX_CREDIBLE_EV_PCT for c in cands if not c.model_artifact)
    first_flag = next(i for i, c in enumerate(cands) if c.model_artifact)
    assert all(c.model_artifact for c in cands[first_flag:])  # flagged rows never lead


def test_nhl_sgp_shapes_suppressed():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    # Rows that WOULD previously have produced both suppressed NHL shapes
    nhl_rows = [
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Connor McDavid",
            "player_norm": "connor mcdavid",
            "stat": "goals",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": 190, "under": -250},
                {"book": "draftkings", "over": 185, "under": -240},
                {"book": "betmgm", "over": 180, "under": -235},
                {"book": "caesars", "over": 182, "under": -238},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Connor McDavid",
            "player_norm": "connor mcdavid",
            "stat": "assists",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": 130, "under": -170},
                {"book": "draftkings", "over": 125, "under": -165},
                {"book": "betmgm", "over": 120, "under": -160},
                {"book": "caesars", "over": 122, "under": -162},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Connor McDavid",
            "player_norm": "connor mcdavid",
            "stat": "points",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": -320, "under": 250},
                {"book": "draftkings", "over": -310, "under": 240},
                {"book": "betmgm", "over": -300, "under": 235},
                {"book": "caesars", "over": -305, "under": 238},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Leon Draisaitl",
            "player_norm": "leon draisaitl",
            "stat": "points",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": -280, "under": 220},
                {"book": "draftkings", "over": -270, "under": 215},
                {"book": "betmgm", "over": -265, "under": 212},
                {"book": "caesars", "over": -275, "under": 218},
            ],
        },
    ]

    cands = build_sgp_proxy_candidates(nhl_rows, min_ev_pct=-50.0)
    assert not any(c.archetype == "nhl_linemate_pts_stack" for c in cands)
    assert not any(c.archetype == "nhl_goal_ast_vs_pts" for c in cands)


def test_build_sgp_proxy_linemate_stacks_real_lines():
    from ev.sgp_proxy_builder import build_sgp_proxy_candidates

    # Line assignments from NHL/lines/lines_latest.json built 2026-11-12 (trailing-10 window, as of 2026-11-11);
    # prices are fixture prices in the style of the previous test.
    nhl_rows = [
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Connor McDavid",
            "player_norm": "connor mcdavid",
            "stat": "points",
            "line": 0.5,
            "line_id": "EDM-F1",
            "books": [
                {"book": "fanduel", "over": -320, "under": 250},
                {"book": "draftkings", "over": -310, "under": 240},
                {"book": "betmgm", "over": -300, "under": 235},
                {"book": "caesars", "over": -305, "under": 238},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Leon Draisaitl",
            "player_norm": "leon draisaitl",
            "stat": "points",
            "line": 0.5,
            "line_id": "EDM-F1",
            "books": [
                {"book": "fanduel", "over": -280, "under": 220},
                {"book": "draftkings", "over": -270, "under": 215},
                {"book": "betmgm", "over": -265, "under": 212},
                {"book": "caesars", "over": -275, "under": 218},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Vasily Podkolzin",
            "player_norm": "vasily podkolzin",
            "stat": "points",
            "line": 0.5,
            "line_id": "EDM-F1",
            "books": [
                {"book": "fanduel", "over": -170, "under": 140},
                {"book": "draftkings", "over": -165, "under": 138},
                {"book": "betmgm", "over": -160, "under": 135},
                {"book": "caesars", "over": -168, "under": 142},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Ryan Nugent-Hopkins",
            "player_norm": "ryan nugent-hopkins",
            "stat": "points",
            "line": 0.5,
            "line_id": "EDM-F2",
            "books": [
                {"book": "fanduel", "over": -210, "under": 175},
                {"book": "draftkings", "over": -200, "under": 168},
                {"book": "betmgm", "over": -205, "under": 170},
                {"book": "caesars", "over": -208, "under": 172},
            ],
        },
        {
            "game_key": "nhl:2026-10-30:EDM@CGY",
            "commence": "2026-10-30T23:00:00Z",
            "away": "EDM",
            "home": "CGY",
            "team": "EDM",
            "player": "Zach Hyman",
            "player_norm": "zach hyman",
            "stat": "points",
            "line": 0.5,
            "books": [
                {"book": "fanduel", "over": -190, "under": 160},
                {"book": "draftkings", "over": -185, "under": 155},
                {"book": "betmgm", "over": -180, "under": 152},
                {"book": "caesars", "over": -188, "under": 158},
            ],
        },
    ]

    cands = build_sgp_proxy_candidates(nhl_rows, min_ev_pct=-50.0)
    expected_players = {
        "Connor McDavid + Leon Draisaitl (EDM)",
        "Connor McDavid + Vasily Podkolzin (EDM)",
        "Leon Draisaitl + Vasily Podkolzin (EDM)",
    }
    assert {c.player for c in cands} == expected_players
    assert len(cands) == 3
    for c in cands:
        assert c.archetype == "nhl_linemate_pts_stack"
        assert c.rho_true == 0.32
        assert "Hyman" not in c.player and "hyman" not in c.player_norm
        assert "Nugent-Hopkins" not in c.player and "nugent-hopkins" not in c.player_norm
        assert "/event/nhl:" not in (c.deeplink or "")

