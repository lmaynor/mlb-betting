"""Arm T prose loader: Argon's Q4 input contract + Q5 decision-time and
cleansing rules, over either banked prose corpus.

Governing spec: ``arm_t_config`` (Argon's 2026-11-11/12 rulings, verbatim at
``~/workspace/beezy/profitability/argon-minutes-program-VERBATIM-2026-11-11.md``).

What this module owns:

- Corpus adapters for the two banked archives, normalized into one item
  shape. NBC Sports (``NBA/news/nbcsports_archive/``) is the graded source:
  a tagged feed, so a player's parsed file mixes items he headlines
  (``is_subject``) with teammate items he is tagged in, and items that
  surfaced on his feed but were banked under another player's page arrive
  as mention references that this loader resolves against the corpus pool.
  RotoWire (``NBA/news/rotowire_archive/``) is the dev harness: a player's
  page carries only his own items, so every record is a subject item and
  the mention pool is empty by construction. The case set and decision
  times are identical across corpora; the corpus is a config switch.
- Decision-time resolution (Q5): the freeze timestamp is the designation
  stamp Arm S used (the last banked official report listing the player at
  or before tip - 60m), or the last banked stamp at or before tip - 60m
  when the player is unlisted. FALLBACK AMENDMENT: when the expected T-60
  stamp is unbanked, the freeze rolls back to that banked stamp, and if it
  sits outside [tip - 360m, tip - 60m] the case is excluded.
- The Q4 slice: subject items (<= 5, 14-day window), teammate mention
  excerpts (<= 2 items, 72-hour window, sentence-level last-name + workload
  token filter, separate field at <= 0.25x weight), the zero-subject
  ABSTAIN rule, and the migration-stratum hard exclusions.
- Cleansing as a RUN-TIME VIEW (Q5): banked raw is immutable; this module
  never writes to a corpus. Items retrospectively edited after the freeze
  (``updated_ts > decision_report_ts``) are stripped here, and every case
  logs raw/cleansed token counts and subject/mention item counts.

Neither banked corpus currently carries an ``updated_ts`` field, so the
retrospective-edit strip is wired and tested but counts zero on both; it
fires on any future corpus that carries one.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import pandas as pd

from .arm_t_config import (
    MIGRATION_STRATUM_CUTOFF,
    STAMP_GRID_HOURS_ET,
    STAMP_GRID_MINUTE_ET,
    STRATA_CLUSTER_MIN,
    WORKLOAD_TOKENS,
    ArmTConfig,
)
from .cases import _norm_name
from .decisions_client import BandPrediction

_ET = "America/New_York"
_MIGRATION_CUTOFF_TS = pd.Timestamp(MIGRATION_STRATUM_CUTOFF)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv"}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def _fold(token: str) -> str:
    """ASCII-fold one name token for text matching (diacritics stripped)."""
    return (
        unicodedata.normalize("NFKD", token)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def last_name_key(player: str) -> str:
    """The player's last name, folded, generational suffixes dropped.

    Used by the mention sentence filter, which requires the case player's
    last name to appear explicitly in the sentence (Q4).
    """
    parts = [p for p in re.split(r"[\s.]+", str(player).strip()) if p]
    parts = [p for p in parts if _fold(p) not in _SUFFIX_TOKENS]
    return _fold(parts[-1]) if parts else ""


# ---------------------------------------------------------------------------
# Unified corpus shape
# ---------------------------------------------------------------------------

@dataclass
class ProseItem:
    """One banked prose item, corpus-normalized."""

    corpus_key: tuple
    source: str                        # "nbc" | "rotowire"
    page_player: str                   # display name of the feed it was banked from
    subject_player: Optional[str]      # display name of the headline player
    is_subject_for_page: bool          # subject == page player (banked flag)
    published_ts: pd.Timestamp         # tz-aware UTC
    headline: str
    body: str
    updated_ts: Optional[pd.Timestamp] = None
    migration_stratum: bool = False    # banked flag (NBC), else False
    # Subject identity by NAME (normalized subject_player == page player).
    # The NBC puller's banked is_subject flag compares source ids whose
    # types are unstable across records (int 9526 vs str "9526"), which
    # marks every item of some players as a non-subject; the record's
    # subject_player name is the reliable identity. The loader's subject
    # role is flag OR name match, and disagreements are counted on the
    # corpus (see ProseCorpus.n_subject_flag_disagreements).
    subject_name_match: bool = False


@dataclass
class MentionRef:
    """An item that appeared on a player's feed but was banked elsewhere."""

    corpus_key: tuple
    published_ts: pd.Timestamp
    headline: str


