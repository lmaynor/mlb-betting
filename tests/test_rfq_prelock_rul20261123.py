"""tests/test_rfq_prelock_rul20261123.py -- RUL-20261123 pre-lock staging (Branch 1).

Covers:
- item 2: the shadow path refuses a bare (uncalibrated) admission; build_shadow_admission
  refuses empty / flag-less fit rows; trust accrual is settled-only with a one-day lag.
- item 4: the EB weight's n_f counts settled FIRES when rows carry fire flags.
- item 5: a combo calibrated to <= 0 is refused (NONPOSITIVE_CALIBRATED_ROI), never admitted.
- item 6: one combo's ValueError (unknown-sport player leg) fails that combo closed only.
- item 7: timing_approx fires stay under the Tier B reservation; commence_lookup has no
  future-snapshot fallback (no look-ahead).
- item 8: window2_day_key buckets by the ET date of the earliest leg's start.
- item 11: candidate_from_legs carries disp / age / p_max stress inputs; q_kalshi stays None.
- item 14(b): the Window 1 liability charged by the harness is fee-inclusive and <= $15.
"""

from __future__ import annotations

import pytest

from ev.combo_taker_scanner import ComboCandidate, ComboLeg, evaluate_combo
from ev.combo_taker_window2 import (
    FamilyTrustBook,
    Window2Admission,
    Window2Config,
    Window2Fire,
    build_shadow_admission,
    fit_calibrator,
)
from ev.kalshi_rfq_pricer import InMemoryFairBook, LegFairValue
from ev.kalshi_rfq_replay import SnapshotStore
from ev.kalshi_rfq_taker_replay import (
    candidate_from_legs,
    commence_lookup,
    evaluate_taker_combo,
    run_candidates,
    window2_day_key,
)

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z = 03:00 EST


def _leg(**kw) -> ComboLeg:
    base = dict(ticker="T1", venue="kalshi", player_norm=None, game_id="g1", sport="nfl",
                stat="rec_yds", line=59.5, direction=1, p_lambda=0.55, routing="OVER_ELIGIBLE",
                q_implied=0.50, is_hot_scratch=False, game_start_ts=NOW + 7200.0, team=None)
    base.update(kw)
    return ComboLeg(**base)


def _fire(cid: str, legs, bid_yes: float = 0.70, now: float = NOW, **kw) -> Window2Fire:
    cand = ComboCandidate(combo_id=cid, venue="kalshi", legs=legs, bid_yes=bid_yes,
                          age_since_creation_sec=0.0, is_rfq_live=True, now_ts=now, stake=None)
    sig = evaluate_combo(cand)
    assert sig.fires, f"fixture {cid} does not fire: {sig.reasons}"
    return Window2Fire(candidate=cand, signal=sig, day="2027-01-15", **kw)


def _fit_rows(family="NFL_PROP", n=60, pnl=0.18, fires=True, days=8):
    return [{"family": family, "day": f"2026-10-{10 + (i % days):02d}",
             "is_fire": True, "fires": True, "ask_no": 0.55,
             "roi_claimed": 0.05 + 0.01 * (i % 40), "pnl_per_contract": pnl}
            for i in range(n)] if fires else [
            {"family": family, "day": f"2026-10-{10 + (i % days):02d}",
             "ask_no": 0.55, "roi_claimed": 0.05 + 0.01 * (i % 40), "pnl_per_contract": pnl}
            for i in range(n)]


# ---------------------------------------------------------------- item 2 -----

def test_shadow_path_refuses_bare_admission():
    book = InMemoryFairBook(clock=lambda: NOW)
    book.replace_all([LegFairValue(
        ticker="T1", sport="nfl", game_key="nfl:2027-01-15:BUF@MIA", market_class="game_total",
        player_norm=None, stat="total", strike=45.5, p_cons_yes=0.5, n_books=4, disp=0.01,
        p_model_yes=0.5, p_true_yes=0.5, pooled=False, updated_ts=NOW)])
    cand, status = candidate_from_legs("c1", "kalshi", [("T1", "yes"), ("T1", "no")], book, NOW, 0.45,
                                       game_start_lookup=lambda gk: NOW + 3600.0)
    assert status == "OK"
    with pytest.raises(ValueError, match="no calibrator"):
        run_candidates([(cand, {"day": "2027-01-15", "yes_won": False})],
                       window2_admission=Window2Admission())


