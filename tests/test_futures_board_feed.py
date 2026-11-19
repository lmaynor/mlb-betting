"""tests/test_futures_board_feed.py -- Unit and integration tests for futures board feed and shadow logging."""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

import ev.futures_board_feed as feed
from ev.futures_board_feed import (
    bp_rows_to_snapshots,
    build_board,
    discover_bp_futures_partitions,
    kalshi_rows_to_snapshots,
    load_bp_futures,
    load_kalshi_partition,
    shadow_signal_log,
    write_shadow_log,
)
from ev.futures_sleeve import FuturesBoardRow


# ==============================================================================
# Recorded-shape Fixtures (matching real Nov 19, 2026 schema)
# ==============================================================================

def make_kalshi_fixture() -> pd.DataFrame:
    """Create a recorded-shape DataFrame matching Kalshi futures partition schema.

    Contains:
    - 3-outcome two-sided outright board (BOS, NYK, PHI)
    - 1 ask-only tail market row (TOR: bid 0.0, ask 0.01)
    - 1 last_price only row (MIA: bid 0.0, ask 0.0, last 0.12)
    - 1 dead row (WAS: bid 0.0, ask 0.0, last 0.0) -> must be skipped
    """
    rows = [
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-BOS",
            "outcome": "BOS",
            "yes_sub_title": "Boston Celtics",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.40,
            "yes_ask": 0.50,
            "last_price": 0.46,
            "volume_fp": 1500.0,
            "open_interest_fp": 3200.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-NYK",
            "outcome": "NYK",
            "yes_sub_title": "New York Knicks",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.20,
            "yes_ask": 0.30,
            "last_price": 0.24,
            "volume_fp": 800.0,
            "open_interest_fp": 1200.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-PHI",
            "outcome": "PHI",
            "yes_sub_title": "Philadelphia 76ers",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.10,
            "yes_ask": 0.20,
            "last_price": 0.16,
            "volume_fp": 400.0,
            "open_interest_fp": 600.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-TOR",
            "outcome": "TOR",
            "yes_sub_title": "Toronto Raptors",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.0,
            "yes_ask": 0.01,
            "last_price": 0.0,
            "volume_fp": 10.0,
            "open_interest_fp": 50.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-MIA",
            "outcome": "MIA",
            "yes_sub_title": "Miami Heat",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.0,
            "yes_ask": 0.0,
            "last_price": 0.12,
            "volume_fp": 20.0,
            "open_interest_fp": 100.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
        {
            "venue": "kalshi",
            "series_ticker": "KXNBADIV",
            "event_ticker": "KXNBADIV-2027",
            "market": "KXNBADIV",
            "market_ticker": "KXNBADIV-WAS",
            "outcome": "WAS",
            "yes_sub_title": "Washington Wizards",
            "strike_type": "greater",
            "floor_strike": None,
            "cap_strike": None,
            "yes_bid": 0.0,
            "yes_ask": 0.0,
            "last_price": 0.0,
            "volume_fp": 0.0,
            "open_interest_fp": 0.0,
            "status": "active",
            "fee_type": "standard",
            "fee_multiplier": 1.0,
            "league": "NBA",
            "family": "basketball",
            "captured_at": "2026-11-19T06:00:00Z",
            "dt": "2026-11-19",
        },
    ]
    return pd.DataFrame(rows)


