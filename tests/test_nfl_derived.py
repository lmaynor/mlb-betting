"""Offline tests for nfl.data.derived (synthetic play-by-play; no GCS)."""
import numpy as np
import pandas as pd
import pytest

from nfl.data import derived as D


def _play(pid, off, deff, **kw):
    base = {"game_id": "2026_01_BBB_AAA", "play_id": pid, "season": 2026, "week": 1, "season_type": "REG",
            "posteam": off, "defteam": deff, "drive": 1, "play_type": "pass", "pass": 1, "rush": 0, "pass_attempt": 1,
            "rush_attempt": 0, "qb_dropback": 1, "qb_kneel": 0, "qb_spike": 0, "sack": 0, "epa": 0.1, "success": 1,
            "yards_gained": 8, "air_yards": 6.0, "yards_after_catch": 2.0, "cpoe": 1.0, "xpass": 0.6, "pass_oe": 40.0,
            "down": 1, "wp": 0.5, "half_seconds_remaining": 1500.0, "game_seconds_remaining": 3000.0 - pid * 30,
            "yardline_100": 60.0, "shotgun": 1, "no_huddle": 0, "receiver_player_id": "wr1", "rusher_player_id": None,
            "passer_player_id": "qb1", "complete_pass": 1}
    base.update(kw)
    return base


def _rush(pid, off, deff, **kw):
    return _play(pid, off, deff, **{"play_type": "run", "pass": 0, "rush": 1, "pass_attempt": 0, "rush_attempt": 1,
                                    "qb_dropback": 0, "air_yards": np.nan, "cpoe": np.nan, "xpass": 0.4,
                                    "pass_oe": -40.0, "receiver_player_id": None, "passer_player_id": None,
                                    "rusher_player_id": "rb1", "complete_pass": 0, "yards_gained": 4, **kw})


@pytest.fixture
def pbp():
    rows = [
        _play(1, "AAA", "BBB"),
        _play(2, "AAA", "BBB", air_yards=25.0, yards_gained=30, yardline_100=18.0, down=2),
        _rush(3, "AAA", "BBB", yardline_100=8.0, yards_gained=12),
        _rush(4, "AAA", "BBB", yardline_100=2.0, yards_gained=1, down=3),                  # not neutral (3rd down)
        _play(5, "AAA", "BBB", sack=1, pass_attempt=0, receiver_player_id=None, air_yards=np.nan, yards_gained=-7,
              epa=-1.0, success=0, complete_pass=0),
        _play(6, "AAA", "BBB", down=2, wp=0.95),                                           # not neutral (wp)
        _play(7, "AAA", "BBB", half_seconds_remaining=90.0),                               # not neutral (2-min)
        # kneel / spike / no_play never count
        _rush(8, "AAA", "BBB", qb_kneel=1), _play(9, "AAA", "BBB", qb_spike=1),
        _play(10, "AAA", "BBB", play_type="no_play"),
        # the other team's offense
        _rush(11, "BBB", "AAA", drive=2, yards_gained=3), _rush(12, "BBB", "AAA", drive=2, yards_gained=15),
    ]
    return pd.DataFrame(rows)


def test_offensive_plays_drops_kneels_spikes_and_no_plays(pbp):
    p = D.offensive_plays(pbp)
    assert sorted(p["play_id"]) == [1, 2, 3, 4, 5, 6, 7, 11, 12]
    assert set(p["_pass"]) == {0.0, 1.0}


def test_neutral_mask_requires_down_wp_and_clock(pbp):
    p = D.offensive_plays(pbp)
    n = p.set_index("play_id")
    mask = D.neutral_mask(p)
    mask.index = p["play_id"]
    assert [pid for pid, v in mask.items() if v and pid < 11] == [1, 2, 3, 5]
    assert not mask[4] and not mask[6] and not mask[7]


def test_team_game_offense_rates(pbp):
    tg = D.team_game_from_pbp(pbp).set_index("team")
    a = tg.loc["AAA"]
    assert a["off_plays"] == 7 and a["off_dropbacks"] == 5                      # plays 1,2,5,6,7 are dropbacks
    assert a["off_pass_rate"] == pytest.approx(5 / 7)
    assert a["off_neutral_plays"] == 4 and a["off_neutral_pass_rate"] == pytest.approx(3 / 4)
    assert a["off_sack_rate"] == pytest.approx(1 / 5)
    assert a["off_expl_pass_rate"] == pytest.approx(1 / 4)                      # attempts 1,2,6,7; only #2 >= 20 yds
    assert a["off_expl_rush_rate"] == pytest.approx(1 / 2)                      # rushes 3 (12 yd), 4 (1 yd)
    assert a["off_rz_plays"] == 3 and a["off_rz_pass_rate"] == pytest.approx(1 / 3)   # plays 2(18),3(8),4(2)
    assert a["off_adot"] == pytest.approx((6 + 25 + 6 + 6) / 4)
    assert a["off_pass_oe"] == pytest.approx((40 * 5 - 40 * 2) / 7)


