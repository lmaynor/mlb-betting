"""ev.autopilot: Phase 0 shadow stream gates, age caps, thin-anchor guardrail, closes,
and the dual-bar evaluation.  Pure / offline: synthetic records and rows, no network,
no venue client anywhere in the package (asserted in test_no_order_path_imports)."""
import math
from datetime import datetime, timedelta, timezone

import pytest

from ev.autopilot import closes as closes_mod
from ev.autopilot import evaluate as ev_eval
from ev.autopilot import stream as st
from ev.autopilot.config import ShadowConfig

TS = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 15, 0, 30, tzinfo=timezone.utc)     # 450m after TS: standard window


def _prob(american: float) -> float:
    from mlb_core.odds.utils import american_to_decimal
    return 1.0 / american_to_decimal(american)


def rec(**kw) -> st.CandidateRecord:
    base = dict(ts=TS, sport="nhl", stat="sog", system="NHL_SOG", event_id="ev1",
                matchup="A @ B", commence=START, player="Some Player", player_norm="some player",
                line=2.5, p_cons=0.55, disp=0.005, fair_range=0.02, n_books=5,
                book_ages_min=[5.0, 8.0, 10.0, 12.0, 15.0], ages_known=True,
                nov_over=_prob(-110), nov_under=_prob(-110), nov_age_min=2.0)
    base.update(kw)
    return st.CandidateRecord(**base)


CFG = ShadowConfig()


# ------------------------------------------------------------------ gate funnel
def test_clean_pass_fills_at_ask_with_ruling_ceiling():
    oc = st.evaluate_side(rec(), "yes", CFG)
    assert oc.status == "FILLED"
    ask = _prob(-110)
    p_hat = 0.55 - (0.005 + 0.5 * 0.005)                    # production shrink at age 0
    assert oc.row["limit_price_ceiling"] == pytest.approx(min(ask + 0.01, p_hat / 1.015), abs=1e-6)
    assert oc.row["weighted_fill_price"] == pytest.approx(ask)
    assert oc.row["fill_basis"] == "assumed_at_ask"
    assert oc.row["venue_order_id"] is None and oc.row["simulated"] is True


def test_anchor_thin_rejects():
    assert st.evaluate_side(rec(n_books=3), "yes", CFG).status == "REJ_CONS_THIN"


def test_dispersion_wide_rejects():
    assert st.evaluate_side(rec(disp=0.05), "yes", CFG).status == "REJ_CONS_WIDE"


def test_thin_anchor_guardrail_annexes_exactly_four_book_wide_range():
    oc = st.evaluate_side(rec(n_books=4, fair_range=0.06), "yes", CFG)
    assert oc.status == "ANNEX_THIN_ANCHOR"
    # same anchor, tight range: passes the guardrail and fills
    assert st.evaluate_side(rec(n_books=4, fair_range=0.04), "yes", CFG).status == "FILLED"
    # five books: the guardrail does not apply even with a wide range
    assert st.evaluate_side(rec(n_books=5, fair_range=0.06), "yes", CFG).status == "FILLED"


def test_consensus_age_cap_standard_window():
    assert st.evaluate_side(rec(book_ages_min=[28.0, 30.0, 32.0, 34.0, 36.0]), "yes", CFG).status == "REJ_CONS_STALE"
    # median inside the cap, oldest beyond it: still stale
    assert st.evaluate_side(rec(book_ages_min=[5.0, 8.0, 10.0, 12.0, 50.0]), "yes", CFG).status == "REJ_CONS_STALE"
    # unknown ages fail closed
    assert st.evaluate_side(rec(ages_known=False), "yes", CFG).status == "REJ_CONS_STALE"


def test_consensus_age_cap_late_window_is_tighter():
    late = dict(commence=TS + timedelta(minutes=30))       # inside T-60m, outside the 10m buffer
    assert st.evaluate_side(rec(book_ages_min=[18.0, 20.0, 22.0, 24.0, 26.0], **late), "yes", CFG).status == "REJ_CONS_STALE"
    assert st.evaluate_side(rec(book_ages_min=[5.0, 8.0, 10.0, 12.0, 35.0], **late), "yes", CFG).status == "REJ_CONS_STALE"
    assert st.evaluate_side(rec(book_ages_min=[5.0, 8.0, 10.0, 12.0, 15.0], **late), "yes", CFG).status == "FILLED"


