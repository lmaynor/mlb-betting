"""CLI scorecard report for MAK-01 settlement & fee capture (RUL-20261124-maker-lane section (e))."""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ev.maker_settlement import (  # noqa: E402
    NHLSogResolver,
    Outcome,
    STATUS_LOSS,
    STATUS_UNRECONCILED,
    STATUS_VOID,
    STATUS_WIN,
    scorecard,
    settle_fill,
    z_cal,
)

_SETTLED_STATUSES = frozenset({STATUS_WIN, STATUS_LOSS, STATUS_VOID})


class _NoResolver:
    """Fallback resolver used when --boxscores is omitted."""

    def resolve(self, fill: dict[str, Any]) -> Outcome:
        return Outcome(
            settlement_price=None,
            status=STATUS_UNRECONCILED,
            source="none",
            detail="no resolver",
            stat_value=None,
        )


def _load_fills(path: Path) -> list[dict[str, Any]]:
    if path.is_dir():
        states: dict[str, dict[str, Any]] = {}
        for chunk_path in sorted(path.rglob("*.jsonl.gz")):
            with gzip.open(chunk_path, "rt", encoding="ascii", errors="backslashreplace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if "hdr" in obj:
                        continue
                    if obj.get("t") == "quote_state":
                        rec = obj["r"]
                        oid = rec.get("order_id") or rec.get("client_id")
                        if oid:
                            states[oid] = rec
        return [rec for rec in states.values() if float(rec.get("fill_qty_units") or 0.0) > 0.0]

    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [rec for rec in data if float(rec.get("fill_qty_units") or 0.0) > 0.0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MAK-01 settlement & fee-capture scorecard (RUL-20261124-maker-lane section (e))."
    )
    parser.add_argument(
        "--fills",
        required=True,
        help="Path to a JSON list of fill dicts or a directory of maker-study .jsonl.gz chunks.",
    )
    parser.add_argument(
        "--boxscores",
        default=None,
        help="Optional path to NHL skater_boxscores_master.csv.",
    )
    parser.add_argument(
        "--venue",
        default="novig",
        help="Execution venue (novig, kalshi, prophetx). Default: novig.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Optional path to write settled ledger records as JSONL.",
    )
    parser.add_argument(
        "--summary-out",
        default=None,
        help="Optional path to write JSON summary metrics.",
    )
    args = parser.parse_args(argv)

    fills = _load_fills(Path(args.fills))
    resolver: Any = NHLSogResolver(args.boxscores) if args.boxscores else _NoResolver()

    records = [settle_fill(f, resolver=resolver, venue=args.venue) for f in fills]
    print(scorecard(records))

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")

    if args.summary_out:
        settled = [r for r in records if r["settlement_status"] in _SETTLED_STATUSES]
        z_val, z_n = z_cal(records)
        summary = {
            "n_fills": len(records),
            "n_settled": len(settled),
            "n_win": sum(1 for r in records if r["settlement_status"] == STATUS_WIN),
            "n_loss": sum(1 for r in records if r["settlement_status"] == STATUS_LOSS),
            "n_void": sum(1 for r in records if r["settlement_status"] == STATUS_VOID),
            "n_unreconciled": sum(
                1 for r in records if r["settlement_status"] == STATUS_UNRECONCILED
            ),
            "realized_pnl_usd": round(
                sum(
                    float(r["net_realized_pnl_usd"])
                    for r in settled
                    if r["net_realized_pnl_usd"] is not None
                ),
                4,
            ),
            "z_cal": z_val,
            "z_cal_n": z_n,
            "unreconciled_order_ids": [
                r["order_id"]
                for r in records
                if r["settlement_status"] == STATUS_UNRECONCILED
            ],
        }
        summary_path = Path(args.summary_out)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, sort_keys=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
