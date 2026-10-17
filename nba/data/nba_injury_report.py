"""The official NBA injury report: fetch, parse (pdftotext word geometry), resolve to ESPN ids, bank.

Why: ESPN's injury list is live-only and its box listing is partial (docs/solutions/integration-issues/
hoopr-espn-nba-box-scores.md). The league publishes a PDF injury report several times a day with a per-player STATUS
(Out / Doubtful / Questionable / Probable / Available) and reason, and the files are retained: hourly stamps back to at least
2021-10 (`Injury-Report_2024-01-15_05PM.pdf`), 15-minute stamps from about January 2026 (`..._05_15PM.pdf`). That makes a
historical, pre-game, status-level availability feed possible -- the thing every availability feature in nba/props lacks.

Source facts verified 2026-10-16 (range GETs; no auth):
  * https://ak-static.cms.nba.com/referee/injury/Injury-Report_{YYYY-MM-DD}_{HH}{AM|PM}.pdf          hourly   (2021-10 .. 2025-12 seen)
  * https://ak-static.cms.nba.com/referee/injury/Injury-Report_{YYYY-MM-DD}_{HH}_{MM}{AM|PM}.pdf     15-min   (2026-01 .. seen)
  * a missing object answers 403 (S3 AccessDenied, ~263 bytes), not 404; HEAD is also 403. official.nba.com pages are 403.
  * layout: landscape table, columns Game Date | Game Time | Matchup | Team | Player Name ("Last, First") | Current Status |
    Reason. Date/time/matchup/team appear once per group and are carried down; the reason WRAPS onto lines above/below
    the player's line; page 1 carries the header, later pages only repeat the title.

PARSER: `pdftotext -bbox` (poppler) gives every word with coordinates; columns are cut at the midpoints between header x
positions (defaults when no header), rows are anchored on player-name words, wrapped reason words go to the NEAREST player
line, carried columns come from the latest group line at or above the player. NO new Python dependency, but the binary must
exist: poppler-utils is NOT in the current Docker image (see the handoff). Missing binary -> PdfToolMissing, never a guess.

FAIL CLOSED: a report with zero parsed player rows, an unreadable PDF, or a row whose status is not a known status raises /
is dropped and counted; a team not listed in a report is "nothing reported", NOT "healthy" unless the report parsed fully
(the league lists every team with a player on it; "NOT YET SUBMITTED" rows are kept as team-level flags).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from mlb_core.data.player_names import PlayerResolver
from nba.config import NBA_PREFIX, TIMEZONE
from nba.teams import team_code

log = logging.getLogger(__name__)

BASE = "https://ak-static.cms.nba.com/referee/injury"
PREFIX = f"{NBA_PREFIX}/injuries/official"
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
NOT_SUBMITTED = "NOT_SUBMITTED"
COLUMNS = ["report_ts_et", "game_date", "game_time_et", "matchup", "team", "team_code", "player_raw", "player",
           "status", "reason", "reason_cat"]
_ET = ZoneInfo(TIMEZONE)
_TITLE = re.compile(r"Injury Report:\s*(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})\s*([AP]M)", re.I)
_FOOTER = re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.I)
# fallback column starts (x0 of the header words on the 2026-04-01 report, landscape 842pt pages)
_DEFAULT_STARTS = {"date": 23.0, "time": 119.0, "matchup": 200.0, "team": 264.0, "player": 425.0, "status": 585.0,
                   "reason": 666.0}
_ORDER = ("date", "time", "matchup", "team", "player", "status", "reason")


class PdfToolMissing(RuntimeError):
    """`pdftotext` (poppler) is not installed: the official report cannot be parsed here."""


class ReportParseError(ValueError):
    """The PDF produced no usable table (layout change, empty report, wrong file)."""


# ----------------------------------------------------------------------------------------------- urls / fetch
def report_urls(ts_et: datetime) -> list:
    """Both filename conventions for the report stamped `ts_et` (naive/aware ET): hourly 'HHPM' (only on the hour) and
    quarter-hour 'HH_MMPM'. Callers try each; which one exists depends on the date."""
    t = ts_et.astimezone(_ET) if ts_et.tzinfo else ts_et
    h12, ap = t.strftime("%I"), t.strftime("%p")
    day = t.strftime("%Y-%m-%d")
    urls = []
    if t.minute == 0:
        urls.append(f"{BASE}/Injury-Report_{day}_{h12}{ap}.pdf")
    urls.append(f"{BASE}/Injury-Report_{day}_{h12}_{t.strftime('%M')}{ap}.pdf")
    return urls


def fetch_report(ts_et: datetime, session=None, timeout: float = 30.0) -> Optional[tuple]:
    """-> (url, pdf bytes) for the first candidate that exists, or None when not published (403/404). Other HTTP errors
    and non-PDF bodies raise: a rate-limit page must never be mistaken for 'no report'."""
    import requests
    s = session or requests
    for url in report_urls(ts_et):
        r = s.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (compatible; beezy-fyi-data/1.0)"})
        if r.status_code in (403, 404):
            continue
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            raise ReportParseError(f"not a PDF: {url} ({len(r.content)} bytes)")
        return url, r.content
    return None


# ----------------------------------------------------------------------------------------------- words
_WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            .replace("&#39;", "'").replace("&apos;", "'"))


def pdf_words(data: bytes, pdftotext: str = "pdftotext") -> list:
    """PDF bytes -> list (one per page) of words (x0, y0, x1, y1, text), via `pdftotext -bbox`."""
    try:
        p = subprocess.run([pdftotext, "-bbox", "-", "-"], input=data, capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise PdfToolMissing("pdftotext (poppler-utils) is not installed") from exc
    if p.returncode != 0:
        raise ReportParseError(f"pdftotext failed ({p.returncode}): {p.stderr[:200]!r}")
    html = p.stdout.decode("utf-8", "replace")
    pages = []
    for chunk in html.split("<page ")[1:]:
        pages.append([(float(a), float(b), float(c), float(d), _unescape(t).strip())
                      for a, b, c, d, t in _WORD.findall(chunk) if _unescape(t).strip()])
    return pages


# ----------------------------------------------------------------------------------------------- parse
def _boundaries(pages: list) -> list:
    """x cut points between columns from the header words (page 1), else the defaults."""
    starts = dict(_DEFAULT_STARTS)
    if pages:
        by_text = {}
        for x0, y0, _x1, _y1, t in pages[0]:
            by_text.setdefault(t, []).append((y0, x0))
        hdr_y = min((y for y, _x in by_text.get("Matchup", [])), default=None)
        if hdr_y is not None:
            def x_of(word):
                c = [x for y, x in by_text.get(word, []) if abs(y - hdr_y) < 3]
                return min(c) if c else None
            found = {"date": x_of("Game"), "matchup": x_of("Matchup"), "team": x_of("Team"), "player": x_of("Player"),
                     "status": x_of("Current"), "reason": x_of("Reason")}
            gt = sorted(x for y, x in by_text.get("Game", []) if abs(y - hdr_y) < 3)
            if len(gt) >= 2:
                found["date"], found["time"] = gt[0], gt[1]
            starts.update({k: v for k, v in found.items() if v is not None})
    xs = [starts[k] for k in _ORDER]
    return [(a + b) / 2.0 for a, b in zip(xs[:-1], xs[1:])]


def _col(x0: float, cuts: list) -> str:
    for name, cut in zip(_ORDER, cuts):
        if x0 < cut:
            return name
    return _ORDER[-1]


def _lines(words: list) -> list:
    """Cluster words into visual lines by y; -> [{'y': center, 'cols': {col: [(x0, text)]}}]."""
    out: list = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2.0, w[0])):
        yc = (w[1] + w[3]) / 2.0
        if out and abs(out[-1]["y"] - yc) <= 3.0:
            out[-1]["words"].append(w)
        else:
            out.append({"y": yc, "words": [w]})
    return out


def _flip_name(raw: str) -> str:
    """'Porter Jr., Michael' -> 'Michael Porter Jr.'; 'Dennis, RayJ' -> 'RayJ Dennis'; no comma -> unchanged."""
    if "," not in raw:
        return raw.strip()
    last, first = raw.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()


def reason_category(reason: str) -> str:
    """Prefix class of the league's reason string (before ' - '): injury, g_league, not_with_team, injury_management, ..."""
    r = (reason or "").strip().lower()
    if not r:
        return "none"
    if r.startswith("g league"):
        return "g_league"
    if r.startswith("not with team"):
        return "not_with_team"
    if r.startswith("injury management"):
        return "injury_management"
    if r.startswith("injury/illness"):
        return "injury_illness"
    if r.startswith("rest"):
        return "rest"
    if r.startswith("personal"):
        return "personal"
    if r.startswith("suspen"):
        return "suspension"
    if r.startswith("return to competition"):
        return "return_to_competition"
    return "other"