def test_target_quote_stale_is_a_skip_not_a_reject():
    assert st.evaluate_side(rec(nov_age_min=6.0), "yes", CFG).status == "SKIPPED_STALE"
    assert st.evaluate_side(rec(nov_age_min=None), "yes", CFG).status == "SKIPPED_STALE"


def test_price_band_rejects_longshots_before_ev():
    # +400 ask (prob 0.20) against a 0.30 fair: huge nominal EV, band rejects first
    oc = st.evaluate_side(rec(p_cons=0.30, nov_over=_prob(400), nov_under=_prob(-600)), "yes", CFG)
    assert oc.status == "REJ_PRICE_BAND"


def test_ev_floor_and_cap():
    # ask prob 0.537 vs p_hat 0.5425: net EV ~ +1.0% < 1.5%
    assert st.evaluate_side(rec(nov_over=0.537, nov_under=0.50), "yes", CFG).status == "REJ_EV_LOW"
    # p_cons 0.75 vs ask 0.55: net EV ~ +35% > 10% cap (stale-line trap)
    assert st.evaluate_side(rec(p_cons=0.75, nov_over=0.55, nov_under=0.50), "yes", CFG).status == "REJ_EV_CAP"


def test_close_buffer_and_unquoted_side():
    assert st.evaluate_side(rec(commence=TS + timedelta(minutes=5)), "yes", CFG).status == "REJ_GAME_STARTED"
    assert st.evaluate_side(rec(nov_over=None), "yes", CFG) is None


# ------------------------------------------------------------------ stream behaviour
class _Src:
    def __init__(self, snaps):
        self._snaps = snaps

    def snapshots(self, sport):
        return self._snaps.get(sport, [])


def _payload(nov_over_price=105, commence="2026-11-15T00:30:00Z",
             book_lu="2026-11-14T16:52:00Z", nov_lu="2026-11-14T16:58:00Z"):
    """One event, 5 consensus books + novig quoting SOG both sides at the modal line."""
    books = []
    for i in range(5):
        books.append({"key": f"book{i}", "last_update": book_lu,
                      "markets": [{"key": "player_shots_on_goal", "last_update": book_lu,
                                   "outcomes": [{"name": "Over Some Player", "description": "Some Player",
                                                 "price": -115, "point": 2.5},
                                                {"name": "Under Some Player", "description": "Some Player",
                                                 "price": -105, "point": 2.5}]}]})
    books.append({"key": "novig", "last_update": nov_lu,
                  "markets": [{"key": "player_shots_on_goal", "last_update": nov_lu,
                               "outcomes": [{"name": "Over Some Player", "description": "Some Player",
                                             "price": nov_over_price, "point": 2.5},
                                            {"name": "Under Some Player", "description": "Some Player",
                                             "price": -110, "point": 2.5}]}]})
    return [{"id": "ev1", "canonical_event_id": "ev1", "commence_time": commence,
             "home_team": "B", "away_team": "A", "bookmakers": books}]


def test_kill_switch_refuses_unarmed_run():
    with pytest.raises(RuntimeError):
        st.run_stream(_Src({}), ShadowConfig())


