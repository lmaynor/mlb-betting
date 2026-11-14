"""ev.decisions_evals.eval_corrections -- Evaluation harness for N5 Sentinel.

DOCTRINE:
- Positives = 25 documented corrections from items.jsonl (reconstructed pairs
  via sentinel_items_from_pairs).
- Controls = capture_sample.jsonl rows WITHOUT corrections (capture_row from
  sample row, settled_row = same values, announcement_text = ""). Excludes
  any sample row documenting its own correction.
- Raw evaluation answers are fetched via AnswersCache (answers.jsonl in the
  n5 fixture dir, keyed by settled row_key; Pacer token-bucket rate limiter).
- Scored via run_sentinel with CachedDecisionsClient replaying cached answers.
- Reports: correction recall (bar >= 90%), control false-quarantine rate,
  and per-league recall.
- Writes eval_results.json in the fixture dir and prints.
- CRITICAL: eval scores only live or cached-live answers; without GEMINI_API_KEY
  and with uncached items it RAISES. Never synthesize/simulate answers.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import BatchResult
from ev.decisions_correction_sentinel import (
    run_sentinel,
    sentinel_items_from_pairs,
)
from ev.decisions_evals.common import (
    AnswersCache,
    Pacer,
    live_client,
    run_batched,
)
from ev.decisions_prep import n5_correction_sentinel as n5_prep
from ev.decisions_prep.n5_correction_sentinel import settled_row_key

FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "tests"
    / "fixtures"
    / "decisions"
    / "n5_corrections"
)
DEFAULT_ITEMS_PATH = FIXTURE_DIR / "items.jsonl"
DEFAULT_SAMPLE_PATH = FIXTURE_DIR / "capture_sample.jsonl"
DEFAULT_ANSWERS_PATH = FIXTURE_DIR / "answers.jsonl"
DEFAULT_RESULTS_PATH = FIXTURE_DIR / "eval_results.json"

RECALL_BAR = 0.90


def cache_key_for_row(row: Mapping[str, Any] | tuple) -> str:
    """Produce a canonical colon-separated string key for AnswersCache."""
    if isinstance(row, (tuple, list)):
        return ":".join(str(x) for x in row)
    key = settled_row_key(row)
    return ":".join(str(x) for x in key)


def load_positives(path: Union[str, os.PathLike[str]] = DEFAULT_ITEMS_PATH) -> list[dict[str, Any]]:
    """Load documented corrections from items.jsonl and build evaluation items."""
    raw_items = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_items.append(json.loads(line))

    pairs = sentinel_items_from_pairs(raw_items)
    positives = []
    for raw_item, (c_row, s_row) in zip(raw_items, pairs):
        ckey = cache_key_for_row(s_row)
        rkey = settled_row_key(s_row)
        positives.append({
            "pair": (c_row, s_row),
            "league": str(raw_item.get("league", "")),
            "key": ckey,
            "raw_key": rkey,
            "is_positive": True,
            "player": str(raw_item.get("player", "")),
            "stat": str(raw_item.get("stat", "")),
        })
    return positives


def load_controls(path: Union[str, os.PathLike[str]] = DEFAULT_SAMPLE_PATH) -> list[dict[str, Any]]:
    """Load capture_sample.jsonl rows without corrections as control items."""
    raw_sample = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                raw_sample.append(json.loads(line))

    controls = []
    for row in raw_sample:
        # Exclude rows that document a correction
        if row.get("correction_detected") or (row.get("delta") is not None and row.get("delta") != 0):
            continue

        settled_row = {
            "game_key": row.get("game_key"),
            "game_date": row.get("game_date"),
            "player_id": row.get("player_id"),
            "player": row.get("player"),
            "stat": row.get("stat"),
            "line": row.get("line"),
            "actual": row.get("settled_actual"),
            "value": row.get("settled_actual"),
            "settled_ts": row.get("settled_ts"),
            "league": "NHL",
        }
        capture_row = {
            "game_key": row.get("game_key"),
            "game_date": row.get("game_date"),
            "player_id": row.get("player_id"),
            "player": row.get("player"),
            "stat": row.get("stat"),
            "line": row.get("line"),
            "actual": row.get("settled_actual"),
            "value": row.get("settled_actual"),
            "settled_ts": row.get("settled_ts"),
            "league": "NHL",
            "announcement_text": "",
            "announced_date": "",
            "published_ts": row.get("boxscore_captured_ts") or "",
            "headline": "",
            "body": "",
        }

        ckey = cache_key_for_row(settled_row)
        rkey = settled_row_key(settled_row)
        controls.append({
            "pair": (capture_row, settled_row),
            "league": "NHL",
            "key": ckey,
            "raw_key": rkey,
            "is_positive": False,
            "player": str(row.get("player", "")),
            "stat": str(row.get("stat", "")),
        })
    return controls


def fetch_or_load_answers(
    eval_items: Sequence[Mapping[str, Any]],
    cache_path: Union[str, os.PathLike[str]] = DEFAULT_ANSWERS_PATH,
    client: Optional[Any] = None,
    pacer: Optional[Pacer] = None,
) -> dict[str, dict[str, Any]]:
    """Retrieve answers from AnswersCache, or fetch uncached items live.

    CRITICAL: eval scores only live or cached-live answers; without GEMINI_API_KEY
    and with uncached items it RAISES. Never synthesize/simulate answers.
    """
    cache = AnswersCache(cache_path)

    uncached = [
        it for it in eval_items
        if it["key"] not in cache and str(it["raw_key"]) not in cache
    ]

    if uncached:
        if client is None:
            if not os.environ.get("GEMINI_API_KEY"):
                raise RuntimeError(
                    f"GEMINI_API_KEY is not set and there are {len(uncached)} uncached items. "
                    "Evaluation scores only live or cached-live answers; refusing to synthesize answers."
                )
            client = live_client()
        if pacer is None:
            pacer = Pacer()

        batched_inputs = []
        for it in uncached:
            c_row, s_row = it["pair"]
            prose_item = {
                "published_ts": c_row.get("published_ts") or c_row.get("announced_date") or "",
                "headline": c_row.get("headline") or ("Scoring announcement" if c_row.get("announcement_text") else ""),
                "body": c_row.get("body") if c_row.get("body") is not None else (c_row.get("announcement_text") or ""),
            }
            settled_dict = dict(s_row)
            if "actual" not in settled_dict and "value" in settled_dict:
                settled_dict["actual"] = settled_dict["value"]
            req = n5_prep.build_questions(settled_dict, prose_item)
            batched_inputs.append((req["input"], req["questions"]))

        batch_out = run_batched(client, batched_inputs, pacer=pacer)
        for local_idx, it in enumerate(uncached):
            raw_ans = batch_out.get(local_idx, {})
            cache.store(it["key"], raw_ans)

    all_answers: dict[str, dict[str, Any]] = {}
    for it in eval_items:
        k = it["key"]
        ans = cache.get(k)
        if ans is None:
            ans = cache.get(str(it["raw_key"]), {})
        all_answers[k] = dict(ans)
    return all_answers


class CachedDecisionsClient:
    """DecisionsClient replay stub backed by cached evaluation answers."""

    def __init__(
        self,
        key_to_answers: Mapping[str, Mapping[str, Any]],
        tag_to_key: Mapping[str, str],
    ) -> None:
        self.key_to_answers = dict(key_to_answers)
        self.tag_to_key = dict(tag_to_key)
        self.calls: list[dict[str, Any]] = []

    def call(self, input_text: str, questions: Mapping[str, Any]) -> BatchResult:
        self.calls.append({"input_text": input_text, "questions": dict(questions)})
        wire_answers: dict[str, Any] = {}
        for wire_key in questions:
            parts = wire_key.split("_", 1)
            if len(parts) == 2:
                tag, qid = parts
                item_key = self.tag_to_key.get(tag)
                if item_key is not None:
                    ans_dict = self.key_to_answers.get(item_key, {})
                    if qid in ans_dict:
                        wire_answers[wire_key] = ans_dict[qid]
        return BatchResult(answers=wire_answers, abstained=False)


def score_corrections_eval(
    positives: Sequence[Mapping[str, Any]],
    controls: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score evaluation records against fixture positive / control labels."""
    num_pos = len(positives)
    pos_records = records[:num_pos]
    ctrl_records = records[num_pos:]

    # Positives metrics
    tp = 0
    fn = 0
    league_stats: dict[str, dict[str, Any]] = {}

    for pos_item, rec in zip(positives, pos_records):
        league = str(pos_item.get("league", "UNKNOWN"))
        l_stat = league_stats.setdefault(league, {"planted": 0, "flagged": 0})
        l_stat["planted"] += 1

        is_quarantined = rec.get("status") == "quarantined"
        if is_quarantined:
            tp += 1
            l_stat["flagged"] += 1
        else:
            fn += 1

    recall = (tp / num_pos) if num_pos > 0 else 0.0

    per_league: dict[str, dict[str, Any]] = {}
    for l in sorted(league_stats.keys()):
        s = league_stats[l]
        pl = s["planted"]
        fl = s["flagged"]
        per_league[l] = {
            "planted": pl,
            "flagged": fl,
            "recall": round((fl / pl) if pl > 0 else 0.0, 4),
        }

    # Controls metrics
    num_ctrl = len(controls)
    fp = 0
    tn = 0
    for ctrl_item, rec in zip(controls, ctrl_records):
        if rec.get("status") == "quarantined":
            fp += 1
        else:
            tn += 1

    false_quarantine_rate = (fp / num_ctrl) if num_ctrl > 0 else 0.0

    return {
        "summary": {
            "total_items": num_pos + num_ctrl,
            "positive_items": num_pos,
            "control_items": num_ctrl,
            "quarantined_total": tp + fp,
            "clear_total": fn + tn,
        },
        "metrics": {
            "correction_recall": round(recall, 4),
            "correction_recall_bar": RECALL_BAR,
            "meets_recall_bar": recall >= RECALL_BAR,
            "true_positives": tp,
            "false_negatives": fn,
            "control_false_quarantine_rate": round(false_quarantine_rate, 4),
            "false_positives": fp,
            "true_negatives": tn,
        },
        "per_league": per_league,
        "quarantined_keys": [
            ":".join(str(x) for x in r["row_key"]) if isinstance(r.get("row_key"), (tuple, list)) else str(r.get("row_key"))
            for r in records if r.get("status") == "quarantined" and r.get("row_key")
        ],
        "clear_keys": [
            ":".join(str(x) for x in r["row_key"]) if isinstance(r.get("row_key"), (tuple, list)) else str(r.get("row_key"))
            for r in records if r.get("status") == "clear" and r.get("row_key")
        ],
    }


