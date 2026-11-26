"""Kalshi RFQ ingest filter (ratified RUL-20261125 Neon package).

Pure, deterministic, zero-I/O, zero-network classification, streaming reconciliation,
deterministic 1-in-N audit sampling, and RAM-tiered storage decision helpers.

Window-1 mode is SHADOW: every record is kept at full fidelity (zero records dropped)
with mutually exclusive `primary_class`, overlap `flag_bitmask` / `flag_names`, and
post-waterfall `disposition` stamped for exact reconciliation.
"""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

# SHA-256 of the empty string; Kalshi's obscure_creator_id anonymous bucket.
EMPTY_HASH_CREATOR = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

# Exempt from spam counting AND creator-combo dedup. Extra batch-placeholder IDs can be
# supplied via FilterConfig.extra_exempt_creator_ids without a code change.
EXEMPT_CREATOR_IDS: frozenset[str] = frozenset({EMPTY_HASH_CREATOR})

DAILY_FINANCIAL_SERIES: frozenset[str] = frozenset(
    {"KXBTCD", "KXETHD", "KXSOLD", "KXXRPD", "KXDOGED"}
)

# Worst-leg book age seconds -> (upper_bound_exclusive, weight).
TAPER_BUCKETS: tuple[tuple[float, float], ...] = (
    (900.0, 1.0),
    (1800.0, 0.5),
    (2700.0, 0.25),
    (float("inf"), 0.0),
)
BASE_FRACTIONAL_KELLY = 0.25
STALE_ZERO_AGE_SEC = 2700
BOUNDED_CAP = 3

SPAM_WINDOW_SEC = 600.0
SPAM_LOOKBACK_SEC = 1200.0
SPAM_THRESHOLD = 60

TIER1_RAM_MB = 350.0
TIER2_RAM_MB = 420.0

AUDIT_SAMPLE_N: dict[str, int] = {
    "EXCLUDED_SERIES": 20,
    "PREVIEW_10": 20,
    "SPAM_CREATOR": 10,
    "NON_SPORTS": 50,
    "UNMAPPED_ALL_LEGS": 20,
}

# Bitmask flags: every flag matched by a record is recorded here so overlap cells
# (e.g. PREVIEW_10 & EXCLUDED_SERIES) are preserved independently of primary_class.
FLAG_BITS: dict[str, int] = {
    "MALFORMED": 1 << 0,
    "NON_COMBO": 1 << 1,
    "EXCLUDED_SERIES": 1 << 2,
    "PREVIEW_10": 1 << 3,
    "SPAM_CREATOR": 1 << 4,
    "NON_SPORTS": 1 << 5,
    "UNMAPPED_ANY": 1 << 6,
    "UNMAPPED_ALL": 1 << 7,
    "DUP_COMBO": 1 << 8,
    "EMPTY_HASH_CREATOR": 1 << 9,
    "BATCH_EXEMPT": 1 << 10,
    "SGP_CORRELATED": 1 << 11,
    "MICRO_COST": 1 << 12,
    "SHADOW_ELIGIBLE": 1 << 13,
}

PRIMARY_DENOMINATOR = "DENOMINATOR"
PRIMARY_EX_MALFORMED = "EX_MALFORMED"
PRIMARY_EX_EXCLUDED_SERIES = "EX_EXCLUDED_SERIES"
PRIMARY_EX_PREVIEW_10 = "EX_PREVIEW_10"
PRIMARY_EX_SPAM_CREATOR = "EX_SPAM_CREATOR"


def is_non_sports_series(series: str | None) -> bool:
    """Census class-5 predicate: 15-minute or daily crypto/commodity/FX series."""
    s = str(series or "")
    return s.endswith("15M") or s in DAILY_FINANCIAL_SERIES


def taper_weight(age_sec: float | None) -> float | None:
    """Worst-leg book age seconds -> Kelly taper weight, or None when age is unknown."""
    if age_sec is None:
        return None
    age = float(age_sec)
    for upper, weight in TAPER_BUCKETS:
        if age < upper:
            return weight
    return 0.0


def flag_names(mask: int) -> list[str]:
    """Ordered list of flag names whose bits are set in `mask`."""
    return [name for name, bit in FLAG_BITS.items() if mask & bit]


