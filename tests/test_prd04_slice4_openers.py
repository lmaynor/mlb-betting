"""PRD-04 slice 4 (adopted Nov 15, 2026; RUL-20261115-prd04-officials-scope R2): the opener cohort machinery.

  * league polling windows in CT (NBA wave 1 22:30-01:30, NBA wave 2 + NHL mornings 09:00-13:00,
    NFL Wed 12:00-16:00 / Sun 07:30-10:00), the 900s / 1800s freshness gate, the hourly baseline tick;
  * the OPENER cohort (market_age <= 45 min since the first >= 3-book sighting, Tier 1+2 only,
    n_books >= 3 with the cohort's dispersion caps, mins_to_start >= 180 / >= 600 overnight NBA,
    the unconfirmed-sharp buffer, the injury guard) and cohort locking with cross-cohort dedupe;
  * the c = 0 line-shopping control: computed at board assembly from leave-self-out consensus
    (ev.daily_projections.prd04_control_fields) and locked on the same slates.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from ev import daily_projections as DP
from ev import prd04_officials as P4
from ev import prd04_openers as PO

# 2026-11-15 is a Sunday; CT is UTC-6 (CST). 16:00 UTC = 10:00 CT (morning window).
T0 = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)


def row(now=T0, **kw):
    r = {
        "sport": "nba", "stat": "rebounds", "player": "Jalen Duren", "player_norm": "id4594268",
        "team": "DET", "opp": "CHI", "game_key": "nba:2026-11-15:CHI@DET",
        "commence": (now + timedelta(hours=4)).isoformat(), "mins_to_start": 240.0,
        "has_line": True, "line": 10.5, "side": "over", "proj_mean": 11.8,
        "p_model_side": 0.58, "p_mkt_side": 0.53,
        "best_over_odds": 105, "best_over_book": "fanduel", "best_under_odds": -125,
        "best_under_book": "draftkings",
        "n_books": 6, "model_edge": 0.05, "market_ev": 0.0865, "confluence": True, "edge_capped": False,
        "beezy_score": 72, "engine": "empirical_ewm_v2", "validated": False,
        "p_blend_side": 0.545, "blend_ev": 0.07,
        "p_sharp_ref_over": 0.53, "p_sharp_ref_book": "pinnacle", "n_sharp_ref": 1,
        "p_cons_w_over": 0.53, "cons_disp": 0.01,
    }
    r.update(kw)
    return r


def bypass_row(now=T0, **kw):
    """A 3-book opener row with no sharp reference: bypass-eligible, buffers met."""
    base = {"n_books": 3, "cons_disp": 0.04, "p_sharp_ref_over": None, "p_sharp_ref_book": None,
            "n_sharp_ref": 0, "market_ev": 0.04, "blend_ev": 0.04, "p_blend_side": 0.52}
    base.update(kw)
    return row(now, **base)


# ------------------------------------------------------------------------------------- windows / freshness

def test_poll_windows():
    # morning window (NBA + NHL), Sunday 10:00 CT
    assert PO.poll_window("nba", T0) == "morning"
    assert PO.poll_window("nhl", T0) == "morning"
    # NBA wave 1 crosses midnight: 23:00 CT and 00:30 CT
    assert PO.poll_window("nba", datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)) == "nba_wave1"
    assert PO.poll_window("nba", datetime(2026, 11, 15, 6, 30, tzinfo=timezone.utc)) == "nba_wave1"
    assert PO.poll_window("nhl", datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)) is None
    # NFL windows: Sunday 08:00 CT, Wednesday 13:00 CT; closed Sunday 14:00 CT
    assert PO.poll_window("nfl", datetime(2026, 11, 15, 14, 0, tzinfo=timezone.utc)) == "nfl_sun"
    assert PO.poll_window("nfl", datetime(2026, 11, 18, 19, 0, tzinfo=timezone.utc)) == "nfl_wed"
    assert PO.poll_window("nfl", datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)) is None
    assert PO.poll_window("nba", datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)) is None
    assert PO.poll_window("mlb", T0) is None


def test_poll_due_and_freshness_limits():
    assert PO.poll_due("nba", T0)                                    # in window: every tick
    baseline = datetime(2026, 11, 15, 20, 40, tzinfo=timezone.utc)   # 14:40 CT, outside windows
    assert PO.poll_due("nba", baseline)                              # the :40 baseline tick
    assert not PO.poll_due("nba", datetime(2026, 11, 15, 20, 10, tzinfo=timezone.utc))
    assert PO.freshness_limit_sec("nba", T0) == 900
    assert PO.freshness_limit_sec("nba", baseline) == 1800


def test_payload_freshness_gate():
    def payload(age_min, now):
        return {"ts": (now - timedelta(minutes=age_min)).isoformat(), "rows": []}
    assert PO.payload_fresh_prd04(payload(10, T0), "nba", T0)        # 600s <= 900 in window
    assert not PO.payload_fresh_prd04(payload(20, T0), "nba", T0)    # 1200s > 900 in window
    out = datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc)
    assert PO.payload_fresh_prd04(payload(20, out), "nba", out)      # 1200s <= 1800 intraday
    assert not PO.payload_fresh_prd04(payload(40, out), "nba", out)
    assert not PO.payload_fresh_prd04(None, "nba", T0)


# ------------------------------------------------------------------------------------- the c = 0 control (board stamps)

def _cons(quotes):
    return SimpleNamespace(book_quotes=quotes)


def test_control_fields_flag_the_best_soft_outlier():
    cons = _cons([("draftkings", -110, -110, 0.52), ("fanduel", -110, -110, 0.51),
                  ("betmgm", -105, -115, 0.50), ("williamhill", 120, -140, 0.47),
                  ("prizepicks", 200, None, 0.40)])                  # DFS: never a candidate, never in the fairs
    out = DP.prd04_control_fields(cons)
    assert out["control_book"] == "williamhill" and out["control_side"] == "over"
    assert out["control_american"] == 120
    assert out["control_ev"] == pytest.approx(0.51 * 2.2 - 1.0, abs=1e-3)   # vs the LOO median fair 0.51
    assert out["control_n_books"] == 3


def test_control_fields_none_when_no_book_beats_loo_by_3pct():
    cons = _cons([("draftkings", -110, -110, 0.52), ("fanduel", -110, -110, 0.51),
                  ("betmgm", -110, -110, 0.50), ("caesars", -110, -110, 0.51)])
    out = DP.prd04_control_fields(cons)
    assert out["control_book"] is None and out["control_ev"] is None
    assert DP.prd04_control_fields(None)["control_book"] is None
    assert DP.prd04_control_fields(_cons([]))["control_book"] is None


# ------------------------------------------------------------------------------------- cohort locking

def test_opener_lock_at_first_sighting_with_full_logging_fields():
    state = PO.empty_state()
    fresh, state, stats = PO.cohort_pass({"rows": [bypass_row()]}, T0, state)
    locks = [x for x in fresh if x["lock_cohort"] == "OPENER"]
    assert len(locks) == 1 and stats["locks_opener"] == 1
    lk = locks[0]
    assert lk["n_books_at_lock"] == 3 and lk["disp_at_lock"] == 0.04
    assert lk["sharp_status_at_lock"] == "opener_bypass" and lk["sharp_tag"] == "OPENER_UNCONFIRMED_SHARP"
    assert lk["open_cons_fair"] == pytest.approx(0.53)
    assert lk["market_age_min_at_lock"] == 0.0
    for f in ("CLV_to_1h", "CLV_to_close", "Control_Delta_CLV", "Adverse_News_Rate"):
        assert lk[f] is None                                          # measured later; the lock is immutable
    # a later, better sighting changes nothing: no re-lock, no INTRADAY duplicate
    fresh2, state, _ = PO.cohort_pass({"rows": [bypass_row(best_over_odds=130)]},
                                      T0 + timedelta(minutes=20), state)
    assert fresh2 == []
    fresh3, state, _ = PO.cohort_pass({"rows": [bypass_row(n_books=6, cons_disp=0.02)]},
                                      T0 + timedelta(minutes=70), state)
    assert fresh3 == []


def test_rejected_at_open_locks_intraday_later():
    state = PO.empty_state()
    wide = bypass_row(cons_disp=0.05, p_sharp_ref_over=0.53, p_sharp_ref_book="pinnacle", n_sharp_ref=1)
    fresh, state, _ = PO.cohort_pass({"rows": [wide]}, T0, state)
    assert fresh == []                                                # disp 0.05 > 0.045 cap at 3 books
    later = row(T0 + timedelta(minutes=50), n_books=6, cons_disp=0.02,
                commence=(T0 + timedelta(minutes=290)).isoformat(), mins_to_start=190.0)
    fresh, state, stats = PO.cohort_pass({"rows": [later]}, T0 + timedelta(minutes=50), state)
    assert stats["locks_intraday"] == 1
    assert fresh[0]["lock_cohort"] == "INTRADAY" and fresh[0]["market_age_min_at_lock"] == 50.0


def test_opener_conditions_individually():
    # overnight NBA needs >= 600 minutes to start
    night = datetime(2026, 11, 16, 5, 0, tzinfo=timezone.utc)         # 23:00 CT
    r = bypass_row(night, mins_to_start=300.0, commence=(night + timedelta(hours=5)).isoformat())
    v = P4.shadow_verdict(r, cohort="opener")
    assert "too_close_to_start" in PO.opener_conditions(r, v, 0.0, night)
    r2 = bypass_row(night, mins_to_start=700.0, commence=(night + timedelta(hours=12)).isoformat())
    v2 = P4.shadow_verdict(r2, cohort="opener")
    assert PO.opener_conditions(r2, v2, 0.0, night) == []
    # the unconfirmed-sharp buffer: market_ev 3.2% is inside the gate band but below the 3.5% buffer
    r3 = bypass_row(market_ev=0.032)
    v3 = P4.shadow_verdict(r3, cohort="opener")
    assert "unconfirmed_market_ev_below_buffer" in PO.opener_conditions(r3, v3, 0.0, T0)
    # dispersion cap relaxes at 4+ books
    r4 = bypass_row(n_books=5, cons_disp=0.055)
    v4 = P4.shadow_verdict(r4, cohort="opener")
    assert "disp_above_cap" not in PO.opener_conditions(r4, v4, 0.0, T0)
    # tier 3 is never opener-eligible
    r5 = bypass_row(stat="assists")
    v5 = P4.shadow_verdict(r5, cohort="opener")
    assert "tier_not_opener_eligible" in PO.opener_conditions(r5, v5, 0.0, T0)


def test_injury_guard_blocks_the_lock():
    assert PO.injury_guard(row(injury_status="QUESTIONABLE")) == "injury_guard_injury_status_questionable"
    assert PO.injury_guard(row(injury_status="GTD")) is not None
    assert PO.injury_guard(row(lineup_status="DTD")) is not None
    assert PO.injury_guard(row(b2b_finished_min_ago=30.0)) == "injury_guard_back_to_back_within_60m"
    assert PO.injury_guard(row(b2b_finished_min_ago=90.0)) is None
    assert PO.injury_guard(row()) is None
    state = PO.empty_state()
    fresh, state, _ = PO.cohort_pass({"rows": [bypass_row(injury_status="DOUBTFUL")]}, T0, state)
    assert fresh == []


def test_control_locks_on_the_same_slate_independent_of_the_model_row():
    state = PO.empty_state()
    ctl_row = row(stat="assists", control_book="williamhill", control_side="over", control_american=120,
                  control_ev=0.12, control_fair_side=0.51, control_n_books=5)
    fresh, state, stats = PO.cohort_pass({"rows": [ctl_row]}, T0, state)
    # the model row is Tier 3 (no lock); the control still locks -- that independence is its purpose
    assert stats["control_locks"] == 1 and stats["locks_opener"] == 0
    rec = fresh[0]
    assert rec["lock_cohort"] == "CONTROL" and rec["control_book"] == "williamhill"
    assert rec["CLV_to_1h"] is None and rec["CLV_to_close"] is None
    fresh2, state, _ = PO.cohort_pass({"rows": [ctl_row]}, T0 + timedelta(minutes=10), state)
    assert fresh2 == []


def test_merge_states_keeps_earliest_sighting_and_stored_locks():
    stored = {"first_seen": {"k": "2026-11-15T15:00:00+00:00"}, "locks": {"k|OPENER": {"record_key": "k|OPENER"}}}
    working = {"first_seen": {"k": "2026-11-15T15:30:00+00:00", "j": "2026-11-15T15:30:00+00:00"},
               "locks": {"k|OPENER": {"record_key": "k|OPENER", "american": 999},
                         "j|INTRADAY": {"record_key": "j|INTRADAY"}}}
    merged = PO.merge_states(stored, working)
    assert merged["first_seen"]["k"] == "2026-11-15T15:00:00+00:00"
    assert "american" not in merged["locks"]["k|OPENER"]              # the stored lock is never displaced
    assert merged["locks"]["j|INTRADAY"]["record_key"] == "j|INTRADAY"
