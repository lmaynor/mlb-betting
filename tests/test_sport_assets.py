"""NHL/NBA frontend assets: the generated team table must match the Python source of truth; every shipped logo must be a
validated SVG that matches its manifest; the SVG validator must reject hostile input; player maps must be sane."""
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_sport_assets as bsa  # noqa: E402

PUBLIC = ROOT / "beezy-vip" / "public"


def test_committed_team_table_matches_the_python_tables():
    committed = json.loads((ROOT / "beezy-vip" / "lib" / "sport-teams.json").read_text())
    assert committed == bsa.team_table(), "lib/sport-teams.json drifted: run scripts/build_sport_assets.py --teams"


@pytest.mark.parametrize("sport,expected", [("nhl", 64), ("nba", 60)])
def test_every_logo_is_present_valid_and_matches_its_manifest(sport, expected):
    d = PUBLIC / "logos" / sport
    man = json.loads((d / "manifest.json").read_text())["files"]
    assert len(man) == expected
    for fname, meta in man.items():
        data = (d / fname).read_bytes()
        assert bsa.validate_svg(data)[0], fname
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], f"{fname} changed since it was validated"


@pytest.mark.parametrize("bad", [
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<svg onload='x()'></svg>",
    b"<svg><a href='javascript:alert(1)'><rect/></a></svg>",
    b"<svg><foreignObject><div/></foreignObject></svg>",
    b"<svg><image href='https://evil.example/x.png'/></svg>",
    b"<svg><use xlink:href='//evil.example/x.svg#a'/></svg>",
    b"<!DOCTYPE svg [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><svg>&x;</svg>",
    b"<html><body>not an svg</body></html>",
    b"<svg>" + b"x" * 300_000 + b"</svg>",
])
def test_svg_validator_rejects_hostile_or_wrong_input(bad):
    assert bsa.validate_svg(bad)[0] is False


def test_svg_validator_accepts_a_plain_vector():
    assert bsa.validate_svg(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
                            b'<style>.a{fill:#fff}</style><path class="a" d="M0 0h1v1z"/></svg>')[0]


def test_player_map_keys_policy_and_slug_parity():
    rows = [("Jack Hughes", "NJD", 1), ("Jack Hughes", "VAN", 2), ("Alex Ovechkin", "WSH", 3), ("Nikola Jokić", "DEN", 4)]
    m = bsa._to_map(rows)
    assert "jack_hughes" not in m                                  # duplicate name: NO bare key (never the wrong face)
    assert m["jack_hughes__njd"] == 1 and m["jack_hughes__van"] == 2
    assert m["alex_ovechkin"] == 3 and m["alex_ovechkin__wsh"] == 3
    assert m["nikola_jokic"] == 4                                  # accent-folded, same algorithm as the TS slug


@pytest.mark.parametrize("sport,min_players", [("nhl", 700), ("nba", 550)])
def test_shipped_player_maps_are_complete_enough(sport, min_players):
    m = json.loads((PUBLIC / "headshots" / f"{sport}_player_map.json").read_text())
    team_keys = [k for k in m if "__" in k]
    assert len(team_keys) >= min_players
    assert all(isinstance(v, int) and v > 0 for v in m.values())
    assert len({k.split("__")[1] for k in team_keys}) == (32 if sport == "nhl" else 30)    # every team represented


def test_nfl_rows_take_the_nflcom_token_for_skill_players_only():
    import pandas as pd
    base = "https://static.www.nfl.com/image/upload/f_auto,q_auto/league/"
    roster = pd.DataFrame([
        {"full_name": "C.J. Stroud", "team": "HOU", "position": "QB", "headshot_url": base + "tt4zrtxlhifaljhj0rn7"},
        {"full_name": "Trevor Lawrence", "team": "JAX", "position": "QB", "headshot_url": base + "pyevyueqikk1vbkh9ju1"},   # JAX -> ev code JAC
        {"full_name": "Some Kicker", "team": "ATL", "position": "K", "headshot_url": base + "abcdefghij0123456789"},       # not a prop position
        {"full_name": "No Photo", "team": "KC", "position": "WR", "headshot_url": None},
        {"full_name": "Odd Url", "team": "KC", "position": "WR", "headshot_url": "https://evil.example/league/abcdefghij0123456789"},
        {"full_name": "Ghost Team", "team": "XXX", "position": "WR", "headshot_url": base + "abcdefghij0123456789"},
    ])
    rows = bsa.nfl_rows(roster)
    assert rows == [("C.J. Stroud", "HOU", "tt4zrtxlhifaljhj0rn7"), ("Trevor Lawrence", "JAC", "pyevyueqikk1vbkh9ju1")]
    m = bsa._to_map(rows, cast=str)
    assert m["cj_stroud"] == "tt4zrtxlhifaljhj0rn7" and m["trevor_lawrence__jac"] == "pyevyueqikk1vbkh9ju1"


def test_shipped_nfl_player_map_is_complete_enough():
    m = json.loads((PUBLIC / "headshots" / "nfl_player_map.json").read_text())
    team_keys = [k for k in m if "__" in k]
    assert len(team_keys) >= 800
    assert all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9]{10,40}", v) for v in m.values())     # tokens, never paths or URLs
    assert len({k.split("__")[1] for k in team_keys}) == 32                                          # every franchise represented
