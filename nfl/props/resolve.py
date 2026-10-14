"""nfl.props.resolve -- book / Kalshi player string -> nflverse ``gsis_id``, FAIL CLOSED.

Why: grading a prop against the wrong player's stat line is silent and costs money, and real NFL namesakes exist
(see ``docs`` in the module tests: two ``Lamar Jackson`` (QB BAL / CB ATL), ``Josh Allen`` (QB BUF / LB JAX),
``DeVonta Smith`` (WR PHI / CB CAR), ``Marvin Harrison`` (Jr. ARI WR / Sr. HOF) -- and ``norm_name`` strips ``Jr./II``
so generational pairs collapse onto one key). So this resolver NEVER guesses and NEVER fuzzy-matches:

  * the only way a string reaches a player is (a) an EXACT normalized-name key (``ev.props.norm_name``) built from
    nflverse's own name fields, or (b) an entry in the reviewed ``ALIASES`` table below;
  * the candidate set is then narrowed ONLY by evidence (who is on a roster that season/week, the team hint, the
    position hint, active-roster status), and ONLY a single survivor resolves;
  * more than one survivor -> ``Resolution(status="ambiguous", candidates=...)``; none -> ``"unknown"``.
    A hint that CONTRADICTS the only candidate (``Josh Allen (JAX)`` when the only roster Josh Allen that week is
    BUF's QB) is also unknown -- a contradicting hint is evidence it is a different person.

Name keys come from nflverse's own spellings (``players``: display_name, first+last, common_first+last,
football_name+last; ``rosters_weekly``: full_name, first+last, football_name+last), so ``Cam Ward`` / ``Cameron Ward``
and ``Chig Okonkwo`` / ``Chigoziem Okonkwo`` resolve without any hand-maintained list. ``ALIASES`` is for spellings
that appear in NO nflverse field.

Team codes: hints are translated with ``nfl.data.keys.ev_code`` (JAX->JAC, LA->LAR, OAK->LV, ...) plus
``TEAM_HINT_VARIANTS`` below (book 3-letter codes such as ``NOR``). Roster teams are stored in ev/ code space.

Context: ``season`` (+ ``week``, or a ``game_date`` that is looked up in ``schedules``). With no context the
candidate set is "every player ever carrying that name" -- namesakes then correctly stay ambiguous.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Optional

import pandas as pd

from ev.props import norm_name, split_player
from nfl.data.keys import ev_code

RESOLVED, AMBIGUOUS, UNKNOWN = "resolved", "ambiguous", "unknown"

# Book / feed spellings of a team that nfl.data.keys.ev_code does not know. NOR: seen as the "(NOR)" tail on
# real ParlayAPI NFL payloads (EVSweep/nfl_props/raw, 2026-10-04..12). The rest are Pro-Football-Reference style
# 3-letter codes, reviewed against the 32 franchises (NOT yet seen in our payloads; harmless if never used).
TEAM_HINT_VARIANTS = {
    "NOR": "NO",     # seen live
    "NWE": "NE", "GNB": "GB", "KAN": "KC", "SFO": "SF", "TAM": "TB", "SDG": "LAC", "RAI": "LV",
}

# Position hint synonyms (roster position vocabulary is QB RB FB WR TE T G C ...). Only unambiguous renamings.
_POSITION_SYNONYMS = {"HB": "RB"}

# Roster statuses that mean "on the active roster that week" (ACT = active, INA = game-day inactive, still on the
# 53). Used ONLY as a late tie-break among otherwise-equal namesakes: a practice-squad / IR / cut namesake is
# not who a book lists a prop for. Anything else (DEV, RES, CUT, RET, EXE, ...) is not "playing".
_ACTIVE_STATUSES = frozenset({"ACT", "INA"})

# ------------------------------------------------------------------------------------------------ alias table
# (alias as a book writes it, canonical nflverse spelling, evidence). Both sides are passed through norm_name.
# RULES (enforced by PlayerResolver.alias_problems() and by tests/test_nfl_resolve.py):
#   * an alias key maps to exactly ONE canonical key (a duplicate alias with a different target is a bug);
#   * an alias is consulted ONLY when the string has NO exact nflverse match at all -- it can never shadow one;
#   * the canonical key must exist in the nflverse name index (otherwise the alias is dead and reported).
# Review each addition against real payloads: a bad alias is a wrong merge.
#
# Populated 2026-10-14 from a full scan of every player description in the 439 raw ParlayAPI payloads under
# EVSweep/nfl_props/raw (all books, 2026-10-04..12): 532 distinct (name, team-tail) strings, 531 resolve through the
# nflverse-derived name keys, so NO hand alias is currently needed. (Short forms like Cam/Cameron, Chig/Chigoziem,
# Mike/Michael, Kenny/Kenneth are covered by the nflverse-derived keys; 519 of 3,850 recent players have a
# first_name that differs from display_name.)
# The one miss, "Chris Autman-Bell" (betrivers/parx/fanduel), must NOT be aliased to nflverse's "Chris Bell"
# (00-0041525): that is a different person (Louisville WR, 2026 3rd-round pick, MIA). He is on no 2026 nflverse roster,
# so the correct outcome is "unknown" -- the resolver doing its job. The table is the reviewed escape hatch for a
# spelling that really is the same person (e.g. a book nickname absent from every nflverse field).
ALIASES: tuple = ()


def team_code(raw) -> Optional[str]:
    """Hint / roster team spelling -> ev/ code (JAX->JAC, LA->LAR, NOR->NO ...), or None if not an NFL franchise."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    t = raw.strip().upper()
    return ev_code(TEAM_HINT_VARIANTS.get(t, t))