@dataclass
class ProseCorpus:
    """A banked corpus, loaded from local parsed JSONL files."""

    name: str
    items_by_player: dict[str, list[ProseItem]] = field(default_factory=dict)
    mention_refs_by_player: dict[str, list[MentionRef]] = field(default_factory=dict)
    pool_by_key: dict[tuple, ProseItem] = field(default_factory=dict)
    banked_ids: set[str] = field(default_factory=set)
    id_by_player: dict[str, str] = field(default_factory=dict)
    n_files: int = 0
    n_rows: int = 0
    n_subject_flag_disagreements: int = 0

    def banked_players(self) -> set[str]:
        return set(self.items_by_player) | set(self.mention_refs_by_player)


def _ts(value: Any) -> Optional[pd.Timestamp]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return pd.Timestamp(value, unit="s", tz="UTC")
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _corpus_key_nbc(rec: Mapping[str, Any]) -> tuple:
    subj = rec.get("subject_nbc_id")
    return (
        float(rec["published_ts"]),
        str(rec["headline"]),
        None if subj is None else str(subj),
    )


def load_player_map(path: str | Path) -> dict[str, str]:
    """Player map CSV (player,<source>_id,slug) -> {name_key: source_id str}."""
    df = pd.read_csv(path, dtype=str)
    id_col = next(c for c in df.columns if c.endswith("_id"))
    return {_norm_name(p): str(i) for p, i in zip(df["player"], df[id_col])}


def load_nbc_corpus(
    parsed_dir: str | Path,
    mentions_dir: str | Path | None = None,
    player_map: str | Path | None = None,
) -> ProseCorpus:
    """Load the NBC Sports archive from local parsed (+ mentions) JSONL."""
    corpus = ProseCorpus(name="nbc")
    if player_map is not None:
        corpus.id_by_player = load_player_map(player_map)
    parsed = Path(parsed_dir)
    for path in sorted(parsed.glob("*.jsonl")):
        corpus.n_files += 1
        corpus.banked_ids.add(path.stem)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            corpus.n_rows += 1
            name_match = (
                rec.get("subject_player") is not None
                and _norm_name(str(rec["subject_player"])) == _norm_name(str(rec["player"]))
            )
            if bool(rec.get("is_subject")) != name_match:
                corpus.n_subject_flag_disagreements += 1
            item = ProseItem(
                corpus_key=_corpus_key_nbc(rec),
                source="nbc",
                page_player=str(rec["player"]),
                subject_player=rec.get("subject_player"),
                is_subject_for_page=bool(rec.get("is_subject")),
                published_ts=_ts(rec["published_ts"]),
                headline=str(rec.get("headline") or ""),
                body=str(rec.get("body") or ""),
                updated_ts=_ts(rec.get("updated_ts")),
                migration_stratum=bool(rec.get("migration_stratum")),
                subject_name_match=name_match,
            )
            corpus.items_by_player.setdefault(_norm_name(item.page_player), []).append(item)
            corpus.pool_by_key.setdefault(item.corpus_key, item)
    if mentions_dir is not None:
        id_to_name = {v: k for k, v in corpus.id_by_player.items()}
        for path in sorted(Path(mentions_dir).glob("*.jsonl")):
            page_key = id_to_name.get(path.stem)
            if page_key is None:
                continue  # a mentions file for a player outside the map
            refs = corpus.mention_refs_by_player.setdefault(page_key, [])
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                raw_key = rec["corpus_key"]
                key = (
                    float(raw_key[0]),
                    str(raw_key[1]),
                    None if raw_key[2] is None else str(raw_key[2]),
                )
                refs.append(
                    MentionRef(
                        corpus_key=key,
                        published_ts=_ts(rec["published_ts"]),
                        headline=str(rec.get("headline") or ""),
                    )
                )
    for items in corpus.items_by_player.values():
        items.sort(key=lambda it: it.published_ts)
    return corpus