class FilterMode(str, Enum):
    WINDOW1_SHADOW = "WINDOW1_SHADOW"
    TIER1_SHED = "TIER1_SHED"
    TIER2_SHED = "TIER2_SHED"


@dataclass(frozen=True)
class FilterConfig:
    shed_armed: bool = False
    extra_exempt_creator_ids: frozenset[str] = frozenset()


def mode_for_ram(ram_mb: float | None, armed: bool) -> FilterMode:
    """Select active filter mode from RSS MB and whether shedding is armed."""
    if not armed or ram_mb is None:
        return FilterMode.WINDOW1_SHADOW
    if ram_mb >= TIER2_RAM_MB:
        return FilterMode.TIER2_SHED
    if ram_mb >= TIER1_RAM_MB:
        return FilterMode.TIER1_SHED
    return FilterMode.WINDOW1_SHADOW


class AuditSampler:
    """Deterministic 1-in-N audit sampler over rfq_id.

    Uses the first 8 hex chars (32 bits) of SHA-256(rfq_id) modulo N == 0.
    Truncation to 32 bits yields 2^32 uniform buckets with negligible modulo bias
    (<= 2.3e-8 for N <= 100) while staying stateless and identical across processes.
    """

    def __init__(self, sample_ns: dict[str, int] | None = None):
        self.sample_ns = dict(AUDIT_SAMPLE_N if sample_ns is None else sample_ns)

    def _resolve_n(self, class_name: str) -> int | None:
        if class_name in self.sample_ns:
            return self.sample_ns[class_name]
        stripped = class_name.removeprefix("EX_")
        if stripped in self.sample_ns:
            return self.sample_ns[stripped]
        if class_name == "UNMAPPED_ALL":
            return self.sample_ns.get("UNMAPPED_ALL_LEGS")
        if class_name in ("UNMAPPED_PARTIAL", "UNMAPPED_PARTIAL_OVER_CAP"):
            return self.sample_ns.get("UNMAPPED_PARTIAL", 20)
        return None

    def sampled(self, rfq_id: str, class_name: str) -> bool:
        if not rfq_id:
            return False
        n = self._resolve_n(class_name)
        if n is None or n <= 0:
            return False
        digest = hashlib.sha256(str(rfq_id).encode("utf-8")).hexdigest()[:8]
        return (int(digest, 16) % n) == 0


def decide_storage(
    classification: dict,
    mode: FilterMode,
    sampler: AuditSampler,
    *,
    rfq_id: str | None = None,
    n_unpriced: int | None = None,
) -> str:
    """Return 'FULL' | 'SLIM' | 'COUNTER_ONLY' for a classified record under `mode`."""
    if mode == FilterMode.WINDOW1_SHADOW:
        return "FULL"

    primary = str(classification.get("primary_class") or "")
    flags = set(classification.get("flag_names") or ())
    unmapped_class = classification.get("unmapped_class")
    rid = rfq_id if rfq_id is not None else str(classification.get("rfq_id") or "")

    unpriced = n_unpriced if n_unpriced is not None else classification.get("n_unpriced")
    if unpriced is None and isinstance(classification.get("notes"), dict):
        unpriced = classification["notes"].get("n_unpriced")
    over_cap = (
        (unpriced is not None and int(unpriced) > BOUNDED_CAP)
        or classification.get("disposition") == "FIRE_BOUNDED_OVER_CAP"
    )

    if primary == "EX_NON_COMBO" or "NON_COMBO" in flags:
        return "COUNTER_ONLY"

    if primary != PRIMARY_DENOMINATOR:
        return "FULL" if sampler.sampled(rid, primary) else "COUNTER_ONLY"

    if unmapped_class == "all" or "UNMAPPED_ALL" in flags:
        return "FULL" if sampler.sampled(rid, "UNMAPPED_ALL_LEGS") else "COUNTER_ONLY"

    if mode == FilterMode.TIER2_SHED and unmapped_class == "partial" and over_cap:
        return "FULL" if sampler.sampled(rid, "UNMAPPED_PARTIAL") else "COUNTER_ONLY"

    return "FULL"


