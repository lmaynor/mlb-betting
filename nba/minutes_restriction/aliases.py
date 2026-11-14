"""nba.minutes_restriction.aliases -- the minutes program's player-alias resolution layer (MIN-06).

WHY THIS EXISTS: identity across the minutes program's sources was resolved by hand, repeatedly:

  * the NBC and RotoWire player maps were curated so their display names match the hoopR/ESPN
    spellings character-for-character under a folding key (verified 2026-11-13: all 386 NBC map
    names and all 370 RotoWire map names hit a roster key exactly -- that curation IS the hand
    work this layer replaces);
  * `cases._norm_name` (the box<->report join key) does NOT fold diacritics, so a prose spelling
    like "Luka Doncic" with diacritics ("Doncic" printed with carons in the banked RotoWire/NBC
    prose: 61 headline/body hits for Doncic, 17 Jokic, 7 Vucevic, 3 Nurkic, 1 Valanciunas)
    normalizes to garbage under it ("don i") and silently misses the join;
  * the official report and ESPN disagree on name FORMS for the same player (official "Darius
    Brown" vs ESPN "Darius Brown II"; official "Nicolas Claxton" vs ESPN "Nic Claxton";
    official "Hansen Yang" vs ESPN "Yang Hansen" -- token order swapped);
  * the same folded name can be two different players (Trevon Scott: ESPN ids 3934620/BKN and
    4711005/CLE), so a name alone is not an identity.

THE LAYER: one canonical roster index (hoopR/ESPN athlete ids + full team history, the same
identity `cases` builds cases on) and one resolve() chain:

    exact_key         folded name key unique in the roster (diacritics folded, punctuation and
                      generational suffixes dropped) -- the ONLY normalization either side needs
    exact_key_team    folded key shared by >1 identity, the team hint (checked against the
                      player's FULL team history, not just his latest team) leaves exactly one
    token_swap        same token multiset, different order ("Hansen Yang"), unique in roster
    first_name_variant  last name equal and first names compatible via mlb_core's alias table /
                      prefix rule ("Nic Claxton" <-> "Nicolas Claxton", "Cam Johnson" <->
                      "Cameron Johnson"), unique in roster and team-consistent when hinted

FAIL CLOSED, ALWAYS: anything that does not resolve to exactly one identity is returned as
status "ambiguous" (with the candidate identities, for a human) or "unknown" -- never a guessed
merge. Players listed by a source but absent from the roster (official-report names with no
hoopR box rows: "Thomas Sorber", "Nikola Djurisic", "Graham Ike", mined 2026-11-13) stay
unknown; inventing an id for them would corrupt every downstream join. `audit()` turns a whole
source's name list into the report of what resolved, what is ambiguous, and what is unknown.

This layer never writes to a corpus and never mutates the roster; the crosswalk builder
(scripts/build_minutes_alias_crosswalk.py) is the artifact-producing side.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

import pandas as pd

from mlb_core.data.player_names import _first_compatible, norm_name

SOURCES = ("hoopr", "nbc", "rotowire", "official")


def canonical_key(name) -> str:
    """The one folded name key every minutes source is matched on.

    Delegates to mlb_core.data.player_names.norm_name (NFKD accent fold, lowercase,
    punctuation dropped, generational suffixes dropped) so keys here are interchangeable with
    the cross-book resolver's -- and unlike `cases._norm_name`, diacritics fold instead of
    being punched out as spaces: canonical_key("Luka Doncic" with carons) == "luka doncic".
    """
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    return norm_name(str(name))


@dataclass(frozen=True)
class Identity:
    """One canonical player: ESPN athlete id, roster display name, full team history."""

    player_id: int
    display_name: str
    teams: frozenset = frozenset()          # every team code the box scores show him on


@dataclass(frozen=True)
class Resolution:
    """The outcome of one resolve() call. player_id/display_name are set only when resolved."""

    status: str                             # "resolved" | "ambiguous" | "unknown"
    method: Optional[str] = None            # exact_key | exact_key_team | token_swap |
    #                                       # first_name_variant | source_id | source_id_conflict
    player_id: Optional[int] = None
    display_name: Optional[str] = None
    candidates: tuple = ()                  # Identity tuple when ambiguous


@dataclass
class RosterIndex:
    """Canonical identities built from hoopR box scores (the identity `cases` keys on)."""

    identities: dict = field(default_factory=dict)      # player_id -> Identity
    by_key: dict = field(default_factory=dict)          # canonical_key -> [player_id]
    by_tokens: dict = field(default_factory=dict)       # token multiset -> [player_id]
    by_last: dict = field(default_factory=dict)         # last token -> [player_id]

    @classmethod
    def from_box(cls, box: pd.DataFrame) -> "RosterIndex":
        """Box frame (athlete_id, athlete_display_name, team_abbreviation, game_date) -> index.
        Display name is the player's MOST RECENT box spelling; teams are his full history."""
        idx = cls()
        df = box.dropna(subset=["athlete_id"]).copy()
        df["_pid"] = pd.to_numeric(df["athlete_id"], errors="coerce")
        df = df.dropna(subset=["_pid"])
        teams: dict = defaultdict(set)
        for pid, tc in zip(df["_pid"], df.get("team_abbreviation", "")):
            if isinstance(tc, str) and tc.strip():
                teams[int(pid)].add(tc.strip().upper())
        latest = df.sort_values("game_date").groupby("_pid").tail(1) \
            if "game_date" in df.columns else df.groupby("_pid").tail(1)
        for pid, name in zip(latest["_pid"], latest["athlete_display_name"]):
            pid = int(pid)
            idx.identities[pid] = Identity(pid, str(name), frozenset(teams.get(pid, ())))
        for pid, ident in idx.identities.items():
            key = canonical_key(ident.display_name)
            idx.by_key.setdefault(key, []).append(pid)
            idx.by_tokens.setdefault(tuple(sorted(key.split())), []).append(pid)
            if key:
                idx.by_last.setdefault(key.split()[-1], []).append(pid)
        return idx

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping]) -> "RosterIndex":
        """Rows of {player_id, full_name, team|teams} -> index (tests, small crosswalks)."""
        idx = cls()
        for r in rows:
            pid = int(r["player_id"])
            tms = r.get("teams") or ([r["team"]] if r.get("team") else [])
            idx.identities[pid] = Identity(pid, str(r["full_name"]),
                                           frozenset(str(t).upper() for t in tms))
        for pid, ident in idx.identities.items():
            key = canonical_key(ident.display_name)
            idx.by_key.setdefault(key, []).append(pid)
            idx.by_tokens.setdefault(tuple(sorted(key.split())), []).append(pid)
            if key:
                idx.by_last.setdefault(key.split()[-1], []).append(pid)
        return idx


