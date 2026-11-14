"""Weather guidance-shift classifier for Kalshi Chicago high-temperature contracts.

DOCTRINE:
The classifier detects whether forecast-office guidance for the Chicago daily
high temperature is shifting warmer or cooler between consecutive forecast
cycles; its outputs drive ONLY a deterministic quote-widen/pause gate and
alerting/telemetry. A Decisions probability NEVER shifts a quote price.
The route remains alert-only until a 90-day backtest passes (out of scope here).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_client import (
    BatchResult,
    DecisionsClient,
    ItemBatch,
    choice_pick,
    probabilities,
    score_value,
)
from ev.decisions_common import append_records, make_record, route_mode

CONTRADICTION_THRESHOLD: float = 0.75
CONFIDENCE_LEVELS: list[str] = ["very low", "low", "moderate", "high", "very high"]

_ILZ104_RE = re.compile(r"ILZ104[^\n]*\n([\s\S]*?)(?=\n\$\$|\Z)")
_MND_RE = re.compile(
    r"(?:Issued at\s+)?(\d{1,4}|\d{1,2}:\d{2})\s*(AM|PM)\s*([A-Z]{3,4})\s*[A-Za-z]{3}\s*([A-Za-z]{3})\s*(\d{1,2})\s*(\d{4})"
)
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

_TZ_OFFSETS_TO_UTC = {
    "CDT": 5,
    "CST": 6,
    "EDT": 4,
    "EST": 5,
    "MDT": 6,
    "MST": 7,
    "PDT": 7,
    "PST": 8,
    "UTC": 0,
    "GMT": 0,
    "Z": 0,
}

_WEEKDAYS = {
    "MONDAY": 0,
    "TUESDAY": 1,
    "WEDNESDAY": 2,
    "THURSDAY": 3,
    "FRIDAY": 4,
    "SATURDAY": 5,
    "SUNDAY": 6,
}


def build_questions(item: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Construct the Decisions API questions mapping for a cycle item."""
    return {
        "guidance_shift": {
            "type": "choice",
            "instructions": (
                "Does the forecast discussion indicate that guidance for the target"
                " day's Chicago high temperature is shifting compared with the"
                " previous forecast cycle described in the input?"
            ),
            "criteria": {
                "warmer": (
                    "guidance/forecast reasoning is shifting the expected high warmer"
                    " (warming trend, warmer air mass arriving sooner/stronger,"
                    " upward revisions discussed)"
                ),
                "cooler": (
                    "guidance is shifting the expected high cooler"
                    " (front arriving sooner, cooler air mass, downward revisions discussed)"
                ),
                "steady": "no material shift in guidance for the target day's high is indicated",
            },
        },
        "forecaster_confidence": {
            "type": "score",
            "instructions": (
                "Rate the forecaster's expressed confidence in the temperature"
                " guidance for the target day, from the discussion's own language."
            ),
            "criteria": CONFIDENCE_LEVELS,
        },
    }


def build_input(item: Mapping[str, Any]) -> str:
    """Build the input context block for an item.

    Includes target date, previous cycle high, new cycle issuance time,
    and discussion excerpt. Crucially, does NOT include the new forecast
    high, as that is the label ground truth.
    """
    target_date = str(item.get("target_date", ""))
    forecast_high_prev = item.get("forecast_high_prev", "")
    new_cycle_issued = str(item.get("new_cycle_issued", ""))
    afd_short_term = str(item.get("afd_short_term", ""))

    lines = [
        f"Target date: {target_date}",
        f"Previous cycle ZFP high for {target_date}: {forecast_high_prev}F",
        f"New cycle issued time: {new_cycle_issued}",
        "",
        "Forecast discussion excerpt:",
        afd_short_term,
    ]
    return "\n".join(lines).strip()