def _position(raw) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = raw.strip().upper()
    return _POSITION_SYNONYMS.get(p, p)


@dataclass(frozen=True)
class Resolution:
    status: str                       # resolved | ambiguous | unknown
    gsis_id: Optional[str] = None     # set iff status == resolved
    candidates: tuple = ()            # surviving candidate ids (ambiguous) or the pre-filter set (unknown, diagnostics)
    how: str = ""                     # e.g. "name", "name+week", "team", "team+position", "alias:name+week"
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED


def _clean_id(x) -> Optional[str]:
    return x.strip() if isinstance(x, str) and x.strip() else None


class PlayerResolver:
    """name (+ context) -> gsis_id. Build once from nflverse frames; ``PlayerResolver.from_mirror(seasons)`` loads them.

    rosters_weekly : needs season, week, team, gsis_id (+ position, status, full_name, first_name, last_name,
                     football_name when present). This is the "who was on which team in which week" evidence.
    players        : optional id crosswalk (gsis_id, display_name, first_name, last_name, common_first_name,
                     football_name). Adds name keys only; a player is never resolved from `players` alone when a
                     season is given (they must be on that season's roster).
    schedules      : optional; lets resolve(game_date=...) find the (season, week).
    aliases        : optional override of the module ALIASES (tests).
    """

    def __init__(self, rosters_weekly: pd.DataFrame, players: Optional[pd.DataFrame] = None,
                 schedules: Optional[pd.DataFrame] = None, aliases: Optional[Iterable] = None):
        self._names: dict = defaultdict(set)          # norm key -> {gsis_id}
        self._display: dict = {}                      # gsis_id -> display name
        self._pres_week: dict = defaultdict(lambda: defaultdict(list))    # (season, week) -> gid -> [(team,pos,status)]
        self._pres_season: dict = defaultdict(lambda: defaultdict(list))  # season -> gid -> [(team,pos,status)]
        self._pres_all: dict = defaultdict(list)      # gid -> [(team,pos,status)] over every loaded season
        self._games: dict = defaultdict(list)         # 'YYYY-MM-DD' -> [(season, week, away, home)]
        self._add_rosters(rosters_weekly)
        if players is not None:
            self._add_players(players)
        if schedules is not None:
            self._add_schedules(schedules)
        self._alias_rows = tuple(ALIASES if aliases is None else aliases)
        self._alias_map, self._alias_problems = self._build_aliases(self._alias_rows)
        self.size = len(self._display)

    # -------------------------------------------------------------------------------------------- construction
    @classmethod
    def from_mirror(cls, seasons: Iterable[int]) -> "PlayerResolver":
        from nfl.data.read import load
        seasons = sorted(set(seasons))
        return cls(load("rosters_weekly", seasons=seasons), players=load("players"), schedules=load("schedules"))

    def _add_name(self, text, gid) -> None:
        if isinstance(text, str):
            k = norm_name(text)
            if k:
                self._names[k].add(gid)

    def _add_players(self, p: pd.DataFrame) -> None:
        for row in p.to_dict("records"):
            gid = _clean_id(row.get("gsis_id"))
            if gid is None:
                continue
            self._add_name(row.get("display_name"), gid)
            first, last = row.get("first_name"), row.get("last_name")
            common, football = row.get("common_first_name"), row.get("football_name")
            if isinstance(last, str) and last:
                for f in (first, common, football):
                    if isinstance(f, str) and f:
                        self._add_name(f"{f} {last}", gid)
            if isinstance(row.get("display_name"), str):
                self._display.setdefault(gid, row["display_name"])

    def _add_rosters(self, r: pd.DataFrame) -> None:
        need = {"gsis_id", "season", "week", "team"}
        miss = need - set(r.columns)
        if miss:
            raise ValueError(f"rosters_weekly frame is missing columns {sorted(miss)}")
        for row in r.to_dict("records"):
            gid = _clean_id(row.get("gsis_id"))
            if gid is None:
                continue
            season, week = int(row["season"]), int(row["week"])
            rec = (team_code(row.get("team")), _position(row.get("position")), row.get("status"))
            self._pres_week[(season, week)][gid].append(rec)
            self._pres_season[season][gid].append(rec)
            self._pres_all[gid].append(rec)
            self._add_name(row.get("full_name"), gid)
            last = row.get("last_name")
            if isinstance(last, str) and last:
                for f in (row.get("first_name"), row.get("football_name")):
                    if isinstance(f, str) and f:
                        self._add_name(f"{f} {last}", gid)
            if isinstance(row.get("full_name"), str):
                self._display.setdefault(gid, row["full_name"])

    def _add_schedules(self, g: pd.DataFrame) -> None:
        for row in g.to_dict("records"):
            day = str(row.get("gameday"))[:10]
            a, h = team_code(row.get("away_team")), team_code(row.get("home_team"))
            if day and a and h and pd.notna(row.get("season")) and pd.notna(row.get("week")):
                self._games[day].append((int(row["season"]), int(row["week"]), a, h))

    def _build_aliases(self, rows):
        amap, problems, seen = {}, [], {}
        for alias, canonical, *_evidence in rows:
            a, c = norm_name(alias), norm_name(canonical)
            if not a or not c:
                problems.append(f"alias {alias!r}->{canonical!r}: empty after normalisation")
                continue
            if a in seen and seen[a] != c:
                problems.append(f"alias {a!r} maps to two targets: {seen[a]!r} and {c!r}")
                continue
            seen[a] = c
            if a == c:
                problems.append(f"alias {a!r} equals its own target")
            elif a in self._names:
                problems.append(f"alias {a!r} shadows an exact nflverse name ({len(self._names[a])} id(s)); "
                                f"it would never be consulted -- remove it")
            if c not in self._names:
                problems.append(f"alias {a!r}: target {c!r} is not a known nflverse name (dead alias)")
            amap[a] = c
        return amap, problems

    def alias_problems(self) -> list:
        """Integrity report for the alias table against THIS resolver's name index; [] means clean."""
        return list(self._alias_problems)

    # -------------------------------------------------------------------------------------------- context
    def display_name(self, gsis_id: str) -> Optional[str]:
        return self._display.get(gsis_id)

    def has_roster(self, season: int, week: Optional[int] = None) -> bool:
        return (season, week) in self._pres_week if week is not None else season in self._pres_season

    def teams_of(self, gsis_id: str, season: int, week: Optional[int] = None) -> frozenset:
        """Team codes (ev/ space) the player is rostered on that week (season-wide if the week is not in the data)."""
        if week is not None and (season, week) in self._pres_week:
            recs = self._pres_week[(season, week)].get(gsis_id, [])
        else:
            recs = self._pres_season.get(season, {}).get(gsis_id, [])
        return frozenset(r[0] for r in recs if r[0])

    def _season_week(self, game_date, team):
        """game_date (+ optional team) -> (season, week) via schedules; None if not exactly one (season, week)."""
        games = self._games.get(str(game_date)[:10], [])
        if team:
            games = [g for g in games if team in (g[2], g[3])]
        sw = {(g[0], g[1]) for g in games}
        return next(iter(sw)) if len(sw) == 1 else None

    # -------------------------------------------------------------------------------------------- resolve
    def resolve(self, name: str, *, season: Optional[int] = None, week: Optional[int] = None, game_date=None,
                team: Optional[str] = None, position: Optional[str] = None) -> Resolution:
        """Resolve one player string. `name` may carry a team tail ("Jonathan Taylor (IND)"); `team` may be any
        spelling team_code() understands. Returns a Resolution; only status == "resolved" has a gsis_id."""
        raw, tail = split_player(name)
        key = norm_name(raw)
        if not key:
            return Resolution(UNKNOWN, how="empty_name")

        # ---- team hint(s)
        hint, hint_codes = None, set()
        for label, t in (("team", team), ("tail", tail)):
            if t:
                code = team_code(t)
                if code is None:
                    return Resolution(UNKNOWN, how="bad_team_hint", note=f"unrecognised team hint {t!r} ({label})")
                hint_codes.add(code)
        if len(hint_codes) > 1:
            return Resolution(AMBIGUOUS, how="team_hint_conflict", note=f"conflicting team hints {sorted(hint_codes)}")
        hint = next(iter(hint_codes)) if hint_codes else None

        # ---- context: season / week (or a date)
        if season is None and week is None and game_date is not None:
            sw = self._season_week(game_date, hint)
            if sw is None:
                return Resolution(UNKNOWN, how="no_game_for_date",
                                  note=f"game_date {str(game_date)[:10]} does not identify one (season, week)")
            season, week = sw
        if week is not None and season is None:
            return Resolution(UNKNOWN, how="week_without_season")

        # ---- exact name key, then the reviewed alias table (only when there is NO exact key at all)
        prefix = ""
        cands = set(self._names.get(key, ()))
        if not cands and key in self._alias_map:
            cands, prefix = set(self._names.get(self._alias_map[key], ())), "alias:"
        if not cands:
            return Resolution(UNKNOWN, how="no_name_match")
        all_cands = tuple(sorted(cands))

        steps = ["name"]
        # ---- roster presence for the season / week
        if season is not None:
            if week is not None and (season, week) in self._pres_week:
                pres, level = self._pres_week[(season, week)], "week"
            elif season in self._pres_season:
                pres, level = self._pres_season[season], ("season_fallback" if week is not None else "season")
            else:
                return Resolution(UNKNOWN, candidates=all_cands, how=prefix + "no_roster_data",
                                  note=f"no rosters_weekly rows for season {season}")
            cands = {g for g in cands if g in pres}
            steps.append(level)
            if not cands:
                return Resolution(UNKNOWN, candidates=all_cands, how=prefix + "not_on_roster",
                                  note=f"name matches {len(all_cands)} id(s) but none is on a {season} roster"
                                       f"{'' if week is None else f' (week {week})'}")
        elif hint is not None or _position(position) is not None:
            # no season: a team / position hint is still evidence -- test it against EVERY loaded season
            pres = self._pres_all
            cands = {g for g in cands if g in pres}
            steps.append("any_season")
            if not cands:
                return Resolution(UNKNOWN, candidates=all_cands, how=prefix + "not_on_any_roster",
                                  note="name matches but no id is on any loaded roster")
        else:
            pres = None

        def rows_of(g):
            return pres[g] if pres is not None else []

        def done(g, how):
            return Resolution(RESOLVED, g, candidates=(g,), how=prefix + how)

        # ---- team hint: contradiction is evidence of a different person
        if hint is not None and pres is not None:
            keep = {g for g in cands if any(r[0] == hint for r in rows_of(g))}
            if not keep:
                return Resolution(UNKNOWN, candidates=tuple(sorted(cands)), how=prefix + "team_hint_mismatch",
                                  note=f"no candidate is on {hint}")
            if keep != cands:
                steps.append("team")
            cands = keep
        if len(cands) == 1:
            return done(next(iter(cands)), "+".join(steps))

        # ---- position hint
        pos = _position(position)
        if pos is not None and pres is not None:
            keep = {g for g in cands if any(r[1] == pos for r in rows_of(g))}
            if not keep:
                return Resolution(UNKNOWN, candidates=tuple(sorted(cands)), how=prefix + "position_hint_mismatch",
                                  note=f"no candidate plays {pos}")
            if keep != cands:
                steps.append("position")
            cands = keep
            if len(cands) == 1:
                return done(next(iter(cands)), "+".join(steps))

        # ---- last tie-break: active-roster status (practice-squad / IR / cut namesakes are not who is listed)
        if pres is not None:
            keep = {g for g in cands if any(r[2] in _ACTIVE_STATUSES for r in rows_of(g)
                                            if hint is None or r[0] == hint)}
            if keep and keep != cands:
                cands = keep
                steps.append("active")
                if len(cands) == 1:
                    return done(next(iter(cands)), "+".join(steps))

        return Resolution(AMBIGUOUS, candidates=tuple(sorted(cands)), how=prefix + "+".join(steps),
                          note=f"{len(cands)} candidates remain; pass team= / position= or a fuller context")

    def resolve_id(self, name: str, **ctx) -> Optional[str]:
        """gsis_id or None (None for BOTH ambiguous and unknown -- use resolve() when the reason matters)."""
        r = self.resolve(name, **ctx)
        return r.gsis_id if r.ok else None

    # -------------------------------------------------------------------------------------------- coverage
    def coverage(self, truth: pd.DataFrame, levels=("name", "team", "season", "season_team", "week", "week_team")) -> dict:
        """Self-consistency check: for each truth row (gsis_id, season, week, team) query the resolver with that
        player's OWN display name at several context levels and score the answer against the truth id.

        levels (context passed to resolve()):
          name         bare name, no context at all (the pessimistic floor: every namesake ever listed competes)
          team         name + team hint only (tested against every loaded season's rosters)
          season       name + season (books that send no team tail, no week)
          season_team  name + season + team hint
          week         name + season + week (no team hint)
          week_team    name + season + week + team hint (the real use)
        Returns {level: {"n", "correct", "ambiguous", "unknown", "wrong", "frac_correct",
                         "players", "players_all_correct", "frac_players_all_correct"}, ...,
                 "ambiguous_names": {level: [(display_name, n_rows), ...]}, "wrong_examples": {level: [...]}}.
        `wrong` must be 0: a non-zero value means the resolver confidently named a DIFFERENT player.
        Rows are player-weeks; "players_*" is the same score collapsed to distinct gsis_ids (a player counts as
        all-correct only if every one of their rows resolved to them).
        """
        out: dict = {"ambiguous_names": {}, "wrong_examples": {}}
        rows = truth[["gsis_id", "season", "week", "team"]].drop_duplicates().to_dict("records")
        for lvl in levels:
            c = {"n": 0, "correct": 0, "ambiguous": 0, "unknown": 0, "wrong": 0}
            amb: dict = defaultdict(int)
            bad_ids: set = set()
            wrong = []
            for r in rows:
                nm = self._display.get(r["gsis_id"])
                c["n"] += 1
                if nm is None:
                    c["unknown"] += 1
                    bad_ids.add(r["gsis_id"])
                    continue
                ctx: dict = {}
                if lvl in ("season", "season_team", "week", "week_team"):
                    ctx["season"] = int(r["season"])
                if lvl in ("week", "week_team"):
                    ctx["week"] = int(r["week"])
                if lvl in ("team", "season_team", "week_team"):
                    ctx["team"] = r["team"]
                res = self.resolve(nm, **ctx)
                if res.status == RESOLVED and res.gsis_id == r["gsis_id"]:
                    c["correct"] += 1
                    continue
                bad_ids.add(r["gsis_id"])
                if res.status == RESOLVED:
                    c["wrong"] += 1
                    wrong.append((nm, r["season"], r["week"], r["team"], res.gsis_id))
                elif res.status == AMBIGUOUS:
                    c["ambiguous"] += 1
                    amb[nm] += 1
                else:
                    c["unknown"] += 1
            ids = {r["gsis_id"] for r in rows}
            c["frac_correct"] = c["correct"] / c["n"] if c["n"] else float("nan")
            c["players"] = len(ids)
            c["players_all_correct"] = len(ids - bad_ids)
            c["frac_players_all_correct"] = c["players_all_correct"] / len(ids) if ids else float("nan")
            out[lvl] = c
            out["ambiguous_names"][lvl] = sorted(amb.items(), key=lambda kv: -kv[1])
            out["wrong_examples"][lvl] = wrong[:10]
        return out
