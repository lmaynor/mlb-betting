"""Tests for MOD-04 Cluster (e) E1 walk-forward harness."""
import numpy as np
import pandas as pd
import pytest

from scripts import mod04e_walkforward as wf


def test_fold_construction_never_trains_on_scored_week_or_later():
    available = [(s, w) for s in range(2021, 2026) for w in range(1, 19)]
    folds = wf.iter_walkforward_folds(available, scored_seasons=(2024, 2025))
    assert len(folds) == 36
    for season, week, train_sw, score_sw in folds:
        assert score_sw == (season, week)
        assert train_sw, f"expected non-empty train set for {score_sw}"
        assert max(train_sw) < score_sw
        # Only S-3..S-1 and S weeks 1..w-1 allowed
        for ts, tw in train_sw:
            assert season - 3 <= ts <= season
            if ts == season:
                assert tw < week

    # Check week 1 of 2024 includes 2021..2023 only
    s0, w0, tr0, sc0 = folds[0]
    assert (s0, w0) == (2024, 1)
    assert all(ts in (2021, 2022, 2023) for ts, _ in tr0)

    # Check --limit-weeks
    limited = wf.iter_walkforward_folds(available, scored_seasons=(2024, 2025), limit_weeks=2)
    assert len(limited) == 4
    assert [f[3] for f in limited] == [(2024, 1), (2024, 2), (2025, 1), (2025, 2)]


def test_consensus_builder_picks_modal_line_and_consensus_book():
    rows = [
        # Player A: no Consensus book -> real books quote 4.5 (3 books) and 5.5 (1 book) -> modal line 4.5
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "over", "book_id": 10, "book": "FanDuel", "book_kind": "book", "odds": -115, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "under", "book_id": 10, "book": "FanDuel", "book_kind": "book", "odds": -105, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "over", "book_id": 12, "book": "DraftKings", "book_kind": "book", "odds": -120, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "under", "book_id": 12, "book": "DraftKings", "book_kind": "book", "odds": 100, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "over", "book_id": 19, "book": "BetMGM", "book_kind": "book", "odds": -110, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 4.5, "side": "under", "book_id": 19, "book": "BetMGM", "book_kind": "book", "odds": -110, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 5.5, "side": "over", "book_id": 13, "book": "Caesars", "book_kind": "book", "odds": 110, "is_open": False},
        {"date": "2024-09-15", "player": "Player A", "team": "KC", "market": "receptions",
         "line": 5.5, "side": "under", "book_id": 13, "book": "Caesars", "book_kind": "book", "odds": -140, "is_open": False},
        # Player B: Consensus book row present -> uses Consensus row directly
        {"date": "2024-09-15", "player": "Player B", "team": "KC", "market": "receptions",
         "line": 6.5, "side": "over", "book_id": 0, "book": "Consensus", "book_kind": "consensus", "odds": -130, "is_open": False},
        {"date": "2024-09-15", "player": "Player B", "team": "KC", "market": "receptions",
         "line": 6.5, "side": "under", "book_id": 0, "book": "Consensus", "book_kind": "consensus", "odds": 110, "is_open": False},
        {"date": "2024-09-15", "player": "Player B", "team": "KC", "market": "receptions",
         "line": 5.5, "side": "over", "book_id": 10, "book": "FanDuel", "book_kind": "book", "odds": -110, "is_open": False},
        {"date": "2024-09-15", "player": "Player B", "team": "KC", "market": "receptions",
         "line": 5.5, "side": "under", "book_id": 10, "book": "FanDuel", "book_kind": "book", "odds": -110, "is_open": False},
    ]
    cons = wf.consensus_from_bp(pd.DataFrame(rows))
    a = cons[cons["player_norm"] == "player a"].iloc[0]
    b = cons[cons["player_norm"] == "player b"].iloc[0]
    assert a["line"] == pytest.approx(4.5)
    assert a["n_books"] == 3
    assert a["source"] == "real_books_modal"
    assert 0.50 < a["p_market"] < 0.56

    assert b["line"] == pytest.approx(6.5)
    assert b["source"] == "consensus"
    assert b["p_market"] > 0.54


def test_compute_metrics_outputs_expected_fields():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, size=50).astype(float)
    pm = np.clip(0.3 + 0.4 * y + rng.normal(0, 0.05, 50), 0.05, 0.95)
    pk = np.full(50, 0.5)
    px = np.clip(0.4 + 0.2 * y, 0.05, 0.95)
    m = wf.compute_metrics(y, pm, pk, px)
    assert m["n"] == 50
    assert m["model_ll"] < m["market_ll"]
    assert m["delta_ll"] == pytest.approx(m["model_ll"] - m["market_ll"])
    assert m["z"] < 0.0
    assert m["slope"] > 0.0
    assert 0.0 <= m["ece"] <= 1.0
    assert m["xgb_ll"] is not None
