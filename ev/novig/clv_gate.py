"""
ev.novig.clv_gate -- forward-CLV gate for Novig-venue plays, per (sport, stat) slice.

ROADMAP RULE (the only thing this module decides): a slice may be routed to AUTOMATED execution on a 0%-fee pre-game
venue only once it has cleared the forward CLV gate.  A slice is OPEN only when ALL of these hold:

    n  >= min_n (250)   forward Novig plans that FILLED in the shadow ledger AND have a usable closing line
    mean CLV > 0        same-book, same-side price-ratio CLV (mlb_core.odds.utils.clv_pct_from_prices definition)
    lower CI bound > 0  two-sided `ci_level` (95%) interval of the mean, CLUSTERED BY GAME DAY with a t quantile on
                        G-1 degrees of freedom (G = number of game days).  Props within one game / one day share
                        game flow, injuries and line moves, so counting every bet as independent would overstate n.
    consensus confirm   mean of (closing leave-Novig-out consensus fair P(side) / entry price - 1) is also > 0
                        (config `require_consensus_confirm`, default on).  An extra restriction, not a loosening: a
                        stale Novig quote must not be able to open a slice on its own.

Everything else is CLOSED, including every slice that has no data at all (default closed).  This module never places or
routes an order; it turns the shadow ledger + banked closing lines into a verdict table and answers `is_open()`.

WHAT IS MEASURED.  Entry = the shadow fill's VWAP price (filled_cost / fill_count, $1-payout units, fee 0) from the
`novig` ledger mode (decided and paper-filled against the PRODUCTION public Novig book).  Close = Novig's own quote for
the same (game, player, stat, line, side) in the LAST banked pre-kick ParlayAPI snapshot of that game
(`nfl.props.lines` tables, from `EVSweep/nfl_props/raw/**`).  Usable close = last snapshot is <= `close_fresh_min` before
kick (the sweep stops fetching a game 20 min out, so this is a proxy, not the true close), taken AFTER the entry, the
quote is still posted at the same line, and the quote's own `last_update` age is <= `close_quote_age_max_min`.
Bets without a usable close are dropped from n (and counted in `coverage`) -- survivorship: a line that moved off
the point is not a clean close.

Verdict persistence: a local JSON file (NOVIG_GATE_VERDICT_FILE).  A missing, unreadable or stale file means CLOSED.
No network, no GCS write, no credentials anywhere in this module.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timezone

from ev.novig.map import STAT_BY_TYPE
from ev.props import norm_name
from mlb.execution.kalshi.config import _coerce
from mlb_core.odds.utils import american_to_decimal
from mlb_core.risk.clv import clv_verdict

ENV_PREFIX = "NOVIG_GATE_"
MODE = "novig"                                        # ledger mode of the production-priced shadow plans
SPORT_STATS = {"nfl": tuple(sorted(set(STAT_BY_TYPE.values())))}   # the only slices a Novig mapper exists for today
DEFAULT_VERDICT_FILE = os.path.expanduser("~/.kalshi_exec/novig_gate_verdicts.json")


@dataclass(frozen=True)
class GateConfig:
    enabled: bool = False                  # NOVIG_GATE_ENABLED: default OFF; off == shadow/paper path is byte-for-byte unchanged
    min_n: int = 250                       # roadmap: 250-bet forward CLV gate
    ci_level: float = 0.95                 # two-sided; the LOWER bound must be > 0
    require_consensus_confirm: bool = True
    cons_min_books: int = 3                # leave-Novig-out books needed for the closing consensus
    close_fresh_min: float = 60.0          # == nfl.props.lines.CLOSE_FRESH_MIN
    close_quote_age_max_min: float = 30.0  # the closing Novig quote's own staleness
    venue_book: str = "novig"              # book key of the venue in the banked ParlayAPI payloads
    verdict_file: str = DEFAULT_VERDICT_FILE
    verdict_max_age_h: float = 72.0        # an older verdict file is treated as no verdict (closed)
    skip_closed_paper: bool = True         # closed slice: do not mirror the plan to the Novig PAPER sandbox


def load_gate_config(env=None) -> GateConfig:
    """GateConfig from NOVIG_GATE_<FIELD> env vars (same convention as the Kalshi KEXEC_ config)."""
    env = os.environ if env is None else env
    defaults, kwargs = GateConfig(), {}
    for f in fields(GateConfig):
        raw = env.get(f"{ENV_PREFIX}{f.name.upper()}")
        if raw not in (None, ""):
            kwargs[f.name] = _coerce(raw, getattr(defaults, f.name))
    return GateConfig(**kwargs)


# ----------------------------------------------------------------------------------------------- statistics (no scipy)
def _betacf(a: float, b: float, x: float) -> float:
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x)
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    x = df / (df + t * t)
    tail = 0.5 * _betai(df / 2.0, 0.5, x)                       # P(T > |t|)
    return 1.0 - tail if t > 0 else tail


def t_ppf(p: float, df: float) -> float:
    """Student-t quantile by bisection on the exact CDF (accurate to ~1e-9; df >= 1)."""
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2.0
        if hi > 1e9:
            break
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def cluster_mean_ci(values, clusters, level: float = 0.95) -> dict:
    """Mean of `values` with a cluster-robust SE: V = G/(G-1) * sum_g (sum_{i in g}(x_i - xbar))^2 / n^2, t quantile on
    G-1 df.  G < 2 clusters (or zero variance) -> se / t / ci are None: the interval cannot be formed, which the gate
    treats as NOT open.  A day is the cluster; this is conservative when only a few game days exist (t_{G-1} is wide)."""
    n = len(values)
    if n == 0:
        return {"n": 0, "n_clusters": 0, "mean": None, "se": None, "t": None, "df": None, "ci_lo": None, "ci_hi": None}
    mean = sum(values) / n
    sums: dict = {}
    for x, g in zip(values, clusters):
        sums[g] = sums.get(g, 0.0) + (x - mean)
    big_g = len(sums)
    out = {"n": n, "n_clusters": big_g, "mean": mean, "se": None, "t": None, "df": None, "ci_lo": None, "ci_hi": None}
    if big_g < 2:
        return out
    var = big_g / (big_g - 1.0) * sum(s * s for s in sums.values()) / (n * n)
    if var <= 1e-18:
        return out
    se = math.sqrt(var)
    crit = t_ppf(0.5 + level / 2.0, big_g - 1)
    out.update(se=se, t=mean / se, df=big_g - 1, ci_lo=mean - crit * se, ci_hi=mean + crit * se)
    return out


# ----------------------------------------------------------------------------------------------- shadow ledger -> bets
def ledger_bets(conn, mode: str = MODE) -> list:
    """Filled shadow plans of one ledger mode, joined to their decision (which carries player/stat/line).
    `conn` is a sqlite3 connection with row_factory=sqlite3.Row (Ledger._c, or a read-only connection).
    The paper-sandbox mode ('novig_paper') validates plumbing, not edge, and is never read here."""
    rows = conn.execute(
        "SELECT o.intent_id, o.day, o.ts, o.game_pk, o.side, o.fill_count, o.filled_cost, o.fee, d.inputs_json "
        "FROM orders o JOIN decisions d ON d.intent_id = o.intent_id AND d.mode = o.mode "
        "AND d.outcome IN ('EXECUTED','PARTIAL') WHERE o.mode = ? AND o.fill_count > 0 "
        "GROUP BY o.intent_id ORDER BY o.ts", (mode,)).fetchall()
    out = []
    for r in rows:
        try:
            inp = json.loads(r["inputs_json"] or "{}")
            gk = str(r["game_pk"])
            out.append({"intent_id": r["intent_id"], "day": r["day"], "ts": r["ts"], "game_key": gk,
                        "sport": gk.split(":")[0], "stat": str(inp["stat"]), "player_norm": norm_name(inp["player"]),
                        "line": float(inp["line"]), "side": r["side"],
                        "entry_price": (float(r["filled_cost"]) + float(r["fee"] or 0.0)) / float(r["fill_count"]), "units": float(r["fill_count"]),
                        "cost": float(r["filled_cost"]), "fee": float(r["fee"] or 0.0)})
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue                                                       # unparsable row: not a countable bet
    return out


# ----------------------------------------------------------------------------------------------- closing lines
def _utc(ts) -> datetime:
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _pt(x) -> float:
    return round(float(x), 3)


def attach_closing(bets: list, series, books, cfg: GateConfig) -> list:
    """Adds close fields to each bet (never mutates the input): clv_pct, clv_cons_pct, close_ts, close_lead_min and
    `close_reason` (None = usable close for the primary CLV; else why not).  `series` / `books` are the
    nfl.props.lines.build_lines tables (PRE-KICK ONLY).  Pure: no I/O."""
    last: dict = {}                                    # game_key -> (snap_ts, mins_to_kick) of the LAST pre-kick snapshot
    if series is not None and len(series):
        for gk, g in series.groupby("game_key"):
            ts = g["snap_ts"].max()
            last[gk] = (ts, float(g.loc[g["snap_ts"] == ts, "mins_to_kick"].min()))
    quotes: dict = {}                                  # (gk, player_norm, stat, point) -> (over, under, age_min) of the venue at close
    if books is not None and len(books):
        b = books[books["book"] == cfg.venue_book]
        for r in b.itertuples(index=False):
            if r.game_key in last and r.snap_ts == last[r.game_key][0]:
                quotes[(r.game_key, r.player_norm, r.stat, _pt(r.point))] = (r.over, r.under, r.age_min)
    fair: dict = {}                                    # same key -> closing per-book fair P(over)
    if series is not None and len(series):
        for r in series.itertuples(index=False):
            if r.game_key in last and r.snap_ts == last[r.game_key][0]:
                fair[(r.game_key, r.player_norm, r.stat, _pt(r.point))] = r.book_fair
    out = []
    for bet in bets:
        b = dict(bet, clv_pct=None, clv_cons_pct=None, close_ts=None, close_lead_min=None, close_reason=None)
        out.append(b)
        if bet["game_key"] not in last:
            b["close_reason"] = "no_closing_snapshot"
            continue
        cts, lead = last[bet["game_key"]]
        b["close_ts"], b["close_lead_min"] = cts.isoformat(), lead
        if lead > cfg.close_fresh_min:
            b["close_reason"] = "close_stale"
            continue
        if cts <= _utc(bet["ts"]):
            b["close_reason"] = "entry_after_close"
            continue
        key = (bet["game_key"], bet["player_norm"], bet["stat"], _pt(bet["line"]))
        q = quotes.get(key)
        if q is None:
            b["close_reason"] = "quote_gone_at_close"
        else:
            over, under, age = q
            price = over if bet["side"] == "yes" else under
            if price is None or price != price:
                b["close_reason"] = "side_not_quoted_at_close"
            elif age is None or age != age or float(age) > cfg.close_quote_age_max_min:
                b["close_reason"] = "close_quote_stale_or_age_unknown"
            else:
                # (1/entry)/dec_close - 1: identical to clv_pct_from_prices(entry_american, close_american) without
                # rounding the entry price to a whole American number.
                b["clv_pct"] = round(((1.0 / bet["entry_price"]) / american_to_decimal(price) - 1.0) * 100.0, 4)
        bf = fair.get(key)
        if bf:
            from ev.novig.exec import leave_out
            lo = leave_out({"book_fair": json.loads(bf) if isinstance(bf, str) else bf}, exclude=frozenset({cfg.venue_book}))
            if lo is not None and lo["n_books"] >= cfg.cons_min_books:
                p_side = lo["p_over"] if bet["side"] == "yes" else 1.0 - lo["p_over"]
                b["clv_cons_pct"] = round((p_side / bet["entry_price"] - 1.0) * 100.0, 4)
    return out


# ----------------------------------------------------------------------------------------------- verdicts
def slice_key(sport: str, stat: str) -> str:
    return f"{sport}|{stat}"


def slice_verdict(bets: list, cfg: GateConfig) -> dict:
    """One slice's gate result from bets that already went through attach_closing."""
    n_bets = len(bets)
    used = [b for b in bets if b["clv_pct"] is not None]
    stats = cluster_mean_ci([b["clv_pct"] for b in used], [b["day"] for b in used], cfg.ci_level)
    cons = [b for b in bets if b["clv_cons_pct"] is not None]
    cstats = cluster_mean_ci([b["clv_cons_pct"] for b in cons], [b["day"] for b in cons], cfg.ci_level)
    res = {"n_bets": n_bets, "n": stats["n"], "n_days": stats["n_clusters"], "mean_clv_pct": stats["mean"],
           "se": stats["se"], "t_clustered": stats["t"], "ci_lo": stats["ci_lo"], "ci_hi": stats["ci_hi"],
           "ci_level": cfg.ci_level, "cons_n": cstats["n"], "mean_clv_cons_pct": cstats["mean"],
           "cons_ci_lo": cstats["ci_lo"], "min_n": cfg.min_n}
    res["t17"] = clv_verdict(stats["mean"], None if stats["t"] is None else round(stats["t"], 3), stats["n"], min_n=cfg.min_n)
    reasons = []
    if n_bets == 0:
        status, reasons = "no_data", ["no filled forward Novig plans in this slice"]
    elif stats["n"] < cfg.min_n:
        status = "insufficient_data"
        reasons = [f"n={stats['n']} < {cfg.min_n} (bets with a usable close; {n_bets} filled)"]
    else:
        if not stats["mean"] > 0:
            reasons.append("mean CLV <= 0")
        if stats["ci_lo"] is None:
            reasons.append("CI not formable (< 2 game days or zero variance)")
        elif not stats["ci_lo"] > 0:
            reasons.append(f"lower {cfg.ci_level:.0%} CI bound {stats['ci_lo']:+.2f}% <= 0")
        confirm_ok = (not cfg.require_consensus_confirm) or (cstats["mean"] is not None and cstats["mean"] > 0)
        if reasons:
            status = "closed_no_edge"
        elif not confirm_ok:
            status, reasons = "closed_unconfirmed", ["closing-consensus CLV is not positive (or not computable)"]
        else:
            status = "open"
    res["status"], res["open"], res["reasons"] = status, status == "open", reasons
    return res


