"""
ev.autopilot.closes -- attach closing inputs to shadow FILLED rows and compute the CLV
ruling's three-way decomposition per signal.

Close definitions (RUL-20261115-autopilot-clv-definition):
  closing consensus  = the leave-Novig-out consensus fair in the LAST snapshot for the
      same event before commence, at the same modal line (a moved line = unusable
      close, mirroring clv_gate's survivorship rule);
  same-book close    = Novig's own quote at T-15m: the last snapshot at or before
      commence - 15m, after the entry, at the same line;
  moved quote        = the T-15m quote differs from the fill price by >= 1 tick.

The decomposition logged per signal:
  Entry Dislocation   = P_cons,entry / P_fill - 1
  Consensus Drift     = (P_cons,close - P_cons,entry) / P_fill   (ratio form), and the
                        pp form (P_cons,close - P_cons,entry) * 100 the bar is stated in
  Same-Book CLV       = P_novig,T-15m / P_fill - 1
  Consensus-Confirm   = P_cons,close / P_fill - 1
Net forms equal the gross forms (no drag deducted) per the RUL-20261118 restatement.  A last-snapshot same-book variant
(same_book_clv_last_*) is computed alongside, because the step-b backtest record graded
same-book CLV against the last pre-commence snapshot; the ruling's T-15m form governs
the bars and the record's form is kept for reconciliation.

Honest degradation: where a close is not banked (no later snapshot, line moved, Novig
unquoted, no post-entry T-15m snapshot), the affected CLV fields stay None and the
close_state / t15_state says why.  Nothing is fabricated.

attach_consensus_closes (below) is the evaluator-side companion: it attaches the same
closing consensus to non-FILLED rows so the RUL-20261116 ALL_GATE_PASS drift bar can be
scored.  attach_closes itself -- and therefore the live shadow job's derivation -- is
unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ev.autopilot.config import ShadowConfig


def _utc(ts) -> datetime:
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _side_prob(p_over: float, side: str) -> float:
    return p_over if side == "yes" else 1.0 - p_over


def _novig_side(rec, side: str):
    return rec.nov_over if side == "yes" else rec.nov_under


def attach_closes(rows: list, records: list, cfg: ShadowConfig | None = None) -> list:
    """Returns a new list; FILLED rows gain closing fields + decomposition, all other
    rows pass through unchanged.  `records` is the full parsed snapshot series."""
    cfg = cfg or ShadowConfig()
    series: dict = {}
    for r in records:
        series.setdefault((r.event_id, r.player_norm, r.stat), []).append(r)
    for v in series.values():
        v.sort(key=lambda r: r.ts)
    out = []
    for row in rows:
        if row["status"] != "FILLED":
            out.append(row)
            continue
        r2 = dict(row)
        out.append(r2)
        entry_ts = _utc(row["quote_observed_at"])
        commence = _utc(row["commence"])
        side, line = row["side"], row["line"]
        fill = row["weighted_fill_price"]
        p_cons_entry = row["gates_snapshot"]["consensus_fair_prob"]
        recs = series.get((row["event_id"], row["player_norm"], row["stat"]), [])
        laters = [x for x in recs if entry_ts < x.ts < commence]
        if not laters:
            r2["close_state"] = "no_later_snapshot"
            r2["t15_state"] = "no_later_snapshot"
            continue
        close = laters[-1]
        if close.line != line:
            r2["close_state"] = "line_moved"
            r2["t15_state"] = "line_moved"
            continue
        r2["close_state"] = "ok"
        p_cons_close = _side_prob(close.p_cons, side)
        r2["closing_fair_prob"] = round(p_cons_close, 6)
        r2["entry_dislocation_pct"] = round((p_cons_entry / fill - 1.0) * 100.0, 4)
        r2["consensus_drift_pp"] = round((p_cons_close - p_cons_entry) * 100.0, 4)
        r2["consensus_drift_ratio_pct"] = round((p_cons_close - p_cons_entry) / fill * 100.0, 4)
        cc = (p_cons_close / fill - 1.0) * 100.0
        r2["consensus_confirm_clv_pct"] = round(cc, 4)
        r2["consensus_confirm_clv_net_pct"] = round(cc, 4)
        nov_close = _novig_side(close, side)
        if nov_close is None:
            r2["close_state"] = "novig_unquoted_at_close"
        else:
            r2["novig_close_prob"] = round(nov_close, 6)
            last = (nov_close / fill - 1.0) * 100.0
            r2["same_book_clv_last_pct"] = round(last, 4)
            r2["same_book_clv_last_net_pct"] = round(last, 4)
            r2["moved_quote_last"] = abs(nov_close - fill) >= cfg.moved_tick
        # T-15m same-book close (the ruling's governing form)
        cutoff = commence - timedelta(minutes=cfg.t15_min)
        t15s = [x for x in laters if x.ts <= cutoff]
        if not t15s:
            r2["t15_state"] = "no_t15_snapshot_after_entry"
            continue
        t15 = t15s[-1]
        if t15.line != line:
            r2["t15_state"] = "t15_line_moved"
            continue
        nov_t15 = _novig_side(t15, side)
        if nov_t15 is None:
            r2["t15_state"] = "t15_novig_unquoted"
            continue
        r2["t15_state"] = "ok"
        r2["novig_t15_prob"] = round(nov_t15, 6)
        sb = (nov_t15 / fill - 1.0) * 100.0
        r2["same_book_clv_pct"] = round(sb, 4)
        r2["same_book_clv_net_pct"] = round(sb, 4)
        r2["moved_quote"] = abs(nov_t15 - fill) >= cfg.moved_tick
    return out


def attach_consensus_closes(rows: list, records: list, cfg: ShadowConfig | None = None) -> list:
    """Attach the closing consensus to the rows attach_closes leaves bare (every
    non-FILLED row), so the amended evaluator's ALL_GATE_PASS drift bar
    (RUL-20261116-autopilot-clv-drift, Bar 4) is computable at scoring time.

    Same close definition as attach_closes -- the leave-Novig-out consensus fair in
    the LAST snapshot for the same event before commence, at the same modal line --
    plus the ruling's live-window condition of >= 4 reference books at close.
    FILLED rows keep the closing_fair_prob attach_closes computed (identical
    definition); this function fills only rows whose closing_fair_prob is None and
    stamps gate_close_state on every row: ok / no_later_snapshot / line_moved /
    close_thin_books / attached (already carried a close).  Honest degradation
    mirrors attach_closes: an unusable close stays None, never imputed.

    This is an evaluator-side derivation over the banked snapshot series (the same
    series the shadow job derives its ledger from); the live job does not call it.
    """
    cfg = cfg or ShadowConfig()
    series: dict = {}
    for r in records:
        series.setdefault((r.event_id, r.player_norm, r.stat), []).append(r)
    for v in series.values():
        v.sort(key=lambda r: r.ts)
    out = []
    for row in rows:
        r2 = dict(row)
        out.append(r2)
        if r2.get("closing_fair_prob") is not None:
            r2["gate_close_state"] = "attached"
            continue
        entry_ts = _utc(row["quote_observed_at"])
        commence = _utc(row["commence"])
        recs = series.get((row["event_id"], row["player_norm"], row["stat"]), [])
        laters = [x for x in recs if entry_ts < x.ts < commence]
        if not laters:
            r2["gate_close_state"] = "no_later_snapshot"
            continue
        close = laters[-1]
        if close.line != row["line"]:
            r2["gate_close_state"] = "line_moved"
            continue
        if close.n_books < cfg.n_books_min:
            r2["gate_close_state"] = "close_thin_books"
            continue
        r2["gate_close_state"] = "ok"
        r2["closing_fair_prob"] = round(_side_prob(close.p_cons, row["side"]), 6)
    return out