def make_bp_fixture() -> pd.DataFrame:
    """Create a recorded-shape DataFrame matching BettingPros futures partition schema.

    Contains:
    - DraftKings outright rows (+350, -175)
    - FanDuel outright rows (+350, -175)
    - 1 opening quote row (is_open=True) -> must be excluded
    - 2 season over/under futures rows (one with line!=None, one with side="under") -> must be excluded
    """
    rows = [
        # DraftKings outrights
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "world-series-winner",
            "market_id": 101,
            "player": "New York Yankees",
            "team": "NYY",
            "line": None,
            "side": "new york yankees",
            "book": "DraftKings",
            "book_kind": "sportsbook",
            "odds": 350,
            "is_open": False,
            "updated": "2026-11-19T05:30:00Z",
        },
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "world-series-winner",
            "market_id": 101,
            "player": "Los Angeles Dodgers",
            "team": "LAD",
            "line": None,
            "side": "los angeles dodgers",
            "book": "DraftKings",
            "book_kind": "sportsbook",
            "odds": -175,
            "is_open": False,
            "updated": "2026-11-19T05:30:00Z",
        },
        # FanDuel outrights
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "world-series-winner",
            "market_id": 101,
            "player": "New York Yankees",
            "team": "NYY",
            "line": None,
            "side": "new york yankees",
            "book": "FanDuel",
            "book_kind": "sportsbook",
            "odds": 350,
            "is_open": False,
            "updated": "2026-11-19T05:35:00Z",
        },
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "world-series-winner",
            "market_id": 101,
            "player": "Los Angeles Dodgers",
            "team": "LAD",
            "line": None,
            "side": "los angeles dodgers",
            "book": "FanDuel",
            "book_kind": "sportsbook",
            "odds": -175,
            "is_open": False,
            "updated": "2026-11-19T05:35:00Z",
        },
        # Opening quote row (is_open=True) -> excluded
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "world-series-winner",
            "market_id": 101,
            "player": "New York Yankees",
            "team": "NYY",
            "line": None,
            "side": "new york yankees",
            "book": "DraftKings",
            "book_kind": "sportsbook",
            "odds": 350,
            "is_open": True,
            "updated": "2026-11-01T00:00:00Z",
        },
        # Season O/U future with non-null line -> excluded
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "regular-season-wins",
            "market_id": 102,
            "player": "New York Yankees",
            "team": "NYY",
            "line": 92.5,
            "side": "over",
            "book": "DraftKings",
            "book_kind": "sportsbook",
            "odds": -110,
            "is_open": False,
            "updated": "2026-11-19T05:30:00Z",
        },
        # Season O/U future with side="under" -> excluded
        {
            "date": "2026-11-19",
            "sport": "mlb",
            "market": "regular-season-wins",
            "market_id": 102,
            "player": "New York Yankees",
            "team": "NYY",
            "line": None,
            "side": "under",
            "book": "DraftKings",
            "book_kind": "sportsbook",
            "odds": -110,
            "is_open": False,
            "updated": "2026-11-19T05:30:00Z",
        },
    ]
    return pd.DataFrame(rows)


# ==============================================================================
# Tests
# ==============================================================================

def test_kalshi_rows_to_snapshots_q_convention():
    """Kalshi feed enforces the q convention: mids, ask-only fallback, last fallback, skips dead rows."""
    df = make_kalshi_fixture()
    snapshots, stats = kalshi_rows_to_snapshots(df)

    assert stats["total_rows"] == 6
    assert stats["valid_snapshots"] == 5
    assert stats["skipped_no_price"] == 1
    assert len(snapshots) == 5

    # 1. Two-sided market uses mids (yes_bid + yes_ask)/2
    bos = next(s for s in snapshots if s["outcome"] == "BOS")
    assert math.isclose(bos["q"], 0.45, abs_tol=1e-6)

    nyk = next(s for s in snapshots if s["outcome"] == "NYK")
    assert math.isclose(nyk["q"], 0.25, abs_tol=1e-6)

    phi = next(s for s in snapshots if s["outcome"] == "PHI")
    assert math.isclose(phi["q"], 0.15, abs_tol=1e-6)

    # 2. Ask-only tail market falls back to yes_ask
    tor = next(s for s in snapshots if s["outcome"] == "TOR")
    assert math.isclose(tor["q"], 0.01, abs_tol=1e-6)

    # 3. Last-price only falls back to last_price
    mia = next(s for s in snapshots if s["outcome"] == "MIA")
    assert math.isclose(mia["q"], 0.12, abs_tol=1e-6)

    # 4. Dead row (WAS) is skipped and not present
    assert not any(s["outcome"] == "WAS" for s in snapshots)

    # 5. Extras preserved in dict
    assert bos["series_ticker"] == "KXNBADIV"
    assert bos["market_ticker"] == "KXNBADIV-BOS"
    assert bos["league"] == "NBA"
    assert bos["family"] == "basketball"
    assert bos["bid"] == 0.40
    assert bos["ask"] == 0.50
    assert bos["last"] == 0.46