def compute_verdicts(bets: list, series, books, cfg: GateConfig, now: datetime | None = None) -> dict:
    """The full verdict table.  Every known slice (SPORT_STATS) is listed even with no data; any slice seen in the
    ledger is added.  Unlisted slices are closed by default (`SliceGate.is_open`)."""
    now = now or datetime.now(timezone.utc)
    withc = attach_closing(bets, series, books, cfg)
    groups: dict = {slice_key(s, st): [] for s, stats in SPORT_STATS.items() for st in stats}
    for b in withc:
        groups.setdefault(slice_key(b["sport"], b["stat"]), []).append(b)
    drop: dict = {}
    for b in withc:
        if b["close_reason"] is not None:
            drop[b["close_reason"]] = drop.get(b["close_reason"], 0) + 1
    return {"computed_at": now.isoformat(),
            "rule": {"min_n": cfg.min_n, "mean_clv_gt": 0, "ci_level": cfg.ci_level, "ci": "two-sided, cluster-robust by game day, t(G-1)",
                     "lower_ci_gt": 0, "require_consensus_confirm": cfg.require_consensus_confirm,
                     "close_fresh_min": cfg.close_fresh_min, "close_quote_age_max_min": cfg.close_quote_age_max_min},
            "coverage": {"filled_bets": len(withc), "with_usable_close": sum(1 for b in withc if b["clv_pct"] is not None),
                         "dropped_reasons": drop},
            "slices": {k: slice_verdict(v, cfg) for k, v in sorted(groups.items())}}


