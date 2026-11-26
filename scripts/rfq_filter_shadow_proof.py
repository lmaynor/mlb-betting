"""Offline Window-1 shadow-mode proof script for the Kalshi RFQ ingest filter.

CLI: python scripts/rfq_filter_shadow_proof.py --tape-dir DIR [--max-chunk N] [--out PATH]
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ev.kalshi_rfq_filter import (
    FLAG_BITS,
    AuditSampler,
    FilterConfig,
    FilterMode,
    RFQFilterState,
    classify_rfq_record,
    decide_storage,
)
from ev.kalshi_rfq_shadow_pricing import (
    FamilyFrequencyProvider,
    KalshiLegMidProvider,
    shadow_price_combo,
)


def _iter_chunk_files(tape_dir: str, max_chunk: int | None) -> list[str]:
    files = sorted(glob.glob(os.path.join(tape_dir, "*.jsonl")))
    if max_chunk is not None:
        files = files[:max_chunk]
    return files


def _build_shadow_legs(rec: dict) -> tuple[list[dict], dict[str, float]]:
    dlegs = [l for l in (rec.get("legs") or []) if isinstance(l, dict)]
    book_win_p = {
        str(l["ticker"]): float(l["p_leg"])
        for l in dlegs
        if l.get("ticker") and l.get("p_leg") is not None
    }
    rfq_obj = rec.get("rfq") if isinstance(rec.get("rfq"), dict) else {}
    raw_legs = rfq_obj.get("mve_selected_legs") or rfq_obj.get("legs") or []
    if raw_legs:
        combo_legs = []
        for l in raw_legs:
            if not isinstance(l, dict):
                continue
            tk = str(l.get("market_ticker") or l.get("ticker") or "")
            item = {
                "ticker": tk,
                "side": str(l.get("side") or "").lower(),
                "series": tk.split("-", 1)[0],
            }
            if l.get("event_ticker"):
                item["event_ticker"] = str(l["event_ticker"])
            combo_legs.append(item)
        return combo_legs, book_win_p

    combo_legs = [
        {
            "ticker": str(l.get("ticker") or ""),
            "side": str(l.get("side") or "").lower(),
            "series": str(l.get("series") or str(l.get("ticker") or "").split("-", 1)[0]),
        }
        for l in dlegs
    ]
    return combo_legs, book_win_p


def run_shadow_proof(tape_dir: str, max_chunk: int | None = None) -> tuple[str, bool]:
    files = _iter_chunk_files(tape_dir, max_chunk)
    state = RFQFilterState()
    cfg = FilterConfig(shed_armed=False)
    sampler = AuditSampler()

    overlap_preview_excl = 0
    storage_counts = {"FULL": 0, "SLIM": 0, "COUNTER_ONLY": 0}
    open1_by_unpriced: dict[int, list[float]] = {}
    book_by_ticker: dict[str, tuple[float | None, float | None]] = {}
    shadow_candidates: list[dict] = []

    for path in files:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("kind") != "rfq":
                    continue

                stamp = classify_rfq_record(rec, state, cfg)
                mask = stamp["flag_bitmask"]
                if (mask & FLAG_BITS["PREVIEW_10"]) and (mask & FLAG_BITS["EXCLUDED_SERIES"]):
                    overlap_preview_excl += 1

                decision = decide_storage(
                    stamp,
                    FilterMode.WINDOW1_SHADOW,
                    sampler,
                    rfq_id=str(rec.get("rfq_id") or ""),
                )
                storage_counts[decision] = storage_counts.get(decision, 0) + 1

                dlegs = [l for l in (rec.get("legs") or []) if isinstance(l, dict)]
                for leg in dlegs:
                    tk = leg.get("ticker")
                    bid = leg.get("q_kalshi_bid")
                    ask = leg.get("q_kalshi_ask")
                    if tk and bid is not None and ask is not None:
                        book_by_ticker[str(tk)] = (float(bid), float(ask))

                if rec.get("is_fire") and rec.get("p_yes_true") is not None and dlegs:
                    p_legs = [
                        min(0.99, float(leg["p_leg"]))
                        for leg in dlegs
                        if leg.get("p_leg") is not None
                    ]
                    if p_legs:
                        denom_prod = math.prod(p_legs)
                        if denom_prod > 0.0:
                            n_unp = int((rec.get("notes") or {}).get("n_unpriced") or 0)
                            open1_by_unpriced.setdefault(n_unp, []).append(
                                float(rec["p_yes_true"]) / denom_prod
                            )

                if stamp["shadow_eligible"]:
                    shadow_candidates.append(rec)

    mid_provider = KalshiLegMidProvider(book_by_ticker)
    empty_family = FamilyFrequencyProvider({})
    shadow_priced = 0
    fail_closed_counts: dict[str, int] = {}
    for rec in shadow_candidates:
        combo_legs, book_win_p = _build_shadow_legs(rec)
        res = shadow_price_combo(combo_legs, book_win_p, [mid_provider, empty_family])
        status = str(res.get("status") or "UNKNOWN")
        if status == "SHADOW_PRICED":
            shadow_priced += 1
        else:
            fail_closed_counts[status] = fail_closed_counts.get(status, 0) + 1

    reconciles = True
    try:
        state.ledger.assert_reconciles()
    except AssertionError:
        reconciles = False

    ledger = state.ledger
    lines: list[str] = []
    lines.append(f"=== RFQ FILTER WINDOW-1 SHADOW PROOF ({len(files)} chunk files) ===")
    lines.append(f"observed={ledger.observed} denom_survivors={ledger.denom_survivors}")
    lines.append("")
    lines.append("(a) Primary partition + bitmask overlap")
    for k, v in sorted(ledger.by_primary_class.items()):
        lines.append(f"  primary.{k}: {v}")
    lines.append(f"  overlap(PREVIEW_10 & EXCLUDED_SERIES): {overlap_preview_excl}")
    lines.append("")
    lines.append("(b) In-denominator fire re-grade")
    lines.append(f"  tier_a: {ledger.admitted_tier_a}")
    lines.append(f"  tier_b_strict: {ledger.admitted_tier_b}")
    lines.append(f"  tier_b_nonstrict: {ledger.nonstrict}")
    lines.append(f"  over_cap: {ledger.over_cap}")
    lines.append(f"  stale_zero: {ledger.stale_zero}")
    lines.append(f"  admitted_raw: {ledger.admitted_raw}")
    lines.append(f"  admitted_deduped: {ledger.admitted_deduped}")
    lines.append("")
    lines.append("(c) Storage decisions (WINDOW1_SHADOW)")
    for k, v in sorted(storage_counts.items()):
        lines.append(f"  {k}: {v}")
    lines.append(f"  dropped: {storage_counts.get('COUNTER_ONLY', 0)}")
    lines.append("")
    lines.append("(d) OPEN-1 numeric corroboration: p_yes_true / prod(min(0.99, p_leg))")
    if not open1_by_unpriced:
        lines.append("  (no fires with p_yes_true)")
    else:
        for n_unp in sorted(open1_by_unpriced):
            vals = open1_by_unpriced[n_unp]
            lines.append(
                f"  n_unpriced={n_unp}: count={len(vals)} "
                f"median={statistics.median(vals):.6f} min={min(vals):.6f} max={max(vals):.6f}"
            )
    lines.append("")
    lines.append("(e) Shadow-pricing census (sports combos with >=1 unmapped leg, empty family table)")
    lines.append(f"  eligible: {len(shadow_candidates)}")
    lines.append(f"  SHADOW_PRICED: {shadow_priced}")
    for reason, cnt in sorted(fail_closed_counts.items()):
        lines.append(f"  {reason}: {cnt}")
    lines.append("")
    lines.append(f"RECONCILIATION: {'PASS' if reconciles else 'FAIL'}")

    return "\n".join(lines) + "\n", reconciles


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Run Window-1 shadow proof of the Kalshi RFQ ingest filter over tape chunks"
    )
    ap.add_argument("--tape-dir", required=True, help="Directory of *.jsonl tape chunks")
    ap.add_argument("--max-chunk", type=int, default=None, help="Optional cap on chunk files read")
    ap.add_argument("--out", default=None, help="Optional path to write report text")
    args = ap.parse_args(argv)

    report, ok = run_shadow_proof(args.tape_dir, max_chunk=args.max_chunk)
    sys.stdout.write(report)
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
