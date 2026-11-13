"""Tests for the B1 blowout-minutes build inside Arm S (nba.minutes_restriction.blowout).

Synthetic fixtures only. Coverage: closing-spread consensus rules (fresh window, modal
line, book filtering, min books), perspective signing on attach, pull-speed shrinkage and
dev-season isolation, the B1 feature matrix (incl. the abs_spread x starter interaction
and the no-imputation rule), walk-forward split honesty, Argon's auto-kill bar
boundaries, and the guarded CLI (no --execute -> nothing is read).
"""
from __future__ import annotations

import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nba.minutes_restriction import arm_s, blowout, cases


# -----------------------------------------------------------------------------------------------
# Synthetic quotes / spreads

def _quote(event_id, day, home, away, book, side, line, *, updated, is_open=False,
           book_kind="book", scheduled=None):
    return {"date": day, "market": "spread", "event_id": event_id,
            "scheduled": scheduled or f"{day} 00:00:00", "season_type": "REG",
            "event_status": "closed", "home": home, "away": away, "side": side,
            "line": line, "book": book, "book_kind": book_kind, "odds": -110,
            "is_main": True, "is_open": is_open, "updated": updated}


def _quotes_one_game():
    """LAL (home) vs BOS on 2025-01-15: modal home line -5.5 from 3 books.
    `scheduled` is the UTC tip (2025-01-16 00:00), as in the real partitions."""
    day, nxt = "2025-01-15", "2025-01-15 23:40:00"
    sched = "2025-01-16 00:00:00"
    return pd.DataFrame([
        _quote(1, day, "LAL", "BOS", "DraftKings", "home", -5.5, updated=nxt, scheduled=sched),
        _quote(1, day, "LAL", "BOS", "FanDuel", "away", 5.5, updated=nxt, scheduled=sched),   # home -5.5
        _quote(1, day, "LAL", "BOS", "BetMGM", "home", -5.5, updated=nxt, scheduled=sched),
        _quote(1, day, "LAL", "BOS", "Caesars", "home", -7.5, updated=nxt, scheduled=sched),  # off-modal
        _quote(1, day, "LAL", "BOS", "BetRivers", "home", -5.5,
               updated="2025-01-10 12:00:00", scheduled=sched),                                # stale: excluded
        _quote(1, day, "LAL", "BOS", "DraftKings", "home", -9.0,
               updated=nxt, is_open=True, scheduled=sched),                                    # opener: excluded
        _quote(1, day, "LAL", "BOS", "Consensus", "home", -6.5,
               updated=nxt, book_kind="consensus", scheduled=sched),                           # pseudo-book: excluded
    ])


def test_consensus_modal_line_fresh_books_only():
    out = blowout.consensus_closing_spreads(_quotes_one_game())
    assert len(out) == 1
    row = out.iloc[0]
    assert (row["home"], row["away"], row["game_day"]) == ("LAL", "BOS", "2025-01-15")
    assert row["closing_spread_home"] == pytest.approx(-5.5)
    assert row["n_books"] == 3


def test_consensus_fails_closed_below_min_books():
    quotes = _quotes_one_game()
    quotes = quotes[quotes["book"] != "BetMGM"]          # modal line now has only 2 books
    assert not len(blowout.consensus_closing_spreads(quotes))
    relaxed = blowout.consensus_closing_spreads(quotes, min_books=2)
    assert len(relaxed) == 1 and relaxed.iloc[0]["n_books"] == 2


def _case(team, opp, game_date, closing=None):
    return {"case_id": f"c-{team}-{game_date}", "player": "P", "team": team,
            "opponent": opp, "game_id": 1, "game_date": game_date,
            "season_start": 2024, "season_label": "2024-25", "case_class": "return",
            "games_missed": 4, "missed_band": "4-9", "is_injury_return": True,
            "is_game_two": False, "baseline_minutes": 30.0, "realized_minutes": 24.0,
            "realized_ratio": 0.8, "designation": None, "injury_class": "injury_illness",
            "is_back_to_back": False, "started": True, "closing_spread": closing}


