"""nba.shots: court geometry, the raw-CSV adapter, the league artifact and the player read path (synthetic shots only)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nba.shots import build as B
from nba.shots import charts as C
from nba.shots.court import HEX_R, ZONE_KEYS, hex_center, hex_of, shot_group


PLAYERS = {"NYK": (1, "Jalen Brunson", 10, "rim"), "BOS": (2, "Jayson Tatum", 20, "corner"), "MIA": (3, "Bam Adebayo", 30, "rim")}
PAIRS = [("NYK", "BOS"), ("MIA", "NYK"), ("BOS", "MIA")]    # three teams, so every team id maps to exactly one tricode


def _raw(n_games=6, seed=1):
    """Three teams rotating through home/away games; layups at the rim, corner threes, mid-range."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        h, a = PAIRS[g % 3]
        for team in (h, a):
            pid, name, tid, zone = PLAYERS[team]
            for k in range(25):
                if zone == "rim":
                    x, y, bz, ab, st = rng.normal(0, 1.0), 5.25 + rng.uniform(0.2, 3), "Restricted Area", "C", "2PT Field Goal"
                elif k % 2:
                    x, y, bz, ab, st = -22.0 + rng.normal(0, .2), 3.0 + rng.uniform(0, 7), "Left Corner 3", "L", "3PT Field Goal"
                else:
                    x, y, bz, ab, st = rng.uniform(-10, 10), rng.uniform(15, 20), "Mid-Range", "C", "2PT Field Goal"
                rows.append({
                    "SEASON_1": 2025, "TEAM_ID": tid, "PLAYER_ID": pid, "PLAYER_NAME": name,
                    "GAME_DATE": f"01-{g + 1:02d}-2025", "GAME_ID": 22400000 + g, "HOME_TEAM": h, "AWAY_TEAM": a,
                    "SHOT_MADE": bool(rng.random() < 0.55), "ACTION_TYPE": "Driving Layup Shot" if zone == "rim" else "Jump Shot",
                    "SHOT_TYPE": st, "BASIC_ZONE": bz, "ZONE_ABB": ab, "LOC_X": float(x), "LOC_Y": float(y),
                    "QUARTER": 1, "MINS_LEFT": 11 - k // 10, "SECS_LEFT": (k * 7) % 60,
                })
    return pd.DataFrame(rows)


def test_hex_roundtrip_and_zones():
    q, r = hex_of(3.2, 11.0)
    cx, cy = hex_center(q, r)
    assert abs(cx - 3.2) <= HEX_R and abs(cy - 11.0) <= HEX_R
    assert hex_of(cx, cy) == (q, r)
    assert len(ZONE_KEYS) == 14 and len(set(ZONE_KEYS)) == 14


@pytest.mark.parametrize("action,group", [
    ("Alley Oop Dunk Shot", "Dunk"), ("Driving Finger Roll Layup Shot", "Layup"), ("Pullup Jump shot", "Pull-up"),
    ("Step Back Jump shot", "Step-back"), ("Turnaround Fadeaway shot", "Fadeaway"), ("Driving Floating Jump Shot", "Floater"),
    ("Tip Layup Shot", "Tip-in"), ("Turnaround Hook Shot", "Hook"), ("Jump Shot", "Jump shot"), (None, "Jump shot"),
])
def test_shot_group(action, group):
    assert shot_group(action) == group


def test_normalize_derives_team_home_opp_and_drops_heaves_and_dups():
    raw = _raw()
    heave = raw.iloc[[0]].copy()
    heave["LOC_Y"] = 70.0                                  # a backcourt heave
    dup = raw.iloc[[1]].copy()                              # an exact repeat of one attempt
    df = B.normalize_csv_frame(pd.concat([raw, heave, dup], ignore_index=True))
    assert len(df) == len(raw) and (df["y"] <= 40).all()
    assert set(df["team"]) == {"NYK", "BOS", "MIA"}
    g0 = df[(df["game_id"] == "22400000") & (df["player_id"] == 1)]
    assert g0["home"].all() and (g0["opp"] == "BOS").all()
    g1 = df[df["game_id"] == "22400001"]                   # MIA home, NYK away
    assert (g1["home"] == (g1["team"] == "MIA")).all() and (g1[g1["team"] == "NYK"]["opp"] == "MIA").all()
    assert (df["season"] == 2024).all()
    assert df.loc[df["zone"] >= 0, "zone"].map(lambda i: ZONE_KEYS[i]).str.startswith("Restricted").any()


