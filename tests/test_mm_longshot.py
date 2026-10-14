"""scripts/mm_longshot.py accounting on hand-computed cases (no network, no tape files)."""
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, "scripts")
import mm_longshot as ml  # noqa: E402

OCC = "2026-10-04T20:00:00Z"                    # kick = 17:00Z


def raw(rows):
    """rows: (ticker, created_time, yes_price, count, taker_side, result, strike)"""
    return pd.DataFrame([{"series": "KXNFLRECYDS", "ticker": t, "event": "KXNFLRECYDS-26OCT04AAABBB", "result": res,
                          "occurrence": OCC, "open_time": None, "close_time": None, "mkt_volume": 0,
                          "created_time": ts, "yes_price": p, "count": n, "taker_side": side, "strike": k, "title": ""}
                         for t, ts, p, n, side, res, k in rows])


def test_buying_no_pays_p_minus_y_and_taker_fee_is_quadratic_on_the_no_price():
    d = ml.prep(raw([("KX-AAABBB-P1-50", "2026-10-04T12:00:00Z", 0.10, 10, "no", "no", 49.5),
                     ("KX-AAABBB-P1-50", "2026-10-04T12:01:00Z", 0.10, 10, "no", "yes", 49.5)]))
    win, lose = d.iloc[0], d.iloc[1]
    assert win["gross"] == pytest.approx(0.10) and lose["gross"] == pytest.approx(-0.90)
    fee = 0.07 * 0.90 * 0.10                    # P paid = 1 - p = 0.90
    assert win["taker_fee"] == pytest.approx(fee) and win["taker_net"] == pytest.approx(0.10 - fee)
    assert win["cap"] == pytest.approx(0.90)


def test_pre_kick_flag_uses_occurrence_minus_three_hours():
    d = ml.prep(raw([("T", "2026-10-04T16:59:00Z", 0.3, 1, "no", "no", 1.5),
                     ("T", "2026-10-04T17:01:00Z", 0.3, 1, "no", "no", 1.5)]))
    assert d["mtk"].iloc[0] == pytest.approx(1.0) and d["mtk"].iloc[1] == pytest.approx(-1.0)


@pytest.mark.parametrize("p,label", [(0.02, "[0.02,0.10)"), (0.0999, "[0.02,0.10)"), (0.10, "[0.10,0.20)"),
                                     (0.1999, "[0.10,0.20)"), (0.20, "[0.20,0.35)"), (0.35, "[0.35,0.65)"),
                                     (0.65, "[0.65,0.98]"), (0.98, "[0.65,0.98]")])
def test_bucket_edges_are_lower_inclusive(p, label):
    assert ml.bucketize(pd.Series([p])).iloc[0] == label


def test_out_of_range_prices_are_not_bucketed():
    assert ml.bucketize(pd.Series([0.01, 0.99])).isna().all()


def test_dedupe_keeps_the_richer_copy_of_a_market_and_never_drops_identical_prints():
    a = raw([("M1", "2026-10-04T12:00:00Z", 0.2, 5, "no", "no", 1.5)] * 2 + [("M2", "2026-10-04T12:00:00Z", 0.2, 5, "no", "no", 1.5)])
    b = raw([("M1", "2026-10-04T12:00:00Z", 0.2, 5, "no", "no", 1.5)] * 3)
    out = ml.dedupe_markets([a, b])
    assert (out["ticker"] == "M1").sum() == 3 and (out["ticker"] == "M2").sum() == 1     # M1 from b, M2 from a


def _cell_frame(games):
    rows = []
    for g, (pnl, n) in enumerate(games):
        rows.append({"game": f"G{g}", "ticker": f"T{g}", "taker_net": pnl, "gross": pnl, "n": n, "cap": 0.9, "p": 0.1, "y": 0.0})
    return pd.DataFrame(rows)


