"""Tests for ev.betmgm_under_shadow (RES-17 / RUL-20261119)."""

import gzip
import io
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ev import betmgm_under_shadow as SH
from ev.novig.shadow_bank import make_store


def make_an_row(**kw) -> dict:
    """Helper creating a valid Action Network partition row."""
    base = {
        "date": "2026-11-24",
        "event_id": 101,
        "player_id": 1001,
        "player_name": "Jayson Tatum",
        "team_id": 1,
        "team_abbr": "BOS",
        "home_abbr": "BOS",
        "away_abbr": "NYK",
        "start_time": "2026-11-24T23:30:00Z",
        "game_status": "scheduled",
        "market": "points",
        "source_type": "prop",
        "is_milestone": False,
        "threshold": None,
        "book_id": 75,
        "book_name": "betmgm",
        "side": "over",
        "line": 26.5,
        "odds": -110,
        "is_alt_market": False,
        "line_status": "normal",
        "period": "full_game",
        "market_id": 1,
        "outcome_id": 1,
        "scraped_at": "2026-11-25T07:15:00Z",
    }
    base.update(kw)
    return base


def test_two_sided_pair_extraction():
    """One-sided BetMGM rows do NOT form pairs; alt-market and suspended rows excluded."""
    rows = [
        # Player 1: BetMGM has only 'over' (one-sided) -> excluded
        make_an_row(player_id=1, side="over", line=20.5),
        # Player 2: BetMGM has both over and under, but is_alt_market=True -> excluded
        make_an_row(player_id=2, side="over", line=15.5, is_alt_market=True),
        make_an_row(player_id=2, side="under", line=15.5, is_alt_market=True),
        # Player 3: BetMGM has both over and under, but line_status="suspended" -> excluded
        make_an_row(player_id=3, side="over", line=8.5, line_status="suspended"),
        make_an_row(player_id=3, side="under", line=8.5, line_status="suspended"),
        # Player 4: Valid two-sided BetMGM quote at normal line -> extracted
        make_an_row(player_id=4, player_name="Jaylen Brown", side="over", line=22.5, odds=-115),
        make_an_row(player_id=4, player_name="Jaylen Brown", side="under", line=22.5, odds=-105),
    ]
    df = pd.DataFrame(rows)
    core = SH.core_frame(df)
    pairs = SH.extract_pairs(core)

    assert len(pairs) == 1
    p = pairs.iloc[0]
    assert p["player_id"] == 4
    assert p["player_name"] == "Jaylen Brown"
    assert p["line_mgm"] == 22.5
    assert p["odds_over_mgm"] == -115
    assert p["odds_under_mgm"] == -105
    assert p["stat"] == "points"


def test_consensus_modal_and_refcount():
    """line_cons is the modal ref line, excludes book 75 and book 15; n_ref_books counts only ref books."""
    rows = [
        # BetMGM (book 75) at 24.5
        make_an_row(player_id=10, book_id=75, line=24.5, side="over", odds=-110),
        make_an_row(player_id=10, book_id=75, line=24.5, side="under", odds=-110),
        # Consensus (book 15 - not a book!) at 26.5
        make_an_row(player_id=10, book_id=15, line=26.5, side="over", odds=-110),
        make_an_row(player_id=10, book_id=15, line=26.5, side="under", odds=-110),
        # Ref book 68 (DK) at 25.5
        make_an_row(player_id=10, book_id=68, line=25.5, side="over", odds=-115),
        make_an_row(player_id=10, book_id=68, line=25.5, side="under", odds=-105),
        # Ref book 69 (FD) at 25.5
        make_an_row(player_id=10, book_id=69, line=25.5, side="over", odds=-112),
        make_an_row(player_id=10, book_id=69, line=25.5, side="under", odds=-108),
        # Ref book 79 (bet365) at 24.5
        make_an_row(player_id=10, book_id=79, line=24.5, side="over", odds=-120),
        make_an_row(player_id=10, book_id=79, line=24.5, side="under", odds=100),
    ]
    df = pd.DataFrame(rows)
    core = SH.core_frame(df)
    pairs = SH.extract_pairs(core)
    cons = SH.consensus_fields(core, pairs)

    assert len(cons) == 1
    c = cons.iloc[0]
    # Modal ref line between {25.5 (DK), 25.5 (FD), 24.5 (bet365)} is 25.5 (count 2 vs 1)
    assert c["line_cons"] == 25.5
    # n_ref_books counts only 68, 69, 79 -> exactly 3 (excluding book 15 and book 75)
    assert c["n_ref_books"] == 3


