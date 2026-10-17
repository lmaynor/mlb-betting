---
title: "Official NBA injury report PDFs: two filename conventions, missing = 403, parse by word geometry, title time != file stamp"
module: nba/data/nba_injury_report.py, nba/data/injury_bank.py, nba/props/official_availability.py
tags: [nba, injury-report, pdf, pdftotext, poppler, availability, s3-403, backfill]
problem_type: integration_issue
category: integration-issues
date: 2026-10-16
---

## What it is

The league publishes an injury report PDF several times a day on a static CDN, with a per-player STATUS (Out / Doubtful / Questionable /
Probable / Available) and a reason, and the files are retained. That is a historical, pre-game, status-level availability feed -- the
thing ESPN's live-only list and ESPN's partial box listing (`hoopr-espn-nba-box-scores.md`) cannot give.

## Verified 2026-10-16 (range GETs, no auth)

1. **Two filename conventions.** `Injury-Report_{YYYY-MM-DD}_{HH}{AM|PM}.pdf` (hourly, 2021-10 .. 2025-12 seen) and
   `Injury-Report_{YYYY-MM-DD}_{HH}_{MM}{AM|PM}.pdf` (quarter-hour, from about 2026-01). Which exists depends on the date;
   `report_urls()` returns both and `fetch_report()` tries each.
2. **A missing object answers 403** (S3 AccessDenied, ~263 bytes), NOT 404. HEAD is 403 for existing files too, so probe with a range
   GET (`-r 0-1023`). `official.nba.com` pages answer 403. A 403 is "not published"; any other error status or a non-PDF 200 body raises.
3. **The title time is not the file stamp.** The `05PM` file's title reads `Injury Report: 01/15/24 05:30 PM`. The parser records the TITLE
   time (`report_ts_et`) -- that is when the list was current -- and all pre-game selection uses it.
4. **Layout** (landscape, 842x595pt): Game Date | Game Time | Matchup | Team | Player Name ("Last, First") | Current Status | Reason.
   Date/time/matchup/team appear once per group and carry down (the date only on the day's first matchup, the group continues across
   pages); the reason WRAPS onto lines above and below the player's line; the header is on page 1 only. A long name
   ("Gilgeous-Alexander, Shai") overruns into the status column. Parsed correctly on reports from 2021-11, 2022-01, 2023-01, 2024-01,
   2025-01, 2025-12 and 2026-04 (all rows, no unknown statuses).
5. **Parsing needs poppler's `pdftotext -bbox`** (word coordinates). No Python dependency, but the binary is NOT in the Docker image;
   `PdfToolMissing` is raised, never a guess. Banking the official report from Cloud Run needs `poppler-utils` in a feeds-image
   Dockerfile (a decision for the image owner; the production image was not touched).
6. **Teams with no entries are simply omitted** from a covered game; a team can carry `NOT YET SUBMITTED` (kept as a team-level flag, treated
   as UNKNOWN, never healthy). `official_availability.select_listing` encodes both rules.
7. A report on a no-games day (All-Star break) has no player rows: `ReportParseError`, counted as a failed fetch, not a crash.

## Use

```bash
# backfill (resumable; throttled; misses remembered in NBA/injuries/official/_missing.json)
MLB_BASE_DATA=/tmp/store python3 -m nba.data.nba_injury_report --box-dir <hoopr mirror dir> --seasons 2022 2023 2024 2025 2026
# live ESPN list, change-only
python3 -m nba.data.injury_bank
```
Names resolve to ESPN ids through `nba_injury_report.resolve_ids` (fail closed; ~99.3% of listed names resolved on 2021-2024 reports).
