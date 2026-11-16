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
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from ev.autopilot import ledger as ledger_io
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-01 Phase 0 dual-bar evaluation over a shadow ledger (offline)")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--out", default=None, help="write the full evaluation JSON here")
    a = ap.parse_args(argv)
    cfg = load_config()
    res = evaluate_window(ledger_io.read_rows(a.ledger), cfg)
    print(render(res))
    if a.out:
        with open(a.out, "w", encoding="ascii") as fh:
            json.dump(res, fh, indent=1, sort_keys=True, default=str)
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
