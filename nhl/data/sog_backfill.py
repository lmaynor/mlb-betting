"""NHL SOG stats-API backfill -- EV/PP/SH TOI splits + shot attempts per skater-game, from the NHL stats API reports.

Why this exists (design: ~/workspace/beezy/sog-replication-2026-11-11.md, sections 1, 2 and 6):
the SOG replication needs per skater-game EV/PP/SH time-on-ice splits and shot attempts. The banked
masters (NHL/skater_boxscores_master.csv) carry SOG and TOTAL TOI only -- no splits, no attempts.
The source model's pipeline pulled them from the NHL stats API
(https://api.nhle.com/stats/rest/en/skater), four reports per game date:

    summary    -> shots (the SOG outcome), team / opponent / position
    timeonice  -> evTimeOnIce / ppTimeOnIce / shTimeOnIce (+ total TOI, shifts)
    realtime   -> totalShotAttempts (+ its missed / blocked components)
    powerplay  -> ppIndividualSatFor (PP shot attempts), ppShots

This module is that backfill, and ONLY that backfill. It does not build team rates (MoneyPuck is
barred by the Oct 16, 2026 licensing review, design section 2; team rates are a separate later job
on banked play-by-play), and it does not use shift charts -- the stats-API route is the design's
chosen route (the shift-chart builder in nhl.data.toi_splits gives splits only, at ~1 request per
game, and no attempts).

Cost, measured live 2026-11-11 (see the PR body): one date is ONE request per report with
limit=-1 (the endpoint returns the full day, ~144 skater rows for an 8-game day, and reports its
row count in `total`). The design's 5,000-6,000 requests/season estimate assumed ~7 pages per
report-day; with limit=-1 the real cost is 4 requests per date, ~1,100 per season window below.
If a day ever returns total > rows received, this module falls back to start/limit paging.

Output: one JSONL file per game date, one row per skater-game, written through mlb_core.storage
(GCS when MLB_GCS_BUCKET / GCS_BUCKET is set, local files otherwise -- the repo convention):

    NHL/sog_backfill/{season}/{date}.jsonl        e.g. NHL/sog_backfill/2024-25/2025-01-06.jsonl

A date whose games have not been played (or an off day) writes an EMPTY file: the file's existence
is the resume marker, so a re-run skips it instead of re-asking the API (skip-existing is the
default; --force re-pulls). Only regular-season (game type 2) and playoff (3) rows are kept --
game ids encode the type (2024020639 -> season 2024, type 02) -- so a stray preseason row in an
early-October pull cannot leak in.

Politeness: at most 6 worker threads (the design's note on the source pipeline), the repo client's
retry/backoff (429 honours Retry-After), a small per-request delay, and the declared research
User-Agent on every request: "Beezy Research (beezy.fyi; research@beezy.fyi)".

Full runs (one season each; hours-scale wall time is NOT expected at 4 requests/date, but run them
somewhere durable anyway -- a Cloud Run Job or a tmux session, never a foreground laptop shell):

    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.sog_backfill --season 2023-24
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.sog_backfill --season 2024-25
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.sog_backfill --season 2025-26

Smoke / sample runs:

    python3 -m nhl.data.sog_backfill --season 2024-25 --start 2025-01-06 --end 2025-01-12 --dry-run
    GCS_BUCKET=concrete-crow-445205-m4-mlb-data python3 -m nhl.data.sog_backfill --season 2024-25 \
        --start 2025-01-06 --end 2025-01-12 --sample     # writes under NHL/sog_backfill_sample/
"""
from __future__ import annotations

import argparse
import json
import logging
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from nhl.data.client import NhlClient

log = logging.getLogger("nhl.data.sog_backfill")

STATS_BASE = "https://api.nhle.com/stats/rest/en"
USER_AGENT = "Beezy Research (beezy.fyi; research@beezy.fyi)"

GCS_PREFIX = "NHL/sog_backfill"
SAMPLE_PREFIX = "NHL/sog_backfill_sample"
DEFAULT_OUT_DIR = "data/nhl_sog_backfill"

REPORTS = ("summary", "timeonice", "realtime", "powerplay")
MAX_WORKERS_CAP = 6            # design section 2: the source pipeline ran six threads; never exceed
PAGE_SIZE = 100                # only used by the paging fallback when limit=-1 under-returns
KEEP_GAME_TYPES = (2, 3)       # regular season + playoffs (game-id digits 5-6)

# Season window for --season: Oct 1 (start year) -> Jun 30 (next year). Covers every regular-season
# and playoff date of 2023-24 / 2024-25 / 2025-26 and excludes the late-September preseason; the
# game-type filter below is the second line of defence for the first October days.
SEASON_START = (10, 1)
SEASON_END = (6, 30)

