"""Price test of the NBA prop model against historical BettingPros consensus (flat stake at the best quoted price).

    GCS_BUCKET=<bucket> python scripts/nba_bettingpros_price_test.py --consensus a.parquet b.parquet ... \
        --preds-cache <nba_preds.parquet> --out-json res.json --out-md tables.md

Read-only. Reuses the SAME model rows / identity join as `nba.props.evaluate` (nba.props.history_join.join_stat) and the
blend math of `mlb_core.odds.model_vs_market`; adds what that module does not do: a wide threshold sweep, per stat and per
season tables, a walk-forward THRESHOLD choice, always-Over / always-Under / line-shopping controls, a price haircut
sensitivity, an edge ladder, and the postgame-quote grace-window comparison (strict vs +10 min).

Selection caveats (also in the report): the consensus is the LAST pre-start quote of the day, not a time series, and the best
price across the books is an optimistic fill (stale quotes at one book look like value). There is NO closing-line series, so
CLV cannot be computed here.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlb_core.odds import model_vs_market as MVM  # noqa: E402
from nba.props import config as C  # noqa: E402
from nba.props import history_join as HJ  # noqa: E402
from nhl.props import market as M  # noqa: E402

log = logging.getLogger("nba_price_test")
EXTRA = ["upd_min", "novig_over_odds", "novig_under_odds", "disp", "second_o_am", "second_u_am", "med_o_am", "med_u_am",
         "age_best_o", "age_best_u", "age_med_o"]
THRS = [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]
SEL_THRS = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
N_BOOT = 1000
MAX_SHIFT = MVM.BLEND_MAX_SHIFT


# ----------------------------------------------------------------------------------------------------------------- inputs
def load_model(preds_cache: str, local_dir: str | None):
    from nba.props import data as D
    from nba.props.evaluate import _load_tables, evaluate_predictions
    from nba.props.features import build_training_frame
    pb, gm = D.load_masters(local_dir)
    rows, tg = D.prepare(pb, gm, D.gather_tips(gm, local_dir))
    X, meta = build_training_frame(rows, tg)
    X, meta = X.reset_index(drop=True), meta.reset_index(drop=True)
    meta["season_type"] = meta["season_type"].astype(str)
    cache = Path(preds_cache)
    preds, tables = pd.read_parquet(cache), _load_tables(cache.with_suffix(".tables.json"))
    seasons = [2022, 2023, 2024, 2025]
    ev = evaluate_predictions(X, meta, preds, seasons, tables)
    return ev, meta, tables


def load_consensus(paths: list[str], extras: str | None = None) -> pd.DataFrame:
    cons = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    if extras:   # scripts/nba_bp_price_extras.py: second-best / median price and best-quote age per prop
        ex = pd.read_parquet(extras)[["event_id", "player_id", "stat", "line"] + [c for c in EXTRA if c in pd.read_parquet(extras).columns]]
        cons = cons.assign(player_id=pd.to_numeric(cons["player_id"]).astype("Int64"), event_id=cons["event_id"].astype("Int64"))
        ex = ex.assign(player_id=ex["player_id"].astype("Int64"), event_id=ex["event_id"].astype("Int64"))
        cons = cons.merge(ex, on=["event_id", "player_id", "stat", "line"], how="left")
    upd = pd.to_datetime(cons["updated_max"], utc=True, errors="coerce")
    sch = pd.to_datetime(cons["scheduled"], utc=True, errors="coerce", format="mixed")
    cons["upd_min"] = (upd - sch).dt.total_seconds() / 60.0
    return cons


def joined(ev, meta, tables, cons: pd.DataFrame, grace: float) -> tuple[pd.DataFrame, dict]:
    """All stats' joined frames for one grace setting (pregame := updated_max <= scheduled + grace min)."""
    c = cons.copy()
    c["pregame"] = pd.array(np.where(c["upd_min"].notna(), c["upd_min"] <= grace, pd.NA), dtype="boolean")
    HJ.JOINED_COLUMNS = HJ.JOINED_COLUMNS + [x for x in EXTRA if x not in HJ.JOINED_COLUMNS]
    frames, cov = [], {}
    by_date = HJ._players_by_date(meta)
    for stat in C.STATS:
        if stat not in ev["pooled"]:
            continue
        j, cv = HJ.join_stat(ev, meta, c, stat, tables, 3, _by_date=by_date)
        frames.append(j.assign(stat=stat))
        cov[stat] = cv
    f = pd.concat(frames, ignore_index=True)
    for col in ("over", "p_model", "p_market", "best_over_odds", "best_under_odds", "upd_min"):
        f[col] = pd.to_numeric(f[col], errors="coerce")
    return f, cov