def gate(
    item: Mapping[str, Any],
    answers: Mapping[str, Any],
    *,
    market_direction: Optional[str] = None,
) -> dict[str, Any]:
    """Deterministic gate evaluating guidance shift and confidence.

    Uses DecisionsClient.probabilities for guidance_shift and DecisionsClient.score_value
    for confidence. shift = argmax class if prob >= 0.5 else steady-ish handling:
    pick = choice_pick; p_pick = probs.get(pick).

    Deterministic gate output:
        {"item_id": ..., "shift": pick, "p_shift": probs, "confidence_score": score, "action": ...}

    Actions:
        - If market_direction is provided and pick in ("warmer", "cooler") and
          market_direction != pick and probs[pick] >= CONTRADICTION_THRESHOLD:
            "pull_quote"
        - Elif pick in ("warmer", "cooler") and probs[pick] >= 0.75:
            "widen_quote"
        - Elif abstained (no probs):
            "alert_only" (fail soft for telemetry: weather never blocks on abstention,
            it just cannot widen - this is a deliberate difference from execution-blocking routes)
        - Else:
            "no_action"

    NEVER outputs a price field.
    """
    item_id = item.get("item_id") if isinstance(item, Mapping) else None

    shift_ans = answers.get("guidance_shift") if isinstance(answers, Mapping) else None
    conf_ans = answers.get("forecaster_confidence") if isinstance(answers, Mapping) else None

    probs = probabilities(shift_ans)
    score = score_value(conf_ans)
    pick = choice_pick(shift_ans)

    if pick is None and probs:
        pick = max(probs, key=probs.get)

    p_pick = probs.get(pick, 0.0) if (probs and pick) else 0.0

    if probs is None or not probs:
        # Fail soft for telemetry: weather never blocks on abstention, it just cannot widen.
        # This is a deliberate difference from other execution-blocking routes.
        action = "alert_only"
    elif (
        market_direction is not None
        and pick in ("warmer", "cooler")
        and market_direction != pick
        and p_pick >= CONTRADICTION_THRESHOLD
    ):
        action = "pull_quote"
    elif (
        pick in ("warmer", "cooler")
        and p_pick >= 0.75
    ):
        action = "widen_quote"
    else:
        action = "no_action"

    return {
        "item_id": item_id,
        "shift": pick,
        "p_shift": probs,
        "confidence_score": score,
        "action": action,
    }