ROW_FIELDS = ["season", "season_id", "date", "game_id", "game_type", "player_id", "player_name",
              "team", "opponent", "home_road", "position", "shots", "toi_total_sec", "toi_ev_sec",
              "toi_pp_sec", "toi_sh_sec", "toi_ot_sec", "shifts", "attempts_total",
              "attempts_missed", "attempts_blocked", "pp_attempts", "pp_shots", "pp_toi_sec"]


# ---------------------------------------------------------------------------------------------------------------------
# seasons / dates
# ---------------------------------------------------------------------------------------------------------------------
def parse_season(season) -> tuple[int, str, str]:
    """'2024-25' (or 2024, or '20242025') -> (start_year, label '2024-25', season_id '20242025')."""
    s = str(season).strip()
    if "-" in s:
        start = int(s.split("-", 1)[0])
    elif len(s) == 8 and s.isdigit():
        start = int(s[:4])
    else:
        start = int(s)
    return start, f"{start}-{str(start + 1)[-2:]}", f"{start}{start + 1}"


def season_window(season) -> tuple[date, date]:
    start_year, _, _ = parse_season(season)
    return (date(start_year, *SEASON_START), date(start_year + 1, *SEASON_END))


def season_label_for_date(day: date) -> str:
    """The season a calendar date belongs to: Oct-Dec -> that year's season, Jan-Sep -> the prior one."""
    start = day.year if day.month >= SEASON_START[0] else day.year - 1
    return parse_season(start)[1]


def iter_dates(start: date, end: date):
    """Every date from start to end inclusive, ascending."""
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def gcs_key(prefix: str, season_label: str, day: date) -> str:
    return f"{prefix}/{season_label}/{day.isoformat()}.jsonl"


def game_type_of(game_id) -> int | None:
    """Game ids are season(4) + type(2) + number(4): 2024020639 -> 2. Anything else -> None."""
    s = str(game_id)
    return int(s[4:6]) if len(s) == 10 and s.isdigit() else None


# ---------------------------------------------------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------------------------------------------------
class SogStatsClient(NhlClient):
    """api.nhle.com/stats/rest/en with the declared Beezy Research User-Agent forced onto the session.

    Inherits nhl.data.client's retry behaviour (429 -> Retry-After, exponential backoff, 404 -> None).
    One client per worker thread: requests.Session is not shared across threads.
    """

    def __init__(self, delay: float = 0.25, session=None):
        super().__init__(base=STATS_BASE, delay=delay, session=session)
        self.session.headers["User-Agent"] = USER_AGENT

    def report_page(self, report: str, day: date, start: int = 0, limit: int = -1):
        expr = urllib.parse.quote(f'gameDate="{day.isoformat()}"')
        return self._get(f"skater/{report}?isGame=true&limit={limit}&start={start}&cayenneExp={expr}")


def fetch_report(client, report: str, day: date) -> tuple[list[dict], int]:
    """All raw rows of one report for one date, plus the number of API requests spent.

    Primary path is a single limit=-1 request (verified live 2026-11-11: the endpoint returns the
    whole day and its true row count in `total`). If `total` says more rows exist than arrived,
    page with start/limit until the day is complete.
    """
    payload = client.report_page(report, day, start=0, limit=-1)
    requests_made = 1
    if not payload:
        return [], requests_made
    rows = list(payload.get("data") or [])
    total = payload.get("total")
    while total is not None and len(rows) < int(total):
        page = client.report_page(report, day, start=len(rows), limit=PAGE_SIZE)
        requests_made += 1
        chunk = list((page or {}).get("data") or [])
        if not chunk:
            break
        rows.extend(chunk)
    return rows, requests_made


# ---------------------------------------------------------------------------------------------------------------------
# report parsing + the join (pure functions; payloads in, rows out)
# ---------------------------------------------------------------------------------------------------------------------
def _keyed(rows, fields: dict[str, str]):
    """Raw report rows -> {(game_id, player_id): {out_field: value}} for the given field rename map."""
    out = {}
    for r in rows or []:
        gid, pid = r.get("gameId"), r.get("playerId")
        if gid is None or pid is None:
            continue
        if game_type_of(gid) not in KEEP_GAME_TYPES:
            continue
        out[(int(gid), int(pid))] = {out_f: r.get(api_f) for out_f, api_f in fields.items()}
    return out


def _int_or_none(v):
    return int(round(float(v))) if v is not None else None