def test_build_shadow_admission_refusals():
    with pytest.raises(ValueError):
        build_shadow_admission([])
    with pytest.raises(ValueError, match="no fire flags"):
        build_shadow_admission(_fit_rows(fires=False))
    adm = build_shadow_admission(_fit_rows())
    assert adm.calibrator is not None and adm.calibrator.counts_basis == "fires"
    assert adm.trust_book.fires["NFL_PROP"] == 60


def test_trust_accrual_lags_one_day():
    # 90 fires / 6 days seeded: ACCRUING (fires < 100, days < 7). Day A's own settlements
    # (15 more fires, a 7th day) must NOT lift day A's decisions -- only day B's.
    adm = build_shadow_admission(_fit_rows(n=90, days=6))
    assert adm.trust_book.regime("NFL_PROP") == "ACCRUING"
    book = InMemoryFairBook(clock=lambda: NOW)

    def _mk_book(day_start):
        b = InMemoryFairBook(clock=lambda: day_start)
        b.replace_all([
            LegFairValue(ticker="P1", sport="nfl", game_key="nfl:2027-01-15:BUF@MIA",
                         market_class="player_prop", player_norm="star", stat="rec_yds", strike=50.5,
                         p_cons_yes=0.55, n_books=4, disp=0.01, p_model_yes=0.55, p_true_yes=0.55,
                         pooled=False, updated_ts=day_start),
            LegFairValue(ticker="G1", sport="nfl", game_key="nfl:2027-01-15:KC@DEN",
                         market_class="game_total", player_norm=None, stat="total", strike=45.5,
                         p_cons_yes=0.55, n_books=4, disp=0.01, p_model_yes=0.55, p_true_yes=0.55,
                         pooled=False, updated_ts=day_start),
        ])
        return b

    items = []
    day_a = NOW
    day_b = NOW + 86400.0
    for i in range(15):
        cand, st = candidate_from_legs(f"a{i}", "kalshi", [("P1", "yes"), ("G1", "yes")],
                                       _mk_book(day_a), day_a + i, 0.45,
                                       game_start_lookup=lambda gk: day_a + 7200.0)
        assert st == "OK"
        items.append((cand, {"day": "2027-01-15", "yes_won": False}))
    cand_b, st = candidate_from_legs("b0", "kalshi", [("P1", "yes"), ("G1", "yes")],
                                     _mk_book(day_b), day_b, 0.45,
                                     game_start_lookup=lambda gk: day_b + 7200.0)
    assert st == "OK"
    items.append((cand_b, {"day": "2027-01-16", "yes_won": False}))
    records, _summary = run_candidates(items, window2_admission=adm)
    by_id = {r["combo_id"]: r for r in records}
    assert by_id["a0"]["window2_tier"] == "B"          # day A still de-ranked (tau applied in score)
    # All 16 settled fires accrued -- but note where b0 landed: once day A's settlements made
    # NFL_PROP TRUSTED, b0's weakest link became its GAME_LINE leg, so combo_family labels (and
    # accrues) b0 as GAME_LINE. That migration is itself the lag proof: day B was decided with
    # day A's settlements already in the book, while day A's own fires were decided before them.
    assert adm.trust_book.fires["NFL_PROP"] == 90 + 15
    assert adm.trust_book.fires["GAME_LINE"] == 1
    assert adm.trust_book.regime("NFL_PROP") == "TRUSTED"   # lifted only after day A accrued
    assert by_id["b0"]["window2_admitted"] is not None


# ---------------------------------------------------------------- item 4 -----

