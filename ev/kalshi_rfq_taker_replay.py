"""ev/kalshi_rfq_taker_replay.py -- Point-in-time replay and settlement grading of RFQ taker combos.

1. What it does: feeds recorded combo quotes through the taker scanner with the
   merged pricer, records every fire/pass with blade-level reasons, grades admitted
   fires against settlement where the outcome is known.

2. The two real fixture sources and their limits:
   - SETTLED: production combo markets pulled from Kalshi's public API (the same
     jsonl ev.kalshi_rfq_replay.load_combos reads). bid_yes is the market's LAST
     traded YES price, labeled TRADED_YES_PRICE: a proxy for the executable bid,
     not a recorded RFQ quote.
   - TAPE: the banked recorder tape (Kalshi/rfq_tape/demo/{day}/*.jsonl in the data
     bucket). These are REAL RFQ requests, but from the DEMO exchange, recorded
     2026-10-26/27. bid_yes is the first follow-up trade's yes_price_dollars,
     labeled FOLLOWUP_TRADED_YES_PRICE. Demo combos have no settlement in the
     tape, so tape records are never graded.
   - No production RFQ tape exists anywhere (the production-readonly recorder was
     never provisioned).

3. Known input gaps: fixtures carry no injury/scratch feed, so is_hot_scratch is
   False unless the fair leg itself carries a hot flag; per-leg q_implied is the
   sportsbook consensus probability in the leg's direction (the tape carries no
   per-leg retail quote), so Blade 1's price-gap sub-test measures
   consensus-vs-masked-model divergence.
"""

from __future__ import annotations

import collections
import glob
import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ev.capability_mask import apply_capability_mask
from ev.combo_taker_scanner import (
    ComboCandidate,
    ComboLeg,
    ComboLiabilityGuard,
    evaluate_combo,
)
from ev.combo_taker_window2 import Window2Admission, Window2Fire
from ev.kalshi_rfq_pricer import LegFairValue
from ev.kalshi_rfq_replay import MAX_SNAPSHOT_AGE_SEC, epoch
from mlb.execution.kalshi.fees import taker_fee


def leg_to_combo_leg(
    fv: LegFairValue | None,
    side: str,
    venue: str = "kalshi",
    game_start_ts: float | None = None,
) -> ComboLeg | None:
    if fv is None:
        return None
    side_clean = str(side).lower().strip()
    if side_clean not in ("yes", "no"):
        return None

    direction = 1 if side_clean == "yes" else -1

    p_cons_yes = float(fv.p_cons_yes)
    p_true_yes = float(fv.p_true_yes) if fv.p_true_yes is not None else p_cons_yes
    p_model_yes = float(fv.p_model_yes) if fv.p_model_yes is not None else p_cons_yes

    p_dir_true = p_true_yes if direction > 0 else (1.0 - p_true_yes)
    p_dir_cons = p_cons_yes if direction > 0 else (1.0 - p_cons_yes)

    p_model_overdir = p_model_yes
    p_cons_overdir = p_cons_yes

    is_player_prop = (fv.market_class == "player_prop") or (fv.player_norm is not None)
    if is_player_prop:
        decision = apply_capability_mask(
            p_model=p_model_overdir,
            q_book=p_cons_overdir,
            sport=fv.sport,
            stat=fv.stat,
            line=fv.strike,
            p_peer_med=p_cons_overdir,
            n_peer_books=fv.n_books,
        )
        p_lambda = decision.p_lambda if direction > 0 else decision.p_under_masked
        routing = str(decision.routing)
    else:
        p_lambda = p_dir_true
        routing = "OVER_ELIGIBLE"

    q_implied = p_dir_cons
    line = float(fv.strike)
    is_hot_scratch = bool(getattr(fv, "hot", False) or getattr(fv, "is_hot_scratch", False))
    start_ts = game_start_ts if game_start_ts is not None else 0.0

    return ComboLeg(
        ticker=fv.ticker,
        venue=venue,
        player_norm=fv.player_norm,
        game_id=fv.game_key,
        sport=fv.sport,
        stat=fv.stat,
        line=line,
        direction=direction,
        p_lambda=p_lambda,
        routing=routing,
        q_implied=q_implied,
        is_hot_scratch=is_hot_scratch,
        game_start_ts=start_ts,
        team=fv.team,
    )


