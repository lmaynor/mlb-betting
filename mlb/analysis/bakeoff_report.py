"""
mlb.analysis.bakeoff_report -- render a persisted model_bakeoff.py / hr_model_bakeoff.py
run (mlb.analysis.bakeoff_persist) as a git-committable markdown handoff, matching the
structure of handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md (TL;DR, evidence
table, per-system verdict, "how to read this", next steps).

This is the bridge back into git/chat context: a session with no live GCS access (every
Claude Code session on this repo's local checkouts, per CONTEXT.md) cannot read a
--persist run directly, but CAN read a handoff file once it is committed. This script
only PRINTS -- it never writes to disk itself, matching walkforward.rolling()'s own
stdout-report pattern. Redirect it into a new handoff file and commit:

  export MLB_GCS_BUCKET=concrete-crow-445205-m4-mlb-data
  PYTHONPATH=. python3 -m mlb.analysis.bakeoff_report --prefix Analysis/bakeoff/runs/<run_id> \
      > handoffs/handoff_$(date -u +%F)_bakeoff_tuning_verdict.md
  git add handoffs/ && git commit -m "docs: bake-off tuning verdict"

The evidence table and per-system verdict are mechanically derived from scorecard.csv --
no interpretation. TL;DR / how-to-read / next-steps carry a few <TODO> markers for the
qualitative call a human still has to make; a clean NO_EDGE-everywhere outcome renders
with the same table fidelity as a PROMOTE_CANDIDATE outcome -- see
docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md and
handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md for why that is expected, not a
failure of the exercise.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import pandas as pd

from mlb.analysis import bakeoff_persist as bp

EVIDENCE_COLS = ["system", "model", "rank", "n_bets", "clv", "clv_tstat",
                 "lo_n", "lo_clv", "ladder_monotonic", "verdict"]


def _fmt(v) -> str:
    if v is None:
        return "--"
    try:
        if pd.isna(v):
            return "--"
    except TypeError:
        pass
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 1000 else f"{v:.1f}"
    return str(v)


def _evidence_table(board: pd.DataFrame) -> str:
    cols = [c for c in EVIDENCE_COLS if c in board.columns]
    if not cols:
        return "_(scorecard has none of the expected columns -- was it written by "
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in board.sort_values([c for c in ("system", "model") if c in board.columns]).iterrows():
        lines.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def _per_system_verdict(board: pd.DataFrame) -> str:
    if "system" not in board.columns or "verdict" not in board.columns:
        return "_(scorecard missing system/verdict columns)_"
    lines = []
    for sysname, g in board.groupby("system", sort=False):
        promoted = g[g["verdict"] == "PROMOTE_CANDIDATE"]
        if len(promoted):
            for _, r in promoted.iterrows():
                lines.append(f"- **{sysname}** -- `{r['model']}` **PROMOTE_CANDIDATE** -- "
                            f"{r.get('verdict_reason', '')}")
        else:
            g_sorted = g.sort_values("lo_clv", ascending=False, na_position="last") \
                if "lo_clv" in g.columns else g
            if len(g_sorted):
                r = g_sorted.iloc[0]
                lo_clv = _fmt(r.get("lo_clv"))
                lines.append(f"- **{sysname}** -- NO_EDGE across all {len(g)} model(s) "
                            f"(best: `{r['model']}` lo_clv={lo_clv}% -- "
                            f"{r.get('verdict_reason', '')})")
            else:
                lines.append(f"- **{sysname}** -- no scored models")
    return "\n".join(lines)


def render_markdown(prefix: str) -> str:
    board = bp.read_scorecard(prefix)
    meta = bp.read_run_meta(prefix)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n_promoted = int((board["verdict"] == "PROMOTE_CANDIDATE").sum()) if "verdict" in board.columns else 0
    systems = meta.get("systems") or (sorted(board["system"].unique()) if "system" in board.columns else [])

    if n_promoted:
        tldr_headline = (f"**{n_promoted} of {len(board)} system x model combos cleared "
                         f"PROMOTE_CANDIDATE.**")
    else:
        tldr_headline = ("**No system x model combo cleared PROMOTE_CANDIDATE** -- "
                         "consistent with the 2026-06-30 all-system sweep "
                         "(handoffs/handoff_2026-06-30_gen_preds_backtest_verdict.md), "
                         "which found zero capturable model-vs-line edge OOS.")

    until_note = f" until `{meta['until']}`" if meta.get("until") else ""
    requested = set(meta.get("systems") or [])
    completed = set(meta.get("systems_completed") or [])
    missing = sorted(requested - completed)
    missing_note = (f"- **Note:** {', '.join(missing)} requested but not in "
                    f"`systems_completed` -- check run_meta.json `status` / the run's logs "
                    f"for why (skipped for insufficient data, or failed).\n"
                    if missing else "")

    parts = [
        f"# Handoff -- {today} -- model bake-off tuning verdict",
        "",
        f"Real per-system hyperparameter tuning (Optuna, walk-forward-safe -- "
        f"`mlb.analysis.bakeoff_tuning`) across {len(systems)} system(s) "
        f"({', '.join(systems) or 'none'}), judged by the codified profitability rubric "
        f"(`mlb.analysis.backtest_market.verdict`). "
        f"Run `{meta.get('run_id', '?')}` @ commit `{meta.get('git_sha', '?')}` "
        f"(branch `{meta.get('git_branch', '?')}`), cutoff `{meta.get('cutoff', '?')}`{until_note}.",
        "",
        "## TL;DR",
        f"- {tldr_headline}",
        f"- Gates: min_books={meta.get('min_books')}, max_spread={meta.get('max_spread')}, "
        f"calibrate={meta.get('calibrate')}, tuned={meta.get('tune')} "
        f"(trials={meta.get('tune_trials')}, folds={meta.get('tune_folds')}), "
        f"reused tuned params from={meta.get('load_tuned_from') or 'n/a (fresh search)'}.",
        f"- Started {meta.get('started_at', '?')}, finished {meta.get('finished_at', '?')}, "
        f"status={meta.get('status', '?')}.",
        f"{missing_note}"
        "- <TODO: one-line qualitative takeaway -- does this change the HR soft-line-vs-model "
        "strategy call (mlb/analysis/hr_softline.py), or confirm staying on the "
        "soft-line/CLV-capture track?>",
        "",
        "## The evidence (out-of-sample, gated)",
        _evidence_table(board),
        "",
        "## Per-system verdict",
        _per_system_verdict(board),
        "",
        "## How to read this",
        "1. **CLV is the go/no-go, not ROI.** `verdict` already encodes this "
        "(`backtest_market.verdict`: low-edge CLV significant at the T17 bar AND a "
        "monotonic edge ladder). Best-line ROI alone flatters every model.",
        "2. **This is walk-forward OOS**, not in-sample -- tuning and training both stop "
        "strictly before `cutoff`; the holdout was never touched by either.",
        "3. **PROMOTE_CANDIDATE is necessary, not sufficient, for live promotion.** It clears "
        "a bake-off-scaled sample bar; the live paper->live gate (CONTEXT.md T17: n>=100 "
        "settled bets, same CLV bar) is still the real threshold before touching any "
        "`mlb/runners/run_*.py`.",
        "4. **A clean NO_EDGE sweep is a legitimate result**, not a failed exercise -- see "
        "docs/solutions/logic-errors/backtest-roi-vs-clv-soft-line-artifact.md.",
        "",
        "## Next",
        "- <TODO: if any PROMOTE_CANDIDATE row above -- validate on a second, later cutoff "
        "before touching production; if none -- no further action on this axis, defer to "
        "the parallel soft-line/CLV-capture track (`mlb/analysis/hr_softline.py`, "
        "`mlb/runners/track_bettingpros.py`).>",
        f"- Tuned params + full Optuna trial history for this run: `{prefix}/tuning/`.",
        f"- Every settled bet behind these numbers: `{prefix}/candidates/`.",
        "",
    ]
    return "\n".join(parts)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Render a persisted bake-off run as a markdown handoff")
    p.add_argument("--prefix", required=True,
                   help="a --persist run's prefix, e.g. Analysis/bakeoff/runs/<run_id>")
    args = p.parse_args(argv)
    print(render_markdown(args.prefix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