def load_rotowire_corpus(
    parsed_dir: str | Path,
    player_map: str | Path | None = None,
) -> ProseCorpus:
    """Load the RotoWire archive from local parsed JSONL.

    A RotoWire player page carries only that player's items: every record
    is a subject item and there are no mention references.
    """
    corpus = ProseCorpus(name="rotowire")
    if player_map is not None:
        corpus.id_by_player = load_player_map(player_map)
    for path in sorted(Path(parsed_dir).glob("*.jsonl")):
        corpus.n_files += 1
        corpus.banked_ids.add(path.stem)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            corpus.n_rows += 1
            ts = _ts(rec.get("published_ts"))
            if ts is None:
                ts = pd.Timestamp(rec["published_date"], tz="UTC")
            item = ProseItem(
                corpus_key=(str(rec.get("rotowire_id")), float(ts.timestamp()),
                            str(rec.get("headline") or "")),
                source="rotowire",
                page_player=str(rec["player"]),
                subject_player=str(rec["player"]),
                is_subject_for_page=True,
                published_ts=ts,
                headline=str(rec.get("headline") or ""),
                body=str(rec.get("body") or ""),
                updated_ts=_ts(rec.get("updated_ts")),
                migration_stratum=False,
            )
            corpus.items_by_player.setdefault(_norm_name(item.page_player), []).append(item)
            corpus.pool_by_key.setdefault(item.corpus_key, item)
    for items in corpus.items_by_player.values():
        items.sort(key=lambda it: it.published_ts)
    return corpus


def load_corpus(
    kind: str,
    parsed_dir: str | Path,
    mentions_dir: str | Path | None = None,
    player_map: str | Path | None = None,
) -> ProseCorpus:
    if kind == "nbc":
        return load_nbc_corpus(parsed_dir, mentions_dir, player_map)
    if kind == "rotowire":
        return load_rotowire_corpus(parsed_dir, player_map)
    raise ValueError(f"unknown corpus kind: {kind!r} (expected 'nbc' or 'rotowire')")


# ---------------------------------------------------------------------------
# Corpus completeness (the graded-run gate)
# ---------------------------------------------------------------------------

@dataclass
class CompletenessReport:
    n_resolved: int
    n_banked: int
    missing_ids: list[str]
    complete: bool

    def to_dict(self) -> dict:
        return {
            "n_resolved": self.n_resolved,
            "n_banked": self.n_banked,
            "n_missing": len(self.missing_ids),
            "missing_ids": self.missing_ids,
            "complete": self.complete,
        }


def corpus_completeness(corpus: ProseCorpus, resolved_ids: set[str]) -> CompletenessReport:
    """Banked-vs-resolved over the corpus's player map.

    The graded Arm T run may only fire when every resolved player is
    banked (Argon Q1 scope lock: the grade is registered on the full NBC
    corpus). ``resolved_ids`` are source ids as strings.
    """
    missing = sorted(resolved_ids - corpus.banked_ids)
    return CompletenessReport(
        n_resolved=len(resolved_ids),
        n_banked=len(resolved_ids & corpus.banked_ids),
        missing_ids=missing,
        complete=not missing,
    )


# ---------------------------------------------------------------------------
# Migration strata (Q4 hard exclusions)
# ---------------------------------------------------------------------------

