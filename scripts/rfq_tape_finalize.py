"""rfq_tape_finalize.py -- build the derived Parquet layer for one UTC day of the Kalshi RFQ tape.

Design: rfq-tape-storage-design-2026-11-24.md section 3. The raw JSONL tape is evidence and is never
mutated; this batch pass over ONE day's chunks (incremental, never a whole-history rescan) writes, under
{prefix}_derived/:

  dt={day}/combos.parquet   one row per kind="rfq" record: every scalar field (v1 quote fields and the
                            tape_schema v2 materialized fields) as columns; the nested legs / rfq /
                            notes payloads as JSON strings.
  dt={day}/legs.parquet     one row per leg: rfq_id, ts, day_utc + every leg field (incl. the q_kalshi
                            capture and the canonical settle fields).
  dt={day}/leg_settlements.parquet
                            (opt-in via market_resolver / --settle-legs) one row per (rfq_id, ticker)
                            selected leg with resolved market_status, market_result, and leg_won.
                            Banked leg outcomes let shadow classes be graded later and feed
                            ev.kalshi_rfq_shadow_pricing.build_family_frequency_table ({series, won}
                            rows with won = market_result == "yes") to build measured-frequency defaults.
  dt={day}/day_summary.json the recorder's section 2i sidecar copied from the raw prefix when present,
                            else the same counts computed from the records.
  index/rfq_index.parquet   rfq_id -> (dt, chunk_key, line_no) across all days, merged with whatever
                            was already there; dedupe on (rfq_id, chunk_key, line_no), last write wins.

Records written before tape_schema v2 (no tape_schema field) finalize with nulls for the v2-only
columns: missing-fields-as-null semantics, so cross-era queries run over one derived shape.

CLI: python scripts/rfq_tape_finalize.py --day YYYY-MM-DD [--prefix Kalshi/rfq_tape/production]
     [--bucket ...] [--settle-legs]
     (bucket defaults to the mlb_core.storage resolution: MLB_GCS_BUCKET / GCS_BUCKET)
"""

from __future__ import annotations

import argparse
import io
import json
import logging
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
         "creator_counts": {}, "flagged_creators": {},
         "by_primary_class": {}, "by_disposition": {}, "fires_deduped": 0}
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
        pc = str(rec.get("primary_class") or "UNCLASSIFIED")
        s["by_primary_class"][pc] = s["by_primary_class"].get(pc, 0) + 1
        disp = str(rec.get("disposition") or "UNCLASSIFIED")
        s["by_disposition"][disp] = s["by_disposition"].get(disp, 0) + 1
        if rec.get("disposition") == "ADMITTED_FIRE" and not rec.get("is_dup_creator_combo"):
            s["fires_deduped"] += 1
    evaluated = s["evaluated"]
    top = sorted(s["creator_counts"].items(), key=lambda kv: (-kv[1], kv[0]))[:20]
    return {"day": day, "tape_schema": 2, "records_by_kind": s["records_by_kind"], "fires": s["fires"],
            "evaluated": evaluated, "in_denominator": s["in_denominator"], "by_family": s["by_family"],
            "by_excluded_series_class": s["by_excluded_series_class"], "preview_10": s["preview_10"],
            "preview_10_share": (s["preview_10"] / evaluated) if evaluated else 0.0,
            "flagged_creators": s["flagged_creators"],
            "top_creators": [{"creator_id": c, "count": n} for c, n in top],
            "followup_records": s["followup_records"],
            "followup_attach_rate": (s["followup_records"] / evaluated) if evaluated else 0.0,
            "by_primary_class": s["by_primary_class"],
            "by_disposition": s["by_disposition"],
            "fires_deduped": s["fires_deduped"]}


def _selected_legs_for_record(rec: dict) -> list[tuple[str, str]]:
    """Extract (ticker, side) pairs for all selected legs of a kind='rfq' record."""
    rfq_obj = rec.get("rfq") if isinstance(rec.get("rfq"), dict) else {}
    mve_legs = rfq_obj.get("mve_selected_legs")
    if mve_legs:
        src = mve_legs
        tk_keys = ("market_ticker", "ticker")
    else:
        src = rec.get("legs") or []
        tk_keys = ("ticker", "market_ticker")
    out: list[tuple[str, str]] = []
    for leg in src:
        if not isinstance(leg, dict):
            continue
        tk = str(leg.get(tk_keys[0]) or leg.get(tk_keys[1]) or "")
        if not tk:
            continue
        side = str(leg.get("side") or "").lower()
        out.append((tk, side))
    return out