def test_first_sighting_dedupes_and_saves_stay_off_by_default():
    payload = _payload()
    saves = _payload()
    saves[0]["bookmakers"] = [dict(b, markets=[dict(m, key="player_saves") for m in b["markets"]])
                              for b in saves[0]["bookmakers"]]
    payload2 = _payload(book_lu="2026-11-14T16:57:00Z", nov_lu="2026-11-14T17:03:00Z")
    src = _Src({"nhl": [(TS, payload), (TS + timedelta(minutes=5), payload2),
                        (TS, saves)]})
    res = st.run_stream(src, ShadowConfig(), dry_run=True)
    filled = [r for r in res["rows"] if r["status"] == "FILLED"]
    assert len(filled) == 1 and filled[0]["stat"] == "sog"          # second pass is a duplicate intent
    assert any(r["status"] == "REJ_DUP_INTENT" for r in res["rows"])
    assert not any(r["stat"] == "saves" for r in res["rows"])       # saves flag off: never evaluated
    res2 = st.run_stream(src, ShadowConfig(enable_saves=True), dry_run=True)
    assert any(r["stat"] == "saves" for r in res2["rows"])


def test_no_order_path_imports():
    """The Phase 0 build must contain no order-submission code path at all: no venue
    client / paper-orders / signing imports and no order verbs in the package source."""
    import pathlib
    pkg = pathlib.Path(__file__).resolve().parents[1] / "ev" / "autopilot"
    src = "\n".join(p.read_text() for p in pkg.glob("*.py"))
    for banned in ("novig.client", "paper_orders", "novig.signing", "novig.exec",
                   "place_order", "submit_order", "create_order", "private_key"):
        assert banned not in src, f"order-path reference in ev/autopilot: {banned}"


# ------------------------------------------------------------------ closes / decomposition
def _filled_row(**kw):
    row = {"status": "FILLED", "system": "NHL_SOG", "league": "nhl", "stat": "sog",
           "event_id": "ev1", "player_norm": "some player", "side": "yes", "line": 2.5,
           "quote_observed_at": TS.isoformat(), "commence": START.isoformat(),
           "game_day": "2026-11-14", "weighted_fill_price": 0.52,
           "gates_snapshot": {"consensus_fair_prob": 0.55, "consensus_age_bucket": "0-15m"},
           "closing_fair_prob": None, "novig_close_prob": None, "novig_t15_prob": None,
           "close_state": None, "t15_state": None, "moved_quote": None, "moved_quote_last": None,
           "entry_dislocation_pct": None, "consensus_drift_pp": None, "consensus_drift_ratio_pct": None,
           "consensus_confirm_clv_pct": None, "consensus_confirm_clv_net_pct": None,
           "same_book_clv_pct": None, "same_book_clv_net_pct": None,
           "same_book_clv_last_pct": None, "same_book_clv_last_net_pct": None}
    row.update(kw)
    return row


def _close_rec(ts, p_cons, nov_over, line=2.5):
    return st.CandidateRecord(ts=ts, sport="nhl", stat="sog", system="NHL_SOG", event_id="ev1",
                              matchup="A @ B", commence=START, player="Some Player",
                              player_norm="some player", line=line, p_cons=p_cons, disp=0.005,
                              fair_range=0.02, n_books=5, book_ages_min=[5.0], ages_known=True,
                              nov_over=nov_over, nov_under=None, nov_age_min=1.0)


def test_closes_sticky_quote_divergence():
    """The ruling's core scenario: consensus moves toward the fill, Novig's own quote
    never moves.  Consensus-confirm CLV is strongly positive; same-book CLV is exactly
    the negative drag.  Both must be computable from the same row."""
    entry = rec()                                            # fill at _prob(-110) ~ 0.5238
    rows = [_filled_row(weighted_fill_price=_prob(-110))]
    series = [entry,
              _close_rec(TS + timedelta(minutes=60), 0.58, _prob(-110)),     # T-15m snapshot region
              _close_rec(START - timedelta(minutes=12), 0.59, _prob(-110))]  # last pre-commence
    out = closes_mod.attach_closes(rows, series, ShadowConfig())
    r = out[0]
    assert r["close_state"] == "ok" and r["t15_state"] == "ok"
    assert r["consensus_confirm_clv_pct"] == pytest.approx((0.59 / _prob(-110) - 1) * 100, abs=1e-3)
    assert r["same_book_clv_pct"] == pytest.approx(0.0, abs=1e-9)            # sticky: quote never moved
    assert r["same_book_clv_net_pct"] == pytest.approx(-0.37, abs=1e-9)     # net of the ruling's drag
    assert r["moved_quote"] is False
    assert r["consensus_drift_pp"] == pytest.approx((0.59 - 0.55) * 100, abs=1e-3)
    assert r["entry_dislocation_pct"] == pytest.approx((0.55 / _prob(-110) - 1) * 100, abs=1e-3)