@dataclass
class ReconciliationLedger:
    """Dual-accounting ledger: mutually exclusive primary partition + overlap bitmask histogram."""

    observed: int = 0
    denom_survivors: int = 0
    by_primary_class: dict[str, int] = field(default_factory=dict)
    by_disposition: dict[str, int] = field(default_factory=dict)
    bitmask_histogram: dict[int, int] = field(default_factory=dict)
    unmapped_all_by_series: dict[str, int] = field(default_factory=dict)
    dup_records: int = 0
    dup_fires: int = 0
    admitted_raw: int = 0
    admitted_deduped: int = 0
    admitted_tier_a: int = 0
    admitted_tier_b: int = 0
    over_cap: int = 0
    nonstrict: int = 0
    stale_zero: int = 0
    not_fired: int = 0

    def record(self, stamp: dict, leg_series: tuple[str, ...] | list[str] = ()) -> None:
        self.observed += 1
        primary = str(stamp["primary_class"])
        disp = str(stamp["disposition"])
        mask = int(stamp["flag_bitmask"])

        self.by_primary_class[primary] = self.by_primary_class.get(primary, 0) + 1
        self.by_disposition[disp] = self.by_disposition.get(disp, 0) + 1
        self.bitmask_histogram[mask] = self.bitmask_histogram.get(mask, 0) + 1

        if stamp.get("unmapped_class") == "all":
            for series in sorted({s for s in leg_series if s}):
                self.unmapped_all_by_series[series] = (
                    self.unmapped_all_by_series.get(series, 0) + 1
                )

        is_dup = bool(stamp.get("is_dup_creator_combo"))
        if is_dup:
            self.dup_records += 1

        if primary == PRIMARY_DENOMINATOR:
            self.denom_survivors += 1
            if disp == "ADMITTED_FIRE":
                self.admitted_raw += 1
                if not is_dup:
                    self.admitted_deduped += 1
                else:
                    self.dup_fires += 1
                if stamp.get("fire_tier") == "A":
                    self.admitted_tier_a += 1
                elif stamp.get("fire_tier") == "B":
                    self.admitted_tier_b += 1
            elif disp == "FIRE_BOUNDED_OVER_CAP":
                self.over_cap += 1
                if is_dup:
                    self.dup_fires += 1
            elif disp == "FIRE_BOUNDED_NONSTRICT":
                self.nonstrict += 1
                if is_dup:
                    self.dup_fires += 1
            elif disp == "FIRE_STALE_ZERO_SIZE":
                self.stale_zero += 1
                if is_dup:
                    self.dup_fires += 1
            elif disp == "DENOM_NOT_FIRED":
                self.not_fired += 1

    def assert_reconciles(self) -> bool:
        primary_sum = sum(self.by_primary_class.values())
        if self.observed != primary_sum:
            raise AssertionError(
                f"primary partition mismatch: observed={self.observed} != sum(primary)={primary_sum}"
            )
        denom_sum = (
            self.admitted_raw
            + self.over_cap
            + self.nonstrict
            + self.stale_zero
            + self.not_fired
        )
        if self.denom_survivors != denom_sum:
            raise AssertionError(
                f"denominator partition mismatch: denom_survivors={self.denom_survivors} != sum={denom_sum}"
            )
        return True

    def summary(self) -> dict:
        reconciles = True
        try:
            self.assert_reconciles()
        except AssertionError:
            reconciles = False
        return {
            "observed": self.observed,
            "denom_survivors": self.denom_survivors,
            "by_primary_class": dict(self.by_primary_class),
            "by_disposition": dict(self.by_disposition),
            "bitmask_histogram": {str(k): v for k, v in sorted(self.bitmask_histogram.items())},
            "unmapped_all_by_series": dict(sorted(self.unmapped_all_by_series.items())),
            "dup_records": self.dup_records,
            "dup_fires": self.dup_fires,
            "admitted_raw": self.admitted_raw,
            "admitted_deduped": self.admitted_deduped,
            "admitted_tier_a": self.admitted_tier_a,
            "admitted_tier_b": self.admitted_tier_b,
            "over_cap": self.over_cap,
            "nonstrict": self.nonstrict,
            "stale_zero": self.stale_zero,
            "not_fired": self.not_fired,
            "reconciles": reconciles,
        }


@dataclass
class RFQFilterState:
    """Streaming state for spam velocity windows, creator-combo dedup, and reconciliation."""

    creator_ts: dict[str, deque[float]] = field(default_factory=dict)
    seen_combos: dict[tuple[str, str], str] = field(default_factory=dict)
    ledger: ReconciliationLedger = field(default_factory=ReconciliationLedger)