def test_gate_buckets():
    """Verify exact gate delta boundaries (§3.1, §5)."""
    assert SH.gate_bucket(0.029) == "sub_3pp"
    assert SH.gate_bucket(0.030) == "3pp"
    assert SH.gate_bucket(0.049) == "3pp"
    assert SH.gate_bucket(0.050) == "5pp"
    assert SH.gate_bucket(0.079) == "5pp"
    assert SH.gate_bucket(0.080) == "8pp"
    assert SH.gate_bucket(0.120) == "8pp"
    assert SH.gate_bucket(0.121) == "overflow"


def test_trigger_idempotence(tmp_path):
    """Logging run() twice against DirStore banks 0 new rows on second pass with unchanged chunk count."""
    store = make_store(f"dir:{tmp_path}")
    slate_date = "2026-11-24"

    # 1. Write Action Network partition parquet
    an_rows = [
        # BetMGM pair
        make_an_row(player_id=101, player_name="Derrick White", line=15.5, side="over", odds=-110),
        make_an_row(player_id=101, player_name="Derrick White", line=15.5, side="under", odds=-110),
        # DK ref book
        make_an_row(player_id=101, player_name="Derrick White", book_id=68, line=15.5, side="over", odds=-115),
        make_an_row(player_id=101, player_name="Derrick White", book_id=68, line=15.5, side="under", odds=-105),
    ]
    df_an = pd.DataFrame(an_rows)
    buf = io.BytesIO()
    df_an.to_parquet(buf, index=False)
    store.put(f"Odds/actionnetwork_nba/points/{slate_date}.parquet", buf.getvalue())

    # 2. Write EVSweep archive JSON
    archive_payload = {
        "engine": "nba_props",
        "rows": {
            "r1": {
                "stat": "points",
                "player": "Derrick White",
                "team": "BOS",
                "opp": "NYK",
                "line": 15.5,
                "proj_mean": 14.8,
                "p_over_model": 0.44,
                "commence": "2026-11-24T23:30:00Z",
            }
        }
    }
    store.put(
        f"EVSweep/nba_props/archive/{slate_date}.json",
        json.dumps(archive_payload).encode("utf-8"),
    )

    # First run
    res1 = SH.run(store, [slate_date])
    assert res1["total_new_rows"] == 1
    chunks1 = store.list(f"{SH.PREFIX}/chunks/")
    assert len(chunks1) == 1

    # Second run (idempotent)
    res2 = SH.run(store, [slate_date])
    assert res2["total_new_rows"] == 0
    assert res2["dates"][slate_date]["already_banked"] == 1
    chunks2 = store.list(f"{SH.PREFIX}/chunks/")
    assert len(chunks2) == 1
    assert chunks1 == chunks2


def test_unpriced_pair_counted_not_logged():
    """Unpriced pair (missing distribution) is absent from trigger rows but counted in telemetry."""
    pair_row = {
        "game_date": "2026-11-24",
        "event_id": 201,
        "player_id": 999,
        "player_name": "Ghost Player",
        "team_abbr": "MIA",
        "home_abbr": "MIA",
        "away_abbr": "ORL",
        "start_time": "2026-11-24T23:00:00Z",
        "stat": "points",
        "line_mgm": 12.5,
        "odds_under_mgm": -110,
        "odds_over_mgm": -110,
        "scraped_at": "2026-11-25T07:00:00Z",
        "sport": "nba",
    }
    pairs = pd.DataFrame([pair_row])
    cons = pd.DataFrame(columns=["date", "event_id", "player_id", "stat", "line_cons", "n_ref_books", "quotes_by_line"])
    empty_dists = pd.DataFrame(columns=["sport", "stat", "player_norm", "player", "team", "opp", "mu", "disp", "family", "commence", "et_day"])

    rows, stats = SH.build_trigger_rows(pairs, cons, empty_dists, "run_test", "2026-11-25T07:30:00Z")
    assert len(rows) == 0
    assert stats["priced"] == 0
    assert stats["unpriced"] == 1

    unpriced_key = f"{pair_row['game_date']}|{pair_row['event_id']}|{pair_row['player_id']}|{pair_row['stat']}|{float(pair_row['line_mgm']):.2f}"
    telem = SH.telemetry_for_day(pairs, cons, date="2026-11-24", unpriced_keys={unpriced_key})

    assert telem["n_pairs"] == 1
    assert telem["n_pairs_unpriced"] == 1