def parse_words(pages: list) -> pd.DataFrame:
    """Pure: pdftotext word pages -> normalised frame (COLUMNS). Raises ReportParseError when no player row is found."""
    cuts = _boundaries(pages)
    title = None
    rows: list = []
    # ONE reading-order sequence across pages: a group (matchup/team) that starts on page 1 continues onto page 2, and the
    # date appears only on the first matchup of the day. Pages are offset in y so the nearest-line rules never cross pages.
    parsed = []
    for k, pg in enumerate(pages):
        for ln in _lines(pg):
            cols: dict = {}
            for x0, _y0, _x1, _y1, t in sorted(ln["words"], key=lambda w: w[0]):
                cols.setdefault(_col(x0, cuts), []).append(t)
            st = cols.get("status")
            if st and cols.get("player") and st[0] not in STATUSES:
                # a long name ("Gilgeous-Alexander, Shai") overruns into the status column: tokens before the first
                # status word belong to the player
                i = next((j for j, t in enumerate(st) if t in STATUSES), len(st))
                cols["player"] = cols["player"] + st[:i]
                cols["status"] = st[i:]
                if not cols["status"]:
                    del cols["status"]
            txt = {k2: " ".join(v) for k2, v in cols.items()}
            whole = " ".join(w[4] for w in sorted(ln["words"], key=lambda w: w[0]))
            m = _TITLE.search(whole)
            if m:
                title = title or m
                continue
            if _FOOTER.match(whole.strip()) or whole.startswith("Game Date") or whole.strip().startswith("Injury Report"):
                continue
            parsed.append({"y": ln["y"] + 10000.0 * k, **txt})
    players = [p for p in parsed if p.get("player")]
    reason_lines = [p for p in parsed if p.get("reason")]
    carry = {k: [p for p in parsed if p.get(k)] for k in ("date", "time", "matchup")}
    merged_team: list = []                         # wrapped team-name lines (adjacent < 16pt apart) are one name
    for p in (p for p in parsed if p.get("team")):
        if merged_team and 0 <= p["y"] - merged_team[-1]["y_last"] < 16:
            merged_team[-1]["team"] += " " + p["team"]
            merged_team[-1]["y_last"] = p["y"]
        else:
            merged_team.append({"y": p["y"], "y_last": p["y"], "team": p["team"]})
    by_player: dict = {i: [] for i in range(len(players))}
    for rl in reason_lines:
        i = min(range(len(players)), key=lambda j: abs(players[j]["y"] - rl["y"]))
        if abs(players[i]["y"] - rl["y"]) <= 30:
            by_player[i].append(rl)
    for i, pl in enumerate(players):
        def above(key):
            c = [p for p in carry[key] if p["y"] <= pl["y"] + 8]
            return c[-1][key] if c else None
        c = [p for p in merged_team if p["y"] <= pl["y"] + 8]
        reason = " ".join(r["reason"] for r in sorted(by_player[i], key=lambda r: r["y"]))
        rows.append({"game_date": above("date"), "game_time_et": above("time"), "matchup": above("matchup"),
                     "team": c[-1]["team"] if c else None, "player_raw": pl["player"], "status": pl.get("status"),
                     "reason": reason})
    if not rows:
        raise ReportParseError("no player rows found (layout changed, empty report, or wrong file)")
    df = pd.DataFrame(rows)
    df.attrs["n_rows_raw"] = len(df)
    ts = None
    if title:
        ts = datetime.strptime(f"{title.group(1)} {title.group(2)} {title.group(3).upper()}", "%m/%d/%y %I:%M %p")
        ts = ts.replace(tzinfo=_ET)
    submitted = df["player_raw"].str.upper().str.contains("NOT YET SUBMITTED", na=False)
    df.loc[submitted, "status"] = NOT_SUBMITTED
    bad = ~df["status"].isin(STATUSES + (NOT_SUBMITTED,))
    df.attrs["n_bad_status"] = int(bad.sum())
    df = df[~bad].copy()
    df["player"] = [None if s else _flip_name(r) for r, s in zip(df["player_raw"], df["status"] == NOT_SUBMITTED)]
    df["team_code"] = df["team"].map(lambda t: team_code(t) if isinstance(t, str) else None)
    df["reason_cat"] = df["reason"].map(reason_category)
    df["report_ts_et"] = ts.isoformat() if ts else None
    df["game_date"] = pd.to_datetime(df["game_date"], format="%m/%d/%Y", errors="coerce").dt.date.astype("string")
    return df[COLUMNS].reset_index(drop=True)