def _build_leg_settlements(records: list[tuple[dict, str, int]], market_resolver) -> tuple[list[dict], int]:
    """Resolve each distinct selected leg ticker once and build dt={day}/leg_settlements.parquet rows.

    Banked leg outcomes allow shadow classes to be graded later and supply {series, won} rows
    (won = market_result == 'yes') to ev.kalshi_rfq_shadow_pricing.build_family_frequency_table.
    """
    per_record: list[tuple[dict, str, dict[str, str], list[tuple[str, str]]]] = []
    distinct_tickers: dict[str, None] = {}
    for rec, _, _ in records:
        if rec.get("kind") != "rfq":
            continue
        rfq_id = rec.get("rfq_id") or (rec.get("rfq") or {}).get("id") or (rec.get("rfq") or {}).get("rfq_id")
        rfq_id_str = str(rfq_id or "")
        notes = rec.get("notes") if isinstance(rec.get("notes"), dict) else {}
        status_by_ticker: dict[str, str] = {}
        for item in notes.get("legs_status") or []:
            if isinstance(item, dict) and item.get("ticker") and item.get("status") is not None:
                status_by_ticker[str(item["ticker"])] = str(item["status"])
        sel_legs = _selected_legs_for_record(rec)
        for tk, _ in sel_legs:
            distinct_tickers.setdefault(tk, None)
        per_record.append((rec, rfq_id_str, status_by_ticker, sel_legs))

    resolved = {tk: market_resolver(tk) for tk in distinct_tickers}

    rows: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()
    settled = 0
    for rec, rfq_id_str, status_by_ticker, sel_legs in per_record:
        seen_local: set[str] = set()
        for tk, side in sel_legs:
            if rfq_id_str:
                pair = (rfq_id_str, tk)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
            else:
                if tk in seen_local:
                    continue
                seen_local.add(tk)

            res = resolved.get(tk)
            m_status = res.get("status") if isinstance(res, dict) else None
            m_result = res.get("result") if isinstance(res, dict) else None
            if m_status == "finalized" and m_result in ("yes", "no") and side in ("yes", "no"):
                leg_won: bool | None = (side == "yes") == (m_result == "yes")
            else:
                leg_won = None
            if leg_won is not None:
                settled += 1
            rows.append({
                "rfq_id": rfq_id_str,
                "ts": rec.get("ts"),
                "day_utc": rec.get("day_utc"),
                "ticker": tk,
                "side": side,
                "series": tk.split("-", 1)[0],
                "legs_status": status_by_ticker.get(tk, "PRICED"),
                "market_status": m_status,
                "market_result": m_result,
                "leg_won": leg_won,
            })

    rows.sort(key=lambda r: (str(r.get("series") or ""), str(r.get("rfq_id") or ""), str(r.get("ticker") or "")))
    return rows, settled