def test_eb_weight_counts_fires_not_evaluated_rows():
    rows = []
    for i in range(200):   # NFL_PROP: 200 evaluated rows, only 20 of them fires
        rows.append({"family": "NFL_PROP", "day": "2026-10-10", "is_fire": i < 20,
                     "ask_no": 0.55, "roi_claimed": 0.30, "pnl_per_contract": 0.30})
    for i in range(40):    # GAME_LINE: filler so the pooled fit is not the NFL curve itself
        rows.append({"family": "GAME_LINE", "day": "2026-10-10", "is_fire": True,
                     "ask_no": 0.55, "roi_claimed": 0.30, "pnl_per_contract": -0.30})
    cal = fit_calibrator(rows)
    assert cal.counts_basis == "fires"
    assert cal.family_row_counts["NFL_PROP"] == 200
    assert cal.family_fire_counts["NFL_PROP"] == 20
    # 20 fires < 30 minimum -> weight 0 -> prediction is the POOLED curve only. The same rows
    # with every row flagged a fire give the family weight 200/300 and pull the prediction UP
    # toward the family's own strongly-positive curve -- the unit change is what moves it.
    rows_all_fires = [dict(r, is_fire=True) for r in rows]
    cal_all = fit_calibrator(rows_all_fires)
    assert cal_all.family_fire_counts["NFL_PROP"] == 200
    assert cal.predict(0.30, "NFL_PROP") < cal_all.predict(0.30, "NFL_PROP")


def test_eb_weight_fallback_basis_without_flags():
    rows = [{"family": "NFL_PROP", "day": "2026-10-10", "ask_no": 0.55,
             "roi_claimed": 0.30, "pnl_per_contract": 0.30} for _ in range(40)]
    cal = fit_calibrator(rows)
    assert cal.counts_basis == "evaluated_fallback"
    assert cal.family_fire_counts["NFL_PROP"] == 40


# ---------------------------------------------------------------- item 5 -----

def test_nonpositive_calibrated_roi_refused():
    cal = fit_calibrator(_fit_rows(pnl=-0.30))       # realized ROI deeply negative
    assert cal.predict(0.30, "NFL_PROP") <= 0.0
    adm = Window2Admission(calibrator=cal, trust_book=FamilyTrustBook())
    fire = _fire("neg1", [_leg(ticker="A", game_id="g1"), _leg(ticker="B", game_id="g2")])
    decs = adm.run_day([fire], "2027-01-15")
    assert len(decs) == 1
    assert decs[0].admitted is False
    assert decs[0].refusals == ("NONPOSITIVE_CALIBRATED_ROI",)
    assert decs[0].contracts == 0


# ---------------------------------------------------------------- item 6 -----

def test_unknown_sport_leg_fails_closed_not_the_day():
    adm = Window2Admission()
    bad = _fire("bad1", [_leg(ticker="X", game_id="g9", player_norm="someone", sport="mls"),
                         _leg(ticker="B", game_id="g2")])
    good = _fire("good1", [_leg(ticker="A", game_id="g1"), _leg(ticker="C", game_id="g3")])
    decs = adm.run_day([bad, good], "2027-01-15")
    by_id = {d.combo_id: d for d in decs}
    assert by_id["bad1"].admitted is False
    assert by_id["bad1"].refusals == ("EVALUATION_ERROR",)
    assert by_id["good1"].admitted is True


# ---------------------------------------------------------------- item 7 -----

def test_timing_approx_stays_under_tier_b_reservation():
    cfg = Window2Config(shared_leg_cap=25.0)
    adm = Window2Admission(cfg=cfg)
    shared = _leg(ticker="S", game_id="g1", player_norm="star", stat="rec_yds")
    f1 = _fire("c1", [shared, _leg(ticker="A", game_id="g2")])
    f2 = _fire("c2", [shared, _leg(ticker="B", game_id="g3")])
    approx = _fire("c3", [shared, _leg(ticker="C", game_id="g4")],
                   timing_approx=True)
    # c3's legs start at NOW+7200 in the fixture; rebuild it with a synthetic start (now+3600)
    approx.candidate.legs[0] = _leg(ticker="S", game_id="g1", player_norm="star",
                                    stat="rec_yds", game_start_ts=NOW + 3600.0)
    approx.candidate.legs[1] = _leg(ticker="C", game_id="g4", game_start_ts=NOW + 3600.0)
    approx.signal = evaluate_combo(approx.candidate)
    decs = adm.run_day([f1, f2, approx], "2027-01-15")
    by_id = {d.combo_id: d for d in decs}
    assert by_id["c1"].admitted and by_id["c2"].admitted
    assert by_id["c3"].admitted is False
    assert "TIER_B_RESERVATION" in by_id["c3"].refusals

    # Control: the same fire with a VERIFIED start 60 minutes out is released and admitted.
    adm2 = Window2Admission(cfg=cfg)
    g1 = _fire("c1", [shared, _leg(ticker="A", game_id="g2")])
    g2 = _fire("c2", [shared, _leg(ticker="B", game_id="g3")])
    verified = _fire("c3", [shared, _leg(ticker="C", game_id="g4")], timing_approx=False)
    verified.candidate.legs[0] = _leg(ticker="S", game_id="g1", player_norm="star",
                                      stat="rec_yds", game_start_ts=NOW + 3600.0)
    verified.candidate.legs[1] = _leg(ticker="C", game_id="g4", game_start_ts=NOW + 3600.0)
    verified.signal = evaluate_combo(verified.candidate)
    decs2 = adm2.run_day([g1, g2, verified], "2027-01-15")
    by_id2 = {d.combo_id: d for d in decs2}
    assert by_id2["c3"].admitted is True