def strata_cluster_keys(corpus: ProseCorpus) -> set[tuple]:
    """Corpus keys in an identical-minute timestamp cluster (Q4 rule (b)).

    A cluster is >= STRATA_CLUSTER_MIN items on one player's feed (the
    items banked from his pages) sharing an identical minute-level
    publication timestamp -- the shape of a platform migration/backfill
    stratum. Computed once per corpus load.

    Items stamped exactly at midnight UTC are EXEMPT: a date-granular
    source stamps every item of a day at 00:00 (the RotoWire archive does
    this whenever a fragment carries no precise time), so several items
    sharing that stamp is normal publication, not a backfill artifact.
    The rule targets items that claim minute-level precision and collide.
    """
    flagged: set[tuple] = set()
    for items in corpus.items_by_player.values():
        by_minute: dict[pd.Timestamp, list[ProseItem]] = {}
        for item in items:
            if (item.published_ts.hour, item.published_ts.minute,
                    item.published_ts.second) == (0, 0, 0):
                continue  # date-granular stamp, not a minute-level claim
            minute = item.published_ts.floor("min")
            by_minute.setdefault(minute, []).append(item)
        for group in by_minute.values():
            if len(group) >= STRATA_CLUSTER_MIN:
                flagged.update(it.corpus_key for it in group)
    return flagged


def is_stratum_by_date(item: ProseItem) -> bool:
    """Q4 rule (a): the banked migration flag, or dated at/before the
    2018-09-15T23:59:59Z migration-stratum cutoff."""
    return item.migration_stratum or item.published_ts <= _MIGRATION_CUTOFF_TS


# ---------------------------------------------------------------------------
# Decision time (Q5 + fallback amendment)
# ---------------------------------------------------------------------------

@dataclass
class DecisionTime:
    freeze_ts: Optional[pd.Timestamp]     # tz-aware ET; None when excluded
    ideal_stamp: Optional[pd.Timestamp]   # last expected T-60 grid stamp
    last_banked_stamp: Optional[pd.Timestamp]
    was_fallback: bool
    excluded: bool
    exclusion_reason: Optional[str]

    def to_dict(self) -> dict:
        def s(ts: Optional[pd.Timestamp]) -> Optional[str]:
            return None if ts is None else str(ts)
        return {
            "freeze_ts": s(self.freeze_ts),
            "ideal_stamp": s(self.ideal_stamp),
            "last_banked_stamp": s(self.last_banked_stamp),
            "was_fallback": self.was_fallback,
            "excluded": self.excluded,
            "exclusion_reason": self.exclusion_reason,
        }


def expected_stamp_grid(game_date: date) -> list[pd.Timestamp]:
    """The official report's expected stamps for a game: hourly at :30 ET,
    hours 11..23, on the day before and the day of the game."""
    stamps = []
    for d in (game_date - timedelta(days=1), game_date):
        base = pd.Timestamp(d).tz_localize(_ET)
        for h in STAMP_GRID_HOURS_ET:
            stamps.append(base + pd.Timedelta(hours=h, minutes=STAMP_GRID_MINUTE_ET))
    return sorted(stamps)


def build_stamp_calendar(reports: pd.DataFrame) -> dict[date, list[pd.Timestamp]]:
    """game_date -> sorted distinct banked report stamps (ET) for that game.

    ``reports`` is the official-report frame (nba_injury_report COLUMNS:
    report_ts_et, game_date, ...). Only the two columns are read.
    """
    df = reports[["report_ts_et", "game_date"]].copy()
    df["_ts"] = pd.to_datetime(df["report_ts_et"], utc=True).dt.tz_convert(_ET)
    df["_date"] = pd.to_datetime(df["game_date"]).dt.date
    calendar: dict[date, set] = {}
    for gdate, ts in zip(df["_date"], df["_ts"]):
        calendar.setdefault(gdate, set()).add(ts)
    return {d: sorted(v) for d, v in calendar.items()}