def classify_cycles(
    items: Sequence[Mapping[str, Any]],
    client: Optional[DecisionsClient] = None,
    *,
    mode: Optional[str] = None,
    log_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> list[dict[str, Any]]:
    """Classify forecast cycles in ONE batched ItemBatch call.

    Abstention causes every item to be gated with {} ('alert_only').
    Results carry 'mode' and 'applied': False ALWAYS (alert/telemetry only state).
    """
    effective_mode = mode or route_mode("DECISIONS_WEATHER_MODE")
    if not items:
        return []

    if client is None:
        client = DecisionsClient()

    batch = ItemBatch()
    tags: list[str] = []
    for item in items:
        input_block = build_input(item)
        questions = build_questions(item)
        tag = batch.add(input_block, questions)
        tags.append(tag)

    input_text, wire_questions = batch.build()
    batch_result = client.call(input_text, wire_questions)

    if batch_result.abstained:
        split_answers = {tag: {} for tag in tags}
    else:
        split_answers = batch.split(batch_result)

    results: list[dict[str, Any]] = []
    for item, tag in zip(items, tags):
        ans = split_answers.get(tag, {})
        gate_out = gate(item, ans)
        gate_out["mode"] = effective_mode
        gate_out["applied"] = False
        results.append(gate_out)

    if log_path is not None:
        records = [
            make_record(
                route="weather",
                item_id=str(r.get("item_id")),
                decision=r.get("action"),
                mode=effective_mode,
                applied=False,
                shift=r.get("shift"),
                p_shift=r.get("p_shift"),
                confidence_score=r.get("confidence_score"),
            )
            for r in results
        ]
        append_records(log_path, records)

    return results


# ---------------------------------------------------------------------------
# Backtest harness (90-day instrument, code only)
# ---------------------------------------------------------------------------

def fetch_products(
    pil: str,
    start_iso: str,
    end_iso: str,
    *,
    opener: Optional[Any] = None,
) -> str:
    """Fetch raw NWS text products from IEM AFOS retrieve service via stdlib urllib."""
    params = urllib.parse.urlencode({
        "limit": "9999",
        "pil": pil,
        "fmt": "text",
        "sdate": start_iso,
        "edate": end_iso,
    })
    url = f"https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?{params}"
    headers = {"User-Agent": "Beezy Research (beezy.fyi; research@beezy.fyi)"}
    req = urllib.request.Request(url, headers=headers)

    if opener is not None:
        if hasattr(opener, "open"):
            with opener.open(req) as resp:
                return resp.read().decode("utf-8", "replace")
        elif callable(opener):
            with opener(req) as resp:
                return resp.read().decode("utf-8", "replace")

    with urllib.request.urlopen(req) as resp:
        return resp.read().decode("utf-8", "replace")


_WMO_RE = re.compile(r"\b[A-Z0-9]{4,6}\s+KLOT\s+(\d{2})(\d{2})(\d{2})\b")


def _parse_timestamp(text: str) -> Optional[tuple[str, datetime]]:
    """Extract an ISO UTC string and local datetime from product headers."""
    m_iso = _ISO_RE.search(text)
    if m_iso:
        iso_str = m_iso.group(0)
        dt = datetime.fromisoformat(iso_str)
        return iso_str, dt

    m_wmo = _WMO_RE.search(text)
    m_mnd = _MND_RE.search(text)

    if m_mnd:
        raw_time, am_pm, tz, month_name, day_str, year_str = m_mnd.groups()
        if ":" in raw_time:
            h_str, m_str = raw_time.split(":")
            h, m = int(h_str), int(m_str)
        else:
            h = int(raw_time[:-2])
            m = int(raw_time[-2:])

        if am_pm.upper() == "PM" and h < 12:
            h += 12
        elif am_pm.upper() == "AM" and h == 12:
            h = 0

        year = int(year_str)
        month = _MONTH_MAP.get(month_name.capitalize(), 1)
        day = int(day_str)
        local_dt = datetime(year, month, day, h, m)

        if m_wmo:
            w_day = int(m_wmo.group(1))
            w_hour = int(m_wmo.group(2))
            w_min = int(m_wmo.group(3))
            iso_str = f"{year:04d}-{month:02d}-{w_day:02d}T{w_hour:02d}:{w_min:02d}:00"
            return iso_str, local_dt

        tz_offset = _TZ_OFFSETS_TO_UTC.get(tz.upper(), 0)
        utc_dt = datetime(year, month, day, h, m, tzinfo=timezone.utc) + timedelta(hours=tz_offset)
        iso_str = utc_dt.strftime("%Y-%m-%dT%H:%M:%S")
        return iso_str, local_dt

    return None


def _parse_high_temp(text: str) -> Optional[int]:
    """Parse high temperature midpoint or exact value from forecast text."""
    # 1. Verbal bucket: "in the lower 70s", "mid 80s", "upper 40s"
    m_verb = re.search(
        r"\b(?:highs?|high temperatures?)\s+(?:in\s+the\s+)?(lower|mid|middle|upper)\s+(\d{1,3})s\b",
        text,
        re.IGNORECASE,
    )
    if m_verb:
        base = int(m_verb.group(2))
        bucket = m_verb.group(1).lower()
        offset = 2 if bucket == "lower" else (5 if bucket in ("mid", "middle") else 8)
        return base + offset

    # 2. Near/around: "highs near 80", "highs around 75"
    m_near = re.search(
        r"\b(?:highs?|high temperatures?)\s+(?:near|around)\s+(\d{1,3})\b",
        text,
        re.IGNORECASE,
    )
    if m_near:
        return int(m_near.group(1))

    # 3. Plain bucket: "highs in the 70s"
    m_plain = re.search(
        r"\b(?:highs?|high temperatures?)\s+in\s+the\s+(\d{1,3})s\b",
        text,
        re.IGNORECASE,
    )
    if m_plain:
        return int(m_plain.group(1)) + 5

    # 4. Range: "highs 70 to 75"
    m_range = re.search(
        r"\b(?:highs?|high temperatures?)\s+(\d{1,3})\s+to\s+(\d{1,3})\b",
        text,
        re.IGNORECASE,
    )
    if m_range:
        return int(round((int(m_range.group(1)) + int(m_range.group(2))) / 2.0))

    return None


def parse_zfp_cycles(text: str) -> list[dict[str, Any]]:
    """Parse ZFP cycles for zone ILZ104 into issued timestamps and target highs."""
    if "FPUS53" in text:
        products = re.split(r"(?:\x01|\x03|\n+(?=FPUS53\b))", text)
    else:
        products = re.split(r"(?:\x01|\x03|\n+(?=ZFPLOT\b))", text)
    cycles: list[dict[str, Any]] = []

    for prod in products:
        if "ILZ104" not in prod:
            continue

        zone_match = _ILZ104_RE.search(prod)
        if not zone_match:
            continue
        zone_body = zone_match.group(1)

        ts_info = _parse_timestamp(zone_body) or _parse_timestamp(prod)
        if not ts_info:
            continue
        issued_iso, local_dt = ts_info

        highs: dict[str, int] = {}
        periods = list(
            re.finditer(
                r"(?:^|\n)\.([A-Z0-9\s/]+?)\.\.\.([^\n]+(?:\n(?!\.[A-Z0-9\s/]+?\.\.\.)[^\n]+)*)",
                zone_body,
            )
        )

        ref_date = local_dt.date()
        for p_match in periods:
            period_name = p_match.group(1).strip().upper()
            period_text = p_match.group(2)

            high = _parse_high_temp(period_text)
            if high is None:
                continue

            target_date = None
            if "TODAY" in period_name:
                target_date = ref_date
            elif "TOMORROW" in period_name:
                target_date = ref_date + timedelta(days=1)
            else:
                for weekday_name, w_idx in _WEEKDAYS.items():
                    if weekday_name in period_name:
                        days_ahead = (w_idx - ref_date.weekday()) % 7
                        target_date = ref_date + timedelta(days=days_ahead)
                        ref_date = target_date
                        break

            if target_date is not None:
                highs[target_date.isoformat()] = high

        if highs:
            cycles.append({"issued": issued_iso, "highs": highs})

    cycles.sort(key=lambda c: str(c["issued"]))
    return cycles


def parse_afd_cycles(text: str) -> list[dict[str, Any]]:
    """Parse AFD cycles extracting the SHORT TERM/DISCUSSION excerpt (or fallback)."""
    if "FXUS63" in text:
        products = re.split(r"(?:\x01|\x03|\n+(?=FXUS63\b))", text)
    else:
        products = re.split(r"(?:\x01|\x03|\n+(?=AFDLOT\b))", text)
    afds: list[dict[str, Any]] = []

    for prod in products:
        if not prod.strip():
            continue

        ts_info = _parse_timestamp(prod)
        if not ts_info:
            continue
        issued_iso, _ = ts_info

        m_section = re.search(
            r"(?:^|\n)\.?(?:SHORT\s+TERM|DISCUSSION)\b[^\n]*\n([\s\S]*?)(?=\n\.[A-Z\s/]+|\n&&|\n\$\$|\Z)",
            prod,
            re.IGNORECASE,
        )
        if not m_section:
            m_section = re.search(
                r"(?:^|\n)\.?KEY\s+MESSAGES\b[^\n]*\n([\s\S]*?)(?=\n\.[A-Z\s/]+|\n&&|\n\$\$|\Z)",
                prod,
                re.IGNORECASE,
            )

        raw_excerpt = m_section.group(1) if m_section else prod
        collapsed = " ".join(raw_excerpt.split())
        excerpt = collapsed[:2200]

        if excerpt:
            afds.append({"issued": issued_iso, "short_term": excerpt})

    afds.sort(key=lambda a: str(a["issued"]))
    return afds


def build_cycle_pairs(
    zfps: Sequence[Mapping[str, Any]],
    afds: Sequence[Mapping[str, Any]],
    *,
    seed: int = 20261113,
    max_steady: int = 100,
) -> list[dict[str, Any]]:
    """Build fixture-shaped pair dicts comparing consecutive forecast cycles.

    target_date = day after new cycle's date.
    label is 'warmer' (rev >= +3), 'cooler' (rev <= -3), or 'steady'.
    Keeps all shift pairs + up to max_steady steady pairs (seeded).
    """
    sorted_zfps = sorted(zfps, key=lambda z: str(z.get("issued", "")))
    sorted_afds = sorted(afds, key=lambda a: str(a.get("issued", "")))

    all_pairs: list[dict[str, Any]] = []

    for i in range(len(sorted_zfps) - 1):
        prev_zfp = sorted_zfps[i]
        new_zfp = sorted_zfps[i + 1]

        prev_issued = str(prev_zfp.get("issued", ""))
        new_issued = str(new_zfp.get("issued", ""))
        try:
            new_dt = datetime.fromisoformat(new_issued)
        except (ValueError, TypeError):
            continue

        target_date = (new_dt.date() + timedelta(days=1)).isoformat()

        prev_highs = prev_zfp.get("highs", {})
        new_highs = new_zfp.get("highs", {})

        if target_date not in prev_highs or target_date not in new_highs:
            continue

        h_prev = int(prev_highs[target_date])
        h_new = int(new_highs[target_date])
        rev = h_new - h_prev

        if rev >= 3:
            label = "warmer"
        elif rev <= -3:
            label = "cooler"
        else:
            label = "steady"

        best_afd: Optional[Mapping[str, Any]] = None
        best_diff: Optional[float] = None
        for afd in sorted_afds:
            try:
                afd_dt = datetime.fromisoformat(str(afd.get("issued", "")))
            except (ValueError, TypeError):
                continue
            diff = abs((afd_dt - new_dt).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_afd = afd

        afd_issued = str(best_afd["issued"]) if best_afd else new_issued
        afd_short_term = str(best_afd.get("short_term", "")) if best_afd else ""

        all_pairs.append({
            "target_date": target_date,
            "prev_cycle_issued": prev_issued,
            "new_cycle_issued": new_issued,
            "forecast_high_prev": h_prev,
            "forecast_high_new": h_new,
            "afd_issued": afd_issued,
            "afd_short_term": afd_short_term,
            "label": label,
        })

    shift_pairs = [p for p in all_pairs if p["label"] in ("warmer", "cooler")]
    steady_pairs = [p for p in all_pairs if p["label"] == "steady"]

    if len(steady_pairs) > max_steady:
        rng = random.Random(seed)
        steady_pairs = rng.sample(steady_pairs, max_steady)

    selected = shift_pairs + steady_pairs
    selected.sort(key=lambda p: (p["new_cycle_issued"], p["prev_cycle_issued"]))

    for idx, p in enumerate(selected, start=1):
        p["item_id"] = f"W{idx:03d}"

    return selected


def run_backtest(
    days: int = 90,
    *,
    client: Optional[DecisionsClient] = None,
    out_path: Optional[Union[str, os.PathLike[str]]] = None,
) -> dict[str, Any]:
    """Fetch 90-day archive, build pairs, classify them batched, and summarize."""
    now = datetime.now(timezone.utc)
    start_dt = now - timedelta(days=days)
    start_iso = start_dt.strftime("%Y-%m-%d")
    end_iso = now.strftime("%Y-%m-%d")

    afd_text = fetch_products("AFDLOT", start_iso, end_iso)
    zfp_text = fetch_products("ZFPLOT", start_iso, end_iso)

    afds = parse_afd_cycles(afd_text)
    zfps = parse_zfp_cycles(zfp_text)

    pairs = build_cycle_pairs(zfps, afds)
    classified = classify_cycles(pairs, client=client)

    shift_items = [p for p in pairs if p["label"] in ("warmer", "cooler")]
    correct_shifts = 0
    for p, r in zip(pairs, classified):
        if p["label"] in ("warmer", "cooler"):
            p_shift = r.get("p_shift") or {}
            pick = r.get("shift")
            pred = pick if (pick and p_shift.get(pick, 0.0) >= 0.5) else "steady"
            if pred == p["label"]:
                correct_shifts += 1

    shift_recall = correct_shifts / len(shift_items) if shift_items else 0.0

    steady_items = [p for p in pairs if p["label"] == "steady"]
    steady_fa = 0
    for p, r in zip(pairs, classified):
        if p["label"] == "steady":
            p_shift = r.get("p_shift") or {}
            pick = r.get("shift")
            pred = pick if (pick and p_shift.get(pick, 0.0) >= 0.5) else "steady"
            if pred in ("warmer", "cooler"):
                steady_fa += 1

    steady_far = steady_fa / len(steady_items) if steady_items else 0.0

    table: dict[str, dict[str, int]] = {
        "warmer": {"warmer": 0, "cooler": 0, "steady": 0},
        "cooler": {"warmer": 0, "cooler": 0, "steady": 0},
        "steady": {"warmer": 0, "cooler": 0, "steady": 0},
    }
    for p, r in zip(pairs, classified):
        act = p["label"]
        p_shift = r.get("p_shift") or {}
        pick = r.get("shift")
        pred = pick if (pick and p_shift.get(pick, 0.0) >= 0.5) else "steady"
        if act in table and pred in table[act]:
            table[act][pred] += 1

    summary = {
        "n_pairs": len(pairs),
        "shift_recall": round(shift_recall, 4),
        "steady_false_alarm_rate": round(steady_far, 4),
        "per_class_table": table,
        "per-class table": table,
    }

    if out_path is not None:
        fpath = Path(out_path)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI to classify fixture-shaped cycle items."""
    parser = argparse.ArgumentParser(
        description="Classify forecast-cycle guidance shifts for Kalshi Chicago high-temp contracts."
    )
    parser.add_argument("--cycles", required=True, help="Path to cycles.jsonl file.")
    parser.add_argument("--out", required=True, help="Path to output report.json.")
    parser.add_argument("--log", default=None, help="Optional path to append decision records JSONL.")
    parser.add_argument("--mode", default=None, help="Operating mode: shadow or live.")
    args = parser.parse_args(argv)

    cycles_path = Path(args.cycles)
    items: list[dict[str, Any]] = []
    with open(cycles_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    results = classify_cycles(items, mode=args.mode, log_path=args.log)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"Classified {len(results)} items -> {args.out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
