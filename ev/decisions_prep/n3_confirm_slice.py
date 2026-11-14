"""N3 Stage 2 confirm slice: deterministic focused evidence (NO API calls).

RUL-20261113-dec-03-contradiction, Stage 2: the isolated confirm pass sees a
focused slice of the packaged evidence chain, not the whole chain (the full
chain is what let cross-document number collisions leak contradiction mass
onto clean claims). The slice contains:

- the claim's primary cited artifact (first attached artifact, citation
  order) in full, and
- from every OTHER attached artifact, only the paragraph/table-row windows
  around lines that share at least one normalized number, date, or named
  entity with the claim: +/- WINDOW_LINES lines around each matching line,
  expanded to the full contiguous table block when the matching line is a
  Markdown table row (the table is the enclosing section for a row).

If no secondary artifact yields any window, the slice falls back to the
full packaged evidence chain, exactly as the ruling specifies.

Pure deterministic packaging (filesystem reads of already-resolved
artifacts only; NO network, NO API calls).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_common import MAX_INPUT_TOKENS, estimate_tokens
from ev.decisions_prep.n3_claim_auditor import _close, extract_numeric_claims
from ev.decisions_prep.n3_evidence_packaging import BINARY_EXTENSIONS, TRUNCATION_MARKER

WINDOW_LINES = 3


def _read_artifact_text(path: Path) -> str:
    """Read one resolved artifact the way the packager inlines it:
    directory -> deterministic listing; binary -> one-line notice; else
    the file's text (errors replaced)."""
    if path.is_dir():
        try:
            entries = sorted(list(path.iterdir()), key=lambda e: e.name)[:200]
            lines: list[str] = []
            for entry in entries:
                if entry.is_dir():
                    lines.append(f"{entry.name}/\t(dir)")
                else:
                    try:
                        sz = entry.stat().st_size
                    except OSError:
                        sz = 0
                    lines.append(f"{entry.name}\t{sz} bytes")
            return "\n".join(lines)
        except OSError:
            return ""
    if path.suffix.lower() in BINARY_EXTENSIONS or path.name.lower().endswith(BINARY_EXTENSIONS):
        try:
            sz = path.stat().st_size
        except OSError:
            sz = 0
        return f"[binary artifact not inlined: {path.name}, {sz} bytes]"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Shared-token extraction from the claim
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONTH_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}\b"
)
_ENTITY_PHRASE_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)+")
_CAMEL_RE = re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b")
_ENTITY_STOP: frozenset = frozenset({"The", "In", "On", "As", "At", "Claim", "Cited"})


def _claim_numbers(text: str) -> set[float]:
    return {value for value, _span in extract_numeric_claims(text)}


def _claim_dates(text: str) -> set[str]:
    out: set[str] = set()
    for rx in (_ISO_DATE_RE, _MONTH_DATE_RE):
        for m in rx.finditer(text):
            out.add(" ".join(m.group(0).lower().split()))
    return out


def _claim_entities(text: str) -> set[str]:
    out: set[str] = set()
    for m in _ENTITY_PHRASE_RE.finditer(text):
        words = m.group(0).split()
        while words and words[0] in _ENTITY_STOP:
            words = words[1:]
        if len(words) >= 2:
            phrase = " ".join(words)
            if len(phrase) >= 3:
                out.add(phrase)
    for m in _CAMEL_RE.finditer(text):
        tok = m.group(0)
        if len(tok) >= 3:
            out.add(tok)
    return out


def _line_matches(
    line: str,
    numbers: set[float],
    dates: set[str],
    entities: set[str],
) -> bool:
    """True when the line shares a normalized number, date, or named
    entity with the claim."""
    if numbers:
        for value, _span in extract_numeric_claims(line):
            if any(_close(value, n) for n in numbers):
                return True
    if dates:
        lowered = " ".join(line.lower().split())
        if any(d in lowered for d in dates):
            return True
    if entities:
        if any(e in line for e in entities):
            return True
    return False


def _is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