def _extract_rfq_id(record: dict) -> str:
    rfq = record.get("rfq") if isinstance(record.get("rfq"), dict) else {}
    rid = record.get("rfq_id") or rfq.get("id") or rfq.get("rfq_id") or ""
    return str(rid).strip()


def _extract_raw_legs(record: dict) -> tuple[list[dict] | None, bool]:
    """Return (raw_legs_list_or_None, legs_parseable)."""
    rfq_val = record.get("rfq")
    if rfq_val is not None and not isinstance(rfq_val, dict):
        return None, False
    rfq = rfq_val or {}

    has_raw = False
    raw_legs = None
    if "mve_selected_legs" in rfq and rfq["mve_selected_legs"] is not None:
        raw_legs = rfq["mve_selected_legs"]
        has_raw = True
    elif "legs" in rfq and rfq["legs"] is not None:
        raw_legs = rfq["legs"]
        has_raw = True

    if has_raw:
        if not isinstance(raw_legs, list) or any(not isinstance(x, dict) for x in raw_legs):
            return None, False

    dlegs = record.get("legs")
    if dlegs is not None:
        if not isinstance(dlegs, list) or any(not isinstance(x, dict) for x in dlegs):
            return None, False

    if raw_legs is None and dlegs is None and record.get("n_legs") is None:
        return None, False

    return raw_legs, True


def _leg_series_from_record(record: dict, raw_legs: list[dict] | None) -> list[str]:
    """Extract leg series prefixes from raw rfq legs, falling back to record.legs[]."""
    series: list[str] = []
    if raw_legs:
        for leg in raw_legs:
            tk = str(leg.get("market_ticker") or leg.get("ticker") or "")
            if tk:
                series.append(tk.split("-", 1)[0])
        if series:
            return series
    for leg in record.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        s = leg.get("series")
        if s:
            series.append(str(s))
        else:
            tk = str(leg.get("ticker") or leg.get("market_ticker") or "")
            if tk:
                series.append(tk.split("-", 1)[0])
    if series:
        return series
    notes = record.get("notes") if isinstance(record.get("notes"), dict) else {}
    for item in notes.get("legs_status") or []:
        if isinstance(item, dict) and item.get("ticker"):
            series.append(str(item["ticker"]).split("-", 1)[0])
    return series


def _compute_fallback_combo_id(record: dict, raw_legs: list[dict] | None) -> str:
    if raw_legs:
        pairs = sorted(
            f"{str(l.get('market_ticker') or l.get('ticker') or '')}|{str(l.get('side') or '').lower()}"
            for l in raw_legs
        )
        return hashlib.sha256(",".join(pairs).encode("ascii", "replace")).hexdigest()
    dlegs = record.get("legs") or []
    if dlegs:
        pairs = sorted(
            f"{str(l.get('ticker') or l.get('market_ticker') or '')}|{str(l.get('side') or '').lower()}"
            for l in dlegs
            if isinstance(l, dict)
        )
        return hashlib.sha256(",".join(pairs).encode("ascii", "replace")).hexdigest()
    rfq = record.get("rfq") if isinstance(record.get("rfq"), dict) else {}
    mt = str(rfq.get("market_ticker") or record.get("market_ticker") or "")
    if not mt:
        return ""
    return hashlib.sha256(mt.encode("ascii", "replace")).hexdigest()


def _update_spam_state(state: RFQFilterState, creator_id: str, ts: float) -> bool:
    """Ratified two-window spam check mirroring RFQTape._creator_state."""
    dq = state.creator_ts.setdefault(creator_id, deque())
    dq.append(ts)
    while dq and dq[0] < ts - SPAM_LOOKBACK_SEC:
        dq.popleft()
    count_10m = sum(1 for t in dq if ts - SPAM_WINDOW_SEC <= t <= ts)
    count_prev = sum(1 for t in dq if ts - SPAM_LOOKBACK_SEC <= t < ts - SPAM_WINDOW_SEC)
    return count_prev >= SPAM_THRESHOLD and count_10m >= SPAM_THRESHOLD