def write_verdicts(path: str, verdicts: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="ascii") as f:
        json.dump(verdicts, f, indent=1, sort_keys=True, default=str)
    os.replace(tmp, path)


class SliceGate:
    """Answers `is_open(sport, stat)` from a verdict table.  FAIL CLOSED: no table, a stale table, an unknown slice or
    anything but status == 'open' means closed."""

    def __init__(self, cfg: GateConfig, verdicts: dict | None, now: datetime | None = None):
        self.cfg, self.verdicts = cfg, verdicts or {}
        now = now or datetime.now(timezone.utc)
        self.stale = True
        try:
            age_h = (now - _utc(self.verdicts["computed_at"])).total_seconds() / 3600.0
            self.stale = age_h > cfg.verdict_max_age_h or age_h < -1.0
        except (KeyError, ValueError, TypeError):
            pass

    @classmethod
    def load(cls, cfg: GateConfig, now: datetime | None = None) -> "SliceGate":
        try:
            with open(cfg.verdict_file, encoding="ascii") as f:
                v = json.load(f)
        except (OSError, ValueError):
            v = None
        return cls(cfg, v if isinstance(v, dict) else None, now)

    def status(self, sport: str, stat: str) -> dict:
        k = slice_key(sport, stat)
        if not self.verdicts:
            return {"slice": k, "open": False, "status": "no_verdict"}
        if self.stale:
            return {"slice": k, "open": False, "status": "stale_verdict"}
        row = (self.verdicts.get("slices") or {}).get(k)
        if not isinstance(row, dict):
            return {"slice": k, "open": False, "status": "no_data"}
        return {"slice": slice_key(sport, stat), "open": bool(row.get("open")) and row.get("status") == "open",
                "status": row.get("status"), "n": row.get("n"), "mean_clv_pct": row.get("mean_clv_pct"),
                "ci_lo": row.get("ci_lo")}

    def is_open(self, sport: str, stat: str) -> bool:
        return bool(self.status(sport, stat)["open"])


