"""N2 normals v2 builder (join audit fix, 2026-11-13).

The v1 DEC-02 game-event normals carried only a prose date and a
free-text context ("against the Jazz"): ``game.date`` was the corpus
item's PUBLISH date, not the game's date, and there was no structured
team pair, so the eval's ``match_event`` could never match a normal to
its slate game and the material-change false-positive rate was null by
construction. v2 joins every normal to the actual game it discusses and
stores the structured pair (away/home display names + the TRUE game
date), item for item like the disruptions.

Join evidence, in order (first that yields exactly one game wins):
  1. boxscore_slate: the hoopR boxscore mirror's slate for the window
     (published date +/- 3 days) contains exactly one game between the
     subject's tenure team (nearest boxscore row within 60 days; the
     subject's own row is never required, ESPN omits inactives) and the
     context opponent.
  2. injury_report: the banked official injury reports list the subject
     for exactly one of the candidate games.
  3. weekday_headline: the headline names a weekday ("Wednesday's game
     against the Jazz") and exactly one candidate falls on it.
  4. injury_report also rescues the zero-candidate case (tenure team not
     yet visible in the box mirror): the reports name the subject's
     game directly.

Normals that still do not join uniquely are dropped and backfilled from
the same NBC corpus under the v1 selection rules as documented in the
fixture README (the v1 builder program was not retained): game-context
phrasing, a word-boundary disruption-word filter, per-player cap of 3
including kept v1 normals, exact-headline dedupe, draws evenly spaced
across the corpus date range, keeping only backfills that join
uniquely. The 50 disruptions are carried over byte-identical. The v1
normals are preserved verbatim in normals_v1.jsonl, and per-item join
provenance is written to normals_v2_build.json beside the fixture.

Usage:
  python scripts/build_n2_normals_v2.py \
      --corpus-dir /path/to/nbc-merge-work/stage2/corpus/parsed \
      --box-dir /path/to/player_boxscores --reports-dir /path/to/official
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from nba.minutes_restriction.cases import _norm_name  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "decisions" / "n2_event_gate"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# The v1 selection rules, as documented in the fixture README (the v1
# builder program was not retained): a word-boundary disruption filter
# and game-context phrasing ("win over the ...", "loss to the ...",
# "game against the ...").
DISRUPTION_RE = re.compile(
    r"\b(postpone\w*|suspend\w*|cancel\w*|weather|rain|storm|delay\w*|"
    r"doubleheader|wildfire\w*|hurricane\w*|snow\w*)\b",
    re.IGNORECASE,
)
CONTEXT_RES = [
    ("win over the", re.compile(r"\bwin over the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
    ("loss to the", re.compile(r"\bloss to the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
    ("game against the", re.compile(r"\bgame against the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
    ("against the", re.compile(r"\bagainst the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
    ("versus the", re.compile(r"\bversus the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
    ("vs. the", re.compile(r"\bvs\. the ([A-Z][A-Za-z]*(?: [A-Z][A-Za-z]*)?)")),
]


def extract_context(headline: str) -> str | None:
    """The v1 fixture's context string for a headline, or None."""
    for phrase, rx in CONTEXT_RES:
        m = rx.search(headline)
        if m:
            return f"{phrase} {m.group(1)}"
    return None


def has_disruption_language(headline: str) -> bool:
    return bool(DISRUPTION_RE.search(headline))


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def load_corpus(corpus_dir: Path) -> list[dict]:
    records = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def load_games(box_dir: Path) -> tuple[list[dict], dict[str, list[tuple[date, str]]]]:
    """Slate games and player tenure rows from the hoopR box mirror."""
    games_by_key: dict[tuple, dict] = {}
    tenure: dict[str, list[tuple[date, str]]] = {}
    for path in sorted(box_dir.glob("player_box_*.parquet")):
        df = pd.read_parquet(
            path,
            columns=[
                "game_id", "game_date", "team_display_name",
                "opponent_team_display_name", "home_away", "athlete_display_name",
            ],
        )
        df["game_date"] = pd.to_datetime(df["game_date"]).dt.date
        for row in df.itertuples(index=False):
            tenure.setdefault(row.athlete_display_name, []).append(
                (row.game_date, row.team_display_name)
            )
            if row.home_away == "away":
                key = (row.game_id, row.game_date)
                games_by_key[key] = {
                    "date": row.game_date,
                    "away": row.team_display_name,
                    "home": row.opponent_team_display_name,
                }
    games = sorted(games_by_key.values(), key=lambda g: (g["date"], g["away"]))
    for rows in tenure.values():
        rows.sort()
    return games, tenure