def test_cell_is_contract_weighted_in_cents_and_roi_is_on_capital():
    df = _cell_frame([(0.10, 100), (-0.90, 10)])              # +$10 and -$9 -> +$1 over 110 contracts
    r = ml.cell(df, "taker_net", np.random.default_rng(1), B=50)
    assert r["edge_c"] == pytest.approx(100 * 1.0 / 110) and r["roi_pct"] == pytest.approx(100 * 1.0 / (0.9 * 110))
    assert r["contracts"] == 110 and r["games"] == 2 and np.isnan(r["edge_lo"])             # < 4 games: no CI


def test_cluster_bootstrap_ci_brackets_the_point_estimate_and_excludes_zero_for_a_clear_edge():
    df = _cell_frame([(0.05 + 0.01 * (i % 3), 50) for i in range(30)])
    r = ml.cell(df, "taker_net", np.random.default_rng(1), B=400)
    assert r["edge_lo"] <= r["edge_c"] <= r["edge_hi"] and r["edge_lo"] > 0


def test_rung_ratio_uses_the_strike_whose_prekick_vwap_is_closest_to_half():
    rows = []
    for strike, p in ((30.5, 0.90), (50.5, 0.52), (80.5, 0.10)):
        rows.append(("KX-AAABBB-P1-" + str(int(strike)), "2026-10-04T12:00:00Z", p, 10, "no", "no", strike))
    d = ml.add_rung_ratio(ml.prep(raw(rows)))
    assert (d["main_strike"] == 50.5).all()
    assert d.set_index("strike").loc[80.5, "rung_ratio"] == pytest.approx(80.5 / 50.5)


def _vol_tape():
    rows = []
    # M_safe: 5000 pre-kick contracts (>= cutoff 4000 on its own). M_lucky: only 100 pre-kick but a huge in-game run
    # that pushed FINAL volume over the cutoff -> included only because it resolved the way it did.
    rows += [("KX-AAABBB-A-50", "2026-10-04T12:00:00Z", 0.10, 5000, "no", "no", 50.5)]
    rows += [("KX-AAABBB-B-50", "2026-10-04T12:00:00Z", 0.10, 100, "no", "yes", 50.5),
             ("KX-AAABBB-B-50", "2026-10-04T18:00:00Z", 0.60, 4500, "yes", "yes", 50.5)]          # in-game
    d = raw(rows)
    d["mkt_volume"] = [5000, 4600, 4600]
    return d


def test_selection_safe_keeps_only_markets_whose_prekick_volume_clears_the_cutoff():
    d = ml.prep(_vol_tape())
    d.loc[d["ticker"] == "KX-AAABBB-A-50", "mkt_volume"] = 4000          # series cutoff = min final volume = 4000
    safe = ml.selection_safe(d)
    assert set(safe["ticker"]) == {"KX-AAABBB-A-50"}                    # the in-game-lucky market is dropped


def test_per_market_is_one_row_per_market_with_size_weighted_price():
    d = ml.prep(raw([("M1", "2026-10-04T12:00:00Z", 0.10, 30, "no", "no", 1.5),
                     ("M1", "2026-10-04T12:05:00Z", 0.20, 10, "no", "no", 1.5)]))
    m = ml.per_market(d)
    assert len(m) == 1 and m["p"].iloc[0] == pytest.approx((0.10 * 30 + 0.20 * 10) / 40) and m["n"].iloc[0] == 1.0
    assert m["gross"].iloc[0] == pytest.approx(m["p"].iloc[0] - 0.0)


def test_trade_weighted_cell_ignores_print_size():
    df = _cell_frame([(0.10, 1_000_000), (-0.90, 1)])
    big = ml.cell(df, "taker_net", np.random.default_rng(1), B=20)
    flat = ml.cell(df, "taker_net", np.random.default_rng(1), B=20, wcol="one")
    assert big["edge_c"] > 9.9 and flat["edge_c"] == pytest.approx(100 * (0.10 - 0.90) / 2)