def test_bp_rows_to_snapshots_outrights_and_exclusions():
    """BettingPros feed parses outrights, applies American odds implied prob, excludes O/U and openers."""
    df = make_bp_fixture()
    snapshots, stats = bp_rows_to_snapshots(df, sport="mlb")

    assert stats["total_rows"] == 7
    assert stats["valid_snapshots"] == 4
    assert stats["excluded_is_open"] == 1
    assert stats["excluded_season_ou"] == 2
    assert stats["skipped_invalid_odds"] == 0
    assert len(snapshots) == 4

    # Venues: book name lowercased with spaces removed
    venues = {s["venue"] for s in snapshots}
    assert venues == {"draftkings", "fanduel"}

    # Market format f"{sport}:{slug}"
    markets = {s["market"] for s in snapshots}
    assert markets == {"mlb:world-series-winner"}

    # American odds conversion:
    # +350 -> 100 / (350 + 100) = 100 / 450
    # -175 -> 175 / (175 + 100) = 175 / 275
    dk_nyy = next(s for s in snapshots if s["venue"] == "draftkings" and s["outcome"] == "New York Yankees")
    dk_lad = next(s for s in snapshots if s["venue"] == "draftkings" and s["outcome"] == "Los Angeles Dodgers")

    expected_nyy_q = 100.0 / 450.0
    expected_lad_q = 175.0 / 275.0
    assert math.isclose(dk_nyy["q"], expected_nyy_q, rel_tol=1e-6)
    assert math.isclose(dk_lad["q"], expected_lad_q, rel_tol=1e-6)

    fd_nyy = next(s for s in snapshots if s["venue"] == "fanduel" and s["outcome"] == "New York Yankees")
    fd_lad = next(s for s in snapshots if s["venue"] == "fanduel" and s["outcome"] == "Los Angeles Dodgers")
    assert math.isclose(fd_nyy["q"], expected_nyy_q, rel_tol=1e-6)
    assert math.isclose(fd_lad["q"], expected_lad_q, rel_tol=1e-6)


def test_build_board_devig_sums_to_one():
    """build_board concatenates Kalshi and BP snapshots and devigs via Power devig to sum ~1.0."""
    kalshi_df = make_kalshi_fixture()
    bp_df = make_bp_fixture()

    board_rows = build_board(
        kalshi_df=kalshi_df,
        bp_frames=[(bp_df, "mlb")],
    )

    assert len(board_rows) > 0
    assert all(isinstance(r, FuturesBoardRow) for r in board_rows)

    # Group by (venue, market)
    grouped: dict[tuple[str, str], list[FuturesBoardRow]] = {}
    for r in board_rows:
        key = (r.venue, r.market)
        grouped.setdefault(key, []).append(r)

    # 3 expected groups: ('kalshi', 'KXNBADIV'), ('draftkings', 'mlb:world-series-winner'), ('fanduel', 'mlb:world-series-winner')
    assert len(grouped) == 3

    for (venue, market), rows in grouped.items():
        fair_sum = sum(r.p_fair_power for r in rows)
        assert math.isclose(fair_sum, 1.0, abs_tol=1e-4), f"Fair probs for {venue}:{market} sum to {fair_sum}, expected 1.0"


def test_shadow_signal_log_format_and_fences():
    """shadow_signal_log creates signal and board_summary records with p_sim strictly None."""
    kalshi_df = make_kalshi_fixture()
    bp_df = make_bp_fixture()
    board_rows = build_board(kalshi_df=kalshi_df, bp_frames=[(bp_df, "mlb")])

    records = shadow_signal_log(board_rows, ts="2026-11-19T06:30:00Z")

    signal_recs = [r for r in records if r["kind"] == "signal"]
    summary_recs = [r for r in records if r["kind"] == "board_summary"]

    assert len(signal_recs) == len(board_rows)
    assert len(summary_recs) == 3  # 3 (venue, market) pairs

    # Check signal record guarantees
    for sig in signal_recs:
        assert sig["ts"] == "2026-11-19T06:30:00Z"
        assert sig["p_sim"] is None, "FENCE VIOLATION: p_sim must remain explicitly None"
        assert sig["entry_fired"] is False
        assert sig["reason"] == "NO_BET_NO_PSIM"
        assert isinstance(sig["q_market"], float)
        assert isinstance(sig["p_fair_power"], float)
        assert len(sig["venue"]) > 0
        assert len(sig["market"]) > 0
        assert len(sig["outcome"]) > 0

    # Check summary records
    for summ in summary_recs:
        assert summ["kind"] == "board_summary"
        assert summ["ts"] == "2026-11-19T06:30:00Z"
        assert summ["n_outcomes"] >= 2
        assert summ["sum_q_market"] > 0.0