def resolve_decision_time(
    *,
    game_date: date,
    tip_ts: pd.Timestamp,
    banked_stamps: Sequence[pd.Timestamp],
    designation_ts: Optional[pd.Timestamp] = None,
    config: ArmTConfig = ArmTConfig(),
) -> DecisionTime:
    """Q5 decision time with the fallback amendment.

    The freeze is the designation stamp when the player was listed at an
    eligible banked stamp (Arm S's own ``decision_report_ts``), else the
    last banked stamp at or before tip - lead. When the expected T-60 grid
    stamp is unbanked the loader has fallen back; a fallback freeze outside
    [tip - fallback_max_age, tip - lead] excludes the case.
    """
    tip = pd.Timestamp(tip_ts)
    tip = tip.tz_convert(_ET) if tip.tzinfo is not None else tip.tz_localize(_ET)
    cutoff = tip - pd.Timedelta(minutes=config.decision_lead_min)
    banked = sorted(pd.Timestamp(s) for s in banked_stamps)
    eligible = [s for s in banked if s <= cutoff]
    last_banked = eligible[-1] if eligible else None
    grid = [s for s in expected_stamp_grid(game_date) if s <= cutoff]
    ideal = grid[-1] if grid else None
    was_fallback = ideal is not None and ideal not in set(banked)

    if designation_ts is not None:
        desig = pd.Timestamp(designation_ts)
        desig = desig.tz_convert(_ET) if desig.tzinfo is not None else desig.tz_localize(_ET)
        freeze = desig if desig <= cutoff else last_banked
    else:
        freeze = last_banked

    if freeze is None:
        return DecisionTime(
            freeze_ts=None, ideal_stamp=ideal, last_banked_stamp=last_banked,
            was_fallback=was_fallback, excluded=True,
            exclusion_reason="no_banked_stamp_at_or_before_t60",
        )
    floor = tip - pd.Timedelta(minutes=config.fallback_max_age_min)
    if was_fallback and freeze < floor:
        return DecisionTime(
            freeze_ts=None, ideal_stamp=ideal, last_banked_stamp=last_banked,
            was_fallback=True, excluded=True,
            exclusion_reason="fallback_stamp_outside_window",
        )
    return DecisionTime(
        freeze_ts=freeze, ideal_stamp=ideal, last_banked_stamp=last_banked,
        was_fallback=was_fallback, excluded=False, exclusion_reason=None,
    )


# ---------------------------------------------------------------------------
# Cleansing (run-time view only; banked raw is never mutated)
# ---------------------------------------------------------------------------

_BOILERPLATE_PATTERNS = (
    re.compile(r"\bRead More\b.*$", re.S),
    re.compile(r"\bMore News\b.*$", re.S),
)


def cleanse_text(text: str) -> str:
    """Deterministic surface cleansing: unescape entities, collapse
    whitespace, drop trailing boilerplate markers. Content is never
    rewritten, reconciled, or summarized."""
    import html as _html

    out = _html.unescape(str(text or ""))
    for rx in _BOILERPLATE_PATTERNS:
        out = rx.sub("", out)
    return re.sub(r"\s+", " ", out).strip()


def count_tokens(text: str) -> int:
    """Whitespace token count over headline + body text."""
    return len(str(text or "").split())


# ---------------------------------------------------------------------------
# Mention sentence filter (Q4)
# ---------------------------------------------------------------------------

def matching_sentences(text: str, last_name: str) -> list[str]:
    """Sentences containing the player's last name AND >= 1 workload token.

    Both conditions are required (Q4); matching is case-insensitive
    substring on folded text. Returns the matching sentences, cleansed.
    """
    if not last_name:
        return []
    out = []
    for sentence in _SENTENCE_SPLIT.split(str(text or "")):
        folded = _fold(sentence)
        if last_name in folded and any(tok in folded for tok in WORKLOAD_TOKENS):
            out.append(cleanse_text(sentence))
    return out


# ---------------------------------------------------------------------------
# The Q4 slice
# ---------------------------------------------------------------------------

