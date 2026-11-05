"""MEASURE how an NFL offence's play calling and efficiency respond to the score and the clock (nothing here is assumed).

    python -m nfl.sim.script_state --seasons 2018-2025 [--out table.md]

The drive-simulator's premise is that trailing teams pass more and leading teams run more as the game state evolves. The paper
this work started from put the response at "trailing teams pass 12-16% more". This module measures it from nflverse pbp:

  expected pass rate   a logistic model of P(pass) on down, distance and field position ONLY (no score, no clock), i.e. what
                       the situation alone calls for
  PROE                 pass rate over expected = observed - expected, per (clock state x score state) cell
  relative response    pass rate of a trailing state / pass rate of the matching leading state - 1, with a game-clustered bootstrap
                       interval (plays inside one game are not independent)
  efficiency           completion %, yards per attempt, sack % and yards per carry by script class, so the simulator knows
                       whether a changed play mix also changes what each play yields

Plays are regular-season regulation scrimmage runs and passes (sacks count as passes), exactly the simulator's fitting set.
"""
from __future__ import annotations

import argparse
import sys
from typing import Optional

import numpy as np
import pandas as pd

from nfl.sim import fit as F
from nfl.sim import pbp_prep as PP


def expected_pass(plays: pd.DataFrame, neutral_w: Optional[np.ndarray] = None) -> np.ndarray:
    """P(pass) from the situation alone. Fits the neutral model on `plays` itself unless weights are supplied."""
    X = F.call_design(plays["down"], plays["ytg"], plays["yl"])
    if neutral_w is None:
        neutral_w = F.fit_logistic(X, plays["is_pass"].to_numpy(float), 2.0)
    return F.sigmoid(X @ neutral_w)


def cell_table(plays: pd.DataFrame) -> pd.DataFrame:
    pl = plays.copy()
    pl["exp"] = expected_pass(pl)
    g = pl.groupby(["tb", "sb"]).agg(n=("is_pass", "size"), pass_rate=("is_pass", "mean"), expected=("exp", "mean")).reset_index()
    g["proe"] = g["pass_rate"] - g["expected"]
    return g


def _rate(pl: pd.DataFrame, mask) -> tuple:
    m = mask(pl)
    if not m.any():
        return float("nan"), float("nan"), 0
    return float(pl.loc[m, "is_pass"].mean()), float(pl.loc[m, "exp"].mean()), int(m.sum())


# contrasts: (label, trailing mask, leading mask)
CONTRASTS = [
    ("2nd half, down 9+ vs up 9+", lambda d: (d["tb"] >= 4) & (d["sd"] <= -9), lambda d: (d["tb"] >= 4) & (d["sd"] >= 9)),
    ("2nd half, down 1-8 vs up 1-8", lambda d: (d["tb"] >= 4) & d["sd"].between(-8, -1), lambda d: (d["tb"] >= 4) & d["sd"].between(1, 8)),
    ("Q4, down 1-8 vs up 1-8", lambda d: (d["tb"] >= 5) & d["sd"].between(-8, -1), lambda d: (d["tb"] >= 5) & d["sd"].between(1, 8)),
    ("Q4 last 10 min, down 1-8 vs up 1-8", lambda d: (d["tb"] >= 6) & d["sd"].between(-8, -1), lambda d: (d["tb"] >= 6) & d["sd"].between(1, 8)),
    ("Q4, down 9+ vs up 9+", lambda d: (d["tb"] >= 5) & (d["sd"] <= -9), lambda d: (d["tb"] >= 5) & (d["sd"] >= 9)),
    ("1st half, down 1-8 vs up 1-8", lambda d: (d["tb"] <= 3) & d["sd"].between(-8, -1), lambda d: (d["tb"] <= 3) & d["sd"].between(1, 8)),
    ("1st half, down 9+ vs up 9+", lambda d: (d["tb"] <= 3) & (d["sd"] <= -9), lambda d: (d["tb"] <= 3) & (d["sd"] >= 9)),
    ("all plays, down 1-8 vs up 1-8", lambda d: d["sd"].between(-8, -1), lambda d: d["sd"].between(1, 8)),
]


def _contrast_stats(pl: pd.DataFrame, trail, lead) -> dict:
    pt, et, nt = _rate(pl, trail)
    pb, eb, nb = _rate(pl, lead)
    return {"n_trail": nt, "n_lead": nb, "pass_trail": pt, "pass_lead": pb, "rel_raw": pt / pb - 1.0,
            "exp_trail": et, "exp_lead": eb, "rel_vs_expected": (pt / et) / (pb / eb) - 1.0,
            "proe_gap": (pt - et) - (pb - eb)}


def contrasts(plays: pd.DataFrame, n_boot: int = 300, seed: int = 0) -> pd.DataFrame:
    """Relative pass-rate responses with a game-clustered bootstrap 95% interval (resampling whole games)."""
    pl = plays.copy()
    pl["exp"] = expected_pass(pl)
    rng = np.random.default_rng(seed)
    gid, games = pd.factorize(pl["game_id"])
    out = []
    for label, trail, lead in CONTRASTS:
        base = _contrast_stats(pl, trail, lead)
        # per-game sufficient statistics (n, passes, expected passes) of both sides, then resample whole games
        agg = []
        for mask in (trail(pl).to_numpy(), lead(pl).to_numpy()):
            agg.append(np.column_stack([np.bincount(gid[mask], minlength=len(games)),
                                        np.bincount(gid[mask], weights=pl["is_pass"].to_numpy()[mask], minlength=len(games)),
                                        np.bincount(gid[mask], weights=pl["exp"].to_numpy()[mask], minlength=len(games))]))
        boots_raw, boots_exp = [], []
        for _ in range(n_boot):
            pick = rng.integers(0, len(games), size=len(games))
            (nt, pt, et), (nb, pb, eb) = [a[pick].sum(0) for a in agg]
            boots_raw.append((pt / nt) / (pb / nb) - 1.0)
            boots_exp.append(((pt / et)) / ((pb / eb)) - 1.0)
        base["label"] = label
        base["raw_lo"], base["raw_hi"] = np.percentile(boots_raw, [2.5, 97.5])
        base["exp_lo"], base["exp_hi"] = np.percentile(boots_exp, [2.5, 97.5])
        out.append(base)
    return pd.DataFrame(out)


