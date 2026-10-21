"""
mlb.analysis.sgp_proxy_scanner -- "Official market vs SGP paths" scanner.

A compound outcome (NBA triple-double, MLB 2+ total bases, ...) can often be bought
through several paths at different books:

  * the OFFICIAL pre-packaged prop, e.g. "Triple Double - Yes"
  * an exact multi-leg SGP of milestone legs, e.g. 10+P / 10+R / 10+A
  * a cheaper PROXY SGP that drops a near-certain leg, e.g. 10+R / 10+A

The scanner builds a consensus fair probability from the OFFICIAL market (>= min_books
books), prices every quoted path against it, and ranks by EV%.

EV% = (best_decimal * p_path - 1) * 100, with p_path = min(1, consensus * prob_ratio).
prob_ratio = P(path wins) / P(official wins): 1.0 for an exact identity, <1 when the path
is a strict subset of the official outcome, >1 when it is a strict superset (the 2-leg
proxy also wins when the dropped leg misses). The ratios are ASSUMPTIONS exposed as CLI
flags -- they are not measured here.

Read-only and offline: input is a quotes CSV (or --demo synthetic data). There is NO live
sportsbook endpoint scraping in this module; LiveBetslipQuoteProvider is an interface stub
on purpose (see its docstring). A CopulaFallbackPricer can MODEL an SGP price from
single-leg odds; modeled quotes are excluded from the ranking unless --include-modeled,
because a modeled price is not a price anyone offered.

    PYTHONPATH=. .venv/bin/python -m mlb.analysis.sgp_proxy_scanner --demo
    PYTHONPATH=. .venv/bin/python -m mlb.analysis.sgp_proxy_scanner --input quotes.csv --group nba_td

quotes.csv columns: player,event,book,path,american[,source]   (source: quoted|modeled)
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
from scipy.optimize import brentq
from scipy.stats import multivariate_normal, norm

from mlb_core.odds.utils import american_to_decimal, american_to_implied_prob, devig_unilateral, implied_to_american

QUOTED = "quoted"
MODELED = "modeled"


# -- odds math -----------------------------------------------------------------

def valid_american(odds) -> bool:
    """American odds are never strictly inside (-100, 100) and must be finite."""
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return False
    return math.isfinite(o) and (o >= 100 or o <= -100)


def prob_to_american_str(prob: float) -> str:
    a = implied_to_american(prob)
    if a != a:  # NaN
        return "n/a"
    return f"{int(a):+d}"


def devig_power(raw_probs: Sequence[float]) -> np.ndarray:
    """De-vig a mutually exclusive, exhaustive market with the power method: find k with
    sum(p_i ** k) == 1. Longshots are shaded harder than favourites, unlike proportional."""
    p = np.asarray(raw_probs, dtype=float)
    if p.sum() <= 1.0:
        return p / p.sum()
    return np.power(p, brentq(lambda k: float(np.sum(np.power(p, k)) - 1.0), 1.0, 50.0))


# -- model ---------------------------------------------------------------------

@dataclass(frozen=True)
class PathSpec:
    name: str
    prob_ratio: float = 1.0      # P(path wins) / P(official wins); see module docstring
    is_official: bool = False


@dataclass(frozen=True)
class MarketGroup:
    key: str
    title: str
    subtitle: str
    paths: tuple[PathSpec, ...]

    def path(self, name: str) -> PathSpec | None:
        return next((p for p in self.paths if p.name == name), None)

    @property
    def official(self) -> PathSpec:
        return next(p for p in self.paths if p.is_official)


def build_groups(td_pra_share: float = 0.999, conditional_pts_discount: float = 0.995) -> dict[str, MarketGroup]:
    """td_pra_share: share of official triple-doubles that are points/rebounds/assists
    (the 3-leg SGP is a subset of the official outcome). conditional_pts_discount:
    P(10+ pts | 10+ reb and 10+ ast); the 2-leg proxy wins whenever R and A hit, so
    P(R&A) = P(PRA) / discount."""
    nba = MarketGroup(
        key="nba_td",
        title="BEST OVERALL TRIPLE-DOUBLE VALUE (Market vs. SGP Paths):",
        subtitle="Comparing Official TD Markets against SGP proxies to find the highest possible payout.",
        paths=(
            PathSpec("Official TD", 1.0, True),
            PathSpec("SGP (10+P/10+R/10+A)", td_pra_share),
            PathSpec("SGP (10+R/10+A)", td_pra_share / conditional_pts_discount),
        ),
    )
    mlb = MarketGroup(
        key="mlb_tb",
        title="BEST OVERALL 2+ TOTAL BASES VALUE (Market vs. SGP Paths):",
        subtitle="Official 2+ TB prop vs the logically identical SGP (1+ Hit & 2+ TB): 2+ TB cannot happen without a hit.",
        paths=(
            PathSpec("Official 2+ TB", 1.0, True),
            PathSpec("SGP (1+H/2+TB)", 1.0),
        ),
    )
    return {g.key: g for g in (nba, mlb)}


@dataclass(frozen=True)
class Quote:
    player: str
    event: str
    book: str
    path: str
    american: float
    source: str = QUOTED

    @property
    def decimal(self) -> float:
        return float(american_to_decimal(self.american))


@dataclass
class ScanRow:
    player: str
    event: str
    best: Quote
    consensus_prob: float
    ev_pct: float
    worst: Quote
    n_books: int


# -- quote providers -----------------------------------------------------------

class SGPQuoteProvider(ABC):
    @abstractmethod
    def fetch(self, group: MarketGroup) -> list[Quote]:
        """Return quotes (official and/or SGP paths) for the group."""


@dataclass
class StaticQuoteProvider(SGPQuoteProvider):
    quotes: list[Quote] = field(default_factory=list)

    def fetch(self, group: MarketGroup) -> list[Quote]:
        names = {p.name for p in group.paths}
        return [q for q in self.quotes if q.path in names]


class LiveBetslipQuoteProvider(SGPQuoteProvider):
    """Interface stub, deliberately NOT implemented.

    Pulling SGP prices means calling each sportsbook's internal betslip pricing endpoint
    with private selection ids. That is undocumented, likely against the books' terms of
    service, and unstable; this repo already ruled out the same pattern for 4codds. Supply
    quotes through StaticQuoteProvider / a CSV from a source you are permitted to use
    (a licensed odds feed, or prices you recorded by hand)."""

    def fetch(self, group: MarketGroup) -> list[Quote]:
        raise NotImplementedError("live betslip scraping is intentionally not implemented; see docstring")


def read_quotes_csv(path: str) -> list[Quote]:
    out: list[Quote] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if not valid_american(r.get("american")):
                continue
            out.append(Quote(r["player"].strip(), r["event"].strip(), r["book"].strip().lower(),
                             r["path"].strip(), float(r["american"]), (r.get("source") or QUOTED).strip().lower()))
    return out


def _nearest_psd_corr(corr: np.ndarray) -> np.ndarray:
    corr = np.asarray(corr, dtype=float)
    eigvals, eigvecs = np.linalg.eigh(corr)
    if np.all(eigvals > 1e-9):
        return corr
    fixed = eigvecs @ np.diag(np.maximum(eigvals, 1e-6)) @ eigvecs.T
    d = np.sqrt(np.diag(fixed))
    return fixed / np.outer(d, d)


@dataclass
class CopulaFallbackPricer:
    """MODEL a book's SGP price from single-leg milestone fair probabilities with a Gaussian
    copula: joint = Phi_n(Phi^-1(p_1..p_n); R) (all legs are 'yes'/over events), then apply a
    book SGP hold: offered_prob = joint * (1 + hold).

    corr is REQUIRED -- there is no default correlation, because any default would be a
    guess. See scripts/sgp_empirical_rho.py for how to estimate rho from closing lines."""
    corr: np.ndarray
    hold: float

    def joint_prob(self, leg_probs: Sequence[float]) -> float:
        p = np.asarray(leg_probs, dtype=float)
        if len(p) != self.corr.shape[0]:
            raise ValueError("corr matrix size must equal number of legs")
        if np.any(p <= 0) or np.any(p >= 1):
            raise ValueError("leg probabilities must be strictly inside (0, 1)")
        if len(p) == 1:
            return float(p[0])
        z = norm.ppf(p)
        val = multivariate_normal(mean=np.zeros(len(p)), cov=_nearest_psd_corr(self.corr),
                                  allow_singular=True).cdf(z)
        return float(min(max(val, 1e-12), min(p)))

    def price(self, player: str, event: str, book: str, path: str,
              leg_american: Sequence[float], one_way_vig: float = 0.07) -> Quote:
        fair = [devig_unilateral(american_to_implied_prob(a), one_way_vig) for a in leg_american]
        offered = min(self.joint_prob(fair) * (1.0 + self.hold), 0.999)
        return Quote(player, event, book, path, float(implied_to_american(offered)), MODELED)


# -- scanner -------------------------------------------------------------------

def consensus_prob(official: Iterable[Quote], margin: float = 0.0, stat: str = "mean") -> float:
    """Consensus fair probability from one-way official quotes: implied prob / (1 + margin),
    aggregated across books by mean or median. margin=0 keeps the vig in, which UNDERSTATES
    fair probability and so understates EV (the conservative direction)."""
    probs = [float(american_to_implied_prob(q.american)) / (1.0 + margin) for q in official]
    return statistics.median(probs) if stat == "median" else float(np.mean(probs))


def scan(quotes: Sequence[Quote], group: MarketGroup, min_books: int = 3, margin: float = 0.0,
         stat: str = "mean", include_modeled: bool = False, min_ev: float = 0.0) -> list[ScanRow]:
    off = group.official
    by_pe: dict[tuple[str, str], list[Quote]] = {}
    for q in quotes:
        if group.path(q.path) is None or not valid_american(q.american):
            continue
        if q.source == MODELED and not include_modeled:
            continue
        by_pe.setdefault((q.player, q.event), []).append(q)

    rows: list[ScanRow] = []
    for (player, event), qs in by_pe.items():
        official = [q for q in qs if q.path == off.name and q.source == QUOTED]
        books = {q.book for q in official}
        if len(books) < min_books:
            continue
        cp = consensus_prob(official, margin, stat)
        scored = []
        for q in qs:
            spec = group.path(q.path)
            p_path = min(cp * spec.prob_ratio, 1.0)
            scored.append(((q.decimal * p_path - 1.0) * 100.0, q))
        ev, best = max(scored, key=lambda t: (t[0], t[1].decimal))
        worst = min((q for _, q in scored), key=lambda q: q.decimal)
        if ev > min_ev:
            rows.append(ScanRow(player, event, best, cp, ev, worst, len(books)))
    rows.sort(key=lambda r: r.ev_pct, reverse=True)
    return rows


# -- output --------------------------------------------------------------------

HEADERS = ["Player", "Event", "Best Method", "Best Price", "Best Book", "Consensus", "EV %", "Worst Price", "Worst Book"]


def render_table(rows: Sequence[ScanRow], group: MarketGroup, note: str = "") -> str:
    body = []
    for r in rows:
        mark = "*" if r.best.source == MODELED else ""
        body.append([r.player, r.event, r.best.path + mark, f"{int(r.best.american):+d}", r.best.book,
                     prob_to_american_str(r.consensus_prob), f"{r.ev_pct:+.1f}%",
                     f"{int(r.worst.american):+d}", r.worst.book])
    widths = [max(len(h), *(len(b[i]) for b in body)) if body else len(h) for i, h in enumerate(HEADERS)]

    def fmt(cells):
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    lines = [group.title, group.subtitle, ""]
    if note:
        lines += [note, ""]
    lines += [fmt(HEADERS), sep, *[fmt(b) for b in body]]
    if not body:
        lines.append("(no rows: need >= min_books official quotes and EV > threshold)")
    if any(r.best.source == MODELED for r in rows):
        lines += ["", "* modeled price (copula), not an offered quote"]
    return "\n".join(lines)


# -- demo data (SYNTHETIC) -----------------------------------------------------

def demo_quotes() -> list[Quote]:
    """Synthetic prices for fictional players. NOT real market data."""
    books = ["fanduel", "draftkings", "betmgm", "caesars", "bet365"]
    spec = {
        # player, event: official prices by book, then {path: {book: price}}
        ("Demo Guard", "AAA @ BBB"): (
            [5800, 6600, 7000, 6200, 6400],
            {"SGP (10+P/10+R/10+A)": [6100, 6900, 6300, 5900, 6500],
             "SGP (10+R/10+A)": [11834, 6800, 7200, 6500, 6600]}),
        ("Demo Wing", "CCC @ DDD"): (
            [4200, 3900, 4400, 4100, 4300],
            {"SGP (10+P/10+R/10+A)": [4400, 4600, 4800, 3800, 4200],
             "SGP (10+R/10+A)": [6742, 4700, 4900, 4000, 4300]}),
        ("Demo Big", "EEE @ FFF"): (
            [900, 950, 880, 920, 940],
            {"SGP (10+P/10+R/10+A)": [880, 940, 900, 870, 910],
             "SGP (10+R/10+A)": [930, 960, 910, 890, 920]}),
        ("Thin Market Guy", "GGG @ HHH"): (   # only 2 official books -> filtered by min_books
            [2500, 2700],
            {"SGP (10+R/10+A)": [4000, 2600]}),
    }
    out: list[Quote] = []
    for (player, event), (off, paths) in spec.items():
        for b, a in zip(books, off):
            out.append(Quote(player, event, b, "Official TD", a))
        for path, prices in paths.items():
            for b, a in zip(books, prices):
                out.append(Quote(player, event, b, path, a))
    return out


# -- CLI -----------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--demo", action="store_true", help="run on SYNTHETIC multi-book demo data")
    ap.add_argument("--input", help="quotes CSV: player,event,book,path,american[,source]")
    ap.add_argument("--group", default="nba_td", choices=["nba_td", "mlb_tb"])
    ap.add_argument("--min-books", type=int, default=3)
    ap.add_argument("--margin", type=float, default=0.0, help="one-way margin haircut on official implied prob")
    ap.add_argument("--stat", choices=["mean", "median"], default="mean")
    ap.add_argument("--min-ev", type=float, default=0.0)
    ap.add_argument("--pts-discount", type=float, default=0.995,
                    help="P(10+ pts | 10+ reb & 10+ ast); sets the 2-leg proxy's prob_ratio")
    ap.add_argument("--td-pra-share", type=float, default=0.999)
    ap.add_argument("--include-modeled", action="store_true")
    a = ap.parse_args(argv)
    if not a.demo and not a.input:
        ap.error("pass --demo or --input quotes.csv")

    groups = build_groups(a.td_pra_share, a.pts_discount)
    group = groups["nba_td" if a.demo else a.group]
    quotes = demo_quotes() if a.demo else read_quotes_csv(a.input)
    provider = StaticQuoteProvider(quotes)
    rows = scan(provider.fetch(group), group, a.min_books, a.margin, a.stat, a.include_modeled, a.min_ev)
    note = "DEMO DATA: synthetic prices for fictional players, not market data." if a.demo else ""
    print(render_table(rows, group, note))
    print(f"\nEV% = (best_decimal * consensus_prob * path_ratio - 1) * 100; consensus from official quotes only, "
          f"min_books={a.min_books}, margin={a.margin}, stat={a.stat}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
