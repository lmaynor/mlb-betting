"""Cross-book player-name normalization + resolution to a sport's native player id.

Why this exists (found live 2026-10-14, NHL): the SAME player is spelled differently by different books --
"Alex Ovechkin" (FanDuel/Novig) vs "Alex Ovechkin (WSH)" (Bovada, team suffix) vs "Alexander Ovechkin" -- so a
per-book consensus keyed on the raw string never groups them (n_books stayed 1). Two names can also be the SAME
string for different people (two Max Muncys in MLB; the current NHL/NBA rosters have no such pair, but trades and
call-ups create them). The only safe fix is to resolve
every quote to the league's own player id through a roster crosswalk, using the team hint when a book supplies one,
and to FAIL CLOSED (return None) when it is still ambiguous -- a wrong merge prices a bet off another player's line.

norm_name() is identical in behaviour to ev.props.norm_name (accent-fold, drop punctuation and generational
suffixes) so keys written by either are interchangeable.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}
_TEAM_TAIL = re.compile(r"\s*\(([A-Za-z]{2,4})\)\s*$")

# Short <-> long first names books genuinely disagree on. Deliberately small: a bad alias is a wrong merge.
_ALIAS_PAIRS = [
    ("alex", "alexander"), ("alexandre", "alexander"), ("mike", "michael"), ("matt", "matthew"),
    ("nick", "nicholas"), ("nico", "nicolas"), ("zach", "zachary"), ("zack", "zachary"),
    ("chris", "christopher"), ("joe", "joseph"), ("will", "william"), ("ben", "benjamin"),
    ("tom", "thomas"), ("sam", "samuel"), ("dan", "daniel"), ("danny", "daniel"), ("tony", "anthony"),
    ("jake", "jacob"), ("cam", "cameron"), ("drew", "andrew"), ("josh", "joshua"), ("jon", "jonathan"),
    ("jt", "j t"), ("pj", "p j"), ("tj", "t j"), ("cj", "c j"), ("aj", "a j"), ("ty", "tyler"),
    ("jaxson", "jackson"), ("evgeni", "evgeny"), ("yevgeni", "evgeny"), ("vlad", "vladimir"),
]
_ALIASES: dict[str, set] = defaultdict(set)
for _a, _b in _ALIAS_PAIRS:
    _ALIASES[_a].add(_b)
    _ALIASES[_b].add(_a)


def norm_name(name: str) -> str:
    """'Ja'Marr Chase Jr.' -> 'jamarr chase'. Accent-fold, lowercase, punctuation out, generational suffix out."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[-_]", " ", s)
    toks = [t for t in re.sub(r"[^a-z0-9 ]", " ", s).split() if t not in _SUFFIX_TOKENS]
    return " ".join(toks)


def split_team_tail(raw: str):
    """'Alex Ovechkin (WSH)' -> ('Alex Ovechkin', 'WSH'); no suffix -> (raw, None)."""
    raw = (raw or "").strip()
    m = _TEAM_TAIL.search(raw)
    return (raw[: m.start()].strip(), m.group(1).upper()) if m else (raw, None)


def _first_compatible(a: str, b: str) -> bool:
    """First names equal, one a prefix of the other ('alex'/'alexander'), or a listed alias pair."""
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return b in _ALIASES.get(a, ())


class PlayerResolver:
    """name (+ optional team hint) -> native player id, or None when unknown/ambiguous.

    rows: iterable of dicts with at least {player_id, full_name}; optional {team} (the SAME team-code vocabulary the
    caller will pass as `team`). Build it once per sport from the roster crosswalk master."""

    def __init__(self, rows):
        self._by_norm: dict[str, list] = defaultdict(list)
        self._by_last: dict[str, list] = defaultdict(list)
        self.size = 0
        for r in rows:
            pid, full = r.get("player_id"), r.get("full_name")
            if pid is None or not full:
                continue
            n = norm_name(full)
            if not n:
                continue
            rec = (pid, n, (r.get("team") or "").upper() or None)
            self._by_norm[n].append(rec)
            self._by_last[n.split()[-1]].append(rec)
            self.size += 1

    @staticmethod
    def _pick(cands, team, within=None):
        if within:
            w = {t.upper() for t in within if t}
            narrowed = [c for c in cands if c[2] in w]
            if narrowed:                       # only narrow when it leaves someone; never invent a candidate
                cands = narrowed
        ids = {c[0] for c in cands}
        if len(ids) == 1:
            return next(iter(ids))
        if team:
            t = [c for c in cands if c[2] == team.upper()]
            tids = {c[0] for c in t}
            if len(tids) == 1:
                return next(iter(tids))
        return None

    def resolve(self, raw_name: str, team: str | None = None, within=None):
        """-> (player_id | None, how) where how in {'exact','team','compat','ambiguous','unknown'}.

        `within`: the team codes actually playing in this event ((away, home)); two same-named players on different
        teams (e.g. two same-named players on different teams) resolve cleanly when only one of those teams is on the
        card."""
        name, tail = split_team_tail(raw_name)
        team = (team or tail or "") or None
        n = norm_name(name)
        if not n:
            return None, "unknown"
        exact = self._by_norm.get(n, [])
        if exact:
            pid = self._pick(exact, team, within)
            return (pid, "exact" if len({c[0] for c in exact}) == 1 else "team") if pid is not None else (None, "ambiguous")
        toks = n.split()
        if len(toks) < 2:
            return None, "unknown"
        first, last = toks[0], toks[-1]
        compat = [c for c in self._by_last.get(last, []) if _first_compatible(first, c[1].split()[0])]
        if compat:
            pid = self._pick(compat, team, within)
            return (pid, "compat") if pid is not None else (None, "ambiguous")
        return None, "unknown"