def commence_lookup(store: Any, t: float, max_age: float = MAX_SNAPSHOT_AGE_SEC) -> Callable[[str], float | None]:
    stores = store.stores if hasattr(store, "stores") else [store]
    primary_map: Dict[str, float] = {}
    fallback_map: Dict[str, float] = {}

    for s in stores:
        if hasattr(s, "snapshot_index"):
            idx = s.snapshot_index(t, max_age)
            if idx is not None and len(s._snaps[idx]) > 3 and s._snaps[idx][3]:
                primary_map.update(s._snaps[idx][3])
        if hasattr(s, "_snaps"):
            for snap in s._snaps:
                if len(snap) > 3 and snap[3]:
                    fallback_map.update(snap[3])

    def _lookup(game_key: str) -> float | None:
        if game_key in primary_map:
            return primary_map[game_key]
        return fallback_map.get(game_key)

    return _lookup


def candidate_from_legs(
    combo_id: str,
    venue: str,
    legs_with_sides: Sequence[Tuple[str, str]],
    book: Any,
    t: float,
    bid_yes: float | None,
    game_start_lookup: Optional[Callable[[str], float | None]] = None,
) -> Tuple[Optional[ComboCandidate], str]:
    if bid_yes is None:
        return None, "NO_EXECUTABLE_PRICE"
    if book is None:
        return None, "UNPRICED_LEG"

    combo_legs: List[ComboLeg] = []
    timing_approx = False

    for ticker, side in legs_with_sides:
        fv = book.get(ticker, t)
        if fv is None:
            return None, "UNPRICED_LEG"

        start_ts = game_start_lookup(fv.game_key) if game_start_lookup is not None else None
        if start_ts is None:
            start_ts = t + 3600.0
            timing_approx = True

        try:
            leg = leg_to_combo_leg(fv, side, venue=venue, game_start_ts=start_ts)
        except Exception:
            return None, "MASK_ERROR"

        if leg is None:
            return None, "MASK_ERROR"

        combo_legs.append(leg)

    cand = ComboCandidate(
        combo_id=str(combo_id),
        venue=venue,
        legs=combo_legs,
        bid_yes=float(bid_yes),
        age_since_creation_sec=0.0,
        is_rfq_live=True,
        now_ts=float(t),
        stake=None,
    )
    cand.timing_approx = timing_approx
    return cand, "OK"


def evaluate_taker_combo(
    candidate: ComboCandidate,
    yes_won: Optional[bool] = None,
    guard: Optional[ComboLiabilityGuard] = None,
    bid_src: Optional[str] = None,
    timing_approx: Optional[bool] = None,
) -> dict:
    signal = evaluate_combo(candidate)

    pnl_per_contract: Optional[float] = None
    if yes_won is not None and signal.fires:
        fee = taker_fee(signal.ask_no, 100, 1.0) / 100.0
        pnl_per_contract = (0.0 if yes_won else 1.0) - signal.ask_no - fee

    if timing_approx is None:
        timing_approx = bool(getattr(candidate, "timing_approx", False))
    if bid_src is None:
        bid_src = getattr(candidate, "bid_src", None)

    rec = {
        "combo_id": candidate.combo_id,
        "ts": candidate.now_ts,
        "n_legs": len(candidate.legs),
        "bid_yes": candidate.bid_yes,
        "bid_src": bid_src,
        "ask_no": signal.ask_no,
        "p_true_yes": signal.p_true_yes,
        "p_cons_yes": signal.p_cons_yes,
        "roi_no_taker": signal.roi_no_taker,
        "fires": signal.fires,
        "reasons": list(signal.reasons),
        "has_rotten_leg": signal.has_rotten_leg,
        "is_cannibalistic": signal.is_cannibalistic,
        "timing_approx": timing_approx,
        "yes_won": yes_won,
        "pnl_per_contract": pnl_per_contract,
    }

    if guard is not None:
        if signal.fires:
            liability = min(15.0, signal.ask_no * 100.0)
            candidate.stake = liability
            admitted = guard.admit(candidate, signal)
            rec["admitted"] = admitted
        else:
            rec["admitted"] = False

    return rec


# Evaluable-denominator exclusions (RUL-20261119-closeout-plan): combos carrying a leg
# from these series are graded as their own class and EXCLUDED from the evaluable
# denominator, because the stack has no sportsbook panel for them (NBA 1Q props,
# NCAAF game combos). The flag is explicit on every record so no consumer can
# silently count them in or out.
DENOMINATOR_EXCLUDED_SERIES = ("KXNBA1Q", "KXNCAAFGAME")