def _team_ok(ident: Identity, team: Optional[str]) -> bool:
    return team is None or not ident.teams or team.upper() in ident.teams


class AliasResolver:
    """Resolve source names (and source ids, via the curated source maps) to roster identities."""

    def __init__(self, roster: RosterIndex, source_maps: Optional[Mapping[str, Mapping]] = None):
        """source_maps: {source: {source_id: source_display_name}} -- the player maps the
        pullers bank (player, <source>_id, slug). Ids are compared as strings, so the NBC
        puller's unstable id typing (int 9526 vs str "9526") cannot split an identity."""
        self.roster = roster
        self.source_maps = {
            src: {str(sid): name for sid, name in m.items()}
            for src, m in (source_maps or {}).items()
        }

    # ---------------------------------------------------------------------------------- names
    def resolve(self, name, team: Optional[str] = None) -> Resolution:
        """One source display name (+ optional team hint) -> Resolution. Fail closed."""
        key = canonical_key(name)
        if not key:
            return Resolution("unknown")
        hits = [self.roster.identities[p] for p in self.roster.by_key.get(key, [])]
        if len(hits) == 1:
            return Resolution("resolved", "exact_key", hits[0].player_id, hits[0].display_name)
        if len(hits) > 1:
            inteam = [h for h in hits if _team_ok(h, team)] if team else []
            if team and len(inteam) == 1:
                return Resolution("resolved", "exact_key_team",
                                  inteam[0].player_id, inteam[0].display_name)
            return Resolution("ambiguous", "exact_key", candidates=tuple(hits))
        # token swap: same multiset of name tokens, different order ("Hansen Yang")
        swaps = [self.roster.identities[p]
                 for p in self.roster.by_tokens.get(tuple(sorted(key.split())), [])]
        swaps = [h for h in swaps if _team_ok(h, team)]
        if len(swaps) == 1:
            return Resolution("resolved", "token_swap", swaps[0].player_id, swaps[0].display_name)
        if len(swaps) > 1:
            return Resolution("ambiguous", "token_swap", candidates=tuple(swaps))
        # first-name variant: same last token, compatible first token (alias table / prefix)
        toks = key.split()
        if len(toks) >= 2:
            cands = []
            for pid in self.roster.by_last.get(toks[-1], []):
                ident = self.roster.identities[pid]
                ikey = canonical_key(ident.display_name).split()
                if len(ikey) >= 2 and _first_compatible(toks[0], ikey[0]) \
                        and toks[1:-1] == ikey[1:-1] and _team_ok(ident, team):
                    cands.append(ident)
            if len(cands) == 1:
                return Resolution("resolved", "first_name_variant",
                                  cands[0].player_id, cands[0].display_name)
            if len(cands) > 1:
                return Resolution("ambiguous", "first_name_variant", candidates=tuple(cands))
        return Resolution("unknown")

    # ------------------------------------------------------------------------------------ ids
    def resolve_source_id(self, source: str, source_id, name=None,
                          team: Optional[str] = None) -> Resolution:
        """A source-native id -> identity, through the source map's display name. When the
        caller also has a name for the id (a parsed record's page/subject name), the two must
        agree: a disagreement is reported as ambiguous (source_id_conflict), never resolved by
        picking a side -- that is the is_subject flag-vs-name lesson applied to ids."""
        mapped = self.source_maps.get(source, {}).get(str(source_id))
        if mapped is None:
            return Resolution("unknown")
        via_map = self.resolve(mapped, team)
        if name is None or canonical_key(name) == canonical_key(mapped):
            if via_map.status == "resolved":
                return Resolution("resolved", "source_id", via_map.player_id, via_map.display_name)
            return via_map
        via_name = self.resolve(name, team)
        if via_map.status == "resolved" and via_name.status == "resolved" \
                and via_map.player_id == via_name.player_id:
            return Resolution("resolved", "source_id", via_map.player_id, via_map.display_name)
        pool: dict = {}
        for r in (via_map, via_name):
            for c in r.candidates:
                pool[c.player_id] = c
            if r.status == "resolved":
                pool[r.player_id] = self.roster.identities[r.player_id]
        return Resolution("ambiguous", "source_id_conflict", candidates=tuple(pool.values()))

    # ---------------------------------------------------------------------------------- audit
    def audit(self, names_by_source: Mapping[str, Sequence[str]]) -> dict:
        """Every distinct name in each source -> the coverage report: counts by status and
        method, plus the full ambiguous and unknown lists (the human work queue). Pure."""
        out: dict = {"sources": {}, "ambiguous": [], "unknown": []}
        for source, names in names_by_source.items():
            counts: dict = defaultdict(int)
            for name in sorted({str(n) for n in names if n}):
                r = self.resolve(name)
                counts[r.status] += 1
                if r.status == "resolved":
                    counts[f"via_{r.method}"] += 1
                elif r.status == "ambiguous":
                    out["ambiguous"].append({
                        "source": source, "name": name,
                        "candidates": [{"player_id": c.player_id, "display_name": c.display_name,
                                        "teams": sorted(c.teams)} for c in r.candidates]})
                else:
                    out["unknown"].append({"source": source, "name": name})
            out["sources"][source] = {"n_names": len({str(n) for n in names if n}),
                                      **dict(sorted(counts.items()))}
        return out