# ----------------------------------------------------------------------------------------------------------------- core
def walk_forward_blend(f: pd.DataFrame, min_train: int = 500) -> pd.DataFrame:
    """p_blend per season from a per-stat (a, b, c) fitted on EARLIER seasons only (first season never scored)."""
    parts = []
    f = f.dropna(subset=["over", "p_model", "p_market", "best_over_odds", "best_under_odds"])
    f = f[(f.p_model > 0) & (f.p_model < 1) & (f.p_market > 0) & (f.p_market < 1)]
    for stat, g in f.groupby("stat"):
        for s in sorted(g["season"].unique()):
            tr, te = g[g["season"] < s], g[g["season"] == s]
            if len(tr) < min_train or len(te) == 0:
                continue
            fit = M.blend_regression(tr["over"], tr["p_market"], tr["p_model"], tr["game_id"])
            a, b, c = float(fit["a"]), float(fit["b"]), float(fit["c"])
            lm = M.logit(te["p_market"].to_numpy(float))
            ld = M.logit(te["p_model"].to_numpy(float)) - lm
            pb = np.clip(MVM._sig(a + b * lm + c * ld), te["p_market"] - MAX_SHIFT, te["p_market"] + MAX_SHIFT)
            parts.append(te.assign(p_blend=np.asarray(pb), fit_c=c, fit_t=float(fit["t_c"])))
    return pd.concat(parts, ignore_index=True)


def bets(t: pd.DataFrame, pcol: str, thr: float, haircut: float = 0.0, px=("best_over_odds", "best_under_odds")) -> pd.DataFrame:
    """Rows where the better side's probability beats its best-price implied probability by >= thr. `haircut` shaves that
    many points of implied probability off every price (a worse fill), applied to both selection and payout."""
    if px[0] != "best_over_odds":
        t = t.dropna(subset=list(px))
    y = t["over"].to_numpy(float)
    oo, ou = t[px[0]].to_numpy(float), t[px[1]].to_numpy(float)
    p = t[pcol].to_numpy(float)
    io, iu = MVM._implied(oo) + haircut, MVM._implied(ou) + haircut
    e_over, e_under = p - io, (1.0 - p) - iu
    take_over = e_over >= e_under
    edge = np.where(take_over, e_over, e_under)
    sel = edge >= thr
    win = np.where(take_over, y == 1.0, y == 0.0)
    dec = np.where(take_over, 1.0 / io, 1.0 / iu)           # decimal odds of the (haircut) price
    prof = np.where(win, dec - 1.0, -1.0)
    # both-sides reference on the same props at the same (haircut) prices
    dec_o, dec_u = 1.0 / io, 1.0 / iu
    both = np.where(y == 1.0, dec_o - 1.0, -1.0) + np.where(y == 0.0, dec_u - 1.0, -1.0)
    out = t.loc[sel, ["game_date", "season", "stat", "n_books"]].copy()
    out["profit"], out["both"], out["edge"], out["side"] = prof[sel], both[sel] / 2.0, edge[sel], np.where(take_over[sel], "over", "under")
    if "age_best_o" in t.columns:
        out["age"] = np.where(take_over, t["age_best_o"].to_numpy(float), t["age_best_u"].to_numpy(float))[sel]
    return out


def summarize(b: pd.DataFrame, n_boot: int = N_BOOT) -> dict:
    if len(b) == 0:
        return {"n": 0, "roi": np.nan, "lo": np.nan, "hi": np.nan, "win": np.nan, "vs_noskill": np.nan}
    lo, hi = MVM._cluster_boot_mean(b["profit"].to_numpy(), b["game_date"].astype(str), n_boot)
    return {"n": int(len(b)), "roi": float(b["profit"].mean()), "lo": lo, "hi": hi,
            "win": float((b["profit"] > 0).mean()), "vs_noskill": float((b["profit"] - b["both"]).mean())}