def test_finalize_close_pairing(tmp_path):
    """Finalize correctly pairs ClosingPanel quotes and masters outcomes (push, win, void DNP)."""
    store = make_store(f"dir:{tmp_path}")
    et_day = "2026-11-24"

    # Create 3 banked triggers in a chunk:
    # 1. Tatum: line 24.5, points. Line moves to 25.5 at close. Actual = 22 (win).
    # 2. Brown: line 6.0, assists. MGM absent at close. Actual = 6 (integer push).
    # 3. Horford: line 7.5, rebounds. Played = 0 (void DNP).
    trig1 = {
        "ruling": SH.RULING, "run_id": "r1", "banked_at": "2026-11-25T07:00:00Z",
        "game_date": et_day, "game_key": "101", "sport": "nba", "stat": "points",
        "player_id": 1001, "player_name": "Jayson Tatum", "player_norm": "jayson tatum",
        "team_abbr": "BOS", "home_abbr": "BOS", "away_abbr": "NYK", "side": "under",
        "line_mgm": 24.5, "t_trigger": "2026-11-25T07:00:00Z", "commence_ts": "2026-11-24T23:30:00Z",
        "minutes_to_start": -450.0, "odds_under_mgm": -110, "odds_over_mgm": -110,
        "dec_under_mgm": 1.909091, "p_imp_under_mgm": 0.52381, "p_imp_over_mgm": 0.52381,
        "two_sided_sum_mgm": 1.04762, "p_devig_under_mgm": 0.5, "line_cons": 24.5,
        "line_diff": 0.0, "line_eq_cons": True, "n_ref_books": 4,
        "p_cons_under_at_mgm_line": 0.5, "cons_dispersion": 0.01, "cons_median_age_min": 0.0,
        "mu": 23.5, "disp": 4.5, "family": "normal", "p_dist_under": 0.58, "gate_delta": 0.056,
        "ev_net": 0.107, "gate_bucket": "5pp",
        "t_close": None, "mgm_present_at_close": None, "line_mgm_close": None,
        "odds_under_mgm_close": None, "p_imp_under_mgm_close": None,
        "line_cons_close": None, "p_cons_under_close": None, "n_ref_books_close": None,
        "actual_stat": None, "outcome_under": None, "flat_pnl_1u": None,
    }
    trig2 = dict(trig1, player_id=1002, player_name="Jaylen Brown", player_norm="jaylen brown",
                 stat="assists", line_mgm=6.0, dec_under_mgm=1.85)
    trig3 = dict(trig1, player_id=1003, player_name="Al Horford", player_norm="al horford",
                 stat="rebounds", line_mgm=7.5, dec_under_mgm=1.95)

    SH.write_chunk(store, [trig1, trig2, trig3], "run_fin_fixture")

    # Write ClosingPanel NBA payload
    # Tatum has rows at 24.5 (4 ref books) and 25.5 (BetMGM moved here)
    cp_payload = {
        "rows": [
            {
                "market": "player_points",
                "player": "Jayson Tatum",
                "point": 24.5,
                "commence_time": "2026-11-24T23:30:00Z",
                "close": {
                    "books": {
                        "draftkings": {"prices": {"Over Jayson Tatum": -115, "Under Jayson Tatum": -105}, "snap_ts": "2026-11-24T23:25:00Z"},
                        "fanduel": {"prices": {"Over Jayson Tatum": -110, "Under Jayson Tatum": -110}, "snap_ts": "2026-11-24T23:26:00Z"},
                        "bet365": {"prices": {"Over Jayson Tatum": -115, "Under Jayson Tatum": -105}, "snap_ts": "2026-11-24T23:24:00Z"},
                        "caesars": {"prices": {"Over Jayson Tatum": -120, "Under Jayson Tatum": 100}, "snap_ts": "2026-11-24T23:27:00Z"},
                    }
                }
            },
            {
                "market": "player_points",
                "player": "Jayson Tatum",
                "point": 25.5,
                "commence_time": "2026-11-24T23:30:00Z",
                "close": {
                    "books": {
                        "betmgm": {"prices": {"Over Jayson Tatum": -125, "Under Jayson Tatum": 105}, "snap_ts": "2026-11-24T23:28:00Z"},
                    }
                }
            },
            {
                "market": "player_assists",
                "player": "Jaylen Brown",
                "point": 6.0,
                "commence_time": "2026-11-24T23:30:00Z",
                "close": {
                    "books": {
                        "draftkings": {"prices": {"Over Jaylen Brown": -110, "Under Jaylen Brown": -110}, "snap_ts": "2026-11-24T23:25:00Z"}
                    }
                }
            }
        ]
    }
    store.put(
        f"ClosingPanel/nba/{et_day}.json.gz",
        gzip.compress(json.dumps(cp_payload).encode("utf-8")),
    )

    # Write NBA outcome master
    master_df = pd.DataFrame([
        {"date": "2026-11-24", "name": "Jayson Tatum", "team_abbr": "BOS", "game_id": 101, "played": 1, "points": 22, "rebounds": 8, "assists": 5, "three_points_made": 3},
        {"date": "2026-11-24", "name": "Jaylen Brown", "team_abbr": "BOS", "game_id": 101, "played": 1, "points": 25, "rebounds": 5, "assists": 6, "three_points_made": 2},
        {"date": "2026-11-24", "name": "Al Horford", "team_abbr": "BOS", "game_id": 101, "played": 0, "points": 0, "rebounds": 0, "assists": 0, "three_points_made": 0},
    ])
    store.put("NBA/player_boxscores_master.csv", master_df.to_csv(index=False).encode("utf-8"))

    # Execute finalize_day
    summary = SH.finalize_day(store, et_day)
    assert summary["triggers"] == 3
    assert summary["settled"] == 1  # Tatum win
    assert summary["pushes"] == 1   # Brown push
    assert summary["voids"] == 1    # Horford void
    assert summary["mgm_present"] == 1  # Tatum present

    # Read back finalized rows
    fin_bytes = gzip.decompress(store.read(f"{SH.PREFIX}/finalized/{et_day}.jsonl.gz")).decode("utf-8")
    fin_rows = [json.loads(line) for line in fin_bytes.splitlines() if line.strip()]
    rows_by_player = {r["player_name"]: r for r in fin_rows}

    # Tatum checks: line moved, MGM present, 4 ref books at 24.5, settled win
    t = rows_by_player["Jayson Tatum"]
    assert t["mgm_present_at_close"] is True
    assert t["line_mgm_close"] == 25.5
    assert t["line_mgm_close"] != t["line_mgm"]
    assert t["odds_under_mgm_close"] == 105
    assert t["n_ref_books_close"] == 4
    assert t["p_cons_under_close"] is not None
    assert t["actual_stat"] == 22.0
    assert t["outcome_under"] == "win"
    assert t["flat_pnl_1u"] == pytest.approx(1.909091 - 1.0)

    # Brown checks: MGM absent, push at line 6.0, flat_pnl_1u is None
    b = rows_by_player["Jaylen Brown"]
    assert b["mgm_present_at_close"] is False
    assert b["actual_stat"] == 6.0
    assert b["outcome_under"] == "push"
    assert b["flat_pnl_1u"] is None

    # Horford checks: DNP void, flat_pnl_1u is None
    h = rows_by_player["Al Horford"]
    assert h["outcome_under"] == "void"
    assert h["flat_pnl_1u"] is None