def denominator_exclusion(candidate: Any) -> Optional[str]:
    """Return the excluded series prefix if any leg belongs to one, else None."""
    legs = getattr(candidate, "legs", None) or []
    for leg in legs:
        t = str(getattr(leg, "ticker", "") or "").upper()
        for prefix in DENOMINATOR_EXCLUDED_SERIES:
            if t.startswith(prefix):
                return prefix
    return None


def _meta_leg_exclusion(meta: dict) -> Optional[str]:
    """Same check for unevaluated items, whose legs live in the meta dict as
    [ticker, side] pairs or dicts with a market_ticker."""
    for leg in meta.get("legs", []) or []:
        t = ""
        if isinstance(leg, (list, tuple)) and leg:
            t = str(leg[0] or "")
        elif isinstance(leg, dict):
            t = str(leg.get("market_ticker") or leg.get("ticker") or "")
        t = t.upper()
        for prefix in DENOMINATOR_EXCLUDED_SERIES:
            if t.startswith(prefix):
                return prefix
    return None


def run_candidates(
    candidates_with_meta: Sequence[Any],
    guard: Optional[ComboLiabilityGuard] = None,
    window2_admission: Optional[Window2Admission] = None,
) -> Tuple[List[dict], dict]:
    """Evaluate candidates through the RFQ taker replay harness.

    Optionally runs Window 2 admission engine on fired candidates when
    window2_admission is provided.

    Merge convention:
    - When window2_admission is None: summary["window2"] is None and records have
      no window2_* keys (preserving legacy behavior).
    - When window2_admission is provided:
        - summary["window2"] is populated with n_fires_considered, n_admitted,
          and per-day admitted counts under "by_day" and "per_day".
        - Fired records processed by Window 2 receive:
            window2_admitted (bool)
            window2_tier (str)
            window2_score (float)
            window2_roi_cal (float)
            window2_rotten_leg_v2 (bool)
            window2_refusals (list)
        - Non-fired records and records for days not processed receive:
            window2_admitted: None
            window2_tier: None
            window2_score: None
            window2_roi_cal: None
            window2_rotten_leg_v2: None
            window2_refusals: []
    """
    if guard is None:
        guard = ComboLiabilityGuard()

    records: List[dict] = []
    n_evaluated = 0
    n_fires = 0
    n_admitted = 0
    reasons_hist: Dict[str, int] = {}
    denom_hist: Dict[str, int] = {}
    fired_candidates: List[Tuple[ComboCandidate, Any]] = []

    for item in candidates_with_meta:
        if isinstance(item, tuple) and len(item) == 2:
            cand, meta = item
        elif isinstance(item, ComboCandidate):
            cand = item
            meta = {}
        elif isinstance(item, dict):
            cand = item.get("candidate")
            meta = item
        else:
            cand = None
            meta = {}

        if cand is None:
            status = meta.get("status", "UNEVALUATED")
            rec = {
                "combo_id": meta.get("combo_id", ""),
                "ts": meta.get("ts", 0.0),
                "n_legs": len(meta.get("legs", [])),
                "bid_yes": meta.get("bid_yes"),
                "bid_src": meta.get("bid_src"),
                "ask_no": (1.0 - meta["bid_yes"]) if meta.get("bid_yes") is not None else None,
                "p_true_yes": None,
                "p_cons_yes": None,
                "roi_no_taker": None,
                "fires": False,
                "reasons": [status],
                "has_rotten_leg": False,
                "is_cannibalistic": False,
                "timing_approx": meta.get("timing_approx", False),
                "yes_won": meta.get("yes_won"),
                "pnl_per_contract": None,
                "admitted": False,
                "status": status,
                "day": meta.get("day"),
            }
            excl = _meta_leg_exclusion(meta)
            rec["denominator_excluded"] = excl is not None
            rec["denominator_exclusion"] = excl
            if excl is not None:
                denom_hist[excl] = denom_hist.get(excl, 0) + 1
            records.append(rec)
            continue

        n_evaluated += 1
        yes_won = meta.get("yes_won")
        bid_src = meta.get("bid_src", getattr(cand, "bid_src", None))
        timing_approx = meta.get("timing_approx", getattr(cand, "timing_approx", False))
        day = meta.get("day")

        rec = evaluate_taker_combo(
            cand,
            yes_won=yes_won,
            guard=guard,
            bid_src=bid_src,
            timing_approx=timing_approx,
        )
        rec["day"] = day
        rec["status"] = meta.get("status", "OK")
        excl = denominator_exclusion(cand)
        rec["denominator_excluded"] = excl is not None
        rec["denominator_exclusion"] = excl
        if excl is not None:
            denom_hist[excl] = denom_hist.get(excl, 0) + 1

        for r in rec["reasons"]:
            reasons_hist[r] = reasons_hist.get(r, 0) + 1

        if rec["fires"]:
            n_fires += 1
            if rec.get("admitted"):
                n_admitted += 1
            fired_candidates.append((cand, day))
        else:
            rec["admitted"] = False

        records.append(rec)

    admitted_graded = [
        r for r in records
        if r.get("admitted") and r.get("yes_won") is not None and r.get("pnl_per_contract") is not None
    ]
    pnls = [float(r["pnl_per_contract"]) for r in admitted_graded]
    mean_pnl = round(sum(pnls) / len(pnls), 4) if pnls else None

    by_day_dict: Dict[str, List[float]] = {}
    for r in admitted_graded:
        d = str(r.get("day") or "unknown")
        by_day_dict.setdefault(d, []).append(float(r["pnl_per_contract"]))

    by_day_stats = {
        d: {
            "n": len(vals),
            "mean_pnl_per_contract": round(sum(vals) / len(vals), 4),
        }
        for d, vals in sorted(by_day_dict.items())
    }

    w2_summary: Optional[Dict[str, Any]] = None
    if window2_admission is not None:
        # Group FIRED candidates by their meta "day" (records/candidates with day=None group under "unknown")
        fires_by_day: Dict[str, List[ComboCandidate]] = collections.defaultdict(list)
        for cand, d_val in fired_candidates:
            d_key = "unknown" if (d_val is None or d_val == "") else str(d_val)
            fires_by_day[d_key].append(cand)

        all_decisions: Dict[str, Any] = {}
        w2_by_day: Dict[str, int] = {}
        n_w2_fires_considered = 0
        n_w2_admitted = 0

        for d_key in sorted(fires_by_day.keys()):
            cands_in_day = fires_by_day[d_key]
            # Stress inputs: the tape/settled fixtures carry no per-leg stress inputs,
            # so pass stress=() (rotten_leg_v2 handles missing entries with LegStressInputs defaults).
            fires = [
                Window2Fire(
                    candidate=c,
                    signal=evaluate_combo(c),
                    stress=(),
                    day=d_key,
                )
                for c in cands_in_day
            ]
            day_decisions = window2_admission.run_day(fires, d_key)
            n_w2_fires_considered += len(fires)
            day_admitted = 0
            for dec in day_decisions:
                all_decisions[dec.combo_id] = dec
                if dec.admitted:
                    day_admitted += 1
                    n_w2_admitted += 1
            w2_by_day[d_key] = day_admitted

        # Merge decisions back into harness records with matching combo_id
        for r in records:
            cid = r.get("combo_id")
            if cid in all_decisions:
                dec = all_decisions[cid]
                r["window2_admitted"] = bool(dec.admitted)
                r["window2_tier"] = dec.tier
                r["window2_score"] = float(dec.score)
                r["window2_roi_cal"] = float(dec.roi_cal)
                r["window2_rotten_leg_v2"] = bool(dec.rotten_leg_v2)
                r["window2_refusals"] = list(dec.refusals)
            else:
                r["window2_admitted"] = None
                r["window2_tier"] = None
                r["window2_score"] = None
                r["window2_roi_cal"] = None
                r["window2_rotten_leg_v2"] = None
                r["window2_refusals"] = []

        w2_summary = {
            "n_fires_considered": n_w2_fires_considered,
            "n_admitted": n_w2_admitted,
            "by_day": w2_by_day,
            "per_day": w2_by_day,
        }

    # Dual-Ledger A/B grading (RUL-20261119-closeout-plan): the same fires graded
    # three ways, side by side, never blended. Ledger A = every fire, cap-free.
    # Ledger B = the frozen Window 1 FIFO liability guard's admitted set.
    # Ledger W2 = the Window 2 shadow portfolio's admitted set (None when the
    # Window 2 engine was not run). pnl_per_contract is computed pre-guard for
    # every fire with a known outcome, so all three ledgers grade identical rows.
    def _ledger(rows: List[dict]) -> dict:
        vals = [float(r["pnl_per_contract"]) for r in rows if r.get("pnl_per_contract") is not None]
        return {
            "n": len(vals),
            "mean_pnl_per_contract": round(sum(vals) / len(vals), 4) if vals else None,
        }

    ledgers = {
        "A_cap_free": _ledger([r for r in records if r.get("fires")]),
        "B_window1_fifo": _ledger([r for r in records if r.get("admitted")]),
        "W2_window2_shadow": (
            _ledger([r for r in records if r.get("window2_admitted")])
            if window2_admission is not None else None
        ),
    }

    summary = {
        "n_total": len(candidates_with_meta),
        "n_evaluated": n_evaluated,
        "n_fires": n_fires,
        "n_admitted": n_admitted,
        "reasons": dict(sorted(reasons_hist.items(), key=lambda x: -x[1])),
        "graded_stats": {
            "n": len(admitted_graded),
            "mean_pnl_per_contract": mean_pnl,
            "by_day": by_day_stats,
        },
        "ledgers": ledgers,
        "denominator": {
            "n_records_excluded": sum(denom_hist.values()),
            "by_series": dict(sorted(denom_hist.items())),
            "note": "Excluded-series records are graded as their own class and are NOT part of the evaluable denominator (RUL-20261119).",
        },
        "note": "Rows share legs/games across combos (clustered; no independence claim).",
        "window2": w2_summary,
    }

    return records, summary