def _windows_for(
    text: str,
    numbers: set[float],
    dates: set[str],
    entities: set[str],
) -> list[str]:
    """Windowed spans of ``text`` around claim-token-matching lines.

    Each match contributes +/- WINDOW_LINES lines; a match on a Markdown
    table row expands to the full contiguous table block. Overlapping or
    adjacent spans merge. Returns one string per merged span, in order.
    """
    lines = text.splitlines()
    matched = [
        i for i, line in enumerate(lines) if _line_matches(line, numbers, dates, entities)
    ]
    if not matched:
        return []

    spans: list[list[int]] = []
    for i in matched:
        lo = max(0, i - WINDOW_LINES)
        hi = min(len(lines) - 1, i + WINDOW_LINES)
        if _is_table_row(lines[i]):
            while lo > 0 and _is_table_row(lines[lo - 1]):
                lo -= 1
            while hi < len(lines) - 1 and _is_table_row(lines[hi + 1]):
                hi += 1
        spans.append([lo, hi])

    spans.sort()
    merged: list[list[int]] = [spans[0]]
    for lo, hi in spans[1:]:
        if lo <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    return ["\n".join(lines[lo:hi + 1]) for lo, hi in merged]


# ---------------------------------------------------------------------------
# Slice assembly
# ---------------------------------------------------------------------------


def build_confirm_slice(
    claim: Union[str, Mapping[str, Any]],
    packaged: Mapping[str, Any],
    *,
    budget_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """Build the Stage 2 confirm slice for one claim.

    ``packaged`` is the output of
    ev.decisions_prep.n3_evidence_packaging.package_evidence for the same
    claim. Returns {"slice_text", "primary_pointer", "window_counts",
    "fallback_full_chain"}. Falls back to the full packaged evidence chain
    when there are no attached artifacts or no secondary windows match.
    """
    if budget_tokens is None:
        budget_tokens = int(MAX_INPUT_TOKENS * 0.9)

    attached: Sequence[Mapping[str, Any]] = packaged.get("attached") or []
    if isinstance(claim, Mapping):
        claim_text = str(claim.get("claim_text", ""))
        evidence_prose = str(claim.get("evidence", ""))
    else:
        claim_text = str(claim)
        evidence_prose = ""

    if not attached:
        return {
            "slice_text": str(packaged.get("evidence_text", "")),
            "primary_pointer": None,
            "window_counts": {},
            "fallback_full_chain": True,
        }

    primary = attached[0]
    primary_path = Path(str(primary["resolved_path"]))
    primary_text_full = _read_artifact_text(primary_path)

    numbers = _claim_numbers(claim_text)
    dates = _claim_dates(claim_text)
    entities = _claim_entities(claim_text)

    windowed: list[tuple[str, list[str]]] = []
    window_counts: dict[str, int] = {}
    for artifact in attached[1:]:
        text = _read_artifact_text(Path(str(artifact["resolved_path"])))
        spans = _windows_for(text, numbers, dates, entities)
        if spans:
            windowed.append((str(artifact["pointer"]), spans))
            window_counts[str(artifact["pointer"])] = len(spans)

    if not windowed:
        return {
            "slice_text": str(packaged.get("evidence_text", "")),
            "primary_pointer": primary["pointer"],
            "window_counts": {},
            "fallback_full_chain": True,
        }

    def _assemble(primary_text: str) -> str:
        parts: list[str] = []
        if evidence_prose:
            parts.append(evidence_prose)
        parts.append(
            f"--- Attached artifact: {primary['pointer']} "
            f"(resolved: {primary['resolved_path']}) ---\n{primary_text}"
        )
        for pointer, spans in windowed:
            parts.append(
                f"--- Attached artifact (confirm windows): {pointer} ---\n"
                + "\n...\n".join(spans)
            )
        return "\n\n".join(parts)

    primary_text = primary_text_full
    slice_text = _assemble(primary_text)
    # Budget guard: shrink the primary artifact (the only unbounded part)
    # by halves until the slice fits or only the marker remains.
    while estimate_tokens(slice_text) > budget_tokens and len(primary_text) > len(TRUNCATION_MARKER):
        keep = max(0, (len(primary_text) - len(TRUNCATION_MARKER)) // 2)
        primary_text = primary_text[:keep] + TRUNCATION_MARKER
        slice_text = _assemble(primary_text)

    return {
        "slice_text": slice_text,
        "primary_pointer": primary["pointer"],
        "window_counts": window_counts,
        "fallback_full_chain": False,
    }


__all__ = ["build_confirm_slice", "WINDOW_LINES"]
