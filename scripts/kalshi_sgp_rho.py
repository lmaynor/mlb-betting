#!/usr/bin/env python
"""
Same-game parlay correlation from REAL Kalshi combos: what the market charges (implied) vs what actually happens (realized).

  python scripts/kalshi_rfq_replay.py legs --combos combos.jsonl --out legs.jsonl        # fair p per leg at creation (see that script)
  python scripts/kalshi_sgp_rho.py implied  --legs legs.jsonl
  python scripts/kalshi_sgp_rho.py realized --legs legs.jsonl            # public API: settled leg results (no key)

implied   2-leg combos whose both legs are priced. The traded YES price y is the market's joint; with leg marginals q1, q2 (side-adjusted) the
          Gaussian-copula rho that reproduces y is the market-implied correlation. y carries the platform's markup, so cross-game 2-leg combos
          (independent by construction) give the baseline `y / (q1 q2)`; the margin-adjusted rho divides y by it.
realized  all pairs of distinct priced legs inside one game (YES/YES), outcomes from the settled leg markets, rho by Gaussian-copula MLE with the
          fair p as marginals, game-clustered bootstrap CI. Pairs are NOT chosen by retail (only the legs are), so there is no combo-selection bias.
Compare both with what the pricer assumes (SGP_CORR_TABLE keyed by (relation, stat_a, stat_b), else the per-relation pooled
default: 0.081 for different-player pairs, NO default for unmeasured same-player pairs -- see ev.kalshi_rfq_pricer.lookup_sgp_rho).
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from itertools import combinations

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev.kalshi_rfq_pricer import (  # noqa: E402
    SGP_RELATION_SAME_TEAM_DIFF_PLAYER,
    PricerConfig,
    derive_sgp_relation,
    lookup_sgp_rho,
)

SQRT = math.sqrt
_GL_X, _GL_W = np.polynomial.legendre.leggauss(24)
_ND = __import__("statistics").NormalDist()


def _phi(x):
    return np.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _Phi(x):
    from scipy.special import ndtr
    return ndtr(x)


def bvn_cdf(h, k, rho):
    """P(Z1 <= h, Z2 <= k) for corr rho, vectorised: Phi(h)Phi(k) + integral_0^rho of the bivariate density over the correlation."""
    h, k, rho = np.broadcast_arrays(np.asarray(h, float), np.asarray(k, float), np.asarray(rho, float))
    r = 0.5 * rho[..., None] * (_GL_X + 1.0)                                  # nodes on [0, rho]
    dens = np.exp(-(h[..., None] ** 2 - 2 * r * h[..., None] * k[..., None] + k[..., None] ** 2) / (2 * (1 - r * r))) / (2 * math.pi * np.sqrt(1 - r * r))
    return _Phi(h) * _Phi(k) + 0.5 * rho * (dens * _GL_W).sum(axis=-1)


def implied_rho(q1, q2, y, lo=-0.95, hi=0.95):
    """Vectorised bisection: the rho with P(both) = y. Rows whose y lies outside the Frechet bounds are clipped to +-0.95 and flagged."""
    q1, q2, y = (np.asarray(v, float) for v in (q1, q2, y))
    z1, z2 = np.vectorize(_ND.inv_cdf)(np.clip(q1, 1e-4, 1 - 1e-4)), np.vectorize(_ND.inv_cdf)(np.clip(q2, 1e-4, 1 - 1e-4))
    a, b = np.full_like(y, lo), np.full_like(y, hi)
    for _ in range(40):
        m = 0.5 * (a + b)
        up = bvn_cdf(z1, z2, m) < y                                          # joint rises with rho: too small -> raise rho
        a, b = np.where(up, m, a), np.where(up, b, m)
    rho = 0.5 * (a + b)
    inside = (y > np.maximum(0, q1 + q2 - 1)) & (y < np.minimum(q1, q2))
    return rho, inside


def _q(leg):
    return leg["p_yes"] if leg["side"] == "yes" else 1.0 - leg["p_yes"]


def pricer_rho(stat_a, stat_b, relation=SGP_RELATION_SAME_TEAM_DIFF_PLAYER, cfg=None):
    """What the pricer assumes for a (relation, stat pair): the measured cell, else the per-relation pooled default
    (0.081 different-player; None for an unmeasured same-player pair -- no default prices it). The default relation is
    the pooled different-player bucket, which is what every pre-taxonomy caller meant; pass the pair's real relation
    (derive_sgp_relation over the legs' player / team) wherever the legs carry it."""
    return lookup_sgp_rho(relation, stat_a, stat_b, cfg if cfg is not None else PricerConfig())


def _pair_relation(l1, l2):
    """Relation of a banked leg pair, via the pricer's own mapping (derive_sgp_relation). Banked legs carry `player`
    and, since the taxonomy change, `team` when known; older legs files have no `team`, and those pairs fall back to
    the pooled different-player bucket exactly as the pricer's legs do."""
    return derive_sgp_relation(l1.get("player"), l1.get("team"), l2.get("player"), l2.get("team"))


def cmd_implied(a):
    rows = [json.loads(l) for l in open(a.legs)]
    two = [r for r in rows if r["n_legs"] == 2 and len(r["legs"]) == 2]
    print(f"combos with exactly 2 legs, both priced: {len(two)}")
    cross = [r for r in two if r["legs"][0]["game_key"] != r["legs"][1]["game_key"]]
    same = [r for r in two if r["legs"][0]["game_key"] == r["legs"][1]["game_key"]]
    print(f"cross-game {len(cross)} | same-game {len(same)}")

    def lifts(rs):
        return np.array([r["yes_price"] / max(_q(r["legs"][0]) * _q(r["legs"][1]), 1e-6) for r in rs])
    base = float(np.median(lifts(cross))) if cross else 1.0
    print(f"cross-game baseline y/(q1 q2): median {base:.3f}  mean {float(lifts(cross).mean()) if cross else float('nan'):.3f}  "
          f"(independent legs priced at this multiple of the product = our marginal error + the platform's markup)")
    by = defaultdict(list)
    for r in same:
        l1, l2 = r["legs"]
        # Group by (relation, stat pair): the taxonomy key. A stat pair alone conflates same-player pairs (rho ~0.70) with
        # different-player pairs (rho ~0.1-0.3), which is the hole this grouping now closes.
        by[(tuple(sorted((f"{l1['stat']}:{l1['side']}", f"{l2['stat']}:{l2['side']}"))), _pair_relation(l1, l2))].append(r)
    out = []
    for (k, rel), rs in by.items():
        if len(rs) < a.min_n:
            continue
        q1 = np.array([_q(r["legs"][0]) for r in rs]); q2 = np.array([_q(r["legs"][1]) for r in rs]); y = np.array([r["yes_price"] for r in rs])
        rho_raw, ok = implied_rho(q1, q2, y)
        rho_adj, ok2 = implied_rho(q1, q2, y / base)
        l = y / np.maximum(q1 * q2, 1e-6)
        a_stat, b_stat = k[0].split(":")[0], k[1].split(":")[0]
        out.append((len(rs), k, rel, float(np.median(q1)), float(np.median(q2)), float(np.median(l)), float(np.median(rho_raw[ok])) if ok.any() else float("nan"),
                    float(np.median(rho_adj[ok2])) if ok2.any() else float("nan"), float(np.mean(ok)), pricer_rho(a_stat, b_stat, rel), len({r["day"] for r in rs})))
    out.sort(reverse=True)
    print(f"\n{'n':>5} {'pair (stat:side)':46s} {'relation':24s} {'q1':>5} {'q2':>5} {'lift':>5} {'rho_imp':>8} {'rho_adj':>8} {'in-bnd':>6} {'pricer':>6} {'days':>4}")
    for n, k, rel, m1, m2, lf, ra, rj, fr, pr, nd in out[:a.top]:
        pr_s = f"{pr:6.2f}" if pr is not None else "  none"          # an unmeasured same-player cell: the pricer has NO value for it
        print(f"{n:5d} {' x '.join(k):46s} {rel:24s} {m1:5.2f} {m2:5.2f} {lf:5.2f} {ra:8.3f} {rj:8.3f} {fr:6.2f} {pr_s} {nd:4d}")
    allq1 = np.array([_q(r["legs"][0]) for r in same]); allq2 = np.array([_q(r["legs"][1]) for r in same]); ally = np.array([r["yes_price"] for r in same])
    if len(same):
        ra, ok = implied_rho(allq1, allq2, ally); rj, ok2 = implied_rho(allq1, allq2, ally / base)
        print(f"\nALL same-game 2-leg: n={len(same)} median implied rho {np.median(ra[ok]):.3f} | margin-adjusted {np.median(rj[ok2]):.3f} | pricer different-player default {PricerConfig().sgp_diff_player_default_rho}")


def fetch_results(tickers, session=None, pause=0.3):
    import time
    import requests
    s = session or requests
    out = {}
    tickers = sorted(set(tickers))
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        for attempt in range(6):
            r = s.get("https://api.elections.kalshi.com/trade-api/v2/markets", params={"tickers": ",".join(chunk), "limit": 1000}, timeout=40)
            if r.status_code == 200:
                for m in r.json().get("markets", []):
                    if m.get("result") in ("yes", "no"):
                        out[m["ticker"]] = 1 if m["result"] == "yes" else 0
                break
            time.sleep(1.5 * (attempt + 1))
        time.sleep(pause)
    return out


def load_results(legs, cache=None, pause=0.3):
    """{ticker: 0/1} for every leg; a pickle cache avoids re-fetching ~9k settled markets on every run."""
    import pickle
    have = {}
    if cache and os.path.exists(cache):
        with open(cache, "rb") as fh:
            have = pickle.load(fh)
    need = [t for t in legs if t not in have]
    if need:
        have.update(fetch_results(need, pause=pause))
        if cache:
            with open(cache, "wb") as fh:
                pickle.dump(have, fh)
    return {t: have[t] for t in legs if t in have}


def mle_rho(p1, p2, o1, o2, grid=np.linspace(-0.9, 0.9, 91)):
    z1 = np.vectorize(_ND.inv_cdf)(np.clip(1 - p1, 1e-4, 1 - 1e-4)); z2 = np.vectorize(_ND.inv_cdf)(np.clip(1 - p2, 1e-4, 1 - 1e-4))
    best, best_ll = 0.0, -1e18
    for rho in grid:
        p11 = np.clip(bvn_cdf(-z1, -z2, np.full_like(p1, rho)), 1e-9, None)         # both over  <=>  Z1 > zc1 and Z2 > zc2
        cells = np.where((o1 == 1) & (o2 == 1), p11, np.where((o1 == 1), p1 - p11, np.where(o2 == 1, p2 - p11, 1 - p1 - p2 + p11)))
        ll = float(np.log(np.clip(cells, 1e-9, None)).sum())
        if ll > best_ll:
            best, best_ll = float(rho), ll
    return best


def cmd_realized(a):
    rows = [json.loads(l) for l in open(a.legs)]
    legs = {}
    for r in rows:
        for l in r["legs"]:
            legs.setdefault(l["ticker"], []).append(l)
    res = load_results(legs, a.results_cache, a.pause)
    print(f"unique priced legs {len(legs)} | settled {len(res)}")
    by_game = defaultdict(list)
    for tk, ls in legs.items():
        if tk in res:
            ps = sorted(x["p_yes"] for x in ls)
            by_game[ls[0]["game_key"]].append({"ticker": tk, "stat": ls[0]["stat"], "player": ls[0]["player"], "team": ls[0].get("team"),
                                               "p": ps[len(ps) // 2], "o": res[tk]})
    rng = np.random.default_rng(3)
    pairs = defaultdict(lambda: {"p1": [], "p2": [], "o1": [], "o2": [], "g": []})
    for g, ls in by_game.items():
        cand = [(x, y) for x, y in combinations(ls, 2) if not (x["player"] == y["player"] and x["stat"] == y["stat"]) and 0.03 < x["p"] < 0.97 and 0.03 < y["p"] < 0.97]
        if len(cand) > a.max_pairs_per_game:
            cand = [cand[i] for i in rng.choice(len(cand), a.max_pairs_per_game, replace=False)]
        for x, y in cand:
            # The pair key IS the taxonomy key: (stat, stat, relation). Pre-taxonomy runs labelled the third element
            # "same player" / "different players"; legs banked before the taxonomy carry no `team`, so their
            # different-player pairs land in the pooled same_team_diff_player bucket via derive_sgp_relation.
            k = tuple(sorted((x["stat"], y["stat"]))) + (derive_sgp_relation(x["player"], x["team"], y["player"], y["team"]),)
            if x["stat"] != k[0]:
                x, y = y, x
            d = pairs[k]; d["p1"].append(x["p"]); d["p2"].append(y["p"]); d["o1"].append(x["o"]); d["o2"].append(y["o"]); d["g"].append(g)
    print(f"\n{'pair (stat x stat / relation, YES/YES)':48s} {'pairs':>6} {'games':>5} {'emp P11':>8} {'indep':>7} {'lift':>5} {'rho_mle':>8} {'95% CI (game bootstrap)':>26} {'pricer':>6}")
    for k, d in sorted(pairs.items(), key=lambda kv: -len(kv[1]["g"])):
        n = len(d["g"])
        if n < a.min_n:
            continue
        p1, p2, o1, o2, g = (np.array(d[x]) for x in ("p1", "p2", "o1", "o2", "g"))
        if n > a.max_pairs_per_type:                                            # keep the bootstrap affordable: uniform subsample, games kept
            keep = np.sort(rng.choice(n, a.max_pairs_per_type, replace=False))
            p1, p2, o1, o2, g = p1[keep], p2[keep], o1[keep], o2[keep], g[keep]
        rho = mle_rho(p1, p2, o1, o2)
        games = sorted(set(g)); idx = {gg: np.where(g == gg)[0] for gg in games}
        boots = []
        for _ in range(a.boot):
            pick = np.concatenate([idx[games[i]] for i in rng.integers(0, len(games), len(games))])
            boots.append(mle_rho(p1[pick], p2[pick], o1[pick], o2[pick], grid=np.linspace(-0.9, 0.9, 37)))
        lo, hi = np.percentile(boots, [2.5, 97.5])
        emp = float((o1 * o2).mean()); ind = float((p1 * p2).mean())
        n = len(g)
        pr = pricer_rho(k[0], k[1], k[2])
        pr_s = f"{pr:6.2f}" if pr is not None else "  none"          # an unmeasured same-player cell: the pricer has NO value for it
        print(f"{' x '.join(k[:2]) + ' / ' + k[2]:48s} {n:6d} {len(games):5d} {emp:8.3f} {ind:7.3f} {emp / ind:5.2f} {rho:8.3f} {f'[{lo:.3f}, {hi:.3f}]':>26} {pr_s}")


def cmd_calibration(a):
    """Single-leg calibration: our fair P(YES) (median over the combos a leg appeared in) against the settled outcome, by sport and bin."""
    rows = [json.loads(l) for l in open(a.legs)]
    legs = {}
    for r in rows:
        for l in r["legs"]:
            legs.setdefault(l["ticker"], []).append(l)
    res = load_results(legs, a.results_cache, a.pause)
    recs = [(sorted(x["p_yes"] for x in ls)[len(ls) // 2], res[t], ls[0]["game_key"].split(":")[0], ls[0]["pooled"]) for t, ls in legs.items() if t in res]
    p = np.array([r[0] for r in recs]); o = np.array([r[1] for r in recs]); sp = np.array([r[2] for r in recs]); pool = np.array([r[3] for r in recs])
    bins = [0, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 1.0]
    print(f"settled legs {len(recs)} | mean p {p.mean():.3f} | realized {o.mean():.3f}")
    for name, mask in (("ALL", np.ones(len(p), bool)), ("NFL", sp == "nfl"), ("NBA", sp == "nba"), ("NHL", sp == "nhl"), ("exact rung", ~pool), ("curve/ladder", pool)):
        print(f"\n{name} (n={int(mask.sum())})")
        for lo, hi in zip(bins[:-1], bins[1:]):
            k = mask & (p >= lo) & (p < hi)
            if k.sum() >= 30:
                se = math.sqrt(max(o[k].mean() * (1 - o[k].mean()), 1e-9) / k.sum())
                print(f"  p in [{lo:.2f},{hi:.2f}) n={int(k.sum()):5d} mean p {p[k].mean():.3f} realized {o[k].mean():.3f} diff {o[k].mean() - p[k].mean():+.3f} (se {se:.3f})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("implied"); i.add_argument("--legs", required=True); i.add_argument("--min-n", type=int, default=40); i.add_argument("--top", type=int, default=40)
    r = sub.add_parser("realized"); r.add_argument("--legs", required=True); r.add_argument("--min-n", type=int, default=300)
    r.add_argument("--max-pairs-per-game", type=int, default=3000); r.add_argument("--max-pairs-per-type", type=int, default=20000); r.add_argument("--boot", type=int, default=60); r.add_argument("--pause", type=float, default=0.3)
    r.add_argument("--results-cache", default=None)
    c = sub.add_parser("calibration"); c.add_argument("--legs", required=True); c.add_argument("--results-cache", default=None); c.add_argument("--pause", type=float, default=0.3)
    a = ap.parse_args(argv)
    {"implied": cmd_implied, "realized": cmd_realized, "calibration": cmd_calibration}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
