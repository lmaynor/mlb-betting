"""
ev.novig.shadow_bank -- durable, append-only, idempotent banking of the shadow ledger (orders / decisions / fills) to GCS.

WHY: the Novig forward-CLV gate (ev.novig.clv_gate) is computed from the shadow ledger's filled plans, and that ledger
was one sqlite file on a laptop.  This module banks it under

    Novig/shadow_ledger/{ET day}/{run_id}_{sha8}.jsonl.gz        (chunks; never overwritten, never deleted)
    Novig/shadow_ledger/_verdicts/latest.json                    (derived gate verdicts; overwritten)

and restores it, so the gate (and a later scheduled run of the shadow loop) reads the banked copy instead of the laptop.

APPEND-SAFE / IDEMPOTENT.  A chunk holds only rows that were not already banked, named by run id plus a hash of its own
content, written CREATE-ONLY (GCS `if_generation_match=0`), so concurrent or repeated runs can never clobber an earlier
chunk.  Every row has a natural key (orders: intent_id, fills: fill_id, decisions: hash of the row, kv: key); restore
dedupes on it, so a row banked twice (two writers, a retried run, the laptop and the cloud job) is read once.  Banking
the same ledger twice writes nothing the second time.

WHAT IS BANKED.  `orders`, `decisions`, `fills` of every ledger mode, and the `grp:*` position-hold markers of the kv table
(they are what stops a later run from re-buying a player/stat it already holds).  The kill switch is NOT banked: a trip is a
per-process safeguard and the manual-reset semantics cannot be expressed in an append-only bank.

SHADOW / PAPER RECORDS ONLY.  No order client, no network beyond the object store, no credentials in this module.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from mlb.execution.kalshi.ledger import Ledger

PREFIX = "Novig/shadow_ledger"
VERDICT_KEY = f"{PREFIX}/_verdicts/latest.json"
SCHEMA = 1
_ET = ZoneInfo("America/New_York")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHUNK_RE = re.compile(rf"^{re.escape(PREFIX)}/(\d{{4}}-\d{{2}}-\d{{2}})/[^/]+\.jsonl\.gz$")

ORDER_COLS = ("ts", "day", "mode", "intent_id", "client_order_id", "order_id", "ticker", "game_pk", "side", "contracts",
              "limit_price", "status", "fill_count", "filled_cost", "fee", "raw_json")
DECISION_COLS = ("ts", "day", "mode", "intent_id", "ticker", "series", "market", "game_pk", "side", "outcome", "reason",
                 "p_cons", "p_hat", "n_books", "ask", "ev", "contracts", "limit_price", "lag_class", "inputs_json")
FILL_COLS = ("fill_id", "order_id", "ticker", "side", "count", "price", "fee", "ts", "mode")
KV_PREFIX = "grp:"
DEFAULT_MODES = ("novig", "novig_paper")          # Novig ledger modes; the laptop ledger also holds Kalshi 'shadow' / 'demo' rows
COLS = {"orders": ORDER_COLS, "decisions": DECISION_COLS, "fills": FILL_COLS, "kv": ("k", "v")}
TABLES = ("orders", "decisions", "fills", "kv")


# ----------------------------------------------------------------------------------------------- object stores
class DirStore:
    """A local directory as the bank (tests, a local mirror).  Same contract as GcsStore."""

    def __init__(self, root: str):
        self.root = root

    def _p(self, key: str) -> str:
        return os.path.join(self.root, *key.split("/"))

    def list(self, prefix: str) -> list:
        out = []
        for d, _dirs, files in os.walk(self.root):
            for f in files:
                key = os.path.relpath(os.path.join(d, f), self.root).replace(os.sep, "/")
                if key.startswith(prefix):
                    out.append(key)
        return sorted(out)

    def read(self, key: str) -> bytes:
        with open(self._p(key), "rb") as fh:
            return fh.read()

    def write_new(self, key: str, data: bytes) -> bool:
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return True

    def put(self, key: str, data: bytes) -> None:
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


def default_bucket() -> str:
    b = os.environ.get("MLB_GCS_BUCKET") or os.environ.get("GCS_BUCKET") or ""
    if not b:
        raise RuntimeError("no GCS bucket: set MLB_GCS_BUCKET (or pass --bucket)")
    return b


class GcsStore:
    """GCS through google-cloud-storage (Application Default Credentials; on Cloud Run the job's service account)."""

    def __init__(self, bucket: str | None = None, client=None):
        self.bucket_name = bucket or default_bucket()
        self._client = client

    def _cl(self):
        if self._client is None:
            from google.cloud import storage
            self._client = storage.Client()
        return self._client

    def _blob(self, key: str):
        return self._cl().bucket(self.bucket_name).blob(key)

    def list(self, prefix: str) -> list:
        return sorted(b.name for b in self._cl().list_blobs(self.bucket_name, prefix=prefix))

    def read(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes(timeout=120)

    def write_new(self, key: str, data: bytes) -> bool:
        from google.api_core.exceptions import PreconditionFailed
        try:
            self._blob(key).upload_from_string(data, content_type="application/gzip" if key.endswith(".gz") else "application/json",
                                               if_generation_match=0, timeout=120)
            return True
        except PreconditionFailed:
            return False                                         # already there: same name == same content

    def put(self, key: str, data: bytes) -> None:
        self._blob(key).upload_from_string(data, content_type="application/json", timeout=120)


class GcloudStore:
    """GCS through the `gcloud storage` CLI (a laptop with user credentials but no Application Default Credentials)."""

    def __init__(self, bucket: str | None = None):
        self.bucket_name = bucket or default_bucket()

    def _u(self, key: str) -> str:
        return f"gs://{self.bucket_name}/{key}"

    def _run(self, args: list, data: bytes | None = None) -> bytes:
        return subprocess.run(["gcloud", "storage", *args], input=data, capture_output=True, timeout=180, check=True).stdout

    def list(self, prefix: str) -> list:
        try:
            out = self._run(["ls", "--recursive", self._u(prefix)]).decode()
        except subprocess.CalledProcessError:
            return []                                            # nothing under the prefix yet
        base = f"gs://{self.bucket_name}/"
        return sorted(line[len(base):] for line in out.splitlines() if line.startswith(base) and not line.endswith(":") and not line.endswith("/"))

    def read(self, key: str) -> bytes:
        return self._run(["cat", self._u(key)])

    def write_new(self, key: str, data: bytes) -> bool:
        if self.list(key):
            return False
        self._run(["cp", "-", self._u(key)], data)
        return True

    def put(self, key: str, data: bytes) -> None:
        self._run(["cp", "-", self._u(key)], data)


def make_store(kind: str = "gcs", bucket: str | None = None):
    if kind == "gcs":
        return GcsStore(bucket)
    if kind == "gcloud":
        return GcloudStore(bucket)
    if kind.startswith("dir:"):
        return DirStore(kind[4:])
    raise ValueError(f"unknown store {kind!r} (gcs | gcloud | dir:PATH)")


# ----------------------------------------------------------------------------------------------- row keys / (de)serialisation
def et_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_ET).date().isoformat()


def row_key(table: str, rec: dict) -> str:
    """Natural key of a banked row (the dedup identity)."""
    if table == "orders":
        return f"orders:{rec['intent_id']}"
    if table == "fills":
        return f"fills:{rec['fill_id']}"
    if table == "kv":
        return f"kv:{rec['k']}"
    body = json.dumps(rec, sort_keys=True, default=str, separators=(",", ":"))
    return "decisions:" + hashlib.sha1(body.encode()).hexdigest()[:24]


def _select(ledger: Ledger, table: str, modes=None) -> list:
    """Rows of one table as dicts (no autoincrement id), optionally only those of the given ledger modes.  The Novig
    position-hold markers are `grp:{day}:novig|{game}|{player}|{stat}` (Kalshi's carry no venue prefix)."""
    cols = COLS[table]
    where, args = [], []
    if table == "kv":
        where.append("k LIKE 'grp:%'")
        if modes:
            where.append("(" + " OR ".join("k LIKE ?" for _ in modes) + ")")
            args += [f"grp:%:{m}|%" for m in modes]
    elif modes:
        where.append(f"mode IN ({','.join('?' * len(modes))})")
        args += list(modes)
    q = f"SELECT {','.join(cols)} FROM {table}" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY rowid"
    with ledger._lock:
        return [dict(zip(cols, tuple(r))) for r in ledger._c.execute(q, args).fetchall()]


def _day_of(table: str, rec: dict, order_day: dict, fallback: str) -> str:
    if table in ("orders", "decisions"):
        d = str(rec.get("day") or "")
    elif table == "fills":
        d = order_day.get(rec.get("order_id")) or str(rec.get("ts") or "")[:10]
    else:
        parts = str(rec["k"]).split(":", 2)
        d = parts[1] if len(parts) > 2 else ""
    return d if _DAY_RE.match(d) else fallback


def _encode(run_id: str, day: str, items: list) -> tuple:
    """-> (object key, gzip bytes) for one day's new rows.  gzip mtime is pinned so equal content is byte-identical."""
    lines = [json.dumps({"hdr": {"schema": SCHEMA, "run_id": run_id, "day": day, "rows": len(items)}}, sort_keys=True)]
    lines += [json.dumps({"t": t, "r": rec}, sort_keys=True, default=str) for t, rec in items]
    raw = ("\n".join(lines) + "\n").encode("ascii", "backslashreplace")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return f"{PREFIX}/{day}/{run_id}_{hashlib.sha1(raw).hexdigest()[:8]}.jsonl.gz", buf.getvalue()


def new_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"


# ----------------------------------------------------------------------------------------------- read the bank
def chunk_keys(store, days=None) -> list:
    """Chunk object keys, oldest first.  `days` (ET YYYY-MM-DD strings) restricts to those day folders."""
    prefixes = [f"{PREFIX}/{d}/" for d in days] if days else [f"{PREFIX}/"]
    keys = sorted({k for p in prefixes for k in store.list(p) if _CHUNK_RE.match(k)})
    return sorted(keys, key=lambda k: (k.split("/")[-2], k.split("/")[-1]))


def read_chunk(store, key: str) -> list:
    """[(table, record)] of one chunk (header skipped).  A corrupt chunk raises: a silently dropped chunk would silently
    drop fills from the gate's n."""
    raw = gzip.decompress(store.read(key)).decode("ascii")
    out = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        if "hdr" in o:
            continue
        out.append((o["t"], o["r"]))
    return out


def iter_banked(store, days=None, tables=None):
    """Yield (key, table, record) for every banked row, deduped on the natural key (first occurrence wins)."""
    seen: set = set()
    for ck in chunk_keys(store, days):
        for t, rec in read_chunk(store, ck):
            if tables is not None and t not in tables:
                continue
            k = row_key(t, rec)
            if k in seen:
                continue
            seen.add(k)
            yield k, t, rec


def load_known(store, days=None, tables=None) -> set:
    return {k for k, _t, _r in iter_banked(store, days, tables)}


# ----------------------------------------------------------------------------------------------- bank
def bank(ledger: Ledger, store, *, known: set | None = None, run_id: str | None = None, now: datetime | None = None,
         modes=DEFAULT_MODES) -> dict:
    """Write every ledger row whose natural key is not in `known` as create-only per-day chunks.  `known=None` reads the
    whole bank first (always safe, just slower); callers that restored the ledger from the bank pass the restore's key set.
    The set is updated in place with what was written, so a second call in the same process writes nothing new.
    `modes` limits banking to those ledger modes (default: the Novig ones); None banks every mode."""
    run_id = run_id or new_run_id(now)
    run_day = et_day(now)
    if known is None:
        known = load_known(store)
    order_rows = _select(ledger, "orders", modes)
    order_day = {r["order_id"]: r["day"] for r in order_rows if r.get("order_id")}
    by_day: dict = {}
    counts = {t: 0 for t in TABLES}
    total = 0
    for t in TABLES:
        rows = order_rows if t == "orders" else _select(ledger, t, modes)
        total += len(rows)
        for rec in rows:
            k = row_key(t, rec)
            if k in known:
                continue
            by_day.setdefault(_day_of(t, rec, order_day, run_day), []).append((k, t, rec))
    chunks = []
    for day in sorted(by_day):
        items = by_day[day]
        key, blob = _encode(run_id, day, [(t, rec) for _k, t, rec in items])
        created = store.write_new(key, blob)          # False == an identical chunk is already there (a retried run)
        chunks.append({"key": key, "rows": len(items), "created": created, "bytes": len(blob)})
        for k, t, _rec in items:
            known.add(k)
            counts[t] += 1
    return {"run_id": run_id, "ledger_rows": total, "new_rows": counts, "chunks": chunks,
            "skipped_known": total - sum(counts.values())}


# ----------------------------------------------------------------------------------------------- restore
def restore_into(ledger: Ledger, store, *, days=None, tables=None) -> dict:
    """Insert banked rows the ledger does not have yet (original timestamps preserved).  Returns
    {'known': <natural keys that are in the bank>, 'restored': {table: n}, 'chunks': n}."""
    want = set(tables) if tables else set(TABLES)
    existing = {t: {row_key(t, r) for r in _select(ledger, t)} for t in want}
    known, restored = set(), {t: 0 for t in want}
    n_chunks = len(chunk_keys(store, days))
    with ledger._lock:
        for k, t, rec in iter_banked(store, days, want):
            known.add(k)
            if k in existing[t]:
                continue
            cols = COLS[t]
            if t == "decisions":
                ledger._c.execute(f"INSERT INTO decisions ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  [rec.get(c) for c in cols])
            elif t == "orders":
                ledger._c.execute(f"INSERT OR IGNORE INTO orders ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  [rec.get(c) for c in cols])
            elif t == "fills":
                ledger._c.execute(f"INSERT OR IGNORE INTO fills ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                                  [rec.get(c) for c in cols])
            else:
                ledger._c.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?,?)", (rec["k"], rec["v"]))
            existing[t].add(k)
            restored[t] += 1
        ledger._c.commit()
    return {"known": known, "restored": restored, "chunks": n_chunks}


def banked_ledger(store, days=None, tables=None) -> Ledger:
    """A fresh in-memory Ledger holding the banked rows: the read path for the CLV gate (ev.novig.clv_gate.ledger_bets)."""
    led = Ledger(":memory:")
    restore_into(led, store, days=days, tables=tables)
    return led


# ----------------------------------------------------------------------------------------------- CLI
def summarise(ledger: Ledger) -> dict:
    with ledger._lock:
        return {"orders": ledger._c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                "filled_orders": ledger._c.execute("SELECT COUNT(*) FROM orders WHERE fill_count > 0").fetchone()[0],
                "decisions": ledger._c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0],
                "fills": ledger._c.execute("SELECT COUNT(*) FROM fills").fetchone()[0]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bank / inspect the Novig shadow ledger bank (GCS).  Shadow records only.")
    ap.add_argument("--store", default="gcs", help="gcs (ADC) | gcloud (CLI) | dir:PATH")
    ap.add_argument("--bucket", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bank", help="bank a local sqlite ledger (append-only; re-running writes nothing new)")
    b.add_argument("--db", required=True)
    b.add_argument("--all-modes", action="store_true", help="also bank the Kalshi 'shadow' / 'demo' rows (default: Novig modes only)")
    sub.add_parser("status", help="what is banked (rows by table, filled orders by ledger mode)")
    r = sub.add_parser("restore", help="restore the bank into a local sqlite ledger (rows it lacks only)")
    r.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    store = make_store(a.store, a.bucket)
    if a.cmd == "bank":
        led = Ledger(a.db)
        res = bank(led, store, modes=None if a.all_modes else DEFAULT_MODES)
        print(json.dumps(res, indent=1, default=str))
        return 0
    if a.cmd == "restore":
        led = Ledger(a.db)
        res = restore_into(led, store)
        print(json.dumps({"restored": res["restored"], "chunks": res["chunks"], "ledger": summarise(led)}, indent=1))
        return 0
    led = banked_ledger(store)
    with led._lock:
        modes = [tuple(x) for x in led._c.execute(
            "SELECT mode, COUNT(*), SUM(fill_count > 0), MIN(day), MAX(day) FROM orders GROUP BY mode")]
    print(json.dumps({"banked": summarise(led), "orders_by_mode(mode,n,filled,first_day,last_day)": modes}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