def test_attach_signs_spread_from_case_team_perspective():
    spreads = blowout.consensus_closing_spreads(_quotes_one_game())
    frame = pd.DataFrame([_case("LAL", "BOS", date(2025, 1, 15)),
                          _case("BOS", "LAL", date(2025, 1, 15))])
    out = blowout.attach_closing_spread(frame, spreads)
    assert out.iloc[0]["closing_spread"] == pytest.approx(-5.5)   # home, favored
    assert out.iloc[1]["closing_spread"] == pytest.approx(5.5)    # away, underdog
    assert out.iloc[0]["abs_closing_spread"] == pytest.approx(5.5)


def test_attach_leaves_unmatched_nan():
    spreads = blowout.consensus_closing_spreads(_quotes_one_game())
    frame = pd.DataFrame([_case("MIA", "NYK", date(2025, 1, 15))])
    out = blowout.attach_closing_spread(frame, spreads)
    assert pd.isna(out.iloc[0]["closing_spread"])


# -----------------------------------------------------------------------------------------------
# Pull-speed random effect

def _team_game_rows(team, opp, gid, day, season_end, abs_spread, starter_minutes):
    rows = []
    for i in range(5):
        rows.append({"game_id": gid, "season": season_end, "season_type": 2,
                     "game_date": day, "team_abbreviation": team,
                     "athlete_display_name": f"{team} Starter {i}",
                     "minutes": float(starter_minutes), "starter": True,
                     "did_not_play": False, "reason": None, "home_away": "home"})
    rows.append({"game_id": gid, "season": season_end, "season_type": 2,
                 "game_date": day, "team_abbreviation": opp,
                 "athlete_display_name": f"{opp} Player", "minutes": 30.0,
                 "starter": True, "did_not_play": False, "reason": None,
                 "home_away": "away"})
    return rows


def _pull_speed_fixture(extra_test_rows=False):
    rows, spread_rows = [], []
    gid = 1000
    for team, low_min, high_min in (("LAL", 34.0, 26.0), ("BOS", 32.0, 32.0)):
        for rep in range(30):
            for spread, mins in ((2.0, low_min), (12.0, high_min)):
                gid += 1
                day = (date(2022, 10, 20) + timedelta(days=rep * 4 + (0 if spread < 9.5 else 2))).isoformat()
                rows += _team_game_rows(team, "MIA", gid, day, 2023, spread, mins)
                spread_rows.append({"home": team, "away": "MIA", "game_day": day,
                                    "closing_spread_home": -spread, "n_books": 4})
    if extra_test_rows:
        # Test-season rows with the OPPOSITE slope for LAL: must never move the effect.
        for rep in range(30):
            gid += 1
            day = (date(2024, 10, 20) + timedelta(days=rep * 2)).isoformat()
            rows += _team_game_rows("LAL", "MIA", gid, day, 2025, 12.0, 40.0)
            spread_rows.append({"home": "LAL", "away": "MIA", "game_day": day,
                                "closing_spread_home": -12.0, "n_books": 4})
    return pd.DataFrame(rows), pd.DataFrame(spread_rows)


def test_pull_speed_orders_teams_and_shrinks():
    box, spreads = _pull_speed_fixture()
    effects, diag = blowout.team_pull_speed(box, spreads)
    assert effects["LAL"] < effects["BOS"] < 0 + 1e-9 or effects["LAL"] < effects["BOS"]
    assert effects["LAL"] < -0.3                       # steep raw slope, shrunk but alive
    # Flat team: raw slope 0, shrunk toward the (negative) league slope -- it must land
    # strictly between its raw slope and the steep team's effect.
    assert effects["LAL"] < effects["BOS"] < 0.0
    lal = diag[diag["team"] == "LAL"].iloc[0]
    assert lal["n_games"] == 60
    assert abs(lal["shrunk_slope"]) < abs(lal["raw_slope"])  # shrinkage toward league


