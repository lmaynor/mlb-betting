"""mm_sim.py -- simulate a small, queue-limited market maker on the Kalshi NFL prop tape (pre-kickoff only).

Model: our resting quotes sit at the touch (the tape's own prices); we get a fraction `phi` of each PASSIVE-side trade
(phi = our queue share), subject to a per-market net-inventory cap L (contracts) with a skew rule (no fills that push
|inventory| past L; above L/2 we refuse fills that add to it). Everything is held to settlement ($1/$0). Maker fee 0
(Kalshi 'quadratic' prop series). Optional `shock_c`: extra adverse selection in cents/contract on OUR fills (queue
position means we are filled disproportionately when the price is about to move through us).
Output: per-game P&L distribution, bootstrap CI over games, week-level loss probability, capital at risk."""
import glob
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "scripts")
from mm_analysis import load, prep   # noqa: E402

D = sys.argv[1]
SERIES = sys.argv[2].split(",") if len(sys.argv) > 2 else None
rng = np.random.default_rng(11)


def simulate(df, phi, L, shock_c=0.0, seed=0):
    """Per market, chronological. inv = net YES contracts (+long). Returns per-market P&L, fills, peak capital."""
    r = np.random.default_rng(seed)
    out = []
    for tk, g in df.sort_values("created_time").groupby("ticker", sort=False):
        inv = cash = filled = peak = 0.0
        y = g["y"].iloc[0]
        for p, n, ts in zip(g["p"].to_numpy(), g["n"].to_numpy(), g["taker_side"].to_numpy()):
            want = phi * n * (0.5 + r.random())                 # our queue share of this trade (mean phi*n)
            sign = -1.0 if ts == "yes" else 1.0                 # taker bought YES -> we sold YES (inv down)
            if abs(inv) > L / 2 and np.sign(sign) == np.sign(inv):
                continue                                         # skew: do not add to a large inventory
            take = want
            new = inv + sign * take
            if abs(new) > L and abs(new) > abs(inv):             # cap |inventory| at L (moving outward)
                take = max(0.0, L - abs(inv)) if np.sign(sign) == np.sign(inv) or inv == 0 else take
            if take <= 1e-9:
                continue
            cash += -sign * take * p            # sign=+1 (we buy YES): pay p ; sign=-1 (we sell YES): receive p
            cash -= take * shock_c / 100.0                       # extra adverse selection on OUR fills
            inv += sign * take
            filled += take
            peak = max(peak, abs(inv))
        out.append((tk, g["game"].iloc[0], g["day"].iloc[0], cash + inv * y, filled, peak))
    return pd.DataFrame(out, columns=["ticker", "game", "day", "pnl", "filled", "peak_inv"])


def summarize(res, phi, L, shock_c):
    gp = res.groupby("game").agg(pnl=("pnl", "sum"), filled=("filled", "sum"), peak=("peak_inv", "sum"))
    games = gp.index.to_numpy()
    boots = [gp.loc[rng.choice(games, len(games)), "pnl"].mean() for _ in range(2000)]
    wk = res.groupby("day").pnl.sum()
    return dict(phi=phi, L=L, shock_c=shock_c, games=len(gp), fills_per_game=gp.filled.mean(),
                edge_c=100 * gp.pnl.sum() / max(gp.filled.sum(), 1e-9), mean_game=gp.pnl.mean(), sd_game=gp.pnl.std(),
                ci_lo=np.percentile(boots, 2.5), ci_hi=np.percentile(boots, 97.5),
                p_game_loss=(gp.pnl < 0).mean(), p_day_loss=(wk < 0).mean(), worst_day=wk.min(), total=gp.pnl.sum())


if __name__ == "__main__":
    files = [f for f in sorted(glob.glob(f"{D}/KXNFL*.csv.gz")) if SERIES is None or any(s in f for s in SERIES)]
    df = pd.concat([prep(load(f)) for f in files])
    df = df[df["mtk"] > 15]                                        # quote only until 15 min pre-kick
    print(f"universe: {len(df):,} pre-kick trades, {df['ticker'].nunique():,} markets, {df['game'].nunique()} games; "
          f"series {sorted(df['series'].unique())}")
    rows = []
    for phi in (0.005, 0.01, 0.02, 0.05):
        for L in (200, 1000):
            for shock in (0.0, 0.5):
                rows.append(summarize(simulate(df, phi, L, shock, seed=3), phi, L, shock))
    t = pd.DataFrame(rows)
    pd.set_option("display.width", 250)
    t["phi"] = (t["phi"] * 100).round(1).astype(str) + "%"
    print(t.round(2).to_string(index=False))