@dataclass
class ProseAudit:
    """Per-case loader accounting (Q5 audit spec + drop diagnostics)."""

    raw_token_count: int = 0
    cleansed_token_count: int = 0
    n_subject_items: int = 0
    n_mention_items: int = 0
    n_candidates: int = 0
    n_unresolved_mention_refs: int = 0
    n_strata_dropped: int = 0
    n_pre_spell_dropped: int = 0
    n_retro_stripped: int = 0
    n_mention_items_considered: int = 0
    n_mention_items_admissible: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class CaseProseInput:
    """One case's classifier input under the Q4 contract."""

    case_id: str
    abstention: bool
    abstention_reason: Optional[str]
    subject_blurbs: list[dict[str, Any]] = field(default_factory=list)
    mention_excerpts: list[str] = field(default_factory=list)
    mention_items: list[dict[str, Any]] = field(default_factory=list)
    decision: Optional[DecisionTime] = None
    audit: ProseAudit = field(default_factory=ProseAudit)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "abstention": self.abstention,
            "abstention_reason": self.abstention_reason,
            "subject_blurbs": self.subject_blurbs,
            "mention_excerpts": self.mention_excerpts,
            "mention_items": self.mention_items,
            "decision": None if self.decision is None else self.decision.to_dict(),
            "audit": self.audit.to_dict(),
        }


