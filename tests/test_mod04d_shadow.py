from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mod04d.features import build_rolling_training_frame
from mod04d.nba_points import fit as fit_points
from mod04d.nba_threes import fit_threes
from mod04d.nhl_scoring import SKATER_FEATURES, train_bundle
from mod04d.shadow import (
    FAMILY_ENGINE_MAP,
    build_shadow_rows,
    read_shadow_file,
    rescore_prelock,
    write_shadow_file,
)


def _nba_frame() -> pd.DataFrame:
    rng = np.random.default_rng(5)
    dates = pd.date_range("2025-11-01", periods=14, freq="D")
    rows = []
    for d_i, dt in enumerate(dates):
        for pid in range(12):
            mins = float(np.clip(rng.normal(28.0, 3.0), 12.0, 38.0))
            fga = int(rng.poisson(14))
            fg3a = int(rng.binomial(fga, 0.38))
            fg2a = fga - fg3a
            fta = int(rng.poisson(4))
            fg3m = int(rng.binomial(fg3a, 0.36))
            fg2m = int(rng.binomial(fg2a, 0.52))
            ftm = int(rng.binomial(fta, 0.80))
            rows.append(
                {
                    "game_date": dt.strftime("%Y-%m-%d"),
                    "game_id": 200 + d_i,
                    "player_id": pid,
                    "team": "BOS" if pid < 6 else "NYK",
                    "minutes": mins,
                    "fga": fga,
                    "fgm": fg2m + fg3m,
                    "fg2a": fg2a,
                    "fg2m": fg2m,
                    "fg3a": fg3a,
                    "fg3m": fg3m,
                    "fta": fta,
                    "ftm": ftm,
                    "points": ftm + 2 * fg2m + 3 * fg3m,
                    "oreb": 1,
                    "tov": 1,
                    "starter": 1,
                }
            )
    box = pd.DataFrame(rows)
    feats = build_rolling_training_frame(box, min_prior_games=5)
    targets = box[["game_date", "player_id", "points", "fg3m", "fg3a"]].rename(columns={"game_date": "date"})
    return feats.merge(targets, on=["date", "player_id"], how="inner").reset_index(drop=True)


def _nhl_frame() -> pd.DataFrame:
    rng = np.random.default_rng(9)
    n = 120
    df = pd.DataFrame(index=range(n))
    df["date"] = rng.choice(["2025-11-01", "2025-11-02", "2025-11-03", "2025-11-04", "2025-11-05"], size=n)
    df["game_id"] = np.arange(1, n + 1)
    df["player_id"] = rng.integers(1, 20, size=n)
    for col in SKATER_FEATURES:
        df[col] = rng.uniform(0.3, 1.1, size=n)
    df["goals"] = rng.poisson(0.35, size=n)
    df["points"] = df["goals"] + rng.poisson(0.55, size=n)
    return df


def test_shadow_rows_all_four_families_and_roundtrip(tmp_path: Path) -> None:
    nba_df = _nba_frame()
    nba_slate = nba_df.drop(columns=["points", "fg3m", "fg3a"]).head(6).reset_index(drop=True)
    nhl_df = _nhl_frame()
    nhl_slate = nhl_df.drop(columns=["goals", "points"]).head(6).reset_index(drop=True)

    m_pts = fit_points(nba_df, k_max=70, max_rounds=30, disp_rounds=20)
    m_3 = fit_threes(nba_df, k_max=16, max_rounds=30, disp_rounds=20)
    nhl_bundle = train_bundle({"skater": nhl_df}, stats=("goals", "points"), max_rounds=30, disp_rounds=20)

    mkt = [0.48, 0.52, 0.55, 0.45, 0.60, 0.40]
    cases = [
        ("nba_points", nba_slate, m_pts, 18.5),
        ("nba_threes", nba_slate, m_3, 2.5),
        ("nhl_goals", nhl_slate, nhl_bundle, 0.5),
        ("nhl_points", nhl_slate, nhl_bundle, 0.5),
    ]

    all_rows = []
    for fam, slate, mod, line in cases:
        # c=0 with market_probs -> p_served == p_market
        rows_c0 = build_shadow_rows(fam, slate, mod, market_probs=mkt, lines=line, c=0.0)
        assert len(rows_c0) == len(slate)
        for r, pm in zip(rows_c0, mkt):
            assert r["family"] == fam
            assert r["engine"] == FAMILY_ENGINE_MAP[fam]
            assert 0.0 <= r["p_model"] <= 1.0
            assert r["pmf_over"] == r["p_model"]
            assert np.isclose(r["p_served"], pm, atol=1e-6)

        # No market_probs -> p_served is None
        rows_nomkt = build_shadow_rows(fam, slate, mod, market_probs=None, lines=line, c=0.25)
        assert all(r["p_served"] is None for r in rows_nomkt)
        all_rows.extend(rows_c0)

    out_path = tmp_path / "shadow_latest.json"
    write_shadow_file(all_rows, out_path)
    payload = read_shadow_file(out_path)
    assert payload["shadow_only"] is True
    assert payload["count"] == len(all_rows)
    assert payload["rows"] == all_rows

    # Pre-lock rescore succeeds before lock and raises at/after lock
    rescored = rescore_prelock(
        rows_c0,
        nhl_slate,
        nhl_bundle,
        lock_time="2025-11-06T00:00:00Z",
        now_utc="2025-11-05T23:00:00Z",
        market_probs=mkt,
        c=0.5,
    )
    assert len(rescored) == len(rows_c0)
    with pytest.raises(ValueError, match="lock_time"):
        rescore_prelock(
            rows_c0,
            nhl_slate,
            nhl_bundle,
            lock_time="2025-11-06T00:00:00Z",
            now_utc="2025-11-06T00:00:00Z",
        )


def test_bank_shadow_cli_exits_code_2_when_no_local_data(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/mod04d_bank_shadow.py",
            "--date",
            "2099-01-01",
            "--family",
            "nba_points",
            "--out-dir",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert "No local slate" in proc.stderr
