"""
ev.autopilot.scoring_job -- the AUT-01 Phase 0 DEC 1 SCORING runner
(python -m ev.autopilot.scoring_job; deploy/setup_autopilot_scoring.sh).

WHAT IT IS.  The live shadow job (shadow_job.py) banks a derived ledger at
Autopilot/shadow_ledger/latest.jsonl.gz in the data bucket.  This scoring runner
reads that banked ledger plus the banked OddsAccum snapshot series, attaches
closing consensus to ALL gate-pass rows (closes.attach_consensus_closes -- the
live ledger only carries closes on FILLED rows, so Bar 4 is not computable from
the ledger alone), runs the amended evaluator (evaluate.evaluate_phase0, bars 1-6
per RUL-20261116-autopilot-clv-drift with the RUL-20261118 drag-free net CLV),
renders the verdict (evaluate.render_phase0), and banks the report under
Autopilot/scoring/.

OFFLINE ARTIFACT READER.  This module reads banked artifacts only and contains
no trade execution path of any kind.  It does not submit transactions, interact
with any exchange API, or load credentials beyond the bucket access the job's
service account holds.  It requires no arming environment variable and can run
freely in offline evaluation and test environments.

OUTPUTS:
    Autopilot/scoring/{stem}.md               human-readable verdict report
    Autopilot/scoring/{stem}.json             full evaluation JSON result
    Autopilot/scoring/runs/{run_id}.json      per-run audit summary (create-only)
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
import os

from ev.autopilot.closes import attach_consensus_closes
from ev.autopilot.config import ShadowConfig, load_config
from ev.autopilot.evaluate import evaluate_phase0, render_phase0
from ev.autopilot.shadow_job import DayFloorStore, LEDGER_KEY
from ev.autopilot.stream import StoreFeedSource, parse_snapshot


def _rows_from_gz(data: bytes) -> list:
    decompressed = gzip.decompress(data).decode("utf-8")
    rows = []
    for line in decompressed.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_records(store, cfg: ShadowConfig) -> list:
    source = StoreFeedSource(store)
    records = []
    for sport in ("nhl", "nba"):
        for ts, payload in source.snapshots(sport):
            records.extend(parse_snapshot(payload, ts, cfg))
    return records


def run_scoring(store, cfg: ShadowConfig, *, from_day: str = "2026-11-16",
                stem: str, partial: bool = False, out_dir: str = ".") -> dict:
    raw = store.read(LEDGER_KEY)
    rows = _rows_from_gz(raw)

    records = build_records(store, cfg)
    rows = attach_consensus_closes(rows, records, cfg)
    res = evaluate_phase0(rows, cfg)
    body = render_phase0(res)

    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    scored_at = now.isoformat()

    game_days = sorted({str(r["game_day"]) for r in rows if r.get("game_day")})
    game_days_str = ", ".join(game_days)

    hdr_lines = []
    if partial:
        hdr_lines.append("PARTIAL WINDOW -- DRY RUN -- NOT THE DEC 1 VERDICT")
        hdr_lines.append(
            "This run scores a partial window banked so far; bars that depend on "
            "window span/volume are expected to fail. Do not cite as the verdict."
        )
    hdr_lines.append(f"AUT-01 Phase 0 scoring -- stem {stem}")
    if not partial:
        hdr_lines.append("FINAL SCORING of the 2026-11-16..2026-11-30 live window.")
    hdr_lines.append(f"scored_at_utc: {scored_at}")
    hdr_lines.append(f"window_from_day: {from_day}")
    hdr_lines.append(f"ledger_rows: {len(rows)}")
    hdr_lines.append(f"game_days_in_ledger: {game_days_str}")
    hdr_lines.append(
        "evaluator: amended (RUL-20261116-autopilot-clv-drift; net CLV drag-free per RUL-20261118)"
    )

    header = "\n".join(hdr_lines)
    report = header + "\n\n" + body + "\n"
    json_text = json.dumps(res, indent=1, sort_keys=True, default=str)

    os.makedirs(out_dir, exist_ok=True)
    local_md = os.path.join(out_dir, f"{stem}.md")
    local_json = os.path.join(out_dir, f"{stem}.json")
    with open(local_md, "w", encoding="utf-8") as fh:
        fh.write(report)
    with open(local_json, "w", encoding="utf-8") as fh:
        fh.write(json_text)

    report_key = f"Autopilot/scoring/{stem}.md"
    json_key = f"Autopilot/scoring/{stem}.json"
    store.put(report_key, report.encode("utf-8"))
    store.put(json_key, json_text.encode("utf-8"))

    filled_count = sum(1 for r in rows if r.get("status") == "FILLED")
    summary = {
        "run_id": run_id,
        "job": "autopilot-scoring",
        "stem": stem,
        "partial": bool(partial),
        "from_day": from_day,
        "scored_at": scored_at,
        "ledger_rows": len(rows),
        "filled": filled_count,
        "game_days": game_days,
        "report_key": report_key,
        "json_key": json_key,
    }
    store.write_new(
        f"Autopilot/scoring/runs/{run_id}.json",
        json.dumps(summary, indent=1, sort_keys=True).encode("utf-8"),
    )

    print(report)
    print(json.dumps(summary, indent=1, sort_keys=True))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="AUT-01 Phase 0 scoring runner (banked ledger + snapshots in, banked report out)"
    )
    ap.add_argument(
        "--from-day",
        default="2026-11-16",
        help="first ET snapshot day folder of the live window (default: 2026-11-16)",
    )
    ap.add_argument(
        "--source",
        default="gcs",
        help="shadow_bank store kind for the accumulator bank and ledger bank (gcs | gcloud | dir:PATH)",
    )
    ap.add_argument("--bucket", default=None, help="GCS bucket name (default: MLB_GCS_BUCKET env var)")
    ap.add_argument(
        "--stem",
        required=True,
        help="output file stem, e.g. verdict-2026-12-01 or dry-run-2026-11-19",
    )
    ap.add_argument(
        "--partial",
        action="store_true",
        help="label the report as a partial-window dry run",
    )
    ap.add_argument(
        "--out-dir",
        default=".",
        help="local directory to write report and json files (default: .)",
    )
    a = ap.parse_args(argv)

    cfg = load_config()

    from ev.novig import shadow_bank as sb

    store = DayFloorStore(sb.make_store(a.source, a.bucket), a.from_day)
    run_scoring(
        store,
        cfg,
        from_day=a.from_day,
        stem=a.stem,
        partial=a.partial,
        out_dir=a.out_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
