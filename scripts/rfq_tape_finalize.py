"""rfq_tape_finalize.py -- build the derived Parquet layer for one UTC day of the Kalshi RFQ tape.

Design: rfq-tape-storage-design-2026-11-24.md section 3. The raw JSONL tape is evidence and is never
mutated; this batch pass over ONE day's chunks (incremental, never a whole-history rescan) writes, under
{prefix}_derived/:

  dt={day}/combos.parquet   one row per kind="rfq" record: every scalar field (v1 quote fields and the
                            tape_schema v2 materialized fields) as columns; the nested legs / rfq /
                            notes payloads as JSON strings.
  dt={day}/legs.parquet     one row per leg: rfq_id, ts, day_utc + every leg field (incl. the q_kalshi
                            capture and the canonical settle fields).
  dt={day}/day_summary.json the recorder's section 2i sidecar copied from the raw prefix when present,
                            else the same counts computed from the records.
  index/rfq_index.parquet   rfq_id -> (dt, chunk_key, line_no) across all days, merged with whatever
                            was already there; dedupe on (rfq_id, chunk_key, line_no), last write wins.

Records written before tape_schema v2 (no tape_schema field) finalize with nulls for the v2-only
columns: missing-fields-as-null semantics, so cross-era queries run over one derived shape.

CLI: python scripts/rfq_tape_finalize.py --day YYYY-MM-DD [--prefix Kalshi/rfq_tape/production]
     [--bucket ...]   (bucket defaults to the mlb_core.storage resolution: MLB_GCS_BUCKET / GCS_BUCKET)
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SCALAR = (str, int, float, bool)


def _bucket_name(bucket: str | None) -> str:
    if bucket:
        return bucket
    from mlb_core import storage
    return storage._get_bucket()


def _client(storage_client=None):
    if storage_client is not None:
        return storage_client
    from google.cloud import storage as gcs
    return gcs.Client()


def _download(blob) -> bytes:
    return blob.download_as_bytes()


def _upload(bucket_obj, key: str, data: bytes, content_type: str | None = None) -> None:
    blob = bucket_obj.blob(key)
    if content_type:
        blob.upload_from_string(data, content_type=content_type)
    else:
        blob.upload_from_string(data)


def _jsonable(v):
    return v if isinstance(v, _SCALAR) or v is None else json.dumps(v, default=str, sort_keys=True)


def _rows_to_table(rows: list[dict]):
    """list[dict] -> pyarrow Table. Columns whose non-null values mix type categories (e.g. a v1 string
    where v2 writes a float) are stringified so one era's shape cannot break the other's finalize."""
    import pyarrow as pa
    if not rows:
        return pa.table({})
    cols: dict[str, list] = {}
    for row in rows:
        for k in row:
            cols.setdefault(k, [])
    arrays = {}
    for k in cols:
        vals = [r.get(k) for r in rows]
        cats = {type(v) for v in vals if v is not None}
        if len(cats) > 1:
            vals = [None if v is None else str(v) for v in vals]
        if not cats:                                   # all-null column (e.g. a v2 field null all day)
            arrays[k] = pa.array(vals, type=pa.string())
        else:
            arrays[k] = pa.array(vals)
    return pa.table(arrays)


def _table_bytes(table) -> bytes:
    import pyarrow.parquet as pq
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _read_table(data: bytes):
    import pyarrow.parquet as pq
    return pq.read_table(io.BytesIO(data))


def _compute_summary(day: str, records: list[dict]) -> dict:
    """The section 2i counts computed from the records, for days whose raw sidecar is absent (v1 era,
    or a recorder that died before rollover). Same shape as RFQTape.day_summary."""
    s = {"records_by_kind": {}, "fires": 0, "evaluated": 0, "in_denominator": 0, "by_family": {},
         "by_excluded_series_class": {}, "preview_10": 0, "followup_records": 0,
         "creator_counts": {}, "flagged_creators": {}}
    for rec in records:
        kind = str(rec.get("kind") or "")
        s["records_by_kind"][kind] = s["records_by_kind"].get(kind, 0) + 1
        if kind == "followup":
            s["followup_records"] += 1
        if kind != "rfq":
            continue
        s["evaluated"] += 1
        s["fires"] += 1 if rec.get("is_fire") else 0
        s["in_denominator"] += 1 if rec.get("in_denominator") else 0
        s["preview_10"] += 1 if rec.get("preview_10") else 0
        fam = str(rec.get("family"))
        s["by_family"][fam] = s["by_family"].get(fam, 0) + 1
        exc = str(rec.get("excluded_series_class") or "none")
        s["by_excluded_series_class"][exc] = s["by_excluded_series_class"].get(exc, 0) + 1
        cid = rec.get("creator_id") or (rec.get("rfq") or {}).get("creator_id")
        if cid:
            s["creator_counts"][str(cid)] = s["creator_counts"].get(str(cid), 0) + 1
            if rec.get("creator_flagged"):
                s["flagged_creators"][str(cid)] = s["creator_counts"][str(cid)]
    evaluated = s["evaluated"]
    top = sorted(s["creator_counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    return {"day": day, "tape_schema": 2, "records_by_kind": s["records_by_kind"], "fires": s["fires"],
            "evaluated": evaluated, "in_denominator": s["in_denominator"], "by_family": s["by_family"],
            "by_excluded_series_class": s["by_excluded_series_class"], "preview_10": s["preview_10"],
            "preview_10_share": (s["preview_10"] / evaluated) if evaluated else 0.0,
            "flagged_creators": s["flagged_creators"],
            "top_creators": [{"creator_id": c, "count": n} for c, n in top],
            "followup_records": s["followup_records"],
            "followup_attach_rate": (s["followup_records"] / evaluated) if evaluated else 0.0}


def finalize_day(day_iso: str, prefix: str, *, bucket: str | None = None, storage_client=None) -> dict:
    """Build the derived layer for `day_iso` under {prefix}_derived/. -> dict of what was written."""
    prefix = prefix.rstrip("/")
    client = _client(storage_client)
    bucket_name = _bucket_name(bucket)
    bucket_obj = client.bucket(bucket_name)
    day_prefix = f"{prefix}/{day_iso}/"
    chunk_blobs = sorted((b for b in client.list_blobs(bucket_name, prefix=day_prefix)
                          if b.name.endswith(".jsonl")), key=lambda b: b.name)

    records: list[tuple[dict, str, int]] = []          # (record, chunk_key, line_no)
    for blob in chunk_blobs:
        text = _download(blob).decode("utf-8", "replace")
        for line_no, line in enumerate(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                records.append((rec, blob.name, line_no))

    combos: list[dict] = []
    legs: list[dict] = []
    index: dict[tuple, dict] = {}
    for rec, chunk_key, line_no in records:
        rfq_id = rec.get("rfq_id") or (rec.get("rfq") or {}).get("id") or (rec.get("rfq") or {}).get("rfq_id")
        if rfq_id:
            index[(str(rfq_id), chunk_key, line_no)] = {"rfq_id": str(rfq_id), "dt": day_iso,
                                                         "chunk_key": chunk_key, "line_no": line_no}
        if rec.get("kind") != "rfq":
            continue
        row = {k: _jsonable(v) for k, v in rec.items() if k not in ("legs", "rfq", "notes")}
        row["rfq_id"] = str(rfq_id) if rfq_id else None   # hoisted for v1 rows, which carry it only nested
        row["legs"] = json.dumps(rec.get("legs") or [], default=str, sort_keys=True)
        row["rfq"] = json.dumps(rec.get("rfq") or {}, default=str, sort_keys=True)
        row["notes"] = json.dumps(rec.get("notes") or {}, default=str, sort_keys=True)
        row["chunk_key"], row["line_no"] = chunk_key, line_no
        combos.append(row)
        for leg in rec.get("legs") or []:
            if not isinstance(leg, dict):
                continue
            lrow = {k: _jsonable(v) for k, v in leg.items()}
            lrow["rfq_id"] = str(rfq_id or "")
            lrow["ts"] = rec.get("ts")
            lrow["day_utc"] = rec.get("day_utc")
            legs.append(lrow)

    # Design section 3c sort orders (row-group statistics prune the common slices); null-safe keys.
    combos.sort(key=lambda r: (str(r.get("day_et") or ""), str(r.get("family") or ""),
                               float(r.get("ts") or 0.0)))
    legs.sort(key=lambda r: (str(r.get("settle_key") or ""), float(r.get("ts") or 0.0)))

    derived = f"{prefix}_derived"
    written: list[str] = []

    combos_key = f"{derived}/dt={day_iso}/combos.parquet"
    _upload(bucket_obj, combos_key, _table_bytes(_rows_to_table(combos)))
    written.append(combos_key)

    legs_key = f"{derived}/dt={day_iso}/legs.parquet"
    _upload(bucket_obj, legs_key, _table_bytes(_rows_to_table(legs)))
    written.append(legs_key)

    # Day summary: the recorder's raw sidecar when it exists, else computed from the records.
    summary = None
    try:
        raw = bucket_obj.blob(f"{day_prefix}day_summary.json")
        if raw.exists():
            summary = json.loads(_download(raw).decode("utf-8"))
    except Exception:                                  # noqa: BLE001 -- fall back to computing it
        summary = None
    if summary is None:
        summary = _compute_summary(day_iso, [r for r, _, _ in records])
    summary_key = f"{derived}/dt={day_iso}/day_summary.json"
    _upload(bucket_obj, summary_key,
            (json.dumps(summary, default=str, sort_keys=True) + "\n").encode("utf-8"),
            content_type="application/json")
    written.append(summary_key)

    # Merge the cross-day rfq index (dedupe on (rfq_id, chunk_key, line_no), last write wins).
    index_key = f"{derived}/index/rfq_index.parquet"
    try:
        existing_blob = bucket_obj.blob(index_key)
        if existing_blob.exists():
            for row in _read_table(_download(existing_blob)).to_pylist():
                k = (str(row["rfq_id"]), str(row["chunk_key"]), int(row["line_no"]))
                index.setdefault(k, {"rfq_id": k[0], "dt": str(row["dt"]), "chunk_key": k[1],
                                       "line_no": k[2]})
    except Exception:                                  # noqa: BLE001 -- a broken index rebuilds from today
        pass
    index_rows = sorted(index.values(), key=lambda r: (r["dt"], r["chunk_key"], r["line_no"]))
    _upload(bucket_obj, index_key, _table_bytes(_rows_to_table(index_rows)))
    written.append(index_key)

    return {"day": day_iso, "prefix": prefix, "bucket": bucket_name, "chunks": len(chunk_blobs),
            "records": len(records), "combos": len(combos), "legs": len(legs),
            "index_rows": len(index_rows), "written": written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the derived Parquet layer for one RFQ tape day")
    ap.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--prefix", default="Kalshi/rfq_tape/production")
    ap.add_argument("--bucket", default=None, help="default: mlb_core.storage bucket resolution")
    args = ap.parse_args(argv)
    out = finalize_day(args.day, args.prefix, bucket=args.bucket)
    print(json.dumps(out, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
