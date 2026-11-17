"""contest_sportsbook_research -- measure what the contest's sportsbook features need, from cached past games
(scripts/contest_history_pull.py): the spread / total error (cash-out and alt-line volatility), how well a normal model prices alternate
lines out of sample, same-game correlation (SGP), key numbers, teaser break-evens, and the vig. Results go to
docs/analysis/contest_sportsbook_experiments_2026-11-17.md. Research only: stdlib, deterministic (fixed seed), writes nothing but stdout.

  python3 scripts/contest_sportsbook_research.py ~/contest_history/cfb_2025.jsonl
  python3 scripts/contest_sportsbook_research.py ~/contest_history/cbb_sample.jsonl --label "college basketball" --teaser-points 4 4.5 5

Conventions: `spread_home` is the HOME handicap (-6.5: home favoured). Home covers when margin + spread_home > 0; over when total points
> total. Pushes are dropped from rates. Closing lines are ESPN's pre-game provider lines (DraftKings / ESPN BET); n is small, so every
rate carries a 95% interval and nothing here is a claim about edge.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from statistics import NormalDist

N = NormalDist()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))      # the margin model under test lives in contest/
from contest.margin_model import fit_spread_mu, win_lose_push, _fair  # noqa: E402


FITTED_WEIGHTS = {}


def load(paths):
    rows = []
    for p in paths:
        for line in open(os.path.expanduser(p)):
            r = json.loads(line)
            if r.get("spread_home") is None or r.get("total") is None:
                continue
            r["margin"] = r["home_score"] - r["away_score"]
            r["points"] = r["home_score"] + r["away_score"]
            r["res_m"] = r["margin"] + r["spread_home"]            # > 0: home covered
            r["res_t"] = r["points"] - r["total"]                  # > 0: over
            rows.append(r)
    rows.sort(key=lambda r: (r.get("day") or r["date"], r["id"]))
    return rows


def dec(a):
    return 1 + (100 / -a if a < 0 else a / 100)


def devig(a, b):
    x, y = 1 / dec(a), 1 / dec(b)
    return x / (x + y)


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def boot_ci(vals, fn, reps=1000, seed=7):
    rng = random.Random(seed)
    n = len(vals)
    xs = sorted(fn([vals[rng.randrange(n)] for _ in range(n)]) for _ in range(reps))
    return xs[int(0.025 * reps)], xs[int(0.975 * reps)]


def rms(xs):
    return math.sqrt(sum(x * x for x in xs) / len(xs))


def pearson(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return sxy / math.sqrt(sxx * syy)


def bvn_upper(a, b, r, steps=4000):
    """P(Z1 > a, Z2 > b) for a standard bivariate normal with correlation r (numeric integration over Z1)."""
    lo, hi = max(a, -8.0), 8.0
    if hi <= lo:
        return 0.0
    h = (hi - lo) / steps
    s = 0.0
    for i in range(steps + 1):
        z = lo + i * h
        f = N.pdf(z) * (1 - N.cdf((b - r * z) / math.sqrt(1 - r * r)))
        s += f * (0.5 if i in (0, steps) else 1.0)
    return s * h


def section(title):
    print(f"\n## {title}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--label", default="college football")
    ap.add_argument("--teaser-points", type=float, nargs="+", default=[6.0, 6.5, 7.0])
    a = ap.parse_args(argv)
    rows = load(a.files)
    n = len(rows)
    print(f"# {a.label}: {n} games with closing lines, {rows[0].get('day')} .. {rows[-1].get('day')}")

    # ---------------------------------------------------------------------------------------------- 1. volatility
    section("1. How far the final result lands from the closing line (the sigma for cash out and alt lines)")
    rm, rt = [r["res_m"] for r in rows], [r["res_t"] for r in rows]
    sm, st_ = rms(rm), rms(rt)
    (lm, hm), (lt, ht) = boot_ci(rm, rms), boot_ci(rt, rms)
    print(f"- spread: error mean {statistics.mean(rm):+.2f} (se {statistics.pstdev(rm) / math.sqrt(n):.2f}), sigma {sm:.2f}  [95% {lm:.2f} .. {hm:.2f}]")
    print(f"- total:  error mean {statistics.mean(rt):+.2f} (se {statistics.pstdev(rt) / math.sqrt(n):.2f}), sigma {st_:.2f}  [95% {lt:.2f} .. {ht:.2f}]")
    for name, key in (("spread", "res_m"), ("total", "res_t")):
        xs = [r[key] for r in rows]
        sd = rms(xs)
        within1 = sum(abs(x) <= sd for x in xs) / n
        within2 = sum(abs(x) <= 2 * sd for x in xs) / n
        print(f"- {name} normality: {within1:.1%} within 1 sigma (normal 68.3%), {within2:.1%} within 2 sigma (normal 95.4%)")

    # ---------------------------------------------------------------------------------------------- 2. alt lines
    section("2. Alternate lines: a normal model fitted on the first 60% of games, scored on the last 40%")
    cut = int(n * 0.6)
    train, test = rows[:cut], rows[cut:]
    sig_m, sig_t = rms([r["res_m"] for r in train]), rms([r["res_t"] for r in train])
    print(f"fitted on {len(train)} games: sigma spread {sig_m:.2f}, sigma total {sig_t:.2f}; scored on {len(test)}\n")
    print("| market | shift | n | model | actual | diff | z |\n|---|---|---|---|---|---|---|")
    worst = 0.0
    for market, key, sig in (("spread", "res_m", sig_m), ("total", "res_t", sig_t)):
        for d in (-14, -10, -7, -3, -1, 1, 3, 7, 10, 14):
            # a bettor holding the main line moved d points in their favour wins when the residual plus d exceeds 0
            ok = [r for r in test if r[key] + d != 0]
            pm = [devig(r["home_spread_odds"], r["away_spread_odds"]) if market == "spread" else devig(r["over_odds"], r["under_odds"]) for r in ok]
            pred = statistics.mean(N.cdf(N.inv_cdf(min(max(p, 1e-6), 1 - 1e-6)) + d / sig) for p in pm)
            k = sum(r[key] + d > 0 for r in ok)
            emp = k / len(ok)
            se = math.sqrt(max(emp * (1 - emp), 1e-9) / len(ok))
            worst = max(worst, abs(emp - pred))
            print(f"| {market} | {d:+d} | {len(ok)} | {pred:.3f} | {emp:.3f} | {emp - pred:+.3f} | {(emp - pred) / se:+.1f} |")
    print(f"\nlargest gap between model and actual: {worst:.3f}")

    # ---------------------------------------------------------------------------------------------- 2b. key-number weights
    section("2b. Key-number weights in the margin model: fitted on the first 60%, scored on the last 40% (log loss, plain normal vs weighted)")
    shrink = 8.0                                                   # pseudo-games pulling every weight toward 1
    obs = defaultdict(int)
    for r in train:
        obs[abs(r["margin"])] += 1
    exp = defaultdict(float)
    for r in train:                                                # expected count of |margin| = k under the plain normal at each game's own line
        mu = -r["spread_home"]
        for k in range(1, 41):
            exp[k] += sum(N.cdf((m + 0.5 - mu) / sig_m) - N.cdf((m - 0.5 - mu) / sig_m) for m in (k, -k))
    weights = {k: (obs[k] + shrink) / (exp[k] + shrink) for k in range(1, 41)}
    print("weights (observed share over normal share, shrunk toward 1): " + ", ".join(f"{k}:{weights[k]:.2f}" for k in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 14, 17, 21)))
    shifts = [-10, -7, -6.5, -6, -3.5, -3, -2.5, -1.5, -1, -0.5, 0.5, 1, 1.5, 2.5, 3, 3.5, 6, 6.5, 7, 10]
    ll_plain, ll_w, cross_gap = [], [], []
    for r in test:
        L = r["spread_home"]
        p_main = devig(r["home_spread_odds"], r["away_spread_odds"])
        mu_p = fit_spread_mu(min(max(p_main, 0.001), 0.999), L, sig_m, None)
        mu_w = fit_spread_mu(min(max(p_main, 0.001), 0.999), L, sig_m, weights)
        loss_p = loss_w = 0.0
        n_used = 0
        for sh in shifts:
            h = L + sh
            if r["margin"] + h == 0:
                continue                                           # a push is not a win or a loss
            y = 1.0 if r["margin"] + h > 0 else 0.0
            pp = _fair(*win_lose_push(mu_p, sig_m, None, h)[:2])
            pw = _fair(*win_lose_push(mu_w, sig_m, weights, h)[:2])
            loss_p += -(y * math.log(max(pp, 1e-9)) + (1 - y) * math.log(max(1 - pp, 1e-9)))
            loss_w += -(y * math.log(max(pw, 1e-9)) + (1 - y) * math.log(max(1 - pw, 1e-9)))
            n_used += 1
        ll_plain.append(loss_p / max(n_used, 1))
        ll_w.append(loss_w / max(n_used, 1))
    diff = [x - y for x, y in zip(ll_plain, ll_w)]                 # positive: the weighted model has the lower loss
    dlo, dhi = boot_ci(diff, statistics.mean)
    print(f"\nmean log loss per priced alt line: plain normal {statistics.mean(ll_plain):.4f}, weighted {statistics.mean(ll_w):.4f}")
    print(f"improvement (plain minus weighted) {statistics.mean(diff):+.5f}  [95% {dlo:+.5f} .. {dhi:+.5f}], n={len(test)} test games, {len(shifts)} shifts each")
    print(f"weights tried: 1 (shrink {shrink:g}); thresholds were not tuned on the test games")
    FITTED_WEIGHTS.update(weights)

    # ---------------------------------------------------------------------------------------------- 3. key numbers
    section("3. Key numbers: how often a game lands exactly on each margin (all games, absolute final margin)")
    freq = defaultdict(int)
    for r in rows:
        freq[abs(r["margin"])] += 1
    print("| margin | games | share | neighbours' average | ratio |\n|---|---|---|---|---|")
    for k in (1, 2, 3, 4, 6, 7, 8, 10, 11, 13, 14, 15, 17, 21):
        nb = (freq[k - 1] + freq[k + 1]) / 2
        print(f"| {k} | {freq[k]} | {freq[k] / n:.1%} | {nb:.1f} | {freq[k] / nb if nb else float('nan'):.2f} |")

    # ---------------------------------------------------------------------------------------------- 4. SGP
    section("4. Same-game parlays: how often the spread and the total result move together")
    ok = [r for r in rows if r["res_m"] != 0 and r["res_t"] != 0]
    rho_all = pearson([r["res_m"] for r in ok], [r["res_t"] for r in ok])
    lo, hi = boot_ci(ok, lambda xs: pearson([x["res_m"] for x in xs], [x["res_t"] for x in xs]))
    print(f"- correlation of the spread error and the total error: {rho_all:+.3f}  [95% {lo:+.3f} .. {hi:+.3f}]  (n={len(ok)})")
    print("\nFavorite / underdog legs (home is the favorite when its handicap is negative):\n")
    print("| legs | n | P(first) | P(second) | independent | actual | lift | actual 95% | copula rho implied |\n|---|---|---|---|---|---|---|---|---|")
    combos = defaultdict(lambda: [0, 0, 0, 0])      # (fav covers?, over?) counts
    implied_rho = []
    for r in ok:
        fav_home = r["spread_home"] < 0
        fav_cover = (r["res_m"] > 0) == fav_home if r["spread_home"] != 0 else r["res_m"] > 0
        over = r["res_t"] > 0
        combos[(fav_cover, over)][0] += 1
    tot = sum(v[0] for v in combos.values())
    p_fc = sum(v[0] for (fc, _), v in combos.items() if fc) / tot
    p_ov = sum(v[0] for (_, ov), v in combos.items() if ov) / tot
    for fc, ov, label in ((True, True, "favorite covers + over"), (True, False, "favorite covers + under"), (False, True, "underdog covers + over"), (False, False, "underdog covers + under")):
        k = combos[(fc, ov)][0]
        p1 = p_fc if fc else 1 - p_fc
        p2 = p_ov if ov else 1 - p_ov
        actual = k / tot
        l, h = wilson(k, tot)
        implied = math.sin(2 * math.pi * (actual - p1 * p2)) if abs(2 * math.pi * (actual - p1 * p2)) < 1 else float("nan")   # near-median binary approximation
        print(f"| {label} | {tot} | {p1:.3f} | {p2:.3f} | {p1 * p2:.3f} | {actual:.3f} | {actual / (p1 * p2):.2f}x | {l:.3f}-{h:.3f} | {implied:+.2f} |")
        if implied == implied:
            implied_rho.append(implied if fc == ov else -implied)         # same orientation (fav+over, dog+under) counted as positive
    # moneyline x total: the straight-up winner is the favourite or the dog
    print("\nMoneyline favorite wins outright + over:")
    su = [r for r in ok if r["margin"] != 0]
    fav_wins = lambda r: (r["margin"] > 0) == (r["spread_home"] < 0) if r["spread_home"] != 0 else r["margin"] > 0
    k = sum(fav_wins(r) and r["res_t"] > 0 for r in su)
    pf, po = sum(fav_wins(r) for r in su) / len(su), sum(r["res_t"] > 0 for r in su) / len(su)
    l, h = wilson(k, len(su))
    print(f"- P(fav wins)={pf:.3f}, P(over)={po:.3f}, independent {pf * po:.3f}, actual {k / len(su):.3f} ({k / len(su) / (pf * po):.2f}x, 95% {l:.3f}-{h:.3f}), n={len(su)}")
    print("\nCopula check: the Gaussian-copula joint at the measured error correlation, against the actual 'favorite covers + over' rate:")
    for r_ in (0.0, rho_all, rho_all + 0.1):
        r_ = max(-0.95, min(0.95, r_))
        pj = bvn_upper(0.0, 0.0, r_)
        print(f"- rho {r_:+.3f} -> P(both at the median) {pj:.3f}  (independent 0.250)")

    # ---------------------------------------------------------------------------------------------- 5. teasers
    section("5. Teasers: win rate of a teased leg by line size, against what the fixed payout table needs")
    buckets = [("0.5-2.5", 0.5, 2.5), ("3-3.5", 3, 3.5), ("4-6.5", 4, 6.5), ("7-7.5", 7, 7.5), ("8-9.5", 8, 9.5), ("10-13.5", 10, 13.5), ("14+", 14, 99)]
    for pts in a.teaser_points:
        print(f"\nTeaser {pts:g} points, spread legs (win = side covers after the move; pushes dropped):\n")
        print("| role | line size | n | leg win rate | 95% |\n|---|---|---|---|---|")
        for role in ("favorite", "underdog"):
            for name, lo_, hi_ in buckets:
                k = m = 0
                for r in rows:
                    L = abs(r["spread_home"])
                    if not lo_ <= L <= hi_ or r["spread_home"] == 0:
                        continue
                    home_fav = r["spread_home"] < 0
                    side_home = home_fav if role == "favorite" else not home_fav
                    line = r["spread_home"] if side_home else -r["spread_home"]          # the handicap of the side picked
                    edge = (r["margin"] if side_home else -r["margin"]) + line + pts
                    if edge == 0:
                        continue
                    m += 1
                    k += edge > 0
                if m:
                    l, h = wilson(k, m)
                    print(f"| {role} | {name} | {m} | {k / m:.3f} | {l:.3f}-{h:.3f} |")
    print("\nBreak-even leg win rate for the placeholder table (all legs equal): 2-team -120 needs 73.9%, -130 needs 75.2%, -140 needs 76.4%; 3-team +160 needs 72.7%; 4-team +260 needs 72.6%.")

    # ---------------------------------------------------------------------------------------------- 6. vig
    section("6. The vig in these closing lines")
    holds = {"spread": [], "total": [], "moneyline": []}
    for r in rows:
        holds["spread"].append(1 / dec(r["home_spread_odds"]) + 1 / dec(r["away_spread_odds"]) - 1)
        holds["total"].append(1 / dec(r["over_odds"]) + 1 / dec(r["under_odds"]) - 1)
        if r.get("ml_home") is not None and r.get("ml_away") is not None:
            holds["moneyline"].append(1 / dec(r["ml_home"]) + 1 / dec(r["ml_away"]) - 1)
    for k, v in holds.items():
        print(f"- {k}: average hold {statistics.mean(v):.2%} (median {statistics.median(v):.2%}, n={len(v)})")

    # ---------------------------------------------------------------------------------------------- 7. suggested placeholders
    section("7. Suggested placeholder values (fitted on ALL games; copied into contest/rules.py by hand, still UNVALIDATED)")
    sa_m, sa_t = rms([r["res_m"] for r in rows]), rms([r["res_t"] for r in rows])
    obs_all = defaultdict(int)
    for r in rows:
        obs_all[abs(r["margin"])] += 1
    exp_all = defaultdict(float)
    for r in rows:
        mu = -r["spread_home"]
        for k in (3, 7):
            exp_all[k] += sum(N.cdf((m + 0.5 - mu) / sa_m) - N.cdf((m - 0.5 - mu) / sa_m) for m in (k, -k))
    w_all = {k: (obs_all[k] + shrink) / (exp_all[k] + shrink) for k in (3, 7)}
    print(f"- cashout_sigmas: spread {sa_m:.1f}, total {sa_t:.1f}")
    print(f"- key weights (3 and 7 only; the other margins are left at 1 because the out-of-sample test above cannot tell them apart): 3 -> {w_all[3]:.2f}, 7 -> {w_all[7]:.2f}")
    if implied_rho:
        print(f"- same-game spread x total copula rho: +/-{statistics.mean(abs(x) for x in implied_rho):.2f} (positive for favorite + over and underdog + under)")
    print(f"- hold: spread {statistics.mean(holds['spread']):.2%}, total {statistics.mean(holds['total']):.2%}")


if __name__ == "__main__":
    main()