def _record_ts(record: dict) -> float:
    raw_ts = record.get("ts")
    if raw_ts is None:
        raw_ts = record.get("created_ts")
    try:
        return float(raw_ts) if raw_ts is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def classify_rfq_record(
    record: dict,
    state: RFQFilterState,
    cfg: FilterConfig | None = None,
) -> dict:
    """Classify one RFQ tape record in RUL-20261125 Section 5 waterfall order."""
    cfg = cfg or FilterConfig()
    exempt_ids = EXEMPT_CREATOR_IDS | frozenset(cfg.extra_exempt_creator_ids or ())

    mask = 0
    rfq_id = _extract_rfq_id(record) if isinstance(record, dict) else ""
    raw_legs, legs_ok = _extract_raw_legs(record) if isinstance(record, dict) else (None, False)

    # Stage 0: MALFORMED
    if not isinstance(record, dict) or not rfq_id or not legs_ok:
        mask |= FLAG_BITS["MALFORMED"]
        stamp = {
            "primary_class": PRIMARY_EX_MALFORMED,
            "flag_bitmask": mask,
            "flag_names": flag_names(mask),
            "disposition": PRIMARY_EX_MALFORMED,
            "fire_tier": None,
            "taper_weight": None,
            "worst_leg_age_sec": None,
            "is_dup_creator_combo": False,
            "dup_group_id": None,
            "unmapped_class": "none",
            "pricer_short_circuit": False,
            "strict_bound": None,
            "shadow_eligible": False,
        }
        state.ledger.record(stamp)
        return stamp

    dlegs = record.get("legs") or []
    notes = record.get("notes") if isinstance(record.get("notes"), dict) else {}

    # Creator identity + exemptions
    rfq_obj = record.get("rfq") if isinstance(record.get("rfq"), dict) else {}
    creator_raw = record.get("creator_id")
    if creator_raw is None:
        creator_raw = rfq_obj.get("creator_id")
    creator_id = str(creator_raw) if creator_raw else ""
    is_empty_hash = creator_id == EMPTY_HASH_CREATOR
    is_exempt = bool(creator_id and creator_id in exempt_ids)
    if is_empty_hash:
        mask |= FLAG_BITS["EMPTY_HASH_CREATOR"]
    if is_exempt:
        mask |= FLAG_BITS["BATCH_EXEMPT"]

    # Stage 1: NON_COMBO (shadow tag only in Window-1)
    if raw_legs:
        n_legs = len(raw_legs)
    elif record.get("n_legs") is not None:
        n_legs = int(record["n_legs"])
    else:
        n_legs = len(dlegs)
    non_combo = n_legs < 2
    if non_combo:
        mask |= FLAG_BITS["NON_COMBO"]

    # Stage 2: EXCLUDED_SERIES (always set from banked excluded_series_class)
    exc_class = record.get("excluded_series_class")
    is_excluded_series = exc_class is not None and str(exc_class) != "none"
    if is_excluded_series:
        mask |= FLAG_BITS["EXCLUDED_SERIES"]

    # Stage 3: Spam state update (side effect, no exit; exempt creators bypass)
    spam_flagged = False
    if creator_id and not is_exempt:
        spam_flagged = _update_spam_state(state, creator_id, _record_ts(record))

    # Stage 4: PREVIEW_10 (keyed on banked preview_10 flag ONLY)
    is_preview_10 = bool(record.get("preview_10"))
    if is_preview_10:
        mask |= FLAG_BITS["PREVIEW_10"]

    # Stage 5: SPAM_CREATOR
    if spam_flagged:
        mask |= FLAG_BITS["SPAM_CREATOR"]

    # Primary partition in stage order 0, 2, 4, 5; else DENOMINATOR
    if is_excluded_series:
        primary_class = PRIMARY_EX_EXCLUDED_SERIES
    elif is_preview_10:
        primary_class = PRIMARY_EX_PREVIEW_10
    elif spam_flagged:
        primary_class = PRIMARY_EX_SPAM_CREATOR
    else:
        primary_class = PRIMARY_DENOMINATOR

    # Stage 6: NON_SPORTS bit (tag only in Window-1)
    leg_series = _leg_series_from_record(record, raw_legs)
    is_non_sports = any(is_non_sports_series(s) for s in leg_series)
    if is_non_sports:
        mask |= FLAG_BITS["NON_SPORTS"]

    # Stage 7: UNMAPPED from notes.legs_status
    legs_status = notes.get("legs_status") if isinstance(notes.get("legs_status"), list) else []
    statuses = [item.get("status") for item in legs_status if isinstance(item, dict)]
    unmapped_any = any(st == "REJ_UNMAPPED_TICKER" for st in statuses)
    unmapped_all = bool(statuses) and all(st == "REJ_UNMAPPED_TICKER" for st in statuses)
    if unmapped_any:
        mask |= FLAG_BITS["UNMAPPED_ANY"]
    if unmapped_all:
        mask |= FLAG_BITS["UNMAPPED_ALL"]
        unmapped_class = "all"
    elif unmapped_any:
        unmapped_class = "partial"
    else:
        unmapped_class = "none"

    pricer_short_circuit = primary_class == PRIMARY_DENOMINATOR and unmapped_class == "all"

    # Stage 8: DUP (creator_id, combo_id); exempt creators bypass
    combo_id = str(record.get("combo_id") or "") or _compute_fallback_combo_id(record, raw_legs)
    is_dup = False
    dup_group_id: str | None = None
    if creator_id and combo_id and not is_exempt:
        key = (creator_id, combo_id)
        if key in state.seen_combos:
            is_dup = True
            dup_group_id = combo_id
            mask |= FLAG_BITS["DUP_COMBO"]
        else:
            state.seen_combos[key] = rfq_id

    # Non-excluding telemetry bits
    game_counts: dict[str, int] = {}
    ages: list[float] = []
    for leg in dlegs:
        gk = leg.get("game_key")
        if gk:
            gk_s = str(gk)
            game_counts[gk_s] = game_counts.get(gk_s, 0) + 1
        age = leg.get("age_sec")
        if age is not None:
            ages.append(float(age))
    if any(c >= 2 for c in game_counts.values()):
        mask |= FLAG_BITS["SGP_CORRELATED"]

    tcost = record.get("target_cost_dollars_num")
    if tcost is not None and float(tcost) < 10.0:
        mask |= FLAG_BITS["MICRO_COST"]

    shadow_eligible = (not non_combo) and (not is_non_sports) and unmapped_any
    if shadow_eligible:
        mask |= FLAG_BITS["SHADOW_ELIGIBLE"]

    worst_leg_age_sec = max(ages) if ages else None

    # Stage 9: Disposition
    fire_tier: str | None = None
    tw: float | None = None
    strict_bound: bool | None = None

    if primary_class != PRIMARY_DENOMINATOR:
        disposition = primary_class
    elif not record.get("is_fire"):
        disposition = "DENOM_NOT_FIRED"
    else:
        raw_n_unpriced = notes.get("n_unpriced")
        if raw_n_unpriced is not None:
            n_unpriced = int(raw_n_unpriced)
        elif statuses:
            n_unpriced = sum(1 for st in statuses if st != "OK")
        else:
            n_unpriced = 0

        strict_bound = all(
            leg.get("pricing_source") == "book" and not leg.get("assumed_leg")
            for leg in dlegs
        )
        tw = taper_weight(worst_leg_age_sec)

        if n_unpriced >= BOUNDED_CAP + 1:
            disposition = "FIRE_BOUNDED_OVER_CAP"
        elif 1 <= n_unpriced <= BOUNDED_CAP and not strict_bound:
            disposition = "FIRE_BOUNDED_NONSTRICT"
        elif worst_leg_age_sec is not None and worst_leg_age_sec >= STALE_ZERO_AGE_SEC:
            disposition = "FIRE_STALE_ZERO_SIZE"
        elif n_unpriced == 0:
            disposition = "ADMITTED_FIRE"
            fire_tier = "A"
        else:
            disposition = "ADMITTED_FIRE"
            fire_tier = "B"

    stamp = {
        "primary_class": primary_class,
        "flag_bitmask": mask,
        "flag_names": flag_names(mask),
        "disposition": disposition,
        "fire_tier": fire_tier,
        "taper_weight": tw,
        "worst_leg_age_sec": worst_leg_age_sec,
        "is_dup_creator_combo": is_dup,
        "dup_group_id": dup_group_id,
        "unmapped_class": unmapped_class,
        "pricer_short_circuit": pricer_short_circuit,
        "strict_bound": strict_bound,
        "shadow_eligible": shadow_eligible,
    }
    state.ledger.record(stamp, leg_series=leg_series)
    return stamp