def load_report_membership(reports_dir: Path) -> tuple[set, dict, dict]:
    """(player, game_date) membership, per-pair game lookup, code->name map."""
    membership: set[tuple[str, str]] = set()
    pair_game: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
    code_to_name: dict[str, str] = {}
    for day_dir in sorted(reports_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        for path in sorted(day_dir.glob("report_*.csv")):
            df = pd.read_csv(path, usecols=["game_date", "matchup", "team", "team_code", "player"])
            for row in df.itertuples(index=False):
                code_to_name.setdefault(row.team_code, row.team)
                key = (_norm_name(row.player), str(row.game_date))
                membership.add(key)
                away, _, home = str(row.matchup).partition("@")
                pair_game.setdefault(key, set()).add((str(row.game_date), away, home))
    return membership, pair_game, code_to_name


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def nicknames(display: str) -> set[str]:
    words = display.split()
    out = {words[-1]}
    if len(words) >= 2:
        out.add(" ".join(words[-2:]))
    return out


def context_nickname(context: str | None) -> str | None:
    if not context:
        return None
    for prefix in (
        "win over the ", "loss to the ", "game against the ",
        "against the ", "versus the ", "vs. the ",
    ):
        if context.startswith(prefix):
            return context[len(prefix):]
    return None


def tenure_team(rows: list[tuple[date, str]] | None, when: date) -> str | None:
    if not rows:
        return None
    best = min(rows, key=lambda r: abs((r[0] - when).days))
    if abs((best[0] - when).days) > 60:
        return None
    return best[1]


class Joiner:
    def __init__(self, games, tenure, membership, pair_game, code_to_name):
        self.games_by_date: dict[date, list[dict]] = {}
        for g in games:
            self.games_by_date.setdefault(g["date"], []).append(g)
        self.tenure = tenure
        self.membership = membership
        self.pair_game = pair_game
        self.code_to_name = code_to_name

    def _candidates(self, subject: str, published: date, ctx_nick: str | None) -> list[dict]:
        team = tenure_team(self.tenure.get(subject), published)
        out = []
        for delta in range(-3, 4):
            day = published + timedelta(days=delta)
            for g in self.games_by_date.get(day, []):
                sides = {g["away"], g["home"]}
                if team is not None and team not in sides:
                    continue
                if ctx_nick is not None:
                    opp = [s for s in sides if s != team] if team else list(sides)
                    hit = any(
                        ctx_nick in nicknames(s) or any(n.startswith(ctx_nick) for n in nicknames(s))
                        for s in opp
                    )
                    if not hit:
                        continue
                out.append(g)
        return out

    def join(self, subject: str, published: date, headline: str, context: str | None):
        """Return (game, evidence) or (None, reason)."""
        ctx_nick = context_nickname(context)
        cands = self._candidates(subject, published, ctx_nick)
        if len(cands) == 1:
            return cands[0], "boxscore_slate"
        norm = _norm_name(subject)
        if cands:
            pinned = [
                g for g in cands
                if (norm, g["date"].isoformat()) in self.membership
            ]
            if len(pinned) == 1:
                return pinned[0], "injury_report"
            named = {w for w in WEEKDAYS if re.search(rf"\b{w}\b", headline)}
            if named:
                by_day = [g for g in cands if WEEKDAYS[g["date"].weekday()] in named]
                if len(by_day) == 1:
                    return by_day[0], "weekday_headline"
            return None, f"ambiguous:{len(cands)}"
        # Zero candidates: the reports may name the game directly.
        found = set()
        for delta in range(-3, 4):
            day = (published + timedelta(days=delta)).isoformat()
            for gdate, away_code, home_code in self.pair_game.get((norm, day), set()):
                away = self.code_to_name.get(away_code)
                home = self.code_to_name.get(home_code)
                if not away or not home:
                    continue
                for g in self.games_by_date.get(date.fromisoformat(gdate), []):
                    if g["away"] == away and g["home"] == home:
                        found.add((g["date"].isoformat(), g["away"], g["home"]))
        if len(found) == 1:
            dstr, away, home = next(iter(found))
            return {"date": date.fromisoformat(dstr), "away": away, "home": home}, "injury_report"
        return None, "no_candidate"


def subject_of(record: dict) -> str | None:
    subject = record.get("subject_player") or record.get("player")
    if subject:
        return subject
    headline = record.get("headline") or ""
    m = re.match(r"^([A-Z][a-zA-Z.'-]+ [A-Z][a-zA-Z.'-]+)", headline)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def v2_normal(item_id: str, source_item: dict, game: dict, subject: str, published: str) -> dict:
    return {
        "item_id": item_id,
        "league": "NBA",
        "game": {
            "away": game["away"],
            "home": game["home"],
            "date": game["date"].isoformat(),
            "context": source_item["game"].get("context", ""),
        },
        "blurb_text": source_item["blurb_text"],
        "source": source_item["source"],
        "label": "no_change",
        "player": subject,
        "prose_published_date": published,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus-dir", required=True, type=Path)
    ap.add_argument("--box-dir", required=True, type=Path)
    ap.add_argument("--reports-dir", required=True, type=Path)
    ap.add_argument("--fixture-dir", type=Path, default=FIXTURE_DIR)
    ap.add_argument("--target-normals", type=int, default=200)
    args = ap.parse_args(argv)

    items_path = args.fixture_dir / "items.jsonl"
    raw_lines = [l for l in items_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    items = [json.loads(l) for l in raw_lines]
    disruption_lines = [l for l, it in zip(raw_lines, items) if "class" in it]
    v1_normals = [it for it in items if it.get("label") == "no_change"]
    assert len(disruption_lines) == 50 and len(v1_normals) == 200

    corpus = load_corpus(args.corpus_dir)
    by_headline: dict[str, list[dict]] = {}
    for rec in corpus:
        by_headline.setdefault(rec.get("headline") or "", []).append(rec)

    games, tenure = load_games(args.box_dir)
    membership, pair_game, code_to_name = load_report_membership(args.reports_dir)
    joiner = Joiner(games, tenure, membership, pair_game, code_to_name)

    def candidate_subjects(headline: str) -> list[str]:
        """Distinct corpus subjects for a headline (one fixture headline
        maps to two corpus subjects; try each until one joins)."""
        out: list[str] = []
        for rec in by_headline.get(headline, []):
            s = subject_of(rec)
            if s and s not in out:
                out.append(s)
        return out

    # entries: (source item, game, evidence, subject, backfilled?)
    kept: list[tuple[dict, dict, str, str, bool]] = []
    dropped: list[tuple[dict, str]] = []
    for item in v1_normals:
        headline = item["blurb_text"]
        published = item["game"]["date"]
        joined = None
        last_reason = "no_subject"
        for subject in candidate_subjects(headline):
            game, evidence = joiner.join(
                subject, date.fromisoformat(published), headline, item["game"].get("context")
            )
            if game is not None:
                joined = (item, game, evidence, subject, False)
                break
            last_reason = evidence
        if joined is None:
            dropped.append((item, last_reason))
        else:
            kept.append(joined)

    # Backfill pool under the v1 selection rules (fixture README).
    used_texts = {it["blurb_text"] for it in items}
    per_player = Counter(subject for _i, _g, _e, subject, _b in kept)
    pool = []
    for rec in corpus:
        headline = rec.get("headline") or ""
        if not headline or headline in used_texts:
            continue
        if not rec.get("published_date"):
            continue
        if extract_context(headline) is None:
            continue
        if has_disruption_language(headline):
            continue
        if subject_of(rec) is None:
            continue
        pool.append(rec)
    pool.sort(key=lambda r: (r["published_date"], r["headline"]))

    need = args.target_normals - len(kept)
    backfilled: list[tuple[dict, dict, str, str, bool]] = []
    if need > 0 and pool:
        stride = max(1, len(pool) // max(need * 4, 1))
        order = list(range(0, len(pool), stride)) + [
            i for i in range(len(pool)) if i % stride != 0
        ]
        for idx in order:
            if len(backfilled) >= need:
                break
            rec = pool[idx]
            subject = subject_of(rec)
            if per_player[subject] >= 3:
                continue
            headline = rec["headline"]
            context = extract_context(headline)
            game, evidence = joiner.join(
                subject, date.fromisoformat(rec["published_date"]), headline, context
            )
            if game is None:
                continue
            source_item = {
                "league": "NBA",
                "game": {"date": rec["published_date"], "context": context},
                "blurb_text": headline,
                "source": rec.get("source") or rec.get("source_name") or "NBC Sports Edge",
                "label": "no_change",
            }
            backfilled.append((source_item, game, evidence, subject, True))
            per_player[subject] += 1

    all_normals = kept + backfilled
    out_lines = list(disruption_lines)
    provenance = []
    for n, (source_item, game, evidence, subject, is_backfill) in enumerate(
        all_normals, start=1
    ):
        item_id = f"N2N-{n:03d}"
        record = v2_normal(
            item_id, source_item, game, subject, source_item["game"]["date"]
        )
        out_lines.append(json.dumps(record, ensure_ascii=False))
        provenance.append(
            {
                "item_id": item_id,
                "player": subject,
                "prose_published_date": source_item["game"]["date"],
                "game_date": game["date"].isoformat(),
                "away": game["away"],
                "home": game["home"],
                "evidence": evidence,
                "backfilled": is_backfill,
            }
        )

    with items_path.open("w", encoding="utf-8") as fh:
        fh.write("\n".join(out_lines) + "\n")
    with (args.fixture_dir / "normals_v1.jsonl").open("w", encoding="utf-8") as fh:
        for item in v1_normals:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (args.fixture_dir / "normals_v2_build.json").open("w", encoding="utf-8") as fh:
        json.dump(
            {
                "kept_v1": len(kept),
                "dropped_v1": len(dropped),
                "drop_reasons": Counter(r for _i, r in dropped).most_common(),
                "backfilled": len(backfilled),
                "total_normals": len(all_normals),
                "evidence_mix": Counter(
                    e for _i, _g, e, _s, _b in all_normals
                ).most_common(),
                "items": provenance,
            },
            fh,
            indent=1,
        )
    print(f"kept {len(kept)} v1 normals, dropped {len(dropped)}, backfilled {len(backfilled)}")
    print("drop reasons:", Counter(r for _i, r in dropped).most_common())
    print("evidence mix:", Counter(e for _i, _g, e, _s, _b in all_normals).most_common())
    print(f"total normals: {len(all_normals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
