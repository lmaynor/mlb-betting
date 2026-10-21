"""Tests for mlb.analysis.sgp_proxy_scanner (offline; no network)."""
import numpy as np
import pytest

from mlb.analysis import sgp_proxy_scanner as sp
from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob


def _q(player, book, path, american, source=sp.QUOTED, event="A @ B"):
    return sp.Quote(player, event, book, path, american, source)


def _official(price, books=("b1", "b2", "b3"), player="P"):
    return [_q(player, b, "Official TD", price) for b in books]


def test_valid_american():
    assert sp.valid_american(-110) and sp.valid_american(100) and sp.valid_american(+5000)
    assert not sp.valid_american(0) and not sp.valid_american(50) and not sp.valid_american(-99)
    assert not sp.valid_american(float("nan")) and not sp.valid_american(None) and not sp.valid_american("x")


def test_devig_power_sums_to_one_and_shades_longshots_more():
    raw = [american_to_implied_prob(o) for o in (+195, +340, +350, +190)]
    fair = sp.devig_power(raw)
    assert fair.sum() == pytest.approx(1.0, abs=1e-9)
    prop = np.asarray(raw) / sum(raw)
    # power method removes relatively more vig from the longshots than proportional does
    assert fair[1] / raw[1] < prop[1] / raw[1]


def test_ev_math_reproduces_reference_row():
    # reference: best +11834 vs consensus +6259 -> +87.7% (discount 1.0 = exact path)
    groups = sp.build_groups(td_pra_share=1.0, conditional_pts_discount=1.0)
    g = groups["nba_td"]
    quotes = _official(6259) + [_q("P", "fanduel", "SGP (10+R/10+A)", 11834)]
    rows = sp.scan(quotes, g, min_books=3)
    assert len(rows) == 1
    r = rows[0]
    assert r.best.book == "fanduel"
    cp = american_to_implied_prob(6259)
    assert r.consensus_prob == pytest.approx(cp)
    assert r.ev_pct == pytest.approx((american_to_decimal(11834) * cp - 1) * 100)
    assert r.ev_pct == pytest.approx(87.7, abs=0.1)


def test_min_books_filter_uses_distinct_official_books():
    g = sp.build_groups()["nba_td"]
    two_books = _official(5000, books=("b1", "b1", "b2")) + [_q("P", "fd", "SGP (10+R/10+A)", 9000)]
    assert sp.scan(two_books, g, min_books=3) == []
    assert len(sp.scan(two_books, g, min_books=2)) == 1


def test_proxy_ratio_raises_ev_vs_exact_path():
    quotes = _official(5000) + [_q("P", "fd", "SGP (10+R/10+A)", 5000)]
    # exact path at the consensus price has EV exactly 0, so the scan drops it
    assert sp.scan(quotes, sp.build_groups(1.0, 1.0)["nba_td"]) == []
    dom = sp.scan(quotes, sp.build_groups(1.0, 0.9)["nba_td"])[0]   # proxy wins ~11% more often
    assert dom.ev_pct == pytest.approx((american_to_decimal(5000) * american_to_implied_prob(5000) / 0.9 - 1) * 100)


def test_non_positive_ev_excluded_and_worst_is_lowest_payout():
    g = sp.build_groups()["nba_td"]
    quotes = _official(5000) + [_q("P", "fd", "SGP (10+P/10+R/10+A)", 4000)]
    assert sp.scan(quotes, g) == []   # consensus at +5000 beats everything quoted -> EV <= 0 only if no outlier
    quotes2 = _official(5000) + [_q("P", "fd", "SGP (10+R/10+A)", 8000), _q("P", "mgm", "SGP (10+R/10+A)", 3000)]
    r = sp.scan(quotes2, g)[0]
    assert r.best.book == "fd" and r.worst.book == "mgm" and r.worst.american == 3000


def test_modeled_quotes_excluded_by_default():
    g = sp.build_groups()["nba_td"]
    modeled = _q("P", "fd", "SGP (10+R/10+A)", 20000, source=sp.MODELED)
    quotes = _official(5000) + [modeled]
    assert sp.scan(quotes, g) == []
    r = sp.scan(quotes, g, include_modeled=True)[0]
    assert r.best.source == sp.MODELED
    assert "*" in sp.render_table([r], g)


def test_invalid_quotes_ignored():
    g = sp.build_groups()["nba_td"]
    quotes = _official(5000) + [_q("P", "fd", "SGP (10+R/10+A)", 50)]
    assert sp.scan(quotes, g) == []


def test_copula_joint_prob_bounds_and_independence():
    indep = sp.CopulaFallbackPricer(corr=np.eye(3), hold=0.0)
    p = [0.9, 0.5, 0.3]
    assert indep.joint_prob(p) == pytest.approx(np.prod(p), abs=2e-4)
    corr = np.array([[1, .5, .5], [.5, 1, .5], [.5, .5, 1]])
    pos = sp.CopulaFallbackPricer(corr=corr, hold=0.0)
    j = pos.joint_prob(p)
    assert np.prod(p) < j <= min(p)            # positive correlation lifts joint, Frechet upper bound holds
    with pytest.raises(ValueError):
        pos.joint_prob([0.5, 0.5])             # wrong number of legs
    with pytest.raises(ValueError):
        pos.joint_prob([1.0, 0.5, 0.5])


def test_copula_price_applies_hold_and_is_marked_modeled():
    pricer = sp.CopulaFallbackPricer(corr=np.eye(2), hold=0.15)
    q = pricer.price("P", "A @ B", "fd", "SGP (10+R/10+A)", [-200, +150])
    assert q.source == sp.MODELED
    no_hold = sp.CopulaFallbackPricer(corr=np.eye(2), hold=0.0).price("P", "A @ B", "fd", "x", [-200, +150])
    assert american_to_implied_prob(q.american) > american_to_implied_prob(no_hold.american)


def test_live_provider_is_a_stub():
    with pytest.raises(NotImplementedError):
        sp.LiveBetslipQuoteProvider().fetch(sp.build_groups()["nba_td"])


def test_mlb_nested_identity_group():
    g = sp.build_groups()["mlb_tb"]
    quotes = [_q("H", b, "Official 2+ TB", 150) for b in ("a", "b", "c")] + [_q("H", "d", "SGP (1+H/2+TB)", 180)]
    r = sp.scan(quotes, g)[0]
    assert r.best.path == "SGP (1+H/2+TB)"
    assert r.ev_pct == pytest.approx((american_to_decimal(180) * american_to_implied_prob(150) - 1) * 100)


def test_csv_roundtrip_and_demo_cli(tmp_path, capsys):
    p = tmp_path / "q.csv"
    p.write_text("player,event,book,path,american\nP,A @ B,b1,Official TD,5000\nP,A @ B,b2,Official TD,5000\n"
                 "P,A @ B,b3,Official TD,5000\nP,A @ B,fd,SGP (10+R/10+A),9000\nP,A @ B,x,Official TD,50\n")
    qs = sp.read_quotes_csv(str(p))
    assert len(qs) == 4                         # invalid +50 row dropped
    assert sp.main(["--input", str(p)]) == 0
    out = capsys.readouterr().out
    assert "| Player" in out and "Best Method" in out and "+9000" in out
    assert sp.main(["--demo"]) == 0
    demo = capsys.readouterr().out
    assert "DEMO DATA" in demo and "BEST OVERALL TRIPLE-DOUBLE VALUE (Market vs. SGP Paths):" in demo
    assert "Thin Market Guy" not in demo        # only 2 official books -> filtered