def load_tape_records(tape_dir: str) -> List[dict]:
    rfqs: List[dict] = []
    followups: Dict[str, List[dict]] = {}

    pattern = os.path.join(tape_dir, "**", "*.jsonl")
    for path in sorted(glob.glob(pattern, recursive=True)):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    obj = json.loads(line_str)
                except Exception:
                    continue
                kind = obj.get("kind")
                if kind == "rfq":
                    rfqs.append(obj)
                elif kind == "followup":
                    rfq_id = obj.get("rfq_id")
                    if rfq_id:
                        followups.setdefault(rfq_id, []).append(obj)

    records: List[dict] = []
    for rfq_obj in rfqs:
        rfq = rfq_obj.get("rfq") or {}
        legs_raw = rfq.get("mve_selected_legs") or rfq_obj.get("mve_selected_legs") or []
        if len(legs_raw) < 2:
            continue

        rfq_id = rfq.get("id") or rfq_obj.get("id")
        created = rfq.get("created_ts") or rfq_obj.get("created_ts")
        flist = followups.get(rfq_id, [])
        flist.sort(key=lambda x: float(x.get("ts", 0.0)))

        bid_yes: Optional[float] = None
        for f in flist:
            trades = f.get("trades") or []
            if trades:
                first_trade = trades[0]
                yp = first_trade.get("yes_price_dollars")
                if yp is not None:
                    bid_yes = float(yp)
                    break

        bid_src = "FOLLOWUP_TRADED_YES_PRICE" if bid_yes is not None else None
        legs = [
            (str(l.get("market_ticker") or l.get("ticker")), str(l.get("side", "yes")))
            for l in legs_raw
        ]

        rec = {
            "combo_id": rfq_id,
            "ts": float(rfq_obj.get("ts", 0.0)),
            "created": created,
            "legs": legs,
            "bid_yes": bid_yes,
            "bid_src": bid_src,
        }
        if bid_yes is None:
            rec["status"] = "NO_EXECUTABLE_PRICE"
        records.append(rec)

    records.sort(key=lambda x: float(x.get("ts", 0.0)))
    return records


def settled_to_fixture(combo: dict) -> dict:
    created = combo.get("created_time")
    t = epoch(created) if created else 0.0
    last_price = combo.get("last_price_dollars")
    bid_yes = float(last_price) if last_price is not None else None
    close = str(combo.get("close_time") or "")
    legs = [
        (str(l[0]), str(l[1]))
        for l in combo.get("legs", [])
    ]
    return {
        "combo_id": combo.get("ticker", ""),
        "ts": t,
        "legs": legs,
        "bid_yes": bid_yes,
        "bid_src": "TRADED_YES_PRICE",
        "yes_won": combo.get("result") == "yes",
        "day": close[:10] if close else "",
    }
