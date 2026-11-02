"""Point-in-time replay of real Kalshi combos (ev/kalshi_rfq_replay.py): no look-ahead, grading arithmetic, public-API pull."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from ev.kalshi_rfq_pricer import leg_from_row
from ev import kalshi_rfq_replay as rp

T0 = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc).timestamp()


def row(ticker, game, p=0.5, ts=T0, n=4, stat="rec_yds"):
    return {"ticker": ticker, "game_key": f"nfl:2026-10-25:{game}", "stat": stat, "strike": 49.5, "p_over": p, "n_books": n,
            "disp": 0.01, "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "consensus_src": "exact",
            "player_norm": ticker.lower()}


def snap(ts, *rows_):
    return ts, [leg_from_row(r, ts) for r in rows_]


def combo(tickers, created, y=0.2, result="no", sides=None, vol=10.0):
    return {"ticker": "KXMVE-" + "-".join(t[-3:] for t in tickers), "close_time": "2026-10-25T23:00:00Z", "created_time": created,
            "volume_fp": str(vol), "last_price_dollars": str(y), "result": result,
            "legs": [[t, (sides or ["yes"] * len(tickers))[i]] for i, t in enumerate(tickers)]}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def test_book_at_never_uses_a_snapshot_from_the_future_or_a_stale_one():
    s = rp.SnapshotStore([snap(T0, row("A1", "X@Y")), snap(T0 + 1800, row("A1", "X@Y", p=0.9, ts=T0 + 1800))])
    assert s.book_at(T0 - 1) is None                                              # before any snapshot
    assert s.book_at(T0 + 1799).get("A1").p_cons_yes == 0.5                       # the 30-min snapshot is still the future
    assert s.book_at(T0 + 1800).get("A1").p_cons_yes == 0.9
    assert s.book_at(T0 + 1800 + rp.MAX_SNAPSHOT_AGE_SEC + 1) is None            # too old to trust


def test_replay_prices_with_the_snapshot_before_creation_and_grades_the_lay():
    rows_ = [row("A1", "G1@H1"), row("B2", "G2@H2"), row("C3", "G3@H3")]
    s = rp.SnapshotStore([snap(T0, *rows_)])
    rec = rp.replay_one(combo(["A1", "B2", "C3"], iso(T0 + 120), y=0.20, result="no"), s)
    assert rec["n_priced"] == 3 and rec["n_unpriced"] == 0 and not rec["same_game"]
    assert rec["p_true"] == pytest.approx(0.125)                                  # three independent 0.5 legs
    assert rec["edge_ex_ante"] == pytest.approx(0.20 - 0.125)
    assert rec["lay_pnl_at_market"] == pytest.approx(0.20 - rp.fee(0.80))         # NO won: collect y, minus the fee on the NO price
    assert rec["quoted"] and rec["no_bid"] is not None
    assert rec["filled"] == (rec["no_bid"] >= 0.80 - 1e-9)


def test_unpriced_legs_make_p_an_upper_bound_and_are_counted():
    s = rp.SnapshotStore([snap(T0, row("A1", "G1@H1"), row("B2", "G2@H2"))])
    rec = rp.replay_one(combo(["A1", "B2", "ZZ9"], iso(T0 + 60)), s)
    assert rec["n_priced"] == 2 and rec["n_unpriced"] == 1
    assert rec["p_true"] == pytest.approx(0.25)                                   # the unknown leg contributes a factor of 1


def test_no_snapshot_before_the_combo_means_no_record():
    s = rp.SnapshotStore([snap(T0 + 600, row("A1", "G1@H1"))])
    assert rp.replay_one(combo(["A1", "B2"], iso(T0)), s) is None


def test_grade_arithmetic_for_a_yes_result_and_a_fill():
    r = rp.grade({"yes_price": 0.30, "yes_won": True, "p_true": 0.10, "quoted": True, "no_bid": 0.75})
    assert r["lay_pnl_at_market"] == pytest.approx(0.30 - 1.0 - rp.fee(0.70))
    assert r["filled"] is True                                                    # 0.75 >= 1 - 0.30 = 0.70
    assert r["pnl_at_our_bid"] == pytest.approx(0.0 - 0.75 - rp.fee(0.75))        # YES won: we lose the whole NO price
    no = rp.grade({"yes_price": 0.30, "yes_won": False, "p_true": None, "quoted": True, "no_bid": 0.65})
    assert no["filled"] is False and no["pnl_at_our_bid"] is None and no["edge_ex_ante"] is None


def test_from_cycle_frames_stamps_each_file_with_its_own_ts():
    df1 = pd.DataFrame([row("A1", "G1@H1", ts=T0)])
    df2 = pd.DataFrame([row("A1", "G1@H1", p=0.7, ts=T0 + 1800)])
    s = rp.SnapshotStore.from_cycle_frames([df2, df1, None])
    assert len(s) == 2 and s.tickers == {"A1"}
    assert s.book_at(T0 + 10).get("A1").p_cons_yes == 0.5


def test_load_combos_keeps_only_traded_yes_no_results(tmp_path):
    p = tmp_path / "c.jsonl"
    lines = [combo(["A1", "B2"], iso(T0)), dict(combo(["A1", "B2"], iso(T0)), volume_fp="0"),
             dict(combo(["A1", "B2"], iso(T0)), result="scalar"), {"_window": "w", "scanned": 5, "truncated": True}]
    p.write_text("\n".join(json.dumps(x) for x in lines))
    combos, windows = rp.load_combos(str(p))
    assert len(combos) == 1 and windows == [{"_window": "w", "scanned": 5, "truncated": True}]


def test_pull_filters_non_sports_and_reports_truncation():
    sports = {"ticker": "M1", "mve_selected_legs": [{"market_ticker": "KXNFLREC-X", "side": "yes"}], "volume_fp": "5",
              "close_time": "c", "created_time": "t", "last_price_dollars": "0.1", "result": "no"}
    crypto = dict(sports, ticker="M2", mve_selected_legs=[{"market_ticker": "KXEURUSD15M-X", "side": "no"}])
    mixed = dict(sports, ticker="M3", mve_selected_legs=[{"market_ticker": "KXNFLREC-X", "side": "yes"},
                                                         {"market_ticker": "KXEURUSD15M-X", "side": "no"}])
    pages = iter([{"markets": [sports, crypto, mixed], "cursor": "next"}])
    out = list(rp.pull_combos(datetime(2026, 10, 25, tzinfo=timezone.utc), datetime(2026, 10, 25, 4, tzinfo=timezone.utc),
                              max_pages=1, get=lambda params: next(pages), sleep=lambda s: None))
    assert [o["ticker"] for o in out if "ticker" in o] == ["M1"]                  # crypto-only and mixed-sport combos are dropped
    assert out[-1] == {"_window": "2026-10-25T00:00:00+00:00", "scanned": 3, "truncated": True}


def test_summarize_groups_fully_priced_vs_bounded():
    base = dict(day="2026-10-25", yes_price=0.2, yes_won=False, quoted=False, quote_reason="REJ_X", filled=False,
                pnl_at_our_bid=None, lay_pnl_at_market=0.2)
    recs = [dict(base, n_unpriced=0, p_true=0.1, edge_ex_ante=0.1), dict(base, n_unpriced=2, p_true=0.15, edge_ex_ante=0.05),
            dict(base, n_unpriced=3, p_true=None, edge_ex_ante=None)]
    s = rp.summarize(recs)
    assert (s["combos"], s["priced_any_leg"], s["fully_priced"], s["bounded"]) == (3, 2, 1, 1)
    assert s["fully_priced_stats"]["edge_ex_ante"]["mean"] == 0.1 and s["by_day"]["2026-10-25"]["combos"] == 3


# ------------------------------------------------------------------------------------------ rung curve + protected twins
def cyc(rows_, ts=T0):
    cols = [dict(r, exact_n_books=r["n_books"], team=r.get("team", "BUF"), player=r["player_norm"]) for r in rows_]
    return pd.DataFrame(cols)


def prop_row(series, strike, p, n=4, player="jim smith", ts=T0):
    n_plus = int(strike + 0.5)
    t = f"{series}-26OCT25BUFKC-BUFJSMITH12-{n_plus}"
    return {"ticker": t, "game_key": "nfl:2026-10-25:BUF@KC", "stat": "rec_yds", "strike": strike, "p_over": p, "n_books": n,
            "disp": 0.01, "ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(), "consensus_src": "exact",
            "player_norm": player}


def test_other_rung_is_interpolated_from_the_snapshots_own_curve():
    frame = cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])
    s = rp.SnapshotStore.from_cycle_frames([frame])
    book = s.book_at(T0 + 60)
    banked = book.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50")
    assert banked is not None and banked.p_cons_yes == pytest.approx(0.60) and not banked.pooled
    mid = book.get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-60")                     # 59.5 was never swept
    assert mid is not None and mid.pooled and 0.30 < mid.p_cons_yes < 0.60


def test_protected_twin_resolves_to_the_base_series_fair_value():
    from ev.kalshi_props import parse_prop_ticker, ParsedPropTicker
    p = parse_prop_ticker("KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-50")
    assert isinstance(p, ParsedPropTicker) and p.stat == "rec_yds" and p.strike == 49.5
    s = rp.SnapshotStore.from_cycle_frames([cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])])
    leg = s.book_at(T0 + 60).get("KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-50")
    assert leg is not None and leg.p_cons_yes == pytest.approx(0.60)
    assert s.may_price([["KXNFLRECYDSPROT-26OCT25BUFKC-BUFJSMITH12-60", "yes"]]) and not s.may_price([["KXNBAPTS-X-Y-1", "yes"]])


def test_curve_never_extrapolates_far_past_its_anchors():
    s = rp.SnapshotStore.from_cycle_frames([cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])])
    assert s.book_at(T0 + 60).get("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-200") is None


# ------------------------------------------------------------------------------------------ started games, max age, audit
def test_a_leg_whose_game_kicked_off_is_not_priced_from_a_pregame_snapshot():
    r = prop_row("KXNFLRECYDS", 49.5, 0.60)
    frame = cyc([r]); frame["commence"] = datetime.fromtimestamp(T0 + 900, timezone.utc).isoformat()
    s = rp.SnapshotStore.from_cycle_frames([frame])
    tk = "KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50"
    assert s.book_at(T0 + 600).get(tk) is not None                               # 5 min before kickoff: fine
    assert s.book_at(T0 + 1000).get(tk) is None                                  # after kickoff: unpriceable, not stale-priced
    assert s.started_games(T0 + 1000, 0) == frozenset({"nfl:2026-10-25:BUF@KC"})


def test_max_age_is_a_parameter():
    s = rp.SnapshotStore([snap(T0, row("A1", "X@Y"))])
    assert s.book_at(T0 + 3000) is None and s.book_at(T0 + 3000, 7200) is not None


def test_audit_labels_every_outcome():
    frame = cyc([prop_row("KXNFLRECYDS", 49.5, 0.60), prop_row("KXNFLRECYDS", 69.5, 0.30)])
    frame["commence"] = datetime.fromtimestamp(T0 + 5000, timezone.utc).isoformat()
    s = rp.SnapshotStore.from_cycle_frames([frame])
    mk = lambda tk, created: {"created_time": iso(created), "legs": [[tk, "yes"]]}
    combos = [mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 + 60), mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-60", T0 + 60),
              mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 + 9000), mk("KXNFLSPREAD-26OCT25BUFKC-KC3", T0 + 60),
              mk("KXNFLRECYDS-26OCT25BUFKC-BUFJSMITH12-50", T0 - 5000)]
    a = rp.audit_legs(combos, s)
    assert a["KXNFLRECYDS"]["HIT_exact"] == 1 and a["KXNFLRECYDS"]["HIT_curve"] == 1
    assert a["KXNFLRECYDS"]["NO_SNAPSHOT"] == 2                                  # one too old (age gate), one before the first snapshot
    assert a["KXNFLSPREAD"] == {"MISS_no_game_line": 1}                          # a covered league, but no game lines are banked
