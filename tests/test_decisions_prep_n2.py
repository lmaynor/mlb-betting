"""Tests for the N2 event-gate prep workstream (F2 fixture + F7-N2 code).

Pure data/code tests: the fixtures are local files and the module under
test makes no network or API calls.
"""

import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ev.decisions_prep import n2_event_gate as n2  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "decisions"
N2_ITEMS = FIXTURES / "n2_event_gate" / "items.jsonl"
N5_ITEMS = FIXTURES / "n5_corrections" / "items.jsonl"

LEAGUES = {"MLB", "NBA", "NHL", "NFL"}


def _load(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _disruptions():
    return [r for r in _load(N2_ITEMS) if "class" in r]


def _normals():
    return [r for r in _load(N2_ITEMS) if r.get("label") == "no_change"]


# ---------------------------------------------------------------------------
# F2 fixture: shape and counts
# ---------------------------------------------------------------------------

def test_n2_fixture_counts():
    disruptions = _disruptions()
    normals = _normals()
    assert len(disruptions) >= 50
    assert len(normals) >= 200
    by_class = {}
    for item in disruptions:
        by_class[item["class"]] = by_class.get(item["class"], 0) + 1
    # Ruling aims: >= 10 postponements, >= 5 suspensions.
    assert by_class.get("postponed", 0) >= 10
    assert by_class.get("suspended", 0) >= 5


def test_n2_disruption_shape():
    for item in _disruptions():
        assert item["league"] in LEAGUES
        assert item["class"] in n2.ALL_CLASSES and item["class"] != "no_change"
        game = item["game"]
        assert game["away"] and game["home"]
        day = date.fromisoformat(game["date"])
        assert date(2023, 1, 1) <= day <= date(2026, 12, 31)
        assert item["blurb_text"].strip()
        assert item["source_name"].strip()
        assert item["source_url"].startswith("http")
        assert isinstance(item["retrieved"], bool)
        assert isinstance(item["seeded"], bool)
        assert item["seeded"] == (not item["retrieved"])


def test_n2_normal_shape():
    for item in _normals():
        assert item["label"] == "no_change"
        assert item["league"] in LEAGUES
        assert item["blurb_text"].strip()
        assert item["source"].strip()
        assert item["game"]["date"]


def test_n2_blurbs_deduped():
    texts = [r["blurb_text"] for r in _load(N2_ITEMS)]
    assert len(texts) == len(set(texts))


# ---------------------------------------------------------------------------
# F4 fixture: shape and counts
# ---------------------------------------------------------------------------

def test_n5_fixture_shape_and_count():
    items = _load(N5_ITEMS)
    assert len(items) >= 25
    for item in items:
        assert item["league"] in LEAGUES
        date.fromisoformat(item["game_date"])
        assert item["player"].strip()
        assert item["team"].strip()
        assert item["stat"].strip()
        assert item["value_before"] != item["value_after"]
        if item["announced_date"] is not None:
            date.fromisoformat(item["announced_date"])
        assert isinstance(item["prose_announced"], bool)
        assert item["source_name"].strip()
        assert item["source_url"].startswith("http")


# ---------------------------------------------------------------------------
# build_questions: published envelope style
# ---------------------------------------------------------------------------

def test_build_questions_envelope():
    item = _disruptions()[0]
    questions = n2.build_questions(item)
    assert set(questions) == {n2.CHOICE_QUESTION_ID, n2.NOUL_QUESTION_ID}
    choice = questions[n2.CHOICE_QUESTION_ID]
    assert choice["type"] == "choice"
    assert choice["instructions"].strip()
    assert set(choice["criteria"]) == set(n2.ALL_CLASSES)
    assert all(desc.strip() for desc in choice["criteria"].values())
    noul = questions[n2.NOUL_QUESTION_ID]
    assert noul["type"] == "noul"
    assert noul["instructions"].strip()


# ---------------------------------------------------------------------------
# match_event: deterministic slate matching
# ---------------------------------------------------------------------------

def _slate_entry(away, home, day, league="MLB"):
    return {"league": league, "away": away, "home": home, "date": day}


def test_match_event_exact():
    item = _disruptions()[0]
    game = item["game"]
    slate = [_slate_entry(game["away"], game["home"], game["date"], item["league"])]
    assert n2.match_event(item, slate) is slate[0]


def test_match_event_normalization_variants():
    item = {
        "league": "MLB",
        "game": {"away": "Chicago White Sox", "home": "New York Yankees", "date": "2023-06-07"},
    }
    slate = [_slate_entry("Chi White Sox", "NY Yankees", "2023-06-07")]
    assert n2.match_event(item, slate) is slate[0]


def test_match_event_alias_and_home_away_order():
    item = {
        "league": "MLB",
        "game": {"away": "A's", "home": "Minnesota Twins", "date": "2024-06-15"},
    }
    slate = [_slate_entry("Minnesota Twins", "Oakland Athletics", "2024-06-15")]
    assert n2.match_event(item, slate) is slate[0]


def test_match_event_date_mismatch_returns_none():
    item = _disruptions()[0]
    game = item["game"]
    slate = [_slate_entry(game["away"], game["home"], "2024-01-01", item["league"])]
    assert n2.match_event(item, slate) is None


def test_match_event_team_mismatch_returns_none():
    item = _disruptions()[0]
    game = item["game"]
    slate = [_slate_entry("Boston Red Sox", game["home"], game["date"], item["league"])]
    assert n2.match_event(item, slate) is None


def test_match_event_league_mismatch_and_empty_slate():
    item = _disruptions()[0]
    game = item["game"]
    wrong_league = "NBA" if item["league"] != "NBA" else "MLB"
    slate = [_slate_entry(game["away"], game["home"], game["date"], wrong_league)]
    assert n2.match_event(item, slate) is None
    assert n2.match_event(item, []) is None


# ---------------------------------------------------------------------------
# gate: ruling triggers and pass paths
# ---------------------------------------------------------------------------

def _answer(noul=None, picked=None, probs=None):
    ans = {}
    if noul is not None:
        ans[n2.NOUL_QUESTION_ID] = {"probability": noul}
    if picked is not None:
        entry = {"choice": picked}
        if probs is not None:
            entry["probabilities"] = probs
        ans[n2.CHOICE_QUESTION_ID] = entry
    return ans


def test_gate_noul_trigger():
    ans = _answer(noul=0.80, picked="no_change", probs={"no_change": 0.90})
    assert n2.gate(ans)["action"] == "witness_off"


def test_gate_noul_at_threshold():
    ans = _answer(noul=0.75, picked="no_change", probs={"no_change": 0.90})
    assert n2.gate(ans)["action"] == "witness_off"


def test_gate_low_no_change_with_disruption_pick():
    ans = _answer(noul=0.20, picked="postponed",
                  probs={"no_change": 0.20, "postponed": 0.60})
    assert n2.gate(ans)["action"] == "witness_off"


def test_gate_pass_plain_no_change():
    ans = _answer(noul=0.20, picked="no_change", probs={"no_change": 0.85})
    assert n2.gate(ans)["action"] == "pass"


def test_gate_pass_low_no_change_but_no_concrete_class():
    # P(no_change) is low, but the model still picked no_change: the ruling
    # requires a concrete disruption class for this trigger path.
    ans = _answer(noul=0.20, picked="no_change",
                  probs={"no_change": 0.20, "postponed": 0.50})
    assert n2.gate(ans)["action"] == "pass"


def test_gate_pass_at_no_change_ceiling():
    # P(no_change) == 0.30 is not < 0.30.
    ans = _answer(noul=0.20, picked="suspended",
                  probs={"no_change": 0.30, "suspended": 0.55})
    assert n2.gate(ans)["action"] == "pass"


def test_gate_pass_on_malformed_answer():
    assert n2.gate({})["action"] == "pass"
    assert n2.gate({n2.CHOICE_QUESTION_ID: {"choice": "postponed"}})["action"] == "pass"


def test_gate_accepts_bare_noul_and_wrapped_answers():
    assert n2.gate({n2.NOUL_QUESTION_ID: 0.9})["action"] == "witness_off"
    wrapped = {"answers": _answer(noul=0.9, picked="no_change", probs={"no_change": 0.95})}
    assert n2.gate(wrapped)["action"] == "witness_off"