def parse_pdf(data: bytes, pdftotext: str = "pdftotext") -> pd.DataFrame:
    return parse_words(pdf_words(data, pdftotext))


# ----------------------------------------------------------------------------------------------- identity
def resolve_ids(df: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """Add `espn_id` (None when unknown/ambiguous -- fail closed) and `how`, resolving 'First Last' + team tricode against
    ESPN-keyed history (`nba.props.espn_data.prepare_espn` rows: player_id, name, team). Players are matched within the
    team on the report; a traded/new player with no history simply stays unresolved."""
    last = rows.sort_values(["tip_sort", "game_id"]).groupby("player_id", as_index=False).tail(1)
    res = PlayerResolver({"player_id": int(r.player_id), "full_name": r.name, "team": r.team}
                         for r in last.itertuples(index=False))
    out = df.copy()
    ids, how = [], []
    for name, tc in zip(out["player"], out["team_code"]):
        if not isinstance(name, str):
            ids.append(None)
            how.append("team_flag")
            continue
        pid, h = res.resolve(name, tc)
        ids.append(pid)
        how.append(h)
    out["espn_id"] = pd.array(ids, dtype="Int64")
    out["how"] = how
    return out


# ----------------------------------------------------------------------------------------------- bank
def bank_key(ts_et: datetime) -> str:
    t = ts_et.astimezone(_ET) if ts_et.tzinfo else ts_et.replace(tzinfo=_ET)
    return f"{PREFIX}/{t.date().isoformat()}/report_{t.strftime('%H%M')}.csv"


def bank_report(df: pd.DataFrame, ts_et: datetime, pdf: Optional[bytes] = None) -> str:
    """Write the parsed report (and the raw PDF beside it, the durable source of truth) under NBA/injuries/official/."""
    from mlb_core import storage
    key = bank_key(ts_et)
    storage.write_bytes(df.to_csv(index=False).encode(), key)
    if pdf:
        storage.write_bytes(pdf, key.replace("/report_", "/raw_report_").replace(".csv", ".pdf"))
    return key


def status_sets(df: pd.DataFrame, statuses=("Out",)) -> dict:
    """{team_code: set(ESPN ids)} of resolved players with a status in `statuses` -- the shape
    FeatureContext.features(availability=...) takes. Only for a FULLY parsed report; teams absent from the report are NOT
    in the mapping (unknown, not healthy)."""
    out: dict = {}
    for tc in df["team_code"].dropna().unique():
        sel = df[(df["team_code"] == tc) & df["status"].isin(statuses) & df["espn_id"].notna()]
        out[tc] = {int(i) for i in sel["espn_id"]}
    return out


# ----------------------------------------------------------------------------------------------- backfill / CLI
DEFAULT_STAMP_HOURS = (13, 17)         # the ~1:30 PM and ~5:30 PM ET reports (titles run ~30 min after the file stamp)


def backfill(days, stamp_hours=DEFAULT_STAMP_HOURS, delay: float = 0.25, fetch=fetch_report, parse=parse_pdf,
             progress=None) -> dict:
    """Fetch + parse + bank the reports for each ET day in `days` at each stamp hour. Resumable (a banked CSV is skipped), never
    raises on one bad file (counted), throttled. -> {"banked", "skipped_present", "not_published", "failed": [...]}.
    Not-published stamps are recorded in NBA/injuries/official/_missing.json so a re-run does not re-ask for them."""
    from mlb_core import storage
    miss_key = f"{PREFIX}/_missing.json"
    missing = set(json.loads(storage.read_bytes(miss_key).decode())) if storage.exists(miss_key) else set()
    out = {"banked": 0, "skipped_present": 0, "not_published": 0, "failed": []}
    for day in days:
        d = pd.Timestamp(day).date()
        for h in stamp_hours:
            ts = datetime(d.year, d.month, d.day, h, 0, tzinfo=_ET)
            key = bank_key(ts)
            if storage.exists(key):
                out["skipped_present"] += 1
                continue
            if key in missing:
                out["not_published"] += 1
                continue
            try:
                got = fetch(ts)
                time.sleep(delay)
                if got is None:
                    missing.add(key)
                    out["not_published"] += 1
                    continue
                url, pdf = got
                df = parse(pdf)
                df.attrs["url"] = url
                bank_report(df, ts, pdf)
                out["banked"] += 1
            except Exception as exc:                                                     # noqa: BLE001
                log.warning("official report %s failed: %r", key, exc)
                out["failed"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
            if progress and (out["banked"] + out["not_published"]) % 100 == 0:
                progress(out)
    storage.write_bytes(json.dumps(sorted(missing)).encode(), miss_key)
    return out


def game_days_from_box(local_dir: str, seasons) -> list:
    """ET game days (ISO strings) that have at least one regular-season/playoff game in the mirrored ESPN player box."""
    from nba.data import hoopr
    box = hoopr.read_dataset("player_boxscores", seasons, local_dir)
    box = box[box["season_type"].isin([2, 3, 5])]
    return sorted(box["game_date"].astype(str).unique())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--box-dir", required=True, help="hoopR mirror dir with player_boxscores/ (game days come from it)")
    ap.add_argument("--seasons", nargs="+", type=int, required=True, help="hoopR seasons (year the season ends)")
    ap.add_argument("--hours", nargs="+", type=int, default=list(DEFAULT_STAMP_HOURS), help="ET stamp hours (24h)")
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    days = game_days_from_box(a.box_dir, a.seasons)
    print(f"{len(days)} game days x {len(a.hours)} stamps = {len(days) * len(a.hours)} reports")
    if a.dry_run:
        return 0
    res = backfill(days, tuple(a.hours), a.delay, progress=lambda o: log.info("progress %s", {k: (len(v) if isinstance(v, list) else v) for k, v in o.items()}))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}))
    for f in res["failed"][:20]:
        print("FAILED", f)
    return 0 if not res["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