def fixed_side(t: pd.DataFrame, side: str, haircut: float = 0.0) -> pd.DataFrame:
    y = t["over"].to_numpy(float)
    px = t["best_over_odds" if side == "over" else "best_under_odds"].to_numpy(float)
    imp = MVM._implied(px) + haircut
    win = (y == 1.0) if side == "over" else (y == 0.0)
    return t[["game_date", "season", "stat", "n_books"]].assign(profit=np.where(win, 1.0 / imp - 1.0, -1.0), both=0.0)


def fmt(r: dict, ci: bool = True) -> str:
    if not r["n"]:
        return "n=0"
    s = f"{r['roi']:+.2%}"
    return f"{s} [{r['lo']:+.2%}, {r['hi']:+.2%}] (n={r['n']:,})" if ci else f"{s} (n={r['n']:,})"


# ----------------------------------------------------------------------------------------------------------------- report
def run(ev, meta, tables, cons, out_json, out_md):
    res: dict = {}
    md: list[str] = []
    P = md.append

    # ---------------------------------------------------------------- (4) grace window
    f0, cov0 = joined(ev, meta, tables, cons, 0.0)
    f10, cov10 = joined(ev, meta, tables, cons, 10.0)
    n_cons = int(cons["stat"].isin(list(HJ.STAT_MAP)).sum())
    tot = lambda cov, k: sum(v["dropped"].get(k, 0) for v in cov.values())      # noqa: E731
    res["coverage"] = {g: {"consensus_rows": n_cons, "scored": int(len(f)), "postgame_quote": tot(c, "postgame_quote"),
                           "unresolved": tot(c, "unresolved"), "ambiguous": tot(c, "ambiguous"),
                           "no_model_row": tot(c, "no_model_row"), "push": tot(c, "push"), "low_books": tot(c, "low_books")}
                       for g, f, c in (("strict", f0, cov0), ("grace10", f10, cov10))}
    P("### A. Grace window for the postgame filter\n")
    P("| filter | consensus rows | scored | postgame_quote dropped | unresolved | ambiguous | no_model_row | push |")
    P("|---|---|---|---|---|---|---|---|")
    for g, lab in (("strict", "strict (`updated <= start`)"), ("grace10", "grace +10 min")):
        c = res["coverage"][g]
        P(f"| {lab} | {c['consensus_rows']:,} | {c['scored']:,} ({c['scored'] / c['consensus_rows']:.0%}) | {c['postgame_quote']:,} | "
          f"{c['unresolved']:,} | {c['ambiguous']:,} | {c['no_model_row']:,} | {c['push']:,} |")
    key = ["player_id", "game_date", "stat"]
    rec = f10.merge(f0[key].assign(_s=1), on=key, how="left")
    rec = rec[rec["_s"].isna()]
    strict = f10.merge(f0[key], on=key, how="inner")
    P("\nIs the recovered tail contaminated (in-play)? Same model, same market-consensus, split by how long after the scheduled "
      "start the last quote update was. An in-play-contaminated market would look suspiciously SHARP (lower Brier) on the "
      "late buckets:\n")
    P("| bucket (last update vs start) | n | over rate | market Brier | model Brier | blend c (t) |")
    P("|---|---|---|---|---|---|")
    buckets = [("before start", strict), ("0-2 min after", rec[(rec.upd_min > 0) & (rec.upd_min <= 2)]),
               ("2-5 min after", rec[(rec.upd_min > 2) & (rec.upd_min <= 5)]),
               ("5-10 min after", rec[(rec.upd_min > 5) & (rec.upd_min <= 10)]),
               ("all recovered", rec), ("all grace10", f10)]
    res["grace_buckets"] = {}
    for lab, g in buckets:
        g = g.dropna(subset=["over", "p_model", "p_market"])
        g = g[(g.p_model > 0) & (g.p_model < 1)]
        if len(g) < 200:
            P(f"| {lab} | {len(g)} | | | | |")
            continue
        sc = MVM.score_model_vs_market(g["over"], g["p_model"], g["p_market"], g["game_id"])
        res["grace_buckets"][lab] = {k: sc[k] for k in sc if isinstance(sc[k], (int, float))}
        bl = sc.get("blend") or {"c": float("nan"), "t_c": float("nan")}
        P(f"| {lab} | {sc['n']:,} | {g['over'].mean():.3f} | {sc['brier_market']:.4f} | {sc['brier_model']:.4f} | "
          f"{bl['c']:+.2f} ({bl['t_c']:+.1f}) |")

    # ---------------------------------------------------------------- walk-forward blend (headline on grace10, check on strict)
    results = {}
    for lab, f in (("grace10", f10), ("strict", f0)):
        t = walk_forward_blend(f)
        results[lab] = t
    t = results["grace10"]
    seasons = sorted(t["season"].unique())
    res["scored_seasons"] = [int(s) for s in seasons]
    fits = t.groupby(["stat", "season"])[["fit_c", "fit_t"]].first().reset_index()
    P(f"\n### B. Walk-forward blend: fit on earlier seasons only; scored seasons {', '.join(map(str, seasons))} (season = start year of test season; "
      f"first test season has no earlier fit so is not scored). n = {len(t):,} props, grace +10 min\n")
    sc = MVM.score_model_vs_market(t["over"], t["p_blend"], t["p_market"], t["game_id"])
    P("Fitted blend c per stat and season (from EARLIER seasons): " + "; ".join(
        f"{r.stat} {int(r.season)}: {r.fit_c:+.2f}" for r in fits.itertuples()) + "\n")
    y = t["over"].to_numpy(float)
    dbr = (t["p_blend"] - y) ** 2 - (t["p_market"] - y) ** 2
    lo, hi = MVM._cluster_boot_mean(dbr.to_numpy(float), t["game_date"].astype(str), N_BOOT)
    ll = lambda p: M.logloss(y, p.to_numpy(float))                                           # noqa: E731
    P(f"Out-of-sample Brier: market {M.brier(y, t['p_market'].to_numpy(float)):.5f}, blend {M.brier(y, t['p_blend'].to_numpy(float)):.5f}, "
      f"model {M.brier(y, t['p_model'].to_numpy(float)):.5f}; paired dBrier(blend - market) {dbr.mean():+.5f} [{lo:+.5f}, {hi:+.5f}]; "
      f"logloss market {ll(t['p_market']):.5f} blend {ll(t['p_blend']):.5f}.\n")
    res["oos_brier"] = {"market": M.brier(y, t["p_market"].to_numpy(float)), "blend": M.brier(y, t["p_blend"].to_numpy(float)),
                        "model": M.brier(y, t["p_model"].to_numpy(float)), "d": float(dbr.mean()), "lo": lo, "hi": hi}

    # price overround at best price
    io, iu = MVM._implied(t["best_over_odds"]), MVM._implied(t["best_under_odds"])
    over_round = pd.Series(np.asarray(io + iu - 1.0, dtype=float))
    P(f"Best-price overround (implied_over + implied_under - 1, best price on each side): mean {over_round.mean():+.2%}, median "
      f"{over_round.median():+.2%}; {(over_round < 0).mean():.1%} of props have an ARBITRAGE-looking best-price pair "
      "(sum of implied < 1) -- evidence the best price is partly a stale / outlier quote, i.e. optimistic.\n")
    res["overround"] = {"mean": float(over_round.mean()), "median": float(over_round.median()), "arb_frac": float((over_round < 0).mean())}

    # ---------------------------------------------------------------- baselines
    P("### C. Baselines: blind sides at the best price (all scored props, grace +10)\n")
    P("| slice | always Over | always Under |")
    P("|---|---|---|")
    base = {}
    for lab, g in [("ALL", t)] + [(s, t[t.stat == s]) for s in C.STATS] + [(f"season {int(s)}", t[t.season == s]) for s in seasons]:
        o, u = summarize(fixed_side(g, "over")), summarize(fixed_side(g, "under"))
        base[lab] = {"over": o, "under": u}
        P(f"| {lab} | {fmt(o)} | {fmt(u)} |")
    res["baselines"] = base
    P(f"\nOver rate overall {t['over'].mean():.3f}; mean consensus P(over) {t['p_market'].mean():.3f}; mean model P(over) {t['p_model'].mean():.3f}.\n")

    # ---------------------------------------------------------------- sweep
    P("### D. Threshold sweep, flat 1u at the best price (grace +10, all stats, scored seasons pooled)\n")
    P("`edge` = side probability - implied probability of the side's best price. Rules: **blend** (walk-forward blend), "
      "**market** (consensus alone = pure line shopping, the control the blend must beat), **model** (standalone). "
      "`vs no-skill` = ROI minus betting BOTH sides of the same props at the same prices (what the vig alone costs).\n")
    P("| edge >= | rule | ROI [95% CI] (n) | win % | vs no-skill |")
    P("|---|---|---|---|---|")
    sweep = {}
    for thr in THRS:
        for rule, col in (("blend", "p_blend"), ("market", "p_market"), ("model", "p_model")):
            r = summarize(bets(t, col, thr))
            sweep[f"{rule}@{thr}"] = r
            P(f"| {thr:.0%} | {rule} | {fmt(r)} | {r['win']:.1%} | {r['vs_noskill']:+.2%} |" if r["n"] else f"| {thr:.0%} | {rule} | n=0 | | |")
    res["sweep"] = sweep

    P("\n#### Edge ladder (blend rule, mutually exclusive buckets) -- real edge should rise monotonically\n")
    P("| edge bucket | ROI [95% CI] (n) |")
    P("|---|---|")
    ladder = {}
    b0 = bets(t, "p_blend", 0.0)
    for lo_, hi_ in [(0.0, 0.02), (0.02, 0.04), (0.04, 0.06), (0.06, 0.10), (0.10, 9)]:
        r = summarize(b0[(b0.edge >= lo_) & (b0.edge < hi_)])
        ladder[f"{lo_}-{hi_}"] = r
        P(f"| {lo_:.0%}-{hi_:.0%}{'+' if hi_ == 9 else ''} | {fmt(r)} |".replace("-900%+", "+"))
    res["ladder"] = ladder

    # ---------------------------------------------------------------- per stat / per season at fixed thresholds
    for thr in (0.03, 0.05):
        P(f"\n#### Per stat and per season, blend rule, edge >= {thr:.0%}\n")
        P("| slice | blend ROI [CI] (n) | market-rule (line shopping) ROI (n) | blend vs no-skill |")
        P("|---|---|---|---|")
        ps = {}
        for lab, g in [(s, t[t.stat == s]) for s in C.STATS] + [(f"season {int(s)}", t[t.season == s]) for s in seasons]:
            r, m_ = summarize(bets(g, "p_blend", thr)), summarize(bets(g, "p_market", thr), n_boot=200)
            ps[lab] = {"blend": r, "market": m_}
            P(f"| {lab} | {fmt(r)} | {fmt(m_, ci=False)} | {r['vs_noskill']:+.2%} |" if r["n"] else f"| {lab} | n=0 | | |")
        res[f"slices@{thr}"] = ps
        P("\nstat x season blend ROI (n): " + "; ".join(
            f"{s}/{int(se)}: {summarize(bets(t[(t.stat == s) & (t.season == se)], 'p_blend', thr), 100)['roi']:+.1%} "
            f"({len(bets(t[(t.stat == s) & (t.season == se)], 'p_blend', thr))})" for s in C.STATS for se in seasons))

    # ---------------------------------------------------------------- walk-forward threshold
    P("\n### E. Walk-forward threshold choice (blend rule, all stats pooled)\n")
    P("For each test season s, the threshold in {" + ", ".join(f"{x:.0%}" for x in SEL_THRS) + "} with the highest ROI over scored seasons < s (min 300 bets) "
      "is applied to season s. The blend parameters are themselves fitted on earlier seasons only. Seasons with no earlier scored season cannot be chosen for.\n")
    P("| test season | chosen thr (from earlier seasons) | its in-sample ROI | OUT-OF-SAMPLE ROI [CI] (n) | vs no-skill |")
    P("|---|---|---|---|---|")
    wf, oos_parts = {}, []
    for s in seasons[1:]:
        prior, test = t[t.season < s], t[t.season == s]
        best, best_roi = None, -9.0
        for thr in SEL_THRS:
            bb = bets(prior, "p_blend", thr)
            if len(bb) >= 300 and bb["profit"].mean() > best_roi:
                best, best_roi = thr, float(bb["profit"].mean())
        if best is None:
            continue
        bt = bets(test, "p_blend", best)
        oos_parts.append(bt)
        r = summarize(bt)
        wf[int(s)] = {"thr": best, "in_sample_roi": best_roi, **r}
        P(f"| {int(s)} | {best:.0%} | {best_roi:+.2%} | {fmt(r)} | {r['vs_noskill']:+.2%} |")
    if oos_parts:
        r = summarize(pd.concat(oos_parts))
        wf["pooled_oos"] = r
        P(f"| pooled out-of-sample | | | {fmt(r)} | {r['vs_noskill']:+.2%} |")
    # fixed pre-registered thresholds on the latest season only (selection-free read)
    last = seasons[-1]
    P(f"\nSelection-free read of the latest scored season ({int(last)}), fixed thresholds: " + "; ".join(
        f"edge>={x:.0%}: {fmt(summarize(bets(t[t.season == last], 'p_blend', x)))}" for x in (0.03, 0.05)))
    res["walk_forward_threshold"] = wf

    # ---------------------------------------------------------------- haircut and strict-filter robustness
    P("\n### F. Robustness\n")
    P("Price haircut: shave h points of implied probability off BOTH sides' best price (a worse, more realistic fill), edge >= 3%, blend rule, selection and payout both at the haircut price:\n")
    P("| haircut | blend ROI [CI] (n) | always Under | always Over |")
    P("|---|---|---|---|")
    hc = {}
    for h in (0.0, 0.01, 0.02, 0.03):
        r = summarize(bets(t, "p_blend", 0.03, h))
        hc[h] = r
        P(f"| {h:.0%} | {fmt(r)} | {fmt(summarize(fixed_side(t, 'under', h), 200), False)} | {fmt(summarize(fixed_side(t, 'over', h), 200), False)} |")
    res["haircut"] = hc
    P("\nDepth of market (n_books at the modal line), blend rule, edge >= 3%:\n")
    P("| n_books | ROI [CI] (n) |")
    P("|---|---|")
    for lab, g in (("3-5", t[(t.n_books >= 3) & (t.n_books <= 5)]), ("6-9", t[(t.n_books >= 6) & (t.n_books <= 9)]), ("10+", t[t.n_books >= 10])):
        P(f"| {lab} | {fmt(summarize(bets(g, 'p_blend', 0.03), 300))} |")
    ts = results["strict"]
    P(f"\nStrict postgame filter instead of the +10 min grace (n={len(ts):,}, {len(t) - len(ts):+,} rows vs grace): ")
    for thr in (0.03, 0.05):
        P(f"- blend edge >= {thr:.0%}: {fmt(summarize(bets(ts, 'p_blend', thr)))}; always Under {fmt(summarize(fixed_side(ts, 'under'), 300), False)}")
    scs = MVM.score_model_vs_market(ts["over"], ts["p_blend"], ts["p_market"], ts["game_id"])
    res["strict_blend"] = {"n": len(ts), "brier_blend": scs["brier_model"], "brier_market": scs["brier_market"]}
    # recovered rows only
    rr = t.merge(results["strict"][["player_id", "game_date", "stat"]].assign(_s=1), on=["player_id", "game_date", "stat"], how="left")
    rr = rr[rr["_s"].isna()]
    P(f"- recovered-by-grace rows only (n={len(rr):,}): blend edge >= 3%: {fmt(summarize(bets(rr, 'p_blend', 0.03)))}\n")

    # ---------------------------------------------------------------- G. realistic prices
    if "second_o_am" in t.columns:
        P("\n### G. Price realism: second-best and median price instead of the best (blend rule, grace +10)\n")
        P("Selection AND payout both use the stated price. The best price is the max over ~3-14 books of the LAST quote each held; "
          "the second-best / median price is what a bettor would get without picking the single most generous (often stale or outlier) book.\n")
        P("| price used | edge >= 3% | edge >= 5% | always Under | always Over |")
        P("|---|---|---|---|---|")
        gres = {}
        for lab, px in (("best", ("best_over_odds", "best_under_odds")), ("second-best", ("second_o_am", "second_u_am")),
                        ("median book", ("med_o_am", "med_u_am"))):
            tt = t.dropna(subset=list(px))
            cells = [fmt(summarize(bets(tt, "p_blend", x, px=px), 400)) for x in (0.03, 0.05)]
            base_u = tt.assign(best_under_odds=tt[px[1]], best_over_odds=tt[px[0]])
            gres[lab] = cells
            P(f"| {lab} | {cells[0]} | {cells[1]} | {fmt(summarize(fixed_side(base_u, 'under'), 200), False)} | "
              f"{fmt(summarize(fixed_side(base_u, 'over'), 200), False)} |")
        res["price_realism"] = gres


        P("\n#### G2. Model signal at the MEDIAN book price (no line shopping), threshold sweep\n")
        P("The median book's price is about what the consensus offers, so this isolates what the model's side selection adds and "
          "what the vig costs. `vs no-skill` = ROI minus betting both sides of the same props at the same prices.\n")
        P("| edge >= | blend ROI [CI] (n) | vs no-skill | by season (ROI, n) |")
        P("|---|---|---|---|")
        pxm = ("med_o_am", "med_u_am")
        tm = t.dropna(subset=list(pxm))
        g2 = {}
        for thr in (0.0, 0.01, 0.02, 0.03, 0.04):
            bb = bets(tm, "p_blend", thr, px=pxm)
            r = summarize(bb, 600)
            by = "; ".join(f"{int(se)}: {bb[bb.season == se]['profit'].mean():+.1%} ({int((bb.season == se).sum())})" for se in seasons)
            g2[thr] = r
            P(f"| {thr:.0%} | {fmt(r)} | {r['vs_noskill']:+.2%} | {by} |" if r["n"] else f"| {thr:.0%} | n=0 | | |")
        res["median_price_sweep"] = g2
        P("\nSame, the pure line-shopping control (consensus probability only, NO model) at best vs second-best price, edge >= 3%: "
          f"best {fmt(summarize(bets(t, 'p_market', 0.03), 300))}; second-best "
          f"{fmt(summarize(bets(t, 'p_market', 0.03, px=('second_o_am', 'second_u_am')), 300))}.\n")

        P("### H. Age of the best-price quote (minutes before the scheduled start that quote was last updated)\n")
        P("If stamps are genuine, a best price that is hours old is a stale quote; the later consensus then makes it look like value "
          "(look-ahead). 2025 stamps are mostly a close/freeze stamp a few minutes after the start (see the 2026-10-16 loader notes), "
          "so age is only informative where stamps are genuine.\n")
        P("| season | share of selected-side quotes stamped at/after start | median age (min) |")
        P("|---|---|---|")
        b3 = bets(t, "p_market", 0.03)
        b3b = bets(t, "p_blend", 0.03)
        for se in seasons:
            a_ = b3[b3.season == se]["age"].dropna()
            P(f"| {int(se)} | {(a_ <= 0).mean():.0%} | {a_.median():.0f} |")
        edges = [(-1e9, 0, "stamped after start"), (0, 30, "0-30 min before"), (30, 180, "0.5-3 h"), (180, 600, "3-10 h"), (600, 1e9, ">10 h")]
        P("\nROI by age of the selected side's best quote, edge >= 3% (blend / consensus-only line shopping):\n")
        P("| age | season | blend ROI (n) | line-shopping ROI (n) |")
        P("|---|---|---|---|")
        ager = {}
        for se in [None] + list(seasons):
            for lo_, hi_, lab in edges:
                x1 = b3b[(b3b.age > lo_) & (b3b.age <= hi_)] if lo_ > -1e8 else b3b[b3b.age <= hi_]
                x2 = b3[(b3.age > lo_) & (b3.age <= hi_)] if lo_ > -1e8 else b3[b3.age <= hi_]
                if se is not None:
                    x1, x2 = x1[x1.season == se], x2[x2.season == se]
                if len(x1) < 30 and len(x2) < 30:
                    continue
                r1, r2 = summarize(x1, 200), summarize(x2, 200)
                ager[f"{se}|{lab}"] = {"blend": r1, "shop": r2}
                P(f"| {lab} | {'all' if se is None else int(se)} | {fmt(r1, False)} | {fmt(r2, False)} |")
        res["age_buckets"] = ager

    Path(out_json).write_text(json.dumps(res, default=float, indent=1))
    Path(out_md).write_text("\n".join(md) + "\n")
    print("\n".join(md))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--consensus", nargs="+", required=True)
    ap.add_argument("--extras", default=None, help="parquet from scripts/nba_bp_price_extras.py")
    ap.add_argument("--preds-cache", required=True)
    ap.add_argument("--local-dir", default=None)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ev, meta, tables = load_model(a.preds_cache, a.local_dir)
    cons = load_consensus(a.consensus, a.extras)
    log.info("consensus rows %d", len(cons))
    run(ev, meta, tables, cons, a.out_json, a.out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
