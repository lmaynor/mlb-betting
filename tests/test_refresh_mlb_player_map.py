"""scripts/refresh_mlb_player_map.py: slug parity with the frontend, merge-never-drops, placeholder detection."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import refresh_mlb_player_map as rm  # noqa: E402

LIVE_MAP = json.loads((ROOT / "beezy-vip" / "public" / "headshots" / "player_map.json").read_text())


def test_slug_matches_the_keys_already_in_the_shipped_map():
    for name, key in [("Aaron Judge", "aaron_judge"), ("Ha-Seong Kim", "ha_seong_kim"),
                      ("Randy Vásquez", "randy_vásquez"), ("Jazz Chisholm Jr.", "jazz_chisholm_jr")]:
        assert rm.slug(name) == key
    assert "aaron_judge" in LIVE_MAP and "ha_seong_kim" in LIVE_MAP          # same algorithm produced the shipped keys


def test_merge_adds_only_new_keys_and_never_drops_or_overwrites():
    existing = {"aaron_judge": 592450, "retired_guy": 1, "max_muncy": 571970}
    people = [{"fullName": "Aaron Judge", "id": 999},          # existing key: id must NOT change
              {"fullName": "Max Muncy", "id": 691777},          # duplicate name: first one wins
              {"fullName": "New Callup", "id": 777},
              {"fullName": "", "id": 5}, {"fullName": "No Id"}]
    merged, added = rm.merge(existing, people)
    assert added == {"new_callup": 777}
    assert merged["aaron_judge"] == 592450 and merged["max_muncy"] == 571970 and "retired_guy" in merged
    assert list(merged) == sorted(merged)


class _R:
    def __init__(self, content=b"", payload=None):
        self.content, self._p = content, payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_fetch_roster_verifies_tls_by_default_path_and_parses():
    seen = {}

    def get(url, headers=None, timeout=None, **kw):
        seen.update(url=url, kw=kw)
        return _R(payload={"people": [{"fullName": "A B", "id": 1}]})
    assert rm.fetch_roster(2026, get=get) == [{"fullName": "A B", "id": 1}]
    assert "season=2026" in seen["url"] and "verify" not in seen["kw"]      # never passes verify=False


def test_photo_missing_flags_only_the_generic_placeholder():
    generic, real = b"GENERIC-SILHOUETTE", b"REAL-PHOTO-BYTES"

    def get(url, headers=None, timeout=None):
        return _R(content=generic if url.endswith("/1/headshot/silo/current") or "/people/42/" in url else real)
    assert rm.photo_missing({"has_photo": 7, "no_photo": 42}, get=get) == ["no_photo"]
    assert rm.generic_placeholder_hash(get) == hashlib.sha256(generic).hexdigest()
