"""
ev.autopilot.evaluate -- score a shadow window against the Phase 0 dual CLV bar
(RUL-20261115-autopilot-clv-definition, restated table).

Population: simulated FILLED rows whose firing time is in the post-Nov-13 regime
(>= 2026-11-13T00:00Z; pre-regime rows are excluded from every bar and reported only
as an annex count, per ruling Q2).  Per market system and pooled:

  volume            n signals (bar on the pooled window: >= 150)
  consensus-confirm net CLV   mean >= +1.50% AND lower 95% CI > +0.50%   (governing magnitude)
  same-book net CLV           mean >= +0.50% AND lower 95% CI >= 0.00%   (non-reversal floor)
  consensus drift             mean >= 0.00pp
  moved-quote check           DIAGNOSTIC ONLY: on the subset whose T-15m quote moved
                              >= 1 tick, same-book net CLV vs the +0.50% / CI >= 0 floor.
                              A failure flags the window (Phase 1 must re-clear it on
                              live paper fills); it does not fail the Phase 0 verdict.

CIs are the repo's game-day-clustered Student-t estimator (ev.novig.clv_gate.
cluster_mean_ci) on per-signal values clustered by ET game day.  Raw (no-drag) CLV
means are reported alongside the ruling's net (drag-subtracted) forms.  Rows whose
close is not banked stay out of the affected metric's n and are counted as coverage
loss, never imputed.

AMENDED SPEC (evaluate_phase0, --amended): RUL-20261116-autopilot-clv-drift replaces
the filled-only >= 0.00pp drift floor for the LIVE window with a two-population
Drift & Dislocation-Retention bar and an exact pass specification:

  Bar 1  volume & span     FILLED n >= 150 across >= 7 game days (per-slice
                           admission at n >= 40 for graduation / holdout)
  Bar 2  consensus-confirm FILLED mean >= +1.50% AND lo CI > +0.50% pooled
                           (> +0.00% per slice)
  Bar 3  same-book         FILLED mean >= +0.50% AND lo CI >= 0.00%
  Bar 4  all-intent drift  ALL_GATE_PASS mean >= -0.50pp, drift from the
                           first gate-clearing sighting (t0) to the T-15m close
  Bar 5  filled drift      FILLED mean >= -1.00pp from t0
  Bar 6  retention         FILLED gross consensus-confirm at close retains
                           >= 0.50 of gross entry dislocation at t0 (pooled)
  Criterion 7 (moved-quote same-book) is DIAGNOSTIC ONLY: the ruling as rendered
  specifies no pass bar for it, and none is invented here.

ALL_GATE_PASS is reconstructed from the ledger itself (gate_pass_population): the
deduplicated first sighting per signal that clears the ruling's Q4 operational
definition -- Novig quote freshness <= 5m, >= 4 anchor books, the tiered median
consensus-age cap, and dispersion within the book-count tier (<= 0.045 at exactly
4 books, <= 0.060 at >= 5).  Its closing consensus comes from
ev.autopilot.closes.attach_consensus_closes over the banked snapshot series (the
live ledger carries closes on FILLED rows only); pass --source to the CLI to
attach them, or score a ledger that already carries closing_fair_prob on gate
rows.  The fill-selection diagnostic (filled drift minus all-intent drift) is
reported on every group.  Fill-rate and slippage bars stay non-gating (Phase 1;
no depth is banked).  Venue classification is reported under the amended >= 80%
move-rate cutoff (hold <= 4.5% retained but unmeasured in Phase 0, so the Dual
Bar governs every Phase 0 slice regardless).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from ev.autopilot import ledger as ledger_io
from ev.autopilot.closes import attach_consensus_closes
from ev.autopilot.config import REGIME_START, ShadowConfig, load_config
from ev.novig.clv_gate import cluster_mean_ci

GROUPS = ("NHL_SOG", "NBA_THREES", "NBA_REB", "NHL_SAVES")


def _utc(ts) -> datetime:
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _stats(rows: list, field: str, cfg: ShadowConfig) -> dict:
    vals = [(r[field], r["game_day"]) for r in rows if r.get(field) is not None]
    ci = cluster_mean_ci([v for v, _d in vals], [d for _v, d in vals], cfg.ci_level)
    return {"n": ci["n"], "n_days": ci["n_clusters"], "mean": ci["mean"],
            "ci_lo": ci["ci_lo"], "ci_hi": ci["ci_hi"], "se": ci["se"]}


def _group_verdict(rows: list, cfg: ShadowConfig, *, pooled: bool) -> dict:
    cons = _stats(rows, "consensus_confirm_clv_net_pct", cfg)
    cons_raw = _stats(rows, "consensus_confirm_clv_pct", cfg)
    sb = _stats(rows, "same_book_clv_net_pct", cfg)
    sb_raw = _stats(rows, "same_book_clv_pct", cfg)
    drift = _stats(rows, "consensus_drift_pp", cfg)
    moved = [r for r in rows if r.get("moved_quote")]
    moved_sb = _stats(moved, "same_book_clv_net_pct", cfg)
    with_t15 = [r for r in rows if r.get("novig_t15_prob") is not None]
    move_rate = (len(moved) / len(with_t15)) if with_t15 else None
    reasons = []
    if cons["mean"] is None or cons["mean"] < cfg.bar_consensus_clv_pct:
        reasons.append(f"consensus-confirm net CLV mean {cons['mean']} < +{cfg.bar_consensus_clv_pct}%")
    if cons["ci_lo"] is None or not cons["ci_lo"] > cfg.bar_consensus_ci_lo_gt:
        reasons.append(f"consensus-confirm lower CI {cons['ci_lo']} not > +{cfg.bar_consensus_ci_lo_gt}%")
    if sb["mean"] is None or sb["mean"] < cfg.bar_same_book_clv_pct:
        reasons.append(f"same-book net CLV mean {sb['mean']} < +{cfg.bar_same_book_clv_pct}%")
    if sb["ci_lo"] is None or not sb["ci_lo"] >= cfg.bar_same_book_ci_lo_ge:
        reasons.append(f"same-book lower CI {sb['ci_lo']} < {cfg.bar_same_book_ci_lo_ge}%")
    if drift["mean"] is None or drift["mean"] < cfg.bar_drift_pp_min:
        reasons.append(f"consensus drift mean {drift['mean']}pp < {cfg.bar_drift_pp_min}pp")
    moved_ok = (moved_sb["mean"] is not None and moved_sb["mean"] >= cfg.bar_same_book_clv_pct
                and moved_sb["ci_lo"] is not None and moved_sb["ci_lo"] >= cfg.bar_same_book_ci_lo_ge)
    out = {"n_signals": len(rows), "n_days": len({r["game_day"] for r in rows}),
           "coverage": {"consensus_clv_n": cons["n"], "same_book_clv_n": sb["n"],
                        "signals_total": len(rows)},
           "consensus_confirm_net": cons, "consensus_confirm_raw": cons_raw,
           "same_book_net": sb, "same_book_raw": sb_raw, "consensus_drift_pp": drift,
           "move_rate": move_rate,
           "venue_class": (None if move_rate is None else
                           ("sticky" if move_rate < cfg.sticky_move_rate_max else "active")),
           "moved_quote_check": {"n": moved_sb["n"], "mean": moved_sb["mean"],
                                 "ci_lo": moved_sb["ci_lo"], "pass": bool(moved_ok),
                                 "note": "diagnostic only; failure flags the window, does not fail Phase 0"},
           "verdict": "FAIL" if reasons else "PASS", "reasons": reasons}
    if pooled:
        out["volume_bar"] = {"n": len(rows), "bar": cfg.bar_volume, "pass": len(rows) >= cfg.bar_volume}
        if len(rows) < cfg.bar_volume:
            out["verdict"] = "FAIL"
            out["reasons"] = [f"volume {len(rows)} < {cfg.bar_volume}"] + reasons
    return out


def evaluate_window(rows: list, cfg: ShadowConfig | None = None) -> dict:
    cfg = cfg or ShadowConfig()
    filled = [r for r in rows if r["status"] == "FILLED"]
    in_regime = [r for r in filled if _utc(r["quote_observed_at"]) >= REGIME_START]
    groups = {}
    for g in GROUPS:
        grows = [r for r in in_regime if r["system"] == g]
        if grows:
            groups[g] = _group_verdict(grows, cfg, pooled=False)
    groups["ALL"] = _group_verdict(in_regime, cfg, pooled=True)
    by_bucket: dict = {}
    for r in in_regime:
        by_bucket.setdefault(r["gates_snapshot"].get("consensus_age_bucket", "unknown"), []).append(r)
    buckets = {b: {"n": len(rs),
                   "consensus_confirm_net_mean": _stats(rs, "consensus_confirm_clv_net_pct", cfg)["mean"],
                   "same_book_net_mean": _stats(rs, "same_book_clv_net_pct", cfg)["mean"],
                   "drift_pp_mean": _stats(rs, "consensus_drift_pp", cfg)["mean"]}
               for b, rs in sorted(by_bucket.items())}
    return {"regime_start": REGIME_START.isoformat(),
            "filled_total": len(filled), "filled_pre_regime_excluded": len(filled) - len(in_regime),
            "groups": groups, "by_consensus_age_bucket": buckets}


def render(res: dict) -> str:
    f = lambda x: "  -" if x is None else f"{x:+.2f}"          # noqa: E731
    lines = [f"Phase 0 dual bar, post-Nov-13 regime (filled total {res['filled_total']}, "
             f"pre-regime excluded {res['filled_pre_regime_excluded']})",
             f"{'group':<12}{'n':>5}{'days':>5}{'consCLV%':>10}{'loCI':>8}{'sbCLV%':>9}{'loCI':>8}"
             f"{'drift':>8}{'move%':>7}  verdict"]
    for g, v in res["groups"].items():
        lines.append(f"{g:<12}{v['n_signals']:>5}{v['n_days']:>5}"
                     f"{f(v['consensus_confirm_net']['mean']):>10}{f(v['consensus_confirm_net']['ci_lo']):>8}"
                     f"{f(v['same_book_net']['mean']):>9}{f(v['same_book_net']['ci_lo']):>8}"
                     f"{f(v['consensus_drift_pp']['mean']):>8}"
                     f"{('  -' if v['move_rate'] is None else format(v['move_rate'] * 100, '.0f') + '%'):>7}"
                     f"  {v['verdict']}"
                     f"  (moved-quote diag n={v['moved_quote_check']['n']} "
                     f"mean={f(v['moved_quote_check']['mean'])} pass={v['moved_quote_check']['pass']})")
        for r in v["reasons"]:
            lines.append(f"    - {r}")
    return "\n".join(lines)


# ------------------------------------------- amended spec (RUL-20261116-autopilot-clv-drift)
def _clears_gate_pass(row: dict, cfg: ShadowConfig) -> bool:
    """The ruling's ALL_GATE_PASS operational definition (Q4), applied to one ledger
    sighting.  The ruling names the median consensus-age tier only (the coded
    funnel's additional oldest-book cap is a stream gate, not part of this
    population definition).  A field the ledger cannot evidence fails closed:
    an unknown Novig quote age or median book age never clears."""
    g = row.get("gates_snapshot") or {}
    if g.get("consensus_fair_prob") is None:
        return False
    age = g.get("target_quote_age_min")
    if age is None or age > cfg.target_quote_max_age_min:
        return False
    n_books = g.get("n_books") or 0
    if n_books < cfg.n_books_min:
        return False
    med_age, mins = g.get("median_book_age_min"), g.get("mins_to_start")
    if med_age is None or mins is None:
        return False
    cap = cfg.age_median_max_min_late if mins <= cfg.late_window_min else cfg.age_median_max_min
    if med_age > cap:
        return False
    disp = g.get("disp_mad")
    limit = cfg.gate_pass_disp_max_4 if n_books == cfg.n_books_min else cfg.gate_pass_disp_max_5p
    return disp is not None and disp <= limit


def gate_pass_population(rows: list, cfg: ShadowConfig) -> list:
    """Deduplicated first gate-clearing sighting (t0) per signal_id, post-regime.
    Returns copies carrying gate_drift_pp = (P_cons,close - P_cons,t0) * 100 when a
    closing consensus is attached (closing_fair_prob), else None -- the row still
    counts in the population total and the gap shows as Bar 4 coverage loss.
    FILLED rows are members by construction: a fill is the first gate-passing
    sighting of its intent whose simulated order filled."""
    by_sig: dict = {}
    for r in rows:
        if _utc(r["quote_observed_at"]) >= REGIME_START:
            by_sig.setdefault(r["signal_id"], []).append(r)
    out = []
    for sig in sorted(by_sig):
        for r in sorted(by_sig[sig], key=lambda x: x["quote_observed_at"]):
            if _clears_gate_pass(r, cfg):
                r2 = dict(r)
                close = r.get("closing_fair_prob")
                t0 = r["gates_snapshot"]["consensus_fair_prob"]
                r2["gate_drift_pp"] = (round((close - t0) * 100.0, 4) if close is not None else None)
                out.append(r2)
                break
    return out


def _retention(rows: list) -> dict:
    """Bar 6 inputs: gross (no-drag) consensus-confirm CLV at close over gross entry
    dislocation at t0, both as means over the fills carrying both fields."""
    pairs = [(r["consensus_confirm_clv_pct"], r["entry_dislocation_pct"]) for r in rows
             if r.get("consensus_confirm_clv_pct") is not None
             and r.get("entry_dislocation_pct") is not None]
    if not pairs:
        return {"n": 0, "mean_entry_dislocation_pct": None,
                "mean_consensus_confirm_gross_pct": None, "ratio": None}
    me = sum(p[1] for p in pairs) / len(pairs)
    mc = sum(p[0] for p in pairs) / len(pairs)
    return {"n": len(pairs), "mean_entry_dislocation_pct": me,
            "mean_consensus_confirm_gross_pct": mc,
            "ratio": (mc / me) if me > 0 else None}


def _phase0_group(filled: list, gate: list, cfg: ShadowConfig, *, pooled: bool) -> dict:
    cons = _stats(filled, "consensus_confirm_clv_net_pct", cfg)
    sb = _stats(filled, "same_book_clv_net_pct", cfg)
    fdrift = _stats(filled, "consensus_drift_pp", cfg)
    adrift = _stats(gate, "gate_drift_pp", cfg)
    ret = _retention(filled)
    moved = [r for r in filled if r.get("moved_quote")]
    moved_sb = _stats(moved, "same_book_clv_net_pct", cfg)
    with_t15 = [r for r in filled if r.get("novig_t15_prob") is not None]
    move_rate = (len(moved) / len(with_t15)) if with_t15 else None
    days = len({r["game_day"] for r in filled})
    ci_floor = cfg.bar_consensus_ci_lo_gt if pooled else cfg.bar_slice_consensus_ci_lo_gt
    bars: dict = {}
    if pooled:
        bars["bar1_volume_span"] = {
            "n_filled": len(filled), "n_bar": cfg.bar_volume, "game_days": days,
            "days_bar": cfg.bar_volume_days_min,
            "pass": len(filled) >= cfg.bar_volume and days >= cfg.bar_volume_days_min}
    bars["bar2_consensus_confirm"] = {
        "mean": cons["mean"], "ci_lo": cons["ci_lo"], "n": cons["n"],
        "mean_bar": cfg.bar_consensus_clv_pct, "ci_lo_bar_exclusive": ci_floor,
        "pass": (cons["mean"] is not None and cons["mean"] >= cfg.bar_consensus_clv_pct
                 and cons["ci_lo"] is not None and cons["ci_lo"] > ci_floor)}
    bars["bar3_same_book"] = {
        "mean": sb["mean"], "ci_lo": sb["ci_lo"], "n": sb["n"],
        "mean_bar": cfg.bar_same_book_clv_pct, "ci_lo_bar_inclusive": cfg.bar_same_book_ci_lo_ge,
        "pass": (sb["mean"] is not None and sb["mean"] >= cfg.bar_same_book_clv_pct
                 and sb["ci_lo"] is not None and sb["ci_lo"] >= cfg.bar_same_book_ci_lo_ge)}
    bars["bar4_all_intent_drift"] = {
        "mean": adrift["mean"], "n": adrift["n"], "population": len(gate),
        "bar": cfg.bar_all_intent_drift_pp_min,
        "pass": adrift["mean"] is not None and adrift["mean"] >= cfg.bar_all_intent_drift_pp_min}
    bars["bar5_filled_drift"] = {
        "mean": fdrift["mean"], "n": fdrift["n"], "bar": cfg.bar_filled_drift_pp_min,
        "pass": fdrift["mean"] is not None and fdrift["mean"] >= cfg.bar_filled_drift_pp_min}
    if pooled:
        bars["bar6_retention"] = {**ret, "bar": cfg.bar_retention_min,
                                  "pass": ret["ratio"] is not None and ret["ratio"] >= cfg.bar_retention_min}
    _bar_msgs = {
        "bar1_volume_span": f"bar1 volume/span: {len(filled)} fills over {days} game days "
                            f"(bar {cfg.bar_volume} fills / {cfg.bar_volume_days_min} days)",
        "bar2_consensus_confirm": f"bar2 consensus-confirm: mean {cons['mean']}, lo CI {cons['ci_lo']} "
                                  f"(bar mean >= {cfg.bar_consensus_clv_pct}%, lo CI > {ci_floor}%)",
        "bar3_same_book": f"bar3 same-book: mean {sb['mean']}, lo CI {sb['ci_lo']} "
                          f"(bar mean >= {cfg.bar_same_book_clv_pct}%, lo CI >= {cfg.bar_same_book_ci_lo_ge}%)",
        "bar4_all_intent_drift": f"bar4 all-intent drift: mean {adrift['mean']}pp on n={adrift['n']} "
                                 f"of {len(gate)} gate-pass (bar >= {cfg.bar_all_intent_drift_pp_min}pp)",
        "bar5_filled_drift": f"bar5 filled drift: mean {fdrift['mean']}pp "
                             f"(bar >= {cfg.bar_filled_drift_pp_min}pp)",
        "bar6_retention": f"bar6 retention: ratio {ret['ratio']} (bar >= {cfg.bar_retention_min})"}
    reasons = [_bar_msgs[k] for k, v in bars.items() if not v["pass"]]
    admitted = pooled or len(filled) >= cfg.bar_slice_admission_n
    return {"n_filled": len(filled), "n_days": days, "admitted": admitted,
            "bars": bars, "retention": ret,
            "consensus_confirm_net": cons, "same_book_net": sb,
            "filled_drift_pp": fdrift, "all_intent_drift_pp": adrift,
            "fill_selection_diagnostic_pp": (
                round(fdrift["mean"] - adrift["mean"], 4)
                if fdrift["mean"] is not None and adrift["mean"] is not None else None),
            "move_rate": move_rate,
            "venue_classification": {
                "move_rate": move_rate, "active_cutoff": cfg.active_move_rate_min,
                "closing_hold": None, "hold_bar": cfg.active_hold_max,
                "governing": "dual_bar",
                "note": "Active-Exchange requires move rate >= 80% AND closing hold <= 4.5%; "
                        "hold is unmeasured in Phase 0, so the Dual Bar governs (ruling Q2)"},
            "criterion7_moved_quote": {
                "n": moved_sb["n"], "mean": moved_sb["mean"], "ci_lo": moved_sb["ci_lo"],
                "pass": None,
                "note": "DIAGNOSTIC ONLY: RUL-20261116 as rendered specifies no pass bar "
                        "for criterion 7; none is invented here"},
            "verdict": ("FAIL" if reasons else "PASS") if admitted else "NOT_ADMITTED",
            "reasons": reasons if admitted else []}


def evaluate_phase0(rows: list, cfg: ShadowConfig | None = None) -> dict:
    """Score a window under the amended Phase 0 spec (RUL-20261116-autopilot-clv-drift).

    `rows` is a full shadow ledger (every evaluation, not just fills).  Gate-row
    closes must already be attached (closing_fair_prob) -- via
    closes.attach_consensus_closes over the snapshot series -- or Bar 4 reports
    zero coverage and fails closed."""
    cfg = cfg or ShadowConfig()
    filled_all = [r for r in rows if r["status"] == "FILLED"]
    filled = [r for r in filled_all if _utc(r["quote_observed_at"]) >= REGIME_START]
    gate = gate_pass_population(rows, cfg)
    names = [g for g in GROUPS
             if any(r["system"] == g for r in filled) or any(r["system"] == g for r in gate)]
    groups = {g: _phase0_group([r for r in filled if r["system"] == g],
                                [r for r in gate if r["system"] == g], cfg, pooled=False)
              for g in names}
    groups["ALL"] = _phase0_group(filled, gate, cfg, pooled=True)
    pooled_pass = groups["ALL"]["verdict"] == "PASS"
    for g in names:
        grp = groups[g]
        if not grp["admitted"]:
            grp["graduation"] = "NOT_ADMITTED"
        elif not pooled_pass:
            grp["graduation"] = None
        else:
            grp["graduation"] = "GRADUATED" if grp["verdict"] == "PASS" else "HOLDOUT"
    return {"spec": "RUL-20261116-autopilot-clv-drift",
            "regime_start": REGIME_START.isoformat(),
            "filled_total": len(filled_all),
            "filled_pre_regime_excluded": len(filled_all) - len(filled),
            "gate_pass_total": len(gate),
            "gate_pass_with_close": sum(1 for r in gate if r["gate_drift_pp"] is not None),
            "groups": groups,
            "phase1_deferred": {"fill_rate_bar": "non-gating in Phase 0 (no depth banked)",
                                "slippage_bar": "non-gating in Phase 0 (no depth banked)"}}


def render_phase0(res: dict) -> str:
    f = lambda x: "  -" if x is None else f"{x:+.2f}"          # noqa: E731
    lines = [f"Phase 0 AMENDED spec {res['spec']} (filled total {res['filled_total']}, "
             f"pre-regime excluded {res['filled_pre_regime_excluded']}; ALL_GATE_PASS "
             f"{res['gate_pass_total']}, with close {res['gate_pass_with_close']})",
             f"{'group':<12}{'nF':>5}{'days':>5}{'consCLV%':>10}{'loCI':>8}{'sbCLV%':>9}{'loCI':>8}"
             f"{'driftF':>8}{'driftA':>8}{'ret':>6}  verdict"]
    for g, v in res["groups"].items():
        ret = v["retention"]["ratio"]
        lines.append(f"{g:<12}{v['n_filled']:>5}{v['n_days']:>5}"
                     f"{f(v['consensus_confirm_net']['mean']):>10}{f(v['consensus_confirm_net']['ci_lo']):>8}"
                     f"{f(v['same_book_net']['mean']):>9}{f(v['same_book_net']['ci_lo']):>8}"
                     f"{f(v['filled_drift_pp']['mean']):>8}{f(v['all_intent_drift_pp']['mean']):>8}"
                     f"{('  -' if ret is None else format(ret, '.2f')):>6}"
                     f"  {v['verdict']}"
                     f"{(' / ' + v['graduation']) if v.get('graduation') else ''}"
                     f"  (fill-selection diag "
                     f"{'n/a' if v['fill_selection_diagnostic_pp'] is None else f(v['fill_selection_diagnostic_pp']) + 'pp'}; "
                     f"criterion 7 diag n={v['criterion7_moved_quote']['n']} "
                     f"mean={f(v['criterion7_moved_quote']['mean'])}, no bar per ruling)")
        for r in v["reasons"]:
            lines.append(f"    - {r}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-01 Phase 0 dual-bar evaluation over a shadow ledger (offline)")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", default=None, help="write the full evaluation JSON here")
    ap.add_argument("--amended", action="store_true",
                    help="score under the amended RUL-20261116 spec (evaluate_phase0) instead of "
                         "the superseded backtest-window form")
    ap.add_argument("--source", default=None,
                    help="with --amended: attach gate-row consensus closes from this snapshot source "
                         "(dir:ROOT | gcs | gcloud), same forms as ev.autopilot.stream")
    ap.add_argument("--layout", default="accum", choices=("accum", "flat"))
    ap.add_argument("--bucket", default=None)
    a = ap.parse_args(argv)
    cfg = load_config()
    rows = ledger_io.read_rows(a.ledger)
    if a.amended:
        if a.source:
            import os
            from ev.autopilot.config import SPORT_FEEDS
            from ev.autopilot.stream import DirFeedSource, StoreFeedSource, parse_snapshot
            if a.source.startswith("dir:"):
                root = a.source[4:]
                if a.layout == "flat":
                    base = {s: os.path.join(root, f"raw_{s}") for s in ("nhl", "nba")}
                else:
                    base = {s: os.path.join(root, SPORT_FEEDS[s], "raw") for s in ("nhl", "nba")}
                source = DirFeedSource(base)
            else:
                from ev.novig import shadow_bank as sb
                source = StoreFeedSource(sb.make_store(a.source, a.bucket))
            records = []
            for sport in ("nhl", "nba"):
                for ts, payload in source.snapshots(sport):
                    records.extend(parse_snapshot(payload, ts, cfg))
            rows = attach_consensus_closes(rows, records, cfg)
        res = evaluate_phase0(rows, cfg)
        print(render_phase0(res))
    else:
        res = evaluate_window(rows, cfg)
        print(render(res))
    if a.out:
        with open(a.out, "w", encoding="ascii") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, default=str)
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