def parse_summary(rows) -> dict:
    """summary report: shots (the SOG outcome) + identity fields every other report is joined onto."""
    m = _keyed(rows, {"player_name": "skaterFullName", "team": "teamAbbrev", "opponent": "opponentTeamAbbrev",
                      "home_road": "homeRoad", "position": "positionCode", "shots": "shots",
                      "toi_total_sec": "timeOnIcePerGame"})
    for v in m.values():
        v["toi_total_sec"] = _int_or_none(v["toi_total_sec"])
    return m


def parse_timeonice(rows) -> dict:
    """timeonice report: the EV/PP/SH TOI splits (seconds) the masters do not carry.

    ev + pp + sh == timeOnIce exactly (verified on all 1,872 rows of the 2025-01-06..12 sample).
    otTimeOnIce is NOT a fourth additive split: it is the overtime subset already inside ev/pp/sh
    (every sample row with otTimeOnIce > 0 breaks the four-way sum by exactly that amount), so
    toi_ot_sec is carried as information only -- never add it to the other three.
    """
    m = _keyed(rows, {"toi_ev_sec": "evTimeOnIce", "toi_pp_sec": "ppTimeOnIce", "toi_sh_sec": "shTimeOnIce",
                      "toi_ot_sec": "otTimeOnIce", "toi_total_sec": "timeOnIce", "shifts": "shifts",
                      "player_name": "skaterFullName", "team": "teamAbbrev",
                      "opponent": "opponentTeamAbbrev", "home_road": "homeRoad", "position": "positionCode"})
    return m


def parse_realtime(rows) -> dict:
    """realtime report: total shot attempts. attempts == shots + missed + blocked-by-opponent
    (verified live on MacKinnon 2025-01-06: 3 + 2 + 1 == 6)."""
    return _keyed(rows, {"attempts_total": "totalShotAttempts", "attempts_missed": "missedShots",
                         "attempts_blocked": "shotAttemptsBlocked"})


def parse_powerplay(rows) -> dict:
    """powerplay report: ppIndividualSatFor is the player's own PP shot attempts; ppShots his PP SOG."""
    m = _keyed(rows, {"pp_attempts": "ppIndividualSatFor", "pp_shots": "ppShots", "pp_toi_sec": "ppTimeOnIce"})
    for v in m.values():
        v["pp_toi_sec"] = _int_or_none(v["pp_toi_sec"])
    return m


_PARSERS = {"summary": parse_summary, "timeonice": parse_timeonice,
            "realtime": parse_realtime, "powerplay": parse_powerplay}


def join_reports(day: date, season_label: str, season_id: str, by_report: dict[str, list[dict]]) -> list[dict]:
    """Join the four reports on (game_id, player_id) into normalized skater-game rows for one date.

    The union of keys is used, not just summary's: a skater missing from one report keeps the row
    with that report's fields None instead of silently dropping a played game. (Live, all four
    reports carried the identical 144 keys for 2025-01-06, so the union is a safety net, not the
    expected path.) Rows are sorted by (game_id, player_id) so files diff cleanly.
    """
    merged: dict[tuple[int, int], dict] = {}
    for report in REPORTS:  # REPORTS order matters: timeonice's total TOI overrides summary's per-game float
        for key, fields in _PARSERS[report](by_report.get(report) or []).items():
            merged.setdefault(key, {}).update({k: v for k, v in fields.items() if v is not None})
    rows = []
    for (gid, pid), fields in merged.items():
        row = {f: None for f in ROW_FIELDS}
        row.update(fields)
        row.update({"season": season_label, "season_id": season_id, "date": day.isoformat(),
                    "game_id": gid, "game_type": game_type_of(gid), "player_id": pid})
        rows.append({f: row[f] for f in ROW_FIELDS})
    return sorted(rows, key=lambda r: (r["game_id"], r["player_id"]))


def to_jsonl(rows: list[dict]) -> bytes:
    """Rows -> JSONL bytes (one JSON object per line, trailing newline; no rows -> empty bytes)."""
    if not rows:
        return b""
    return ("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n").encode()


# ---------------------------------------------------------------------------------------------------------------------
# storage (mlb_core.storage in production; anything with exists/write in tests)
# ---------------------------------------------------------------------------------------------------------------------
class StorageStore:
    """The repo storage layer: GCS when a bucket env var is set, local files under MLB_BASE_DATA otherwise."""

    def exists(self, key: str) -> bool:
        from mlb_core import storage
        return storage.exists(key)

    def write(self, key: str, data: bytes) -> None:
        from mlb_core import storage
        storage.write_bytes(data, key)