def test_league_artifact_counts_and_defence():
    df = B.normalize_csv_frame(_raw())
    art = B.league_artifact(df, 2024, "synthetic")
    assert art["label"] == "2024-25" and art["fga"] == len(df) and art["fgm"] == int(df["made"].sum())
    assert sum(z["fga"] for z in art["zones"]) == int((df["zone"] >= 0).sum())
    assert set(art["defense"]) == {"NYK", "BOS", "MIA"}
    # the defence table is keyed by the DEFENDING team: BOS defends every shot NYK took against it
    vs_bos = df[(df["team"] == "NYK") & (df["opp"] == "BOS")]
    assert art["defense"]["BOS"]["fga"] == len(df[df["opp"] == "BOS"]) and len(vs_bos) == 50
    json.dumps(art)                                         # serialisable


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    C.clear_cache()
    monkeypatch.setattr(C, "DEFAULT_MIN_FGA", 50)               # the synthetic players shoot 100-150 times
    csv = tmp_path / "raw.csv"
    _raw().to_csv(csv, index=False)
    # the raw file carries every column the builder reads
    full = _raw()
    for c in B.RAW_COLS:
        assert c in full.columns
    B.build(str(csv), [2024], "synthetic")
    yield tmp_path
    C.clear_cache()


def test_shot_chart_reads_built_artifacts(store):
    out = C.shot_chart("Jalen Brunson", opp="BOS")
    assert out["status"] == "ok" and out["label"] == "2024-25" and out["player"] == "Jalen Brunson"
    assert out["fga"] == 100 and out["n_games"] == 4 and len(out["shots"]) == 100
    assert out["fgm"] == sum(s[2] & 1 for s in out["shots"])
    assert out["fg3a"] == 0 and sum(z["fga"] for z in out["zones"]) == 100
    assert out["venue"]["home"]["games"] == 2 and out["venue"]["away"]["games"] == 2
    assert len(out["games"]) == 4 and out["games"][0]["date"] < out["games"][-1]["date"]
    assert out["opponent"]["team"] == "BOS" and out["opponent"]["n_teams"] == 3
    rim = next(z for z in out["zones"] if z["key"] == "Restricted Area|C")
    assert rim["fga"] == 100 and rim["lfga"] == 200
    cells = out["hexes"]
    assert cells and sum(c[2] for c in cells) <= 100 and all(c[4] >= B.MIN_HEX_FGA for c in cells)
    d = out["dist"]
    assert len(d["player"]) == len(d["league"]) == 35 and sum(a for a, _ in d["player"]) == 100
    assert sum(a for a, _ in d["league"]) == 300 and all(m <= a for a, m in d["league"])
    assert sum(a for a, _ in d["player"][:6]) > 90                                   # the synthetic rim shooter stays within 6 ft
    assert out["grid"] and all(len(c) == 3 for c in out["grid"])        # every league cell, so the zone view can tint the whole court
    tatum = C.shot_chart("Jayson Tatum")
    assert tatum["fg3a"] > 0 and tatum["team"] == "BOS" and tatum["fg3m"] <= tatum["fg3a"]


def test_shot_chart_fails_soft(store, tmp_path):
    assert C.shot_chart("Nobody Atall")["status"] == "no_player"
    assert C.shot_chart(None)["status"] == "no_player"
    assert C.shot_chart("Jalen Brunson", season=1999)["status"] == "ok"     # unknown season falls back to the newest
    assert C.shot_chart("Jalen Brunson", opp="XXX")["opponent"] is None
    assert C.shot_chart("Jayson Tatum", opp="NY")["opponent"]["team"] == "NYK"     # ESPN abbreviation folds to the tricode


def test_shot_chart_with_no_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path / "empty"))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    C.clear_cache()
    assert C.shot_chart("Jalen Brunson")["status"] == "no_data"
    C.clear_cache()


@pytest.fixture(scope="module")
def client():
    with patch("main._get_engine", return_value=MagicMock()):
        import main as m
        m.app.config["TESTING"] = True
        m.SITE_API_KEY = "k"
        with m.app.test_client() as c:
            yield c


def test_route_requires_key_and_passes_args(client):
    assert client.get("/api/public/edge/shotchart?player=x").status_code == 401
    with patch("nba.shots.charts.shot_chart", return_value={"status": "ok"}) as f:
        r = client.get("/api/public/edge/shotchart?player=Jalen%20Brunson&season=2024&opp=bos", headers={"X-API-Key": "k"})
    assert r.status_code == 200 and r.get_json() == {"status": "ok"} and r.headers["Cache-Control"] == "public, max-age=300"
    assert f.call_args.args == ("Jalen Brunson",) and f.call_args.kwargs == {"season": 2024, "opp": "BOS"}
    assert client.get("/api/public/edge/shotchart?sport=nhl&player=x", headers={"X-API-Key": "k"}).status_code == 400


# -- ESPN play-by-play adapter -------------------------------------------------------------------------------------------
from nba.shots import espn as E


