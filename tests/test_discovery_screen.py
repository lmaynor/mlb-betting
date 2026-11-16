"""RES-08 discovery screen: unit tests on synthetic frames (no network, no bucket).

Covers the panel builder's two-sided/modal-line/coverage logic, the fail-closed
settle join (unresolved, ambiguity, team mismatch, DNP void, push), the
walk-forward engine on a planted-signal cell vs a pure-noise cell, and the
pre-registered verdict thresholds.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import discovery_screen as DS  # noqa: E402


def an_rows(player="Test Player", book=68, line=10.5, over=-110, under=-110,
            date="2025-01-15", start="2025-01-16T00:00:00.000Z", event=1, market="points"):
    base = dict(date=date, event_id=event, player_name=player, team_abbr="AAA",
                home_abbr="AAA", away_abbr="BBB",
                start_time=start, market=market, is_milestone=False, book_id=book)
    return [dict(base, side="over", line=line, odds=over),
            dict(base, side="under", line=line, odds=under)]


def test_panel_modal_line_and_median():
    rows = []
    # 4 real books at line 10.5, one at 11.5: modal line wins, median is de-vigged
    for b in (68, 69, 75, 79):
        rows += an_rows(book=b, line=10.5, over=-110, under=-110)
    rows += an_rows(book=123, line=11.5, over=-110, under=-110)
    panel, counts = DS.build_panel([pd.DataFrame(rows)], min_books=4)
    assert len(panel) == 1
    r = panel.iloc[0]
    assert r["line"] == 10.5 and r["n_books"] == 4
    assert r["p_mkt"] == pytest.approx(0.5)
    assert r["date"] == "2025-01-16"          # UTC start date, not the slate date
    assert counts["below_min_books"] == 0


def test_panel_excludes_consensus_open_and_one_sided():
    rows = []
    for b in (15, 30):                        # consensus + open are not coverage
        rows += an_rows(book=b)
    for b in (68, 69, 75):                    # only 3 real books
        rows += an_rows(book=b)
    panel, counts = DS.build_panel([pd.DataFrame(rows)], min_books=4)
    assert len(panel) == 0 and counts["below_min_books"] == 1


def test_panel_book_must_be_two_sided_at_one_line():
    rows = an_rows(book=68) + an_rows(book=69) + an_rows(book=75)
    rows += [dict(date="2025-01-15", event_id=1, player_name="Test Player", team_abbr="AAA",
                  home_abbr="AAA", away_abbr="BBB",
                  start_time="2025-01-16T00:00:00.000Z", market="points",
                  is_milestone=False, book_id=79, side="over", line=10.5, odds=-110)]
    panel, _ = DS.build_panel([pd.DataFrame(rows)], min_books=4)
    assert len(panel) == 0                    # book 79 has no under -> 3 books only


def _panel_row(actual_line=10.5, **kw):
    base = dict(date="2025-01-16", event_id=1, player_key="test player",
                player_name="Test Player", team_abbr="AAA", home_abbr="AAA",
                away_abbr="BBB", market="points",
                line=actual_line, n_books=5, p_mkt=0.5, dispersion=0.01,
                med_over_dec=1.91, med_under_dec=1.91, best_over_dec=1.95,
                best_under_dec=1.95, hold_med=0.045)
    base.update(kw)
    return base


def _actuals(rows):
    return pd.DataFrame(rows)


def test_settle_fail_closed():
    panel = pd.DataFrame([_panel_row()])
    # exact one master row, team agrees -> settles as over (12 > 10.5)
    a = _actuals([dict(date="2025-01-16", name_key="test player", team_abbr="AAA",
                       played=True, points=12)])
    out, c = DS.settle(panel, a, "points")
    assert c["settled"] == 1 and out.iloc[0]["y"] == 1.0
    # no master row -> unresolved
    out, c = DS.settle(panel, a.assign(date="2025-01-17"), "points")
    assert c["unresolved"] == 1 and np.isnan(out.iloc[0]["y"])
    # duplicated master rows -> ambiguous, never force-matched
    a2 = pd.concat([a, a], ignore_index=True)
    out, c = DS.settle(panel, a2, "points")
    assert c["ambiguous"] == 1 and np.isnan(out.iloc[0]["y"])
    # ...unless the event team pair disambiguates them via the game map
    a3 = a.assign(game_id="g1")
    a4 = a.assign(game_id="g2")
    a34 = pd.concat([a3, a4], ignore_index=True)
    gm = {"g1": frozenset({"AAA", "BBB"}), "g2": frozenset({"CCC", "DDD"})}
    out, c = DS.settle(panel, a34, "points", game_map=gm)
    assert c["settled"] == 1 and out.iloc[0]["y"] == 1.0
    # candidate team outside the event pair -> mismatch, dropped
    out, c = DS.settle(panel, a.assign(team_abbr="ZZZ"), "points")
    assert c["team_mismatch"] == 1 and np.isnan(out.iloc[0]["y"])
    # candidate on the OTHER event team still settles (AN team_abbr is the
    # player's current team, not his game-time team)
    out, c = DS.settle(panel, a.assign(team_abbr="BBB"), "points")
    assert c["settled"] == 1 and out.iloc[0]["y"] == 1.0
    # DNP -> void
    out, c = DS.settle(panel, a.assign(played=False), "points")
    assert c["void_dnp"] == 1 and np.isnan(out.iloc[0]["y"])
    # push -> excluded
    out, c = DS.settle(panel.assign(line=12.0), a, "points")
    assert c["push"] == 1 and np.isnan(out.iloc[0]["y"])


def _synthetic_cell(n_dates=120, per_date=30, signal=0.0, seed=3):
    """Panel-shaped frame with outcomes drawn from the market plus an optional
    trailing-form signal: with signal > 0 the player's z10 genuinely moves the
    over probability, so a correct engine must find c > 0."""
    rng = np.random.default_rng(seed)
    rows = []
    dates = pd.date_range("2025-01-01", periods=n_dates, freq="D").strftime("%Y-%m-%d")
    for di, d in enumerate(dates):
        for j in range(per_date):
            z = rng.normal()
            p_mkt = float(rng.uniform(0.3, 0.7))
            lm = np.log(p_mkt / (1 - p_mkt))
            p_true = 1 / (1 + np.exp(-(lm + signal * z)))
            y = float(rng.random() < p_true)
            rows.append(dict(date=d, event_id=di * 100 + j // 6, player_key=f"p{j}",
                             player_name=f"P{j}", team_abbr="AAA", market="points",
                             line=10.5, n_books=5, p_mkt=p_mkt, dispersion=0.01,
                             med_over_dec=1.91, med_under_dec=1.91,
                             best_over_dec=1.98, best_under_dec=1.98, hold_med=0.045,
                             y=y, z10=z, z20=z,
                             exc10=float(1 / (1 + np.exp(-(signal * z + rng.normal(0, 0.7)))))))
    return pd.DataFrame(rows)


def test_walk_forward_recovers_planted_signal():
    df = _synthetic_cell(signal=1.2)
    oos = DS.walk_forward(df)
    assert len(oos) > 1000
    m = DS.score_cell(oos)
    assert m["c"] > 0.25 and m["t_c"] > 2.0
    assert m["d_ll_blend"] < 0


def test_walk_forward_noise_has_no_signal():
    df = _synthetic_cell(signal=0.0)
    oos = DS.walk_forward(df)
    m = DS.score_cell(oos)
    # On pure noise the blend must not improve log loss and the cell must not
    # pass. (The raw c point estimate is scale-unstable when the signal's logit
    # deviation has near-zero variance; the pass standard's joint gates are the
    # operative protection, exactly as on real cells.)
    assert m["d_ll_blend"] >= -0.0005
    assert DS.verdict(m)[0] != "pass"


def test_verdict_thresholds():
    base = dict(n_oos=5000, n_dates=100, n_gated=500, c=0.30, t_c=3.0,
                d_ll_blend=-0.002, ece_blend=0.010, ctrl_sub_roi=0.03)
    assert DS.verdict(base)[0] == "pass"
    assert DS.verdict(dict(base, n_oos=500))[0] == "insufficient_data"
    assert DS.verdict(dict(base, n_gated=100))[0] == "insufficient_data"
    assert DS.verdict(dict(base, c=0.10))[0] == "kill"
    assert DS.verdict(dict(base, ece_blend=0.020))[0] == "kill"
    assert DS.verdict(dict(base, ctrl_sub_roi=0.0))[0] == "kill"
    assert DS.verdict(dict(base, d_ll_blend=0.001))[0] == "kill"