class LocalStore:
    """Files under a root dir, keyed exactly like GCS keys. --dry-run writes here; tests use it too."""

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)

    def _path(self, key: str):
        return self.root / key

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def write(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


# ---------------------------------------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------------------------------------
def backfill_date(day: date, season_label: str, season_id: str, store, prefix: str,
                  client_factory, force: bool = False) -> dict:
    """One date end to end: skip if banked, else pull 4 reports, join, write one JSONL file."""
    key = gcs_key(prefix, season_label, day)
    if not force and store.exists(key):
        return {"date": day.isoformat(), "key": key, "status": "skipped", "rows": 0, "requests": 0}
    client = client_factory()
    by_report, n_requests = {}, 0
    for report in REPORTS:
        rows, n = fetch_report(client, report, day)
        by_report[report] = rows
        n_requests += n
    rows = join_reports(day, season_label, season_id, by_report)
    store.write(key, to_jsonl(rows))
    return {"date": day.isoformat(), "key": key, "status": "written", "rows": len(rows),
            "requests": n_requests}


def run(season=None, start: date | None = None, end: date | None = None, store=None, prefix: str = GCS_PREFIX,
        force: bool = False, max_workers: int = MAX_WORKERS_CAP, delay: float = 0.25,
        client_factory=None) -> dict:
    """Backfill one season (or an explicit date range inside it) date by date. Returns run totals.

    Resumable by construction: dates already in the store are skipped unless force=True, so a
    crashed or interrupted season run is restarted with the same command.
    """
    if season is None and start is None:
        raise ValueError("pass a season or an explicit start/end range")
    if season is not None:
        _, season_label, season_id = parse_season(season)
        win_start, win_end = season_window(season)
    else:
        season_label = season_label_for_date(start)
        _, _, season_id = parse_season(season_label)
        win_start, win_end = start, end or start
    start = max(start or win_start, win_start) if season is not None else start
    end = min(end or win_end, win_end) if season is not None else (end or start)
    days = iter_dates(start, end)
    store = store if store is not None else StorageStore()
    client_factory = client_factory or (lambda: SogStatsClient(delay=delay))
    workers = max(1, min(int(max_workers), MAX_WORKERS_CAP))
    log.info("sog backfill season=%s %s..%s (%d dates) prefix=%s workers=%d force=%s",
             season_label, start, end, len(days), prefix, workers, force)

    def one(day):
        return backfill_date(day, season_label, season_id, store, prefix, client_factory, force=force)

    if workers == 1 or len(days) <= 1:
        results = [one(d) for d in days]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(one, days))
    totals = {"season": season_label, "dates": len(days),
              "written": sum(1 for r in results if r["status"] == "written"),
              "skipped": sum(1 for r in results if r["status"] == "skipped"),
              "rows": sum(r["rows"] for r in results),
              "requests": sum(r["requests"] for r in results),
              "results": sorted(results, key=lambda r: r["date"])}
    log.info("sog backfill done: %s", {k: totals[k] for k in ("season", "dates", "written", "skipped", "rows", "requests")})
    return totals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NHL SOG stats-API backfill (summary/timeonice/realtime/powerplay -> JSONL per date)")
    ap.add_argument("--season", help="season label, e.g. 2024-25 (window Oct 1 - Jun 30); repeat with commas for several")
    ap.add_argument("--start", help="first date YYYY-MM-DD (clipped to the season window)")
    ap.add_argument("--end", help="last date YYYY-MM-DD, inclusive (clipped to the season window)")
    ap.add_argument("--force", action="store_true", help="re-pull dates already present in the store")
    ap.add_argument("--dry-run", action="store_true", help="write JSONL under --out-dir instead of GCS/storage")
    ap.add_argument("--sample", action="store_true", help=f"write under the {SAMPLE_PREFIX}/ prefix instead of {GCS_PREFIX}/")
    ap.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="local root for --dry-run output")
    ap.add_argument("--prefix", help="override the output prefix (default: NHL/sog_backfill, or the sample prefix with --sample)")
    ap.add_argument("--max-workers", type=int, default=MAX_WORKERS_CAP, help=f"worker threads, capped at {MAX_WORKERS_CAP}")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between API requests per client (default 0.25)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not a.season and not a.start:
        ap.error("pass --season and/or --start/--end")
    prefix = a.prefix or (SAMPLE_PREFIX if a.sample else GCS_PREFIX)
    store = LocalStore(a.out_dir) if a.dry_run else StorageStore()
    start = date.fromisoformat(a.start) if a.start else None
    end = date.fromisoformat(a.end) if a.end else None
    seasons = [s.strip() for s in a.season.split(",")] if a.season else [None]
    for season in seasons:
        totals = run(season=season, start=start, end=end, store=store, prefix=prefix,
                     force=a.force, max_workers=a.max_workers, delay=a.delay)
        print(json.dumps({k: totals[k] for k in ("season", "dates", "written", "skipped", "rows", "requests")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