def _pbp():
    """Six shooting plays from one game: rim make, a corner three at each basket end, a mid-range miss, a free throw, a playoff shot."""
    base = dict(shooting_play=True, season_type=2, game_id=401, game_date="2026-10-28", home_team_id=18, away_team_id=2,
                home_team_abbrev="NY", away_team_abbrev="BOS", athlete_id_1=3934672.0, athlete_name_1="Jalen Brunson", type_text="Jump Shot")
    rows = [
        dict(base, coordinate_x=40.75, coordinate_y=0.0, text="makes 2-foot layup", scoring_play=True, team_id=18, type_text="Driving Layup Shot"),
        dict(base, coordinate_x=-36.75, coordinate_y=-22.0, text="makes 24-foot three point jumper", scoring_play=True, team_id=18),   # left basket
        dict(base, coordinate_x=36.75, coordinate_y=-22.0, text="misses 25-foot three point jumper", scoring_play=False, team_id=2,
             athlete_id_1=4065648.0, athlete_name_1="Jayson Tatum"),
        dict(base, coordinate_x=25.0, coordinate_y=3.0, text="misses 17-foot jumper", scoring_play=False, team_id=18),
        dict(base, coordinate_x=-28.0, coordinate_y=0.0, text="makes free throw 1 of 2", scoring_play=True, team_id=18, type_text="Free Throw - 1 of 2"),
        dict(base, coordinate_x=40.75, coordinate_y=0.0, text="makes 2-foot layup", scoring_play=True, team_id=18, season_type=3),
    ]
    return pd.DataFrame(rows)


def test_espn_canonical_frame_folds_both_baskets_and_flags_right():
    x, y = E.canonical_xy(np.array([40.75, -40.75, 36.75, -36.75]), np.array([0.0, 0.0, 22.0, -22.0]))
    assert list(np.round(y[:2], 2)) == [6.25, 6.25]                                # rim shots at either end land 6.25 ft from the baseline
    assert x[2] == 22.0 and x[3] == 22.0                                           # 180-degree fold keeps handedness
    x2, _ = E.canonical_xy(np.array([36.75]), np.array([-22.0]))
    assert x2[0] == -22.0                                                          # negative = the shooter's right


def test_espn_frame_drops_free_throws_playoffs_and_resolves_teams_and_zones():
    df = E.normalize_espn_frame(_pbp(), 2027)
    assert len(df) == 4 and (df["season"] == 2026).all()
    assert set(df["team"]) == {"NYK", "BOS"} and set(df["opp"]) == {"NYK", "BOS"}
    rim = df.iloc[0]
    assert rim["zone"] == ZONE_KEYS.index("Restricted Area|C") and rim["home"] and rim["group"] == "Layup" and rim["made"]
    threes = {int(r.player_id): ZONE_KEYS[r.zone] for r in df.itertuples() if r.three}
    assert threes == {3934672: "Left Corner 3|L", 4065648: "Right Corner 3|R"}         # left-basket (-36.75, -22) folds to +22 = left; (36.75, -22) = right
    assert df[df["three"]]["made"].sum() == 1


def test_zone_index_exact_restricted_area_and_unmapped():
    z = E.zone_index(np.array([0.0, 0.0, 3.9]), np.array([5.25, 10.0, 5.25]))
    ra = ZONE_KEYS.index("Restricted Area|C")
    assert z[0] == ra and z[2] == ra                                                # on the rim / 3.9 ft out: restricted area
    assert z[1] == ZONE_KEYS.index("In The Paint (Non-RA)|C")                      # 4.75 ft out, inside the lane
    assert E.zone_index(np.array([24.9]), np.array([39.9]))[0] in (-1, *range(14))


def test_build_espn_writes_the_season_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    C.clear_cache()
    p = tmp_path / "pbp.parquet"
    _pbp().to_parquet(p)
    out = B.build_espn(str(p), 2027)
    assert list(out) == [2026] and out[2026]["rows"] == 4 and out[2026]["last_game"] == "2026-10-28"
    art = json.loads((tmp_path / "NBA/shots/league_2026.json").read_text())
    assert art["label"] == "2026-27" and "ESPN" in art["source"]
    C.clear_cache()


def test_default_season_skips_a_thin_current_season_but_an_explicit_one_has_no_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("MLB_BASE_DATA", str(tmp_path))
    monkeypatch.delenv("MLB_GCS_BUCKET", raising=False)
    C.clear_cache()
    old = B.normalize_csv_frame(_raw())                                    # season 2024: 100 attempts for Brunson
    new = old.copy()
    new["season"] = 2025
    new = new[new["game_id"].isin(["22400000"])]                          # season 2025: 25 attempts
    B.write_season(old, 2024, "t")
    B.write_season(new, 2025, "t")
    monkeypatch.setattr(C, "DEFAULT_MIN_FGA", 60)
    C.clear_cache()
    assert C.shot_chart("Jalen Brunson")["season"] == 2024                  # 25 < 60: fall back to last season
    assert C.shot_chart("Jalen Brunson", season=2025)["fga"] == 25         # asked for explicitly: shown as is
    C.clear_cache()