def test_closes_degrade_honestly():
    rows = [_filled_row()]
    out = closes_mod.attach_closes(rows, [rec()], ShadowConfig())            # no later snapshot at all
    assert out[0]["close_state"] == "no_later_snapshot" and out[0]["consensus_confirm_clv_pct"] is None
    moved = _close_rec(START - timedelta(minutes=12), 0.59, 0.55, line=3.5)  # line moved off the entry point
    out = closes_mod.attach_closes(rows, [rec(), moved], ShadowConfig())
    assert out[0]["close_state"] == "line_moved" and out[0]["same_book_clv_pct"] is None


# ------------------------------------------------------------------ dual-bar evaluation
def _eval_row(day, cons_net, sb_net, drift_pp, moved, system="NHL_SOG", ts_day=None):
    return {"status": "FILLED", "system": system, "game_day": day,
            "quote_observed_at": f"{ts_day or day}T17:00:00+00:00",
            "consensus_confirm_clv_net_pct": cons_net, "consensus_confirm_clv_pct": cons_net + 0.37,
            "same_book_clv_net_pct": sb_net, "same_book_clv_pct": sb_net + 0.37,
            "consensus_drift_pp": drift_pp, "moved_quote": moved,
            "novig_t15_prob": 0.55 if moved is not None else None,
            "gates_snapshot": {"consensus_age_bucket": "0-15m"}}


def test_dual_bar_pass_with_moved_quote_diagnostic_failing():
    rows = []
    for i, day in enumerate(("2026-11-13", "2026-11-14", "2026-11-15")):
        base = 0.10 * i                                        # small day-to-day variation so CIs form
        for _ in range(4):
            rows.append(_eval_row(day, 2.4 + base, 1.00 + base, 0.10, False))
        for _ in range(4):
            rows.append(_eval_row(day, 2.6 + base, 0.20 + base, 0.12, True))   # moved subset below the floor
    res = ev_eval.evaluate_window(rows, ShadowConfig())
    g = res["groups"]["NHL_SOG"]
    assert g["verdict"] == "PASS"
    assert g["moved_quote_check"]["pass"] is False             # diagnostic only: window still passes
    assert g["venue_class"] == "sticky"                        # move rate 50% < 60%


def test_dual_bar_fails_on_same_book_floor_when_fully_sticky():
    rows = []
    for i, day in enumerate(("2026-11-13", "2026-11-14", "2026-11-15")):
        for _ in range(6):
            rows.append(_eval_row(day, 2.5 + 0.05 * i, -0.37, 0.10, False))    # Novig never re-priced
    res = ev_eval.evaluate_window(rows, ShadowConfig())
    g = res["groups"]["NHL_SOG"]
    assert g["verdict"] == "FAIL"
    assert any("same-book" in r for r in g["reasons"])


def test_dual_bar_regime_filter_and_volume():
    rows = [_eval_row("2026-11-10", 3.0, 1.0, 0.2, True, ts_day="2026-11-10")]
    res = ev_eval.evaluate_window(rows, ShadowConfig())
    assert res["filled_pre_regime_excluded"] == 1
    assert res["groups"]["ALL"]["n_signals"] == 0
    assert res["groups"]["ALL"]["volume_bar"]["pass"] is False