def test_evaluate_futures_entry_never_imported_or_invoked():
    """FENCE VERIFICATION: evaluate_futures_entry is never imported or invoked by ev.futures_board_feed.

    We assert by two independent methods:
    1. Direct inspection of module attributes: 'evaluate_futures_entry' is not in feed.__dict__.
    2. Static AST inspection of ev/futures_board_feed.py source: evaluate_futures_entry is never
       imported, called, or referenced anywhere in the module's syntax tree.
    """
    # 1. Namespace check
    assert "evaluate_futures_entry" not in dir(feed), (
        "evaluate_futures_entry must not be imported in ev.futures_board_feed"
    )

    # 2. Source AST check
    module_path = Path(feed.__file__)
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(module_path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "evaluate_futures_entry"
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "evaluate_futures_entry"
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id != "evaluate_futures_entry"
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr != "evaluate_futures_entry"


def test_io_helpers_with_local_fixtures(tmp_path: Path):
    """IO helpers correctly discover and load local parquet partitions."""
    kalshi_df = make_kalshi_fixture()
    bp_df = make_bp_fixture()

    dt = "2026-11-19"
    # Write Kalshi partition: Futures/kalshi/dt=2026-11-19/snapshot.parquet
    kalshi_dir = tmp_path / "Futures" / "kalshi" / f"dt={dt}"
    kalshi_dir.mkdir(parents=True, exist_ok=True)
    kalshi_df.to_parquet(kalshi_dir / "snapshot.parquet", index=False)

    # Write BP partition: Odds/bettingpros_mlb_futures/world-series-winner/2026-11-19.parquet
    bp_dir = tmp_path / "Odds" / "bettingpros_mlb_futures" / "world-series-winner"
    bp_dir.mkdir(parents=True, exist_ok=True)
    bp_df.to_parquet(bp_dir / f"{dt}.parquet", index=False)

    # 1. Load Kalshi partition
    loaded_kalshi = load_kalshi_partition(tmp_path, dt)
    assert loaded_kalshi is not None
    assert len(loaded_kalshi) == len(kalshi_df)

    # Missing date returns None
    assert load_kalshi_partition(tmp_path, "2026-01-01") is None

    # 2. Discover BP partitions
    discovered = discover_bp_futures_partitions(tmp_path, dt)
    assert len(discovered) == 1
    assert discovered[0][0] == "mlb"
    assert discovered[0][1] == "world-series-winner"

    # 3. Load BP partition with explicit slug
    loaded_bp_slug = load_bp_futures(tmp_path, "mlb", dt, slug="world-series-winner")
    assert loaded_bp_slug is not None
    assert len(loaded_bp_slug) == len(bp_df)

    # 4. Load BP partition without slug (auto-discovery)
    loaded_bp_all = load_bp_futures(tmp_path, "mlb", dt)
    assert loaded_bp_all is not None
    assert len(loaded_bp_all) == len(bp_df)

    # 5. Write shadow log to local file
    board_rows = build_board(kalshi_df=loaded_kalshi, bp_frames=[(loaded_bp_all, "mlb")])
    records = shadow_signal_log(board_rows, ts=f"{dt}T07:00:00Z")
    out_rel = f"Futures/sleeve_shadow/dt={dt}/signals.jsonl"
    dest = write_shadow_log(records, out_rel, root=tmp_path)

    out_file = Path(dest)
    assert out_file.exists()
    lines = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == len(records)


def test_runner_cli_missing_and_present_boards(tmp_path: Path):
    """Runner script handles missing boards gracefully (exit 0) and processes present boards."""
    script_path = Path("scripts/futures_sleeve_shadow.py").resolve()
    python_bin = sys.executable

    # 1. Missing boards: exit 0 with clear message
    res_empty = subprocess.run(
        [python_bin, str(script_path), "--bucket-root", str(tmp_path), "--date", "2026-11-19"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_empty.returncode == 0
    assert "no boards banked for 2026-11-19" in res_empty.stdout

    # 2. Present boards: populate fixture and run with --out - (stdout)
    dt = "2026-11-19"
    kalshi_dir = tmp_path / "Futures" / "kalshi" / f"dt={dt}"
    kalshi_dir.mkdir(parents=True, exist_ok=True)
    make_kalshi_fixture().to_parquet(kalshi_dir / "snapshot.parquet", index=False)

    res_present = subprocess.run(
        [python_bin, str(script_path), "--bucket-root", str(tmp_path), "--date", dt, "--out", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_present.returncode == 0
    lines = [json.loads(line) for line in res_present.stdout.splitlines() if line.strip()]
    assert len(lines) > 0
    assert any(r.get("kind") == "signal" for r in lines)
    assert any(r.get("kind") == "board_summary" for r in lines)
    # Stderr holds diagnostic counts
    assert "Kalshi:" in res_present.stderr
    assert "Board:" in res_present.stderr
