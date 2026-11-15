"""Regression tests for the N3 eval cache client (eval_claims.CachedDecisionsClient).

Harness bug (found by the DEC-03 Q2 diagnostics, Nov 13, 2026): the client
split wire keys with ``rsplit("_", 1)``, mangling ``wrong_scope`` -- the only
Stage 1 question id containing an underscore -- so every cached wrong_scope
answer was dropped and P(wrong_scope) scored 0.0 for all 100 claims in
Run 2 as committed. Exactly one Run 2 verdict (N3-C029) turns on the bug.
Wire keys are ``{tag}_{qid}``; question ids may contain underscores
(``wrong_scope``, ``direct_conflict``, ``missing_support``,
``confirm_direct_conflict``), so parsing must resolve against the cached
answer keys, not split positionally.
"""

from __future__ import annotations

import json
from pathlib import Path

from ev.decisions_evals.common import AnswersCache
from ev.decisions_evals.eval_claims import CachedDecisionsClient

_INPUT = "[N3-C999]\nResearch claim under audit:\nClaim ID: N3-C999\nClaim: Some claim."


def _client(tmp_path: Path, answers: dict) -> CachedDecisionsClient:
    path = tmp_path / "answers.jsonl"
    path.write_text(
        json.dumps({"item_id": "N3-C999", "answers": answers}) + "\n",
        encoding="utf-8",
    )
    return CachedDecisionsClient(AnswersCache(path))


def test_wrong_scope_wire_key_round_trips(tmp_path: Path) -> None:
    """The regression: a cached wrong_scope answer must be served."""
    client = _client(
        tmp_path,
        {
            "supported": {"noul": 0.95},
            "wrong_scope": {"noul": 0.30},
            "stale": {"noul": 0.01},
        },
    )
    questions = {
        "N3-C999_supported": {"type": "noul", "instructions": "s"},
        "N3-C999_wrong_scope": {"type": "noul", "instructions": "w"},
        "N3-C999_stale": {"type": "noul", "instructions": "t"},
    }
    result = client.call(_INPUT, questions)
    assert result.abstained is False
    assert result.answers["N3-C999_wrong_scope"] == {"noul": 0.30}
    assert result.answers["N3-C999_supported"] == {"noul": 0.95}
    assert result.answers["N3-C999_stale"] == {"noul": 0.01}


def test_multi_underscore_confirm_key_round_trips(tmp_path: Path) -> None:
    """Stage 2's confirm_direct_conflict id has two underscores."""
    client = _client(tmp_path, {"confirm_direct_conflict": {"noul": 0.42}})
    questions = {
        "N3-C999_confirm_direct_conflict": {"type": "noul", "instructions": "c"},
    }
    result = client.call(_INPUT, questions)
    assert result.answers["N3-C999_confirm_direct_conflict"] == {"noul": 0.42}


def test_uncached_question_stays_absent(tmp_path: Path) -> None:
    """A question with no cached answer stays absent (route abstains on it)."""
    client = _client(tmp_path, {"supported": {"noul": 0.95}})
    questions = {
        "N3-C999_supported": {"type": "noul", "instructions": "s"},
        "N3-C999_direct_conflict": {"type": "noul", "instructions": "d"},
    }
    result = client.call(_INPUT, questions)
    assert "N3-C999_supported" in result.answers
    assert "N3-C999_direct_conflict" not in result.answers
