"""mlb_core.odds.canonical.normalize -- Distinctness-Preserving Name Normalizer (BNK-12).

Spec 2.2 and DEC-09 lesson:
Normalization must be DISTINCTNESS-PRESERVING:
  - Must merge "D.J. Moore" / "DJ Moore"
  - Must NEVER merge "A.J. Brown" with "Amon-Ra St. Brown"
  - Normalization produces a key; two different full names mapping to one key
    is a collision that the resolver treats as ambiguous, not a merge.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Dict, Iterable, Optional, Set, Tuple

_SUFFIX_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_TEAM_TAIL_RE = re.compile(r"\s*\(([A-Za-z]{2,4})\)\s*$")


def split_team_tail(raw: str) -> Tuple[str, Optional[str]]:
    """'Alex Ovechkin (WSH)' -> ('Alex Ovechkin', 'WSH'); no suffix -> (raw, None)."""
    s = (raw or "").strip()
    m = _TEAM_TAIL_RE.search(s)
    if m:
        return s[:m.start()].strip(), m.group(1).upper()
    return s, None


def normalize_name(name: str) -> str:
    """Distinctness-preserving name normalization.

    - Strips team tails like '(BOS)' or '(WSH)'
    - Accent-folds to ASCII (e.g. Dončić -> doncic)
    - Lowercases
    - Strips internal dots and quotes so 'D.J.' and 'DJ' merge to 'dj'
    - Replaces hyphens and underscores with spaces so 'Amon-Ra' becomes 'amon ra'
    - Drops generational suffixes ('jr', 'sr', 'ii', 'iii', 'iv', 'v')
    - Retains full distinct name tokens (never collapses to single-letter initial)

    'D.J. Moore' -> 'dj moore'
    'DJ Moore'   -> 'dj moore'
    'A.J. Brown' -> 'aj brown'
    'Amon-Ra St. Brown' -> 'amon ra st brown'
    """
    if not name:
        return ""

    # Split off any trailing (TEAM)
    clean, _ = split_team_tail(name)

    # Accent-fold to ASCII
    s = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode().lower()

    # Punctuation: strip dots, apostrophes, backticks
    s = re.sub(r"[.'`\"]", "", s)

    # Replace hyphens, slashes, underscores with spaces
    s = re.sub(r"[-_/]", " ", s)

    # Remove any other non-alphanumeric chars
    s = re.sub(r"[^a-z0-9 ]", " ", s)

    # Tokenize and filter generational suffixes
    tokens = [t for t in s.split() if t not in _SUFFIX_TOKENS]
    return " ".join(tokens)


def _canonical_tokens(name: str) -> Tuple[str, ...]:
    """Return raw normalized tokens without suffix filtering for collision detection."""
    clean, _ = split_team_tail(name)
    s = unicodedata.normalize("NFKD", clean).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`\"]", "", s)
    s = re.sub(r"[-_/]", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return tuple(s.split())


class NameCollisionTracker:
    """Tracks name normalization keys and detects collisions.

    If two different full names (beyond trivial punctuation/suffix variants)
    map to the same normalized key, the key is flagged as a collision.
    The resolver checks is_collision(key) and returns 'ambiguous' rather
    than performing an accidental merge.
    """

    def __init__(self):
        self._key_to_raw_names: Dict[str, Set[str]] = defaultdict(set)
        self._key_to_master_ids: Dict[str, Set[str]] = defaultdict(set)
        self._collided_keys: Set[str] = set()

    def register(self, raw_name: str, master_id: Optional[str] = None) -> str:
        """Register a name in the tracker and return its normalized key."""
        key = normalize_name(raw_name)
        if not key:
            return ""

        raw_set = self._key_to_raw_names[key]
        if raw_name not in raw_set:
            # Check if this new name is genuinely a different full name from existing
            for existing in raw_set:
                if self._are_genuinely_different_names(existing, raw_name):
                    self._collided_keys.add(key)
                    break
            raw_set.add(raw_name)

        if master_id:
            mid_set = self._key_to_master_ids[key]
            if mid_set and str(master_id) not in mid_set:
                # Two distinct master IDs share this key -> immediate collision
                self._collided_keys.add(key)
            mid_set.add(str(master_id))

        return key

    def register_collision(self, key: str) -> None:
        """Explicitly flag a normalized key as collided."""
        if key:
            self._collided_keys.add(key)

    def is_collision(self, key: str) -> bool:
        """True if multiple distinct entities share this normalized key."""
        return key in self._collided_keys

    @staticmethod
    def _are_genuinely_different_names(name1: str, name2: str) -> bool:
        """Return True if name1 and name2 represent different full names.

        'D.J. Moore' and 'DJ Moore' are NOT different names (same tokens).
        'A.J. Brown' and 'Amon-Ra St. Brown' ARE different names.
        """
        t1 = _canonical_tokens(name1)
        t2 = _canonical_tokens(name2)
        if t1 == t2:
            return False
        # Remove suffix tokens and compare
        f1 = tuple(t for t in t1 if t not in _SUFFIX_TOKENS)
        f2 = tuple(t for t in t2 if t not in _SUFFIX_TOKENS)
        return f1 != f2