# ----------------------------------------------------------------------------------------------- CLI (local files only)
def _readonly_conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _dir_source(root: str):
    """(lister, reader) over a local mirror laid out <root>/raw/<ET day>/<event>_<HHMMSS>.json."""
    from pathlib import Path
    base = Path(root)
    return (lambda prefix: sorted(str(p.relative_to(base)) for p in base.rglob("*.json")),
            lambda key: (base / key).read_bytes())


def render(verdicts: dict) -> str:
    cov = verdicts["coverage"]
    lines = [f"computed_at {verdicts['computed_at']}  filled bets {cov['filled_bets']}  with usable close "
             f"{cov['with_usable_close']}  dropped {cov['dropped_reasons']}",
             f"{'slice':<16}{'status':<20}{'n/bets':<10}{'days':<6}{'meanCLV%':<10}{'loCI%':<9}{'consCLV%':<10}open"]
    f = lambda x: "  -" if x is None else f"{x:+.2f}"           # noqa: E731
    for k, v in verdicts["slices"].items():
        lines.append(f"{k:<16}{v['status']:<20}{str(v['n']) + '/' + str(v['n_bets']):<10}{v['n_days']:<6}"
                     f"{f(v['mean_clv_pct']):<10}{f(v['ci_lo']):<9}{f(v['mean_clv_cons_pct']):<10}{v['open']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Novig forward-CLV gate (offline; reads a LOCAL ledger + LOCAL raw mirror)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute", help="build the verdict table from a ledger and a local raw-payload mirror")
    c.add_argument("--db", required=True, help="shadow ledger sqlite (opened read-only)")
    c.add_argument("--raw-dir", default=None, help="local mirror laid out <dir>/raw/<ET day>/<event>_<HHMMSS>.json "
                                                  "(gcloud storage cp of EVSweep/nfl_props/raw/<day>/*); omit = no closing data")
    c.add_argument("--start", default=None)
    c.add_argument("--end", default=None)
    c.add_argument("--out", default=None, help="write the verdict JSON here (default: print only)")
    s = sub.add_parser("status", help="print the verdict file the executor would read")
    s.add_argument("--file", default=None)
    a = ap.parse_args(argv)
    cfg = load_gate_config()
    if a.cmd == "status":
        path = a.file or cfg.verdict_file
        gate = SliceGate.load(GateConfig(**{**cfg.__dict__, "verdict_file": path}))
        print(f"verdict file {path}: stale_or_missing={gate.stale}")
        if gate.verdicts:
            print(render(gate.verdicts))
        return 0
    bets = ledger_bets(_readonly_conn(a.db))
    series = books = None
    if a.raw_dir:
        from nfl.props import lines as L
        lister, reader = _dir_source(a.raw_dir)
        series, books, _ = L.build_from_source(lister, reader, a.start, a.end, 0.0, False)
    v = compute_verdicts(bets, series, books, cfg)
    print(render(v))
    if a.out:
        write_verdicts(a.out, v)
        print("wrote", a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
