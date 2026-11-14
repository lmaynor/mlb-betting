"""N3 research-claim evidence packaging.

Argon ruling RUL-20261113-dec-gate-design Q1: deterministic pre-processor
that resolves and attaches every artifact pointer a claim cites or references,
failing closed with MISSING_ARTIFACT when a local pointer does not resolve.

Pure deterministic packaging (filesystem reads of cited artifacts only;
NO network, NO API calls, NO environment reads beyond os.path.expanduser).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from ev.decisions_common import MAX_INPUT_TOKENS, estimate_tokens

DEFAULT_SEARCH_ROOTS: list[Path] = [
    Path.home() / "workspace" / "beezy",
    Path(__file__).resolve().parents[2],
]

BINARY_EXTENSIONS: tuple[str, ...] = (".parquet", ".gz", ".zip")
TRUNCATION_MARKER: str = "\n[artifact truncated to fit the 262k context budget]"

# Regex patterns for pointers
_EXT_RE = r"(?:https?://|gs://)[^\s'\"`),;\]]+"
_PATH_RE = r"(?<![A-Za-z0-9_.\-/])(?:~|/)[^\s'\"`),;\]]+"
_FILE_RE = r"(?<![A-Za-z0-9_.\-/])[A-Za-z0-9_.\-/]*?[A-Za-z0-9_-]\.(?:jsonl|json|parquet|csv|txt|py|md)\b"

_POINTER_RE = re.compile(
    rf"(?P<ext>{_EXT_RE})|(?P<path>{_PATH_RE})|(?P<file>{_FILE_RE})"
)


def extract_pointers(claim: Union[str, Mapping[str, Any]]) -> list[str]:
    r"""Extract citation-ordered, deduped artifact pointers from a claim.

    Order:
      1. claim["source_doc"] first, when present. If the field contains " ("
         (a parenthetical annotation), cut everything from the first " (" onward.
      2. Then pointers found in claim["claim_text"], then in claim["evidence"],
         in text order, via regexes:
         - home/absolute paths: (?:~|/)[^\s'"`),;\]]+ with trailing punctuation .,;: stripped;
         - bare or relative filenames matching [A-Za-z0-9_.\-/]*[A-Za-z0-9_-]+\.(?:md|csv|json|jsonl|txt|py|parquet)
           (no spaces), trailing punctuation stripped; skip matches that are purely
           a version-like token (must contain at least one letter).

    Skip duplicates after the first occurrence. Pointers starting with "http://",
    "https://", "gs://" are returned; classification happens in package_evidence.
    """
    pointers: list[str] = []
    seen: set[str] = set()

    if isinstance(claim, Mapping):
        src = claim.get("source_doc")
        if src and isinstance(src, str):
            if " (" in src:
                src = src.split(" (", 1)[0]
            src = src.strip()
            if src:
                pointers.append(src)
                seen.add(src)

    texts: list[str] = []
    if isinstance(claim, Mapping):
        texts.append(str(claim.get("claim_text", "")))
        texts.append(str(claim.get("evidence", "")))
    else:
        texts.append(str(claim))

    for text in texts:
        if not text:
            continue
        for m in _POINTER_RE.finditer(text):
            tok = m.group(0).rstrip(".,;:")
            if not any(c.isalpha() for c in tok):
                continue
            if tok.startswith("/") and not _looks_like_fs_path(tok):
                # Site routes and URL paths ("/tools", "/picks/history",
                # "/ev/games") are not filesystem pointers.
                continue
            if tok not in seen:
                seen.add(tok)
                pointers.append(tok)

    return pointers


_TABULAR_EXTENSIONS: frozenset = frozenset({".csv", ".jsonl", ".json", ".tsv"})


def _content_tokens(text: str, path: Path) -> int:
    """Token estimate for one artifact's content, by content type.

    Prose and code use the house estimator (ev.decisions_common.
    estimate_tokens, chars/3.5). Tabular data (CSV/JSON/TSV) is mostly
    digits, commas, and short fields, which tokenize far denser:
    measured live on 2026-11-13, a 389,340-char input dominated by a
    float-heavy CSV was rejected by the API as exceeding 262,144 real
    tokens, i.e. the CSV tokenizes at <= ~1.43 chars/token. The factor
    below uses 1.25 chars/token (conservative against that measurement)
    so packaged inputs stay inside the API's real context limit, not
    just the nominal estimate.
    """
    if path.suffix.lower() in _TABULAR_EXTENSIONS:
        return -(-len(text) * 4 // 5)  # ceil(len / 1.25)
    return estimate_tokens(text)


_FS_PREFIXES: tuple[str, ...] = ("/home/", "/tmp/", "/var/", "/Users/", "/opt/", "/etc/")


def _looks_like_fs_path(tok: str) -> bool:
    """True when an absolute '/...' token plausibly names a filesystem path:
    it lives under a known filesystem prefix, or its last segment is a
    filename carrying an extension. Bare site routes fail both tests."""
    if tok.startswith(_FS_PREFIXES):
        return True
    last = tok.rsplit("/", 1)[-1]
    return "." in last


def is_external(pointer: str) -> bool:
    """Classify whether a pointer is an external URL/URI."""
    return pointer.startswith(("http://", "https://", "gs://"))


def resolve_local(
    pointer: str,
    source_doc_dir: Optional[Union[str, os.PathLike[str], Path]] = None,
    search_roots: Optional[Sequence[Union[str, os.PathLike[str], Path]]] = None,
) -> tuple[Optional[Path], int]:
    """Resolve a local artifact pointer against source_doc_dir and search roots.

    Resolution order:
      1. Path(os.path.expanduser(pointer)) if it exists (file or dir);
      2. (source_doc_dir / pointer) if source_doc_dir and it exists;
      3. basename search: for each root in search_roots (in order), rglob(basename)
         over files and dirs, candidates sorted by (number of path parts, str(path));
         first EXISTING candidate across all roots in root order wins.

    Returns (path, candidate_count).
    """
    roots = list(search_roots) if search_roots is not None else DEFAULT_SEARCH_ROOTS

    # 1. Path(os.path.expanduser(pointer)) if it exists (file or dir)
    p = Path(os.path.expanduser(pointer))
    if p.exists():
        return (p.resolve(), 1)

    # 2. (source_doc_dir / pointer) if source_doc_dir and it exists
    if source_doc_dir:
        sdir = Path(source_doc_dir)
        cand = sdir / pointer
        if cand.exists():
            return (cand.resolve(), 1)

    # 3. basename search: for each root in search_roots (in order), rglob(basename)
    basename = Path(pointer).name
    if not basename:
        return (None, 0)

    total_candidates = 0
    first_winner: Optional[Path] = None
    for root_entry in roots:
        root = Path(root_entry)
        if not root.is_dir():
            continue
        try:
            cands = [m for m in root.rglob(basename) if m.exists()]
        except OSError:
            cands = []
        if cands:
            # Rank deterministically: candidates NOT under a copy-like
            # directory (snapshot / stage / bundle / backup / copy segments
            # hold copies, not the live artifact) win over copies; within
            # each class, shallowest path then lexicographic order.
            primary = [c for c in cands if not _has_copy_segment(c)]
            pool = primary if primary else cands
            pool.sort(key=lambda x: (len(x.parts), str(x)))
            if first_winner is None:
                first_winner = pool[0]
            total_candidates += len(cands)

    if first_winner is not None:
        return (first_winner.resolve(), total_candidates)
    return (None, 0)


_COPY_SEGMENTS: frozenset = frozenset({"snapshot", "snapshots", "backup", "backups", "copy", "copies"})


def _has_copy_segment(path: Path) -> bool:
    """True when any path segment marks the file as a copy rather than the
    live artifact (a 'snapshot'/'backup'/'copy' directory, a 'stageN'
    staging directory, or a 'bundle' directory)."""
    for seg in path.parts:
        low = seg.lower()
        if low in _COPY_SEGMENTS or "bundle" in low or low.startswith("stage"):
            return True
    return False


def package_evidence(
    claim: Union[str, Mapping[str, Any]],
    *,
    extra_roots: Sequence[Union[str, os.PathLike[str], Path]] = (),
    budget_tokens: Optional[int] = None,
) -> dict[str, Any]:
    """Deterministically assemble evidence prose and cited artifacts.

    Token budget defaults to int(MAX_INPUT_TOKENS * 0.9) (216,000 tokens).
    Assembly order:
      - Claim's own 'evidence' prose first (verbatim).
      - For each LOCAL pointer in citation order:
        * unresolved -> missing list;
        * resolved to a directory -> attach deterministic listing sorted by name (max 200 entries);
        * resolved to binary (.parquet, .gz, .zip) -> attach one-line notice;
        * resolved to text file -> attach content read with errors="replace".
      - Trailing section for not_attached_budget if context budget exhausted.
      - Trailing section for MISSING_ARTIFACT if any missing pointers exist.

    Returns:
      {
        "evidence_text": str,
        "attached": [{"pointer", "resolved_path", "chars", "truncated"}],
        "missing": [...],
        "external": [...],
        "not_attached_budget": [...],
        "ambiguous": {pointer: candidate_count for counts > 1},
        "multi_artifact": len(attached) >= 2,
      }
    """
    if budget_tokens is None:
        budget_tokens = int(MAX_INPUT_TOKENS * 0.9)

    search_roots = [Path(r) for r in extra_roots] + list(DEFAULT_SEARCH_ROOTS)

    source_doc_dir: Optional[Path] = None
    if isinstance(claim, Mapping):
        src = claim.get("source_doc")
        if src and isinstance(src, str):
            if " (" in src:
                src = src.split(" (", 1)[0]
            src = src.strip()
            sp = Path(os.path.expanduser(src))
            if sp.exists():
                source_doc_dir = sp.parent if sp.is_file() else sp
            else:
                res_src, _ = resolve_local(src, None, search_roots)
                if res_src and res_src.exists():
                    source_doc_dir = res_src.parent if res_src.is_file() else res_src

    pointers = extract_pointers(claim)
    external: list[str] = []
    missing: list[str] = []
    ambiguous: dict[str, int] = {}
    resolved_artifacts: list[tuple[str, Path]] = []

    for p in pointers:
        if is_external(p):
            external.append(p)
        else:
            res_path, count = resolve_local(p, source_doc_dir, search_roots)
            if count > 1:
                ambiguous[p] = count
            if res_path is None:
                missing.append(p)
            else:
                resolved_artifacts.append((p, res_path))

    assembled_text = str(claim.get("evidence", "")) if isinstance(claim, Mapping) else ""
    if isinstance(claim, Mapping):
        c_text = str(claim.get("claim_text", ""))
        cid = str(claim.get("claim_id", ""))
        src_val = str(claim.get("source_doc", ""))
        lines = ["Research claim under audit:"]
        if cid:
            lines.append(f"Claim ID: {cid}")
        lines.append(f"Claim: {c_text}")
        if src_val:
            lines.append(f"Cited source: {src_val}")
        lines.append("")
        lines.append("Evidence (the material the claim must be judged against):")
        claim_header_overhead = "\n".join(lines)
        tag_overhead = f"[{cid}]\n"
    else:
        claim_header_overhead = str(claim)
        tag_overhead = ""

    attached: list[dict[str, Any]] = []
    not_attached_budget: list[str] = []
    budget_exhausted: bool = False

    for idx, (pointer, res_path) in enumerate(resolved_artifacts):
        if budget_exhausted:
            not_attached_budget.append(pointer)
            continue

        if res_path.is_dir():
            try:
                entries = sorted(list(res_path.iterdir()), key=lambda e: e.name)[:200]
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
                content = "\n".join(lines)
            except OSError:
                content = ""
        elif res_path.suffix.lower() in BINARY_EXTENSIONS or res_path.name.lower().endswith(BINARY_EXTENSIONS):
            try:
                sz = res_path.stat().st_size
            except OSError:
                sz = 0
            content = f"[binary artifact not inlined: {res_path.name}, {sz} bytes]"
        else:
            try:
                content = res_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""

        header = f"\n\n--- Attached artifact: {pointer} (resolved: {res_path.resolve()}) ---\n"

        rem_ptrs = [p for p, _ in resolved_artifacts[idx + 1:]]
        prosp_trailer = ""
        if rem_ptrs:
            prosp_trailer += f"\n\n--- Cited artifacts not attached (context budget exhausted): {', '.join(rem_ptrs)} ---"
        if missing:
            prosp_trailer += f"\n\n--- MISSING_ARTIFACT: the following cited artifacts could not be resolved and were not attached: {', '.join(missing)} ---"

        overhead_text = claim_header_overhead + prosp_trailer + tag_overhead

        # Budget accounting is density-aware: prose uses the house estimator,
        # but tabular data (CSV/JSON lines) tokenizes far denser than prose --
        # the house chars/3.5 estimate understates it and the API rejected a
        # nominally in-budget CSV-heavy input with HTTP 400 (Nov 13 re-eval,
        # claim N3-C012). _content_tokens applies the denser factor.
        base_tokens = estimate_tokens(assembled_text + overhead_text)
        header_tokens = estimate_tokens(header)

        if base_tokens + header_tokens + _content_tokens(content, res_path) <= budget_tokens:
            assembled_text += header + content
            attached.append({
                "pointer": pointer,
                "resolved_path": str(res_path.resolve()),
                "chars": len(content),
                "truncated": False,
            })
        else:
            marker = TRUNCATION_MARKER
            marker_tokens = _content_tokens(marker, res_path)
            if base_tokens + header_tokens + marker_tokens > budget_tokens:
                not_attached_budget.append(pointer)
                budget_exhausted = True
            else:
                low = 0
                high = len(content)
                best_k = 0
                while low <= high:
                    mid = (low + high) // 2
                    if base_tokens + header_tokens + _content_tokens(content[:mid], res_path) + marker_tokens <= budget_tokens:
                        best_k = mid
                        low = mid + 1
                    else:
                        high = mid - 1

                prefix = content[:best_k]
                assembled_text += header + prefix + marker
                attached.append({
                    "pointer": pointer,
                    "resolved_path": str(res_path.resolve()),
                    "chars": len(prefix),
                    "truncated": True,
                })
                budget_exhausted = True

    if not_attached_budget:
        assembled_text += (
            f"\n\n--- Cited artifacts not attached (context budget exhausted): "
            f"{', '.join(not_attached_budget)} ---"
        )

    if missing:
        assembled_text += (
            f"\n\n--- MISSING_ARTIFACT: the following cited artifacts could not be resolved and were not attached: "
            f"{', '.join(missing)} ---"
        )

    return {
        "evidence_text": assembled_text,
        "attached": attached,
        "missing": missing,
        "external": external,
        "not_attached_budget": not_attached_budget,
        "ambiguous": ambiguous,
        "multi_artifact": len(attached) >= 2,
    }


__all__ = [
    "DEFAULT_SEARCH_ROOTS",
    "BINARY_EXTENSIONS",
    "TRUNCATION_MARKER",
    "extract_pointers",
    "is_external",
    "resolve_local",
    "package_evidence",
]