def slice_case_prose(
    case: Mapping[str, Any],
    corpus: ProseCorpus,
    decision: DecisionTime,
    *,
    config: ArmTConfig = ArmTConfig(),
    cluster_keys: Optional[set[tuple]] = None,
    absence_start: Optional[pd.Timestamp] = None,
) -> CaseProseInput:
    """Build one case's Q4 input. Fail-closed: any exclusion or a
    zero-subject window returns an abstention with the reason recorded.

    ``absence_start`` (the case spell's first missed team game, when the
    caller can supply it) activates migration-stratum rule (c): items
    published before the absence start are hard-dropped.
    """
    case_id = str(case.get("case_id"))
    audit = ProseAudit()
    if decision.excluded or decision.freeze_ts is None:
        return CaseProseInput(
            case_id=case_id, abstention=True,
            abstention_reason=decision.exclusion_reason or "decision_excluded",
            decision=decision, audit=audit,
        )
    freeze = decision.freeze_ts.tz_convert("UTC")
    player_key = _norm_name(str(case.get("player")))
    cluster_keys = cluster_keys if cluster_keys is not None else set()

    # Candidate pool: his own feed's items + items that appeared on his
    # feed but were banked under another page (mention references).
    candidates: list[tuple[ProseItem, bool]] = []  # (item, is_subject_role)
    seen_keys: set[tuple] = set()
    for item in corpus.items_by_player.get(player_key, []):
        if item.corpus_key in seen_keys:
            continue
        seen_keys.add(item.corpus_key)
        is_subject = (
            (item.is_subject_for_page or item.subject_name_match)
            if corpus.name == "nbc"
            else True
        )
        candidates.append((item, is_subject))
    for ref in corpus.mention_refs_by_player.get(player_key, []):
        if ref.corpus_key in seen_keys:
            continue
        seen_keys.add(ref.corpus_key)
        resolved = corpus.pool_by_key.get(ref.corpus_key)
        if resolved is None:
            audit.n_unresolved_mention_refs += 1
            continue
        subj = resolved.subject_player
        is_subject = subj is not None and _norm_name(subj) == player_key
        candidates.append((resolved, is_subject))
    audit.n_candidates = len(candidates)

    subject_start = freeze - pd.Timedelta(days=config.subject_window_days)
    mention_start = freeze - pd.Timedelta(hours=config.mention_window_hours)
    spell_floor = (
        None if absence_start is None else pd.Timestamp(absence_start).tz_convert("UTC")
        if pd.Timestamp(absence_start).tzinfo is not None
        else pd.Timestamp(absence_start, tz="UTC")
    )

    def drop_reason(item: ProseItem) -> Optional[str]:
        if item.updated_ts is not None and item.updated_ts > freeze:
            return "retro"
        if is_stratum_by_date(item) or item.corpus_key in cluster_keys:
            return "strata"
        if spell_floor is not None and item.published_ts < spell_floor:
            return "pre_spell"
        return None

    subject_pool: list[ProseItem] = []
    mention_pool: list[ProseItem] = []
    for item, is_subject in candidates:
        if item.published_ts > freeze or item.published_ts < subject_start:
            continue
        audit.raw_token_count += count_tokens(item.headline) + count_tokens(item.body)
        reason = drop_reason(item)
        if reason == "retro":
            audit.n_retro_stripped += 1
            continue
        if reason == "strata":
            audit.n_strata_dropped += 1
            continue
        if reason == "pre_spell":
            audit.n_pre_spell_dropped += 1
            continue
        if is_subject:
            subject_pool.append(item)
        elif item.published_ts >= mention_start:
            mention_pool.append(item)

    # Subject: most recent SUBJECT_CAP, returned oldest first.
    subject_pool.sort(key=lambda it: it.published_ts)
    chosen_subjects = subject_pool[-config.subject_cap:]
    subject_blurbs = [
        {
            "published_ts": str(it.published_ts),
            "headline": cleanse_text(it.headline),
            "body": cleanse_text(it.body),
        }
        for it in chosen_subjects
    ]
    audit.n_subject_items = len(subject_blurbs)
    audit.cleansed_token_count += sum(
        count_tokens(b["headline"]) + count_tokens(b["body"]) for b in subject_blurbs
    )

    # Mentions: sentence filter, most recent MENTION_CAP admissible items.
    last = last_name_key(str(case.get("player")))
    admissible: list[tuple[ProseItem, list[str]]] = []
    for item in sorted(mention_pool, key=lambda it: it.published_ts):
        audit.n_mention_items_considered += 1
        sentences = matching_sentences(f"{item.headline}. {item.body}", last)
        if sentences:
            admissible.append((item, sentences))
    audit.n_mention_items_admissible = len(admissible)
    chosen_mentions = admissible[-config.mention_cap:]
    mention_excerpts: list[str] = []
    mention_items: list[dict[str, Any]] = []
    for item, sentences in chosen_mentions:
        mention_excerpts.extend(sentences)
        audit.cleansed_token_count += sum(count_tokens(s) for s in sentences)
        mention_items.append(
            {
                "published_ts": str(item.published_ts),
                "headline": cleanse_text(item.headline),
                "subject_player": item.subject_player,
                "n_excerpt_sentences": len(sentences),
            }
        )
    audit.n_mention_items = len(chosen_mentions)

    if not subject_blurbs:
        return CaseProseInput(
            case_id=case_id, abstention=True,
            abstention_reason="zero_subject_items",
            mention_excerpts=mention_excerpts, mention_items=mention_items,
            decision=decision, audit=audit,
        )
    return CaseProseInput(
        case_id=case_id, abstention=False, abstention_reason=None,
        subject_blurbs=subject_blurbs, mention_excerpts=mention_excerpts,
        mention_items=mention_items, decision=decision, audit=audit,
    )


# ---------------------------------------------------------------------------
# Prediction path (wired; exercised only at smoke volume by the runner)
# ---------------------------------------------------------------------------

def predict_case(classifier: Any, case: Mapping[str, Any], prose: CaseProseInput) -> BandPrediction:
    """One case through the Decisions classifier under the Q4 contract.

    A loader abstention short-circuits BEFORE any API call (zero-subject is
    fail-closed by construction, not by the classifier's discretion).
    """
    if prose.abstention:
        return BandPrediction.abstain(prose.case_id, prose.abstention_reason or "abstention")
    return classifier.classify_case(
        case, prose.subject_blurbs, mentions=prose.mention_excerpts or None
    )
