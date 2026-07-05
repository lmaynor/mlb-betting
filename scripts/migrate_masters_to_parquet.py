"""One-time: create the .parquet twins for the big CSV masters.

Reads each allowlisted master as CSV (forcing the CSV path) and writes the
.parquet twin next to it. After this, storage.read_csv() serves the twin
automatically and the nightly writers keep it fresh.

Rollback: delete the .parquet objects or set MLB_PARQUET_TWIN=0.

Run (Cloud Shell):
  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 scripts/migrate_masters_to_parquet.py
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlb_core import storage  # noqa: E402


def main() -> int:
    os.environ["MLB_PARQUET_TWIN"] = "0"  # force CSV reads during migration
    for key in sorted(storage.PARQUET_TWIN_KEYS):
        twin = key[:-4] + ".parquet"
        if not storage.exists(key):
            print(f"SKIP {key}: source CSV not found")
            continue
        print(f"reading {key} (CSV)...")
        df = storage.read_csv(key, low_memory=False)
        print(f"  {len(df):,} rows x {len(df.columns)} cols")
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        data = buf.getvalue()
        storage.write_bytes(data, twin)
        csv_mb = (storage.stat(key) or {}).get("size", 0) / 1e6
        print(f"  wrote {twin}  ({len(data)/1e6:.1f} MB parquet vs {csv_mb:.1f} MB csv)")
        # verify the twin round-trips
        os.environ["MLB_PARQUET_TWIN"] = "1"
        check = storage.read_csv(key)
        os.environ["MLB_PARQUET_TWIN"] = "0"
        assert len(check) == len(df), f"twin row mismatch for {key}"
        print(f"  verified: twin serves {len(check):,} rows")
    os.environ["MLB_PARQUET_TWIN"] = "1"
    print("done. reads now prefer the parquet twins.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