def evaluate_corrections(
    items_path: Union[str, os.PathLike[str]] = DEFAULT_ITEMS_PATH,
    sample_path: Union[str, os.PathLike[str]] = DEFAULT_SAMPLE_PATH,
    cache_path: Union[str, os.PathLike[str]] = DEFAULT_ANSWERS_PATH,
    out_path: Union[str, os.PathLike[str]] = DEFAULT_RESULTS_PATH,
    client: Optional[Any] = None,
    pacer: Optional[Pacer] = None,
) -> dict[str, Any]:
    """Execute N5 correction sentinel evaluation over the fixture."""
    positives = load_positives(items_path)
    controls = load_controls(sample_path)
    all_items = positives + controls

    answers = fetch_or_load_answers(
        all_items, cache_path=cache_path, client=client, pacer=pacer
    )

    all_pairs = [it["pair"] for it in all_items]
    tag_to_key = {f"i{i+1:03d}": all_items[i]["key"] for i in range(len(all_items))}
    replay_client = CachedDecisionsClient(answers, tag_to_key)

    records = run_sentinel(all_pairs, client=replay_client)
    results = score_corrections_eval(positives, controls, records)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    return results


def main() -> int:
    """CLI entrypoint: runs evaluation, writes eval_results.json, and prints it."""
    results = evaluate_corrections()
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