def _build_market_resolver():
    """Construct a read-only Kalshi market resolver using the recorder daemon's env credentials."""
    from mlb.execution.kalshi.client import (
        PROD_READONLY_ENV,
        KalshiAPIError,
        KalshiClient,
        read_env_keys,
    )

    rfq_env = (os.environ.get("KALSHI_RFQ_ENV") or PROD_READONLY_ENV).strip().lower()
    if rfq_env not in ("demo", PROD_READONLY_ENV):
        raise ValueError(f"unsupported KALSHI_RFQ_ENV={rfq_env!r} (allowed: demo, {PROD_READONLY_ENV})")
    prod = rfq_env == PROD_READONLY_ENV
    id_k, path_k = (
        ("KALSHI_PROD_API_KEY_ID", "KALSHI_PROD_PRIVATE_KEY_PATH")
        if prod
        else ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY_PATH")
    )
    keys = ("KALSHI_ENV", id_k, path_k)
    vals = {k: os.environ.get(k) for k in keys if os.environ.get(k)}
    env_file = os.environ.get("KEXEC_ENV_FILE")
    if env_file:
        for k, v in read_env_keys(env_file, keys).items():
            vals.setdefault(k, v)
    if not prod and vals.get("KALSHI_ENV", "demo") != "demo":
        raise ValueError(f"refusing KALSHI_ENV={vals.get('KALSHI_ENV')!r} for demo")
    pem_k = "KALSHI_PROD_PRIVATE_KEY_PEM" if prod else "KALSHI_PRIVATE_KEY_PEM"
    pem_env = os.environ.get(pem_k)
    if vals.get(id_k) and pem_env:
        key_id, pem = vals[id_k], pem_env.encode()
    elif vals.get(id_k) and vals.get(path_k):
        with open(os.path.expanduser(vals[path_k]), "rb") as fh:
            key_id, pem = vals[id_k], fh.read()
    else:
        raise RuntimeError(f"{rfq_env} credentials not found ({id_k} / {path_k} or {pem_k})")

    client = KalshiClient(rfq_env, key_id, pem)
    log = logging.getLogger("rfq_tape_finalize")

    def resolve(ticker: str) -> dict | None:
        try:
            mk = client.get_market(ticker)
        except (KalshiAPIError, Exception) as exc:  # noqa: BLE001 -- per-ticker miss degrades to None
            log.warning("market lookup failed for %s: %r", ticker, exc)
            return None
        if not isinstance(mk, dict):
            return None
        return {
            "status": str(mk.get("status") or ""),
            "result": str(mk.get("result") or ""),
        }

    return resolve


def finalize_day(
    day_iso: str,
    prefix: str,
    *,
    bucket: str | None = None,
    storage_client=None,
    market_resolver=None,
) -> dict:
    """Build the derived layer for `day_iso` under {prefix}_derived/. -> dict of what was written.

    When `market_resolver` is provided (callable ticker -> {"status", "result"} | None), also writes
    dt={day}/leg_settlements.parquet so shadow classes can be graded later and
    ev.kalshi_rfq_shadow_pricing.build_family_frequency_table can consume {series, won} outcomes.
    """
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

    leg_settlements_out: int | str = "skipped_no_resolver"
    leg_settlements_settled: int | None = None
    if market_resolver is not None:
        leg_settlements, leg_settlements_settled = _build_leg_settlements(records, market_resolver)
        leg_settlements_key = f"{derived}/dt={day_iso}/leg_settlements.parquet"
        _upload(bucket_obj, leg_settlements_key, _table_bytes(_rows_to_table(leg_settlements)))
        written.append(leg_settlements_key)
        leg_settlements_out = len(leg_settlements)

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
    if market_resolver is not None:
        summary["leg_settlements_banked"] = leg_settlements_out
        summary["leg_settlements_settled"] = leg_settlements_settled
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

    result = {"day": day_iso, "prefix": prefix, "bucket": bucket_name, "chunks": len(chunk_blobs),
              "records": len(records), "combos": len(combos), "legs": len(legs),
              "index_rows": len(index_rows), "written": written,
              "leg_settlements": leg_settlements_out}
    if leg_settlements_settled is not None:
        result["leg_settlements_settled"] = leg_settlements_settled
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the derived Parquet layer for one RFQ tape day")
    ap.add_argument("--day", required=True, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--prefix", default="Kalshi/rfq_tape/production")
    ap.add_argument("--bucket", default=None, help="default: mlb_core.storage bucket resolution")
    ap.add_argument("--settle-legs", action="store_true",
                    help="resolve selected leg settlements via read-only KalshiClient and bank leg_settlements.parquet")
    args = ap.parse_args(argv)
    resolver = None
    if args.settle_legs:
        try:
            resolver = _build_market_resolver()
        except Exception as exc:  # noqa: BLE001 -- abort before writing any partial derived layer
            print(f"error: --settle-legs failed to construct Kalshi market resolver: {exc}",
                  file=sys.stderr)
            return 1
    out = finalize_day(args.day, args.prefix, bucket=args.bucket, market_resolver=resolver)
    print(json.dumps(out, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