# ------------------------------------------- amended spec (RUL-20261116-autopilot-clv-drift)
def _p0_row(day, system, sig, *, status="FILLED", t0_prob=0.55, drift_pp=-0.30,
            cons_net=2.60, sb_net=1.00, entry_disloc=5.00, ts=None, disp=0.010,
            n_books=5, med_age=8.0, nov_age=2.0, mins=300.0, close_prob="auto"):
    row = {"status": status, "system": system, "game_day": day, "signal_id": sig,
           "event_id": "ev1", "player_norm": "some player", "stat": "sog", "side": "yes",
           "line": 2.5, "commence": "2026-11-24T00:30:00+00:00",
           "quote_observed_at": ts or f"{day}T17:00:00+00:00",
           "gates_snapshot": {"consensus_fair_prob": t0_prob, "target_quote_age_min": nov_age,
                              "n_books": n_books, "median_book_age_min": med_age,
                              "mins_to_start": mins, "disp_mad": disp},
           "closing_fair_prob": (t0_prob + drift_pp / 100.0) if close_prob == "auto" else close_prob,
           "consensus_confirm_clv_net_pct": cons_net, "consensus_confirm_clv_pct": cons_net + 0.37,
           "same_book_clv_net_pct": sb_net, "same_book_clv_pct": sb_net + 0.37,
           "consensus_drift_pp": drift_pp, "entry_dislocation_pct": entry_disloc,
           "moved_quote": False, "novig_t15_prob": 0.55}
    return row


def _p0_window(days, *, threes_drift=-1.20):
    """8-day-style window: SOG passes everything, REB passes, THREES fails slice drift."""
    spec = {"NHL_SOG": (8, 2.60, 1.00, -0.30), "NBA_THREES": (6, 2.40, 0.90, threes_drift),
            "NBA_REB": (6, 2.20, 1.10, -0.40)}
    rows = []
    for di, day in enumerate(days):
        jit = 0.05 * (di % 3)
        for system, (per_day, cn, sb, dr) in spec.items():
            for k in range(per_day):
                rows.append(_p0_row(day, system, f"{system}-{day}-{k}",
                                    cons_net=cn + jit, sb_net=sb + jit, drift_pp=dr))
            for k in range(2):      # unfilled gate-pass sightings of other intents
                rows.append(_p0_row(day, system, f"{system}-gate-{day}-{k}", status="REJ_EV_LOW",
                                    drift_pp=0.10))
    return rows


def test_phase0_amended_pass_with_slice_holdout():
    days = [f"2026-11-{d}" for d in range(16, 24)]           # 8 game days, 160 fills
    res = ev_eval.evaluate_phase0(_p0_window(days), ShadowConfig())
    allg = res["groups"]["ALL"]
    assert allg["verdict"] == "PASS"
    assert all(v["pass"] for v in allg["bars"].values())
    assert res["gate_pass_total"] == 160 + 48                # fills + unfilled gate sightings
    assert res["groups"]["NHL_SOG"]["graduation"] == "GRADUATED"
    assert res["groups"]["NBA_REB"]["graduation"] == "GRADUATED"
    threes = res["groups"]["NBA_THREES"]
    assert threes["verdict"] == "FAIL" and threes["graduation"] == "HOLDOUT"
    assert any("bar5" in r for r in threes["reasons"])
    assert allg["fill_selection_diagnostic_pp"] is not None
    assert allg["criterion7_moved_quote"]["pass"] is None    # no bar invented for criterion 7


def test_phase0_amended_span_bar_fails_on_three_days():
    days = ["2026-11-16", "2026-11-17", "2026-11-18"]        # n clears, span does not
    rows = []
    for day in days:
        for system in ("NHL_SOG", "NBA_THREES", "NBA_REB"):
            for k in range(18):
                rows.append(_p0_row(day, system, f"{system}-{day}-{k}", drift_pp=-0.30))
    res = ev_eval.evaluate_phase0(rows, ShadowConfig())
    assert res["groups"]["ALL"]["verdict"] == "FAIL"
    assert any("bar1" in r for r in res["groups"]["ALL"]["reasons"])


def test_phase0_bar4_fails_closed_without_gate_closes():
    days = [f"2026-11-{d}" for d in range(16, 24)]
    rows = _p0_window(days)
    for r in rows:
        if r["status"] != "FILLED":
            r["closing_fair_prob"] = None                   # the live ledger's actual shape
    res = ev_eval.evaluate_phase0(rows, ShadowConfig())
    assert res["gate_pass_with_close"] == 160                # only the fills carry closes
    assert res["groups"]["ALL"]["bars"]["bar4_all_intent_drift"]["pass"] is False