def test_pull_speed_small_sample_gets_league_slope():
    box, spreads = _pull_speed_fixture()
    effects, diag = blowout.team_pull_speed(box, spreads, min_games=1000)
    league = diag["league_slope"].iloc[0]
    assert all(v == pytest.approx(league) for v in effects.values())


def test_pull_speed_ignores_test_seasons():
    box_dev, spreads = _pull_speed_fixture()
    box_all, _ = _pull_speed_fixture(extra_test_rows=True)
    eff_dev, _ = blowout.team_pull_speed(box_dev, spreads)
    eff_all, _ = blowout.team_pull_speed(box_all, spreads)
    assert eff_all["LAL"] == pytest.approx(eff_dev["LAL"])


# -----------------------------------------------------------------------------------------------
# Feature matrix

def test_features_b1_columns_and_interaction():
    frame = pd.DataFrame([_case("LAL", "BOS", date(2025, 1, 15), closing=-11.0),
                          {**_case("BOS", "LAL", date(2025, 1, 15), closing=3.0),
                           "started": False, "case_id": "c2"}])
    out = blowout.build_features_b1(frame, {"LAL": -0.8, "BOS": -0.2},
                                    league_pull_speed=-0.5)
    assert list(out.columns) == blowout.B1_FEATURE_COLUMNS
    assert out.iloc[0]["abs_spread_x_starter"] == pytest.approx(11.0)
    assert out.iloc[1]["abs_spread_x_starter"] == pytest.approx(0.0)
    assert out.iloc[0]["team_pull_speed"] == pytest.approx(-0.8)
    assert out.iloc[1]["closing_spread"] == pytest.approx(3.0)


def test_features_b1_refuses_unmatched_cases():
    frame = pd.DataFrame([_case("LAL", "BOS", date(2025, 1, 15), closing=None)])
    with pytest.raises(ValueError, match="without a closing spread"):
        blowout.build_features_b1(frame, {})


# -----------------------------------------------------------------------------------------------
# Walk-forward + grading bars

def _split_cases():
    rows = []
    for season in arm_s.DEV_SEASONS + arm_s.TEST_SEASONS:
        for i, spread in enumerate((-2.0, -6.0, -11.0, -14.0)):
            row = _case("LAL", "BOS", date(season, 11, 1 + i), closing=spread)
            row.update({"case_id": f"{season}-{i}", "season_start": season,
                        "season_label": cases.season_label(season),
                        "games_missed": 2 + i, "missed_band": cases.missed_band(2 + i),
                        "realized_ratio": 0.95 - 0.01 * abs(spread),
                        "realized_minutes": 30.0 * (0.95 - 0.01 * abs(spread))})
            rows.append(row)
    return pd.DataFrame(rows)


def test_walk_forward_b1_fits_dev_only():
    frame = _split_cases()
    preds = blowout.walk_forward_b1(frame, pd.DataFrame(), pd.DataFrame())
    assert set(preds["season_start"]) == set(arm_s.TEST_SEASONS)
    assert preds.attrs["fit_seasons"] == sorted(arm_s.DEV_SEASONS)
    preds2 = blowout.walk_forward_b1(frame, pd.DataFrame(), pd.DataFrame())
    pd.testing.assert_frame_equal(preds, preds2)


def _pred_pair(before_err, after_err, abs_spread, case_id="x"):
    base = {"case_id": case_id, "realized_minutes": 20.0, "is_game_two": False,
            "abs_closing_spread": abs_spread}
    before = pd.DataFrame([{**base, "pred_minutes": 20.0 + before_err}])
    after = pd.DataFrame([{**base, "pred_minutes": 20.0 + after_err}])
    return before, after