def test_commence_lookup_has_no_future_fallback():
    leg = LegFairValue(ticker="T1", sport="nfl", game_key="nfl:2027-01-15:BUF@MIA",
                       market_class="game_total", player_norm=None, stat="total", strike=45.5,
                       p_cons_yes=0.5, n_books=4, disp=0.01, p_model_yes=None, pooled=False,
                       updated_ts=NOW)
    store = SnapshotStore([
        (NOW, [leg], None, {}),                                            # in force at NOW: no commence map
        (NOW + 3600.0, [leg], None, {"nfl:2027-01-15:BUF@MIA": NOW + 7200.0}),  # later snapshot knows it
    ])
    lookup = commence_lookup(store, NOW, 7200.0)
    assert lookup("nfl:2027-01-15:BUF@MIA") is None        # no look-ahead into the later snapshot
    lookup2 = commence_lookup(store, NOW + 3600.0, 7200.0)
    assert lookup2("nfl:2027-01-15:BUF@MIA") == NOW + 7200.0


# ---------------------------------------------------------------- item 8 -----

def test_window2_day_key_uses_et_earliest_leg():
    # 2027-01-15T01:00Z is 2027-01-14 20:00 EST: the ET day is the 14th, not the UTC 15th.
    cand = ComboCandidate(combo_id="x", venue="kalshi",
                          legs=[_leg(game_start_ts=NOW + 90000.0),
                                _leg(ticker="B", game_id="g2", game_start_ts=NOW - 25200.0)],
                          bid_yes=0.5, age_since_creation_sec=0.0, is_rfq_live=True,
                          now_ts=NOW, stake=None)
    assert window2_day_key(cand) == "2027-01-14"


# --------------------------------------------------------------- item 11 -----

def test_candidate_carries_stress_inputs():
    book = InMemoryFairBook(clock=lambda: NOW)
    book.replace_all([LegFairValue(
        ticker="T1", sport="nfl", game_key="nfl:2027-01-15:BUF@MIA", market_class="game_total",
        player_norm=None, stat="total", strike=45.5, p_cons_yes=0.5, n_books=4, disp=0.03,
        p_max=0.62, p_model_yes=0.5, p_true_yes=0.5, pooled=False, updated_ts=NOW - 600.0)])
    cand, status = candidate_from_legs("c1", "kalshi", [("T1", "yes"), ("T1", "no")], book, NOW, 0.45,
                                       game_start_lookup=lambda gk: NOW + 3600.0)
    assert status == "OK"
    s = cand.stress_inputs[0]
    assert s.disp == 0.03 and s.age_sec == 600.0 and s.p_max == 0.62 and s.q_kalshi is None


# ------------------------------------------------------------ item 14(b) -----

def test_window1_liability_is_fee_inclusive():
    cand = ComboCandidate(combo_id="x", venue="kalshi",
                          legs=[_leg(), _leg(ticker="B", game_id="g2")],
                          bid_yes=0.50, age_since_creation_sec=0.0, is_rfq_live=True,
                          now_ts=NOW, stake=None)
    from ev.combo_taker_scanner import ComboLiabilityGuard
    rec = evaluate_taker_combo(cand, yes_won=False, guard=ComboLiabilityGuard())
    assert rec["fires"]
    # ask_no = 0.50: pure stake would be capped at $15.00; fee-inclusive collateral for the
    # largest fitting position (28 contracts x (0.50 + 0.0175)) is $14.49.
    assert cand.stake == pytest.approx(28 * (0.50 + 0.0175), abs=0.01)
    assert cand.stake < 15.0