def test_team_game_defense_is_the_opponents_offense_and_keys_are_complete(pbp):
    tg = D.team_game_from_pbp(pbp).set_index("team")
    assert tg.loc["BBB", "def_plays"] == tg.loc["AAA", "off_plays"]
    assert tg.loc["BBB", "def_epa_play"] == pytest.approx(tg.loc["AAA", "off_epa_play"])
    assert tg.loc["AAA", "opponent"] == "BBB" and tg.loc["BBB", "opponent"] == "AAA"
    assert set(tg["season"]) == {2026} and set(tg["week"]) == {1}


def test_pace_uses_same_drive_gaps_only(pbp):
    tg = D.team_game_from_pbp(pbp).set_index("team")
    # AAA plays 1,2,3 .. are 30s apart on one drive; BBB's drive-2 plays are 30s apart as well
    assert tg.loc["AAA", "off_sec_per_play_neutral"] == pytest.approx(30.0)
    pbp2 = pbp.copy()
    pbp2.loc[pbp2["play_id"] == 2, "drive"] = 9      # play 2 now opens its own drive (no gap), play 3 then
    tg2 = D.team_game_from_pbp(pbp2).set_index("team")   # measures from play 1 (60s); play 5 stays 30s -> mean 45
    assert tg2.loc["AAA", "off_sec_per_play_neutral"] == pytest.approx(45.0)


def test_player_game_usage(pbp):
    pg = D.player_game_from_pbp(pbp).set_index("player_id")
    wr, rb, qb = pg.loc["wr1"], pg.loc["rb1"], pg.loc["qb1"]
    assert wr["tgt_pbp"] == 4 and wr["deep_tgt"] == 1 and wr["air_yards"] == 6 + 25 + 6 + 6
    assert wr["tgt_rz20"] == 1 and wr["tgt_in10"] == 0
    assert qb["dropbacks_pbp"] == 5 and qb["tgt_pbp"] == 0
    aaa_rb = D.player_game_from_pbp(pbp)
    r = aaa_rb[(aaa_rb["player_id"] == "rb1") & (aaa_rb["team"] == "AAA")].iloc[0]
    assert r["car_pbp"] == 2 and r["car_rz10"] == 2 and r["car_in3"] == 1 and r["rush_expl10"] == 1
    assert r["tgt_pbp"] == 0 and np.isnan(r["rec_epa"])                          # no target that game: NaN, not 0


def test_empty_pbp_returns_typed_empty_frames():
    empty = pd.DataFrame({c: [] for c in D.PBP_COLUMNS})
    assert D.team_game_from_pbp(empty).empty and D.player_game_from_pbp(empty).empty
    assert "off_epa_play" in D.team_game_from_pbp(empty).columns


class _Mem:
    """In-memory stand-in for mlb_core.storage."""
    def __init__(self):
        self.blobs = {}

    def exists(self, k): return k in self.blobs
    def read_bytes(self, k): return self.blobs[k]
    def write_bytes(self, b, k): self.blobs[k] = b
    def write_parquet(self, df, k): self.blobs[k] = df.copy()
    def read_parquet(self, k, columns=None):
        df = self.blobs[k]
        return df[columns] if columns else df
    def list_keys(self, prefix): return [k for k in self.blobs if k.startswith(prefix)]


def test_build_season_is_incremental_on_source_sha_and_version(monkeypatch, pbp):
    import json
    from mlb_core import storage
    from nfl import config
    mem = _Mem()
    for name in ("exists", "read_bytes", "write_bytes", "write_parquet", "read_parquet", "list_keys"):
        monkeypatch.setattr(storage, name, getattr(mem, name))
    key = config.DATASETS["pbp"].key(2026)
    mem.blobs[config.MANIFEST_KEY] = json.dumps({key: {"sha256": "abc"}}).encode()
    mem.blobs[key] = pbp

    r1 = D.run([2026])
    assert r1["status"] == "ok" and r1["results"][0]["status"] == "built"
    assert D.table_key(D.TEAM_TABLE, 2026) in mem.blobs
    assert D.run([2026])["results"][0]["status"] == "current"                    # same sha + version: no rebuild
    mem.blobs[config.MANIFEST_KEY] = json.dumps({key: {"sha256": "def"}}).encode()
    assert D.run([2026])["results"][0]["status"] == "built"                      # upstream changed
    monkeypatch.setattr(D, "DERIVED_VERSION", "pbp_ctx_v2")
    assert D.run([2026])["results"][0]["status"] == "built"                      # definition changed
    assert D.run([2027])["results"][0]["status"] == "no_pbp"                     # not mirrored: reported, not raised

    from nfl.data.read import derived_seasons, load_derived
    assert derived_seasons(D.TEAM_TABLE) == [2026]
    assert len(load_derived(D.TEAM_TABLE, seasons=[2026])) == 2
    with pytest.raises(FileNotFoundError):
        load_derived(D.PLAYER_TABLE, seasons=[2025])