def efficiency_by_script(plays: pd.DataFrame) -> pd.DataFrame:
    pl = plays.copy()
    rows = []
    for k, name in enumerate(["normal", "chasing (down 9+, 2nd half)", "milking (up 9+, 2nd half)"]):
        d = pl[pl["script"] == k]
        pas, thr = d[d["is_pass"] == 1], d[(d["is_pass"] == 1) & (d["sack"] == 0)]
        run = d[d["is_pass"] == 0]
        rows.append({"script": name, "plays": len(d), "pass_rate": d["is_pass"].mean(),
                     "cmp_pct": thr["complete"].mean(), "yds_per_att": thr["yards"].mean(),
                     "sack_pct": pas["sack"].mean(), "int_pct": thr["intc"].mean(), "ypc": run["yards"].mean()})
    return pd.DataFrame(rows)


def team_game_pass_attempts(plays: pd.DataFrame) -> dict:
    """Per team-game: pass attempts and the largest deficit / lead faced after halftime (a coarse game-level view)."""
    pl = plays[plays["tb"] >= 4]
    g = pl.groupby(["game_id", "posteam"]).agg(att=("is_pass", "sum"), n=("is_pass", "size"), min_sd=("sd", "min"),
                                               mean_sd=("sd", "mean")).reset_index()
    g["rate"] = g["att"] / g["n"]
    trail = g[g["mean_sd"] <= -4]
    lead = g[g["mean_sd"] >= 4]
    return {"trail_games": len(trail), "lead_games": len(lead), "trail_rate": float(trail["att"].sum() / trail["n"].sum()),
            "lead_rate": float(lead["att"].sum() / lead["n"].sum()),
            "trail_att_per_game": float(trail["att"].mean()), "lead_att_per_game": float(lead["att"].mean())}


def render(plays: pd.DataFrame, n_boot: int = 300) -> str:
    L = []
    cells = cell_table(plays)
    L.append("### Pass rate over expected (PROE) by clock state x score state (offence's lead)\n")
    piv = cells.pivot(index="tb", columns="sb", values="proe")
    piv.index = [PP.TB_NAMES[i] for i in piv.index]
    piv.columns = [PP.SB_NAMES[i] for i in piv.columns]
    L.append("| clock \\ score | " + " | ".join(piv.columns) + " |")
    L.append("|---|" + "---:|" * len(piv.columns))
    for idx, row in piv.iterrows():
        L.append(f"| {idx} | " + " | ".join(f"{v * 100:+.1f}" if v == v else "" for v in row) + " |")
    L.append("\n(percentage points of pass rate above what down / distance / field position alone predict)\n")
    ct = contrasts(plays, n_boot=n_boot)
    L.append("### Relative pass-rate response, trailing vs leading (game-clustered bootstrap 95% CI)\n")
    L.append("| contrast | n trail / lead | pass % trail / lead | raw relative | CI | vs-expected relative | CI |")
    L.append("|---|---|---|---:|---|---:|---|")
    for r in ct.itertuples():
        L.append(f"| {r.label} | {r.n_trail:,} / {r.n_lead:,} | {r.pass_trail * 100:.1f} / {r.pass_lead * 100:.1f} | "
                 f"{r.rel_raw * 100:+.1f}% | [{r.raw_lo * 100:+.1f}, {r.raw_hi * 100:+.1f}] | {r.rel_vs_expected * 100:+.1f}% | "
                 f"[{r.exp_lo * 100:+.1f}, {r.exp_hi * 100:+.1f}] |")
    L.append("\n### Efficiency by script class\n")
    e = efficiency_by_script(plays)
    L.append("| script | plays | pass % | cmp % | yds/att | sack % | int % | ypc |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in e.itertuples():
        L.append(f"| {r.script} | {r.plays:,} | {r.pass_rate * 100:.1f} | {r.cmp_pct * 100:.1f} | {r.yds_per_att:.2f} | "
                 f"{r.sack_pct * 100:.1f} | {r.int_pct * 100:.2f} | {r.ypc:.2f} |")
    tg = team_game_pass_attempts(plays)
    L.append(f"\nTeam-game view (second half, mean lead <= -4 vs >= +4): pass rate {tg['trail_rate'] * 100:.1f}% vs {tg['lead_rate'] * 100:.1f}% "
             f"({tg['trail_rate'] / tg['lead_rate'] - 1:+.1%}); second-half attempts per team-game {tg['trail_att_per_game']:.1f} vs "
             f"{tg['lead_att_per_game']:.1f} ({tg['trail_games']:,} / {tg['lead_games']:,} team-games).")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", default="2018-2025")
    ap.add_argument("--boot", type=int, default=300)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    lo, _, hi = a.seasons.partition("-")
    seasons = list(range(int(lo), int(hi or lo) + 1))
    t = PP.prep(PP.load_pbp(seasons))
    txt = render(t.plays, a.boot)
    print(txt)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