def test_grade_b1_exact_bars_pass():
    b1, a1 = _pred_pair(6.0, 5.85, 3.0, "low")     # low cut: +0.15
    b2, a2 = _pred_pair(6.0, 5.5, 12.0, "high")    # high cut: +0.50; overall +0.325
    report = blowout.grade_b1(pd.concat([b1, b2]), pd.concat([a1, a2]))
    assert report["verdict"] == "PASS"
    assert report["cuts"]["abs_spread_ge_9_5"]["improvement"] == pytest.approx(0.5)
    assert report["bars"]["overall_mae_improvement"]["pass"] is True


def test_grade_b1_high_cut_shortfall_auto_kills():
    b1, a1 = _pred_pair(6.0, 5.0, 3.0, "low")      # low cut: +1.00
    b2, a2 = _pred_pair(6.0, 5.66, 12.0, "high")   # high cut: +0.34 < 0.35
    report = blowout.grade_b1(pd.concat([b1, b2]), pd.concat([a1, a2]))
    assert report["verdict"] == "AUTO-KILLED"
    assert report["bars"]["overall_mae_improvement"]["pass"] is True
    assert report["bars"]["high_spread_mae_improvement"]["pass"] is False
    assert "AUTO-KILLED" in blowout.render_markdown_b1(report)


def test_grade_b1_empty_cut_fails_closed():
    before, after = _pred_pair(6.0, 5.0, 3.0, "only-low")
    report = blowout.grade_b1(before, after)
    assert report["cuts"]["abs_spread_ge_9_5"]["improvement"] is None
    assert report["verdict"] == "AUTO-KILLED"


# -----------------------------------------------------------------------------------------------
# CLI guard

def _load_cli():
    path = Path(__file__).parent.parent / "scripts" / "minutes_arm_s_b1_backtest.py"
    spec = importlib.util.spec_from_file_location("minutes_arm_s_b1_backtest", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_refuses_without_execute(monkeypatch, capsys):
    cli = _load_cli()

    def forbidden(path):
        raise AssertionError(f"loader called without --execute: {path}")

    monkeypatch.setattr(cli, "load_box_scores", forbidden)
    monkeypatch.setattr(cli, "load_spread_quotes", forbidden)
    assert cli.main([]) == 0
    assert cli.main(["--box-path", "x", "--spread-dir", "y"]) == 0
    out = capsys.readouterr().out
    assert "EXECUTION DISABLED" in out and "--execute" in out


def test_cli_execute_writes_reports(monkeypatch, tmp_path, capsys):
    cli = _load_cli()
    monkeypatch.setattr(cli, "load_box_scores", lambda path: pd.DataFrame())
    monkeypatch.setattr(cli, "load_spread_quotes", lambda path: pd.DataFrame())
    canned = {"cuts": {"full": {"n": 1, "before_mae": 6.0, "after_mae": 5.0,
                                "improvement": 1.0},
                       "abs_spread_lt_9_5": {"n": 1, "before_mae": 6.0, "after_mae": 5.0,
                                             "improvement": 1.0},
                       "abs_spread_ge_9_5": {"n": 0, "before_mae": None, "after_mae": None,
                                             "improvement": None},
                       "full_incl_game_two": {"n": 1, "before_mae": 6.0, "after_mae": 5.0,
                                              "improvement": 1.0}},
              "bars": {"overall_mae_improvement": {"required_minutes": 0.15,
                                                   "observed": 1.0, "pass": True},
                       "high_spread_mae_improvement": {"required_minutes": 0.35,
                                                       "observed": None, "pass": False}},
              "verdict": "AUTO-KILLED", "spread_cut": 9.5, "n_joined": 1,
              "fit_seasons": [2021, 2022, 2023], "n_train": 10, "used_fallback": False}
    monkeypatch.setattr(cli, "run_experiment",
                        lambda box, quotes: (canned, pd.DataFrame()))
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    rc = cli.main(["--execute", "--box-path", "m", "--spread-dir", "m",
                   "--out-json", str(out_json), "--out-md", str(out_md)])
    assert rc == 0
    assert json.loads(out_json.read_text())["verdict"] == "AUTO-KILLED"
    assert "AUTO-KILLED" in out_md.read_text()
    assert "Verdict:" in capsys.readouterr().out