def test_gate_pass_population_rules():
    t1, t2 = TS.isoformat(), (TS + timedelta(minutes=30)).isoformat()
    rows = [
        # A: first sighting stale, second clears -> t0 is the second sighting
        _p0_row("2026-11-14", "NHL_SOG", "A", status="SKIPPED_STALE", ts=t1, nov_age=9.0),
        _p0_row("2026-11-14", "NHL_SOG", "A", status="REJ_EV_LOW", ts=t2),
        # B: 5-book dispersion 0.055 clears the >= 5-book tier (0.060) though the coded funnel rejects it
        _p0_row("2026-11-14", "NHL_SOG", "B", status="REJ_CONS_WIDE", ts=t1, disp=0.055, n_books=5),
        # C: 4-book dispersion 0.055 exceeds the 4-book tier (0.045) -> excluded
        _p0_row("2026-11-14", "NHL_SOG", "C", status="REJ_CONS_WIDE", ts=t1, disp=0.055, n_books=4),
        # D: median age 12m inside T-60m clears the late tier (15m)
        _p0_row("2026-11-14", "NHL_SOG", "D", status="REJ_EV_LOW", ts=t1, med_age=12.0, mins=30.0),
        # E: median age 30m intraday exceeds the 25m tier -> excluded
        _p0_row("2026-11-14", "NHL_SOG", "E", status="REJ_EV_LOW", ts=t1, med_age=30.0, mins=300.0),
        # F: a FILLED row is a population member by construction
        _p0_row("2026-11-14", "NHL_SOG", "F", status="FILLED", ts=t1),
    ]
    pop = ev_eval.gate_pass_population(rows, ShadowConfig())
    by_sig = {r["signal_id"]: r for r in pop}
    assert set(by_sig) == {"A", "B", "D", "F"}
    assert by_sig["A"]["quote_observed_at"] == t2
    assert by_sig["B"]["gate_drift_pp"] == pytest.approx(-0.30, abs=1e-3)


def test_attach_consensus_closes_gate_rows():
    gate_row = _filled_row(status="REJ_EV_LOW")
    gate_row["gates_snapshot"] = {"consensus_fair_prob": 0.55, "consensus_age_bucket": "0-15m"}
    series = [rec(), _close_rec(START - timedelta(minutes=12), 0.59, _prob(-110))]
    out = closes_mod.attach_consensus_closes([gate_row], series, ShadowConfig())
    assert out[0]["gate_close_state"] == "ok"
    assert out[0]["closing_fair_prob"] == pytest.approx(0.59, abs=1e-6)
    moved = closes_mod.attach_consensus_closes(
        [gate_row], [rec(), _close_rec(START - timedelta(minutes=12), 0.59, 0.55, line=3.5)],
        ShadowConfig())
    assert moved[0]["gate_close_state"] == "line_moved" and moved[0]["closing_fair_prob"] is None
    thin_rec = st.CandidateRecord(ts=START - timedelta(minutes=12), sport="nhl", stat="sog",
                                  system="NHL_SOG", event_id="ev1", matchup="A @ B", commence=START,
                                  player="Some Player", player_norm="some player", line=2.5,
                                  p_cons=0.59, disp=0.005, fair_range=0.02, n_books=3,
                                  book_ages_min=[5.0], ages_known=True, nov_over=0.55,
                                  nov_under=None, nov_age_min=1.0)
    thin = closes_mod.attach_consensus_closes([gate_row], [rec(), thin_rec], ShadowConfig())
    assert thin[0]["gate_close_state"] == "close_thin_books" and thin[0]["closing_fair_prob"] is None
    filled = _filled_row()
    filled["closing_fair_prob"] = 0.59
    kept = closes_mod.attach_consensus_closes([filled], series, ShadowConfig())
    assert kept[0]["gate_close_state"] == "attached" and kept[0]["closing_fair_prob"] == 0.59
