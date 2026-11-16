"""
ev.autopilot.stream -- the AUTOPILOT_ELIGIBLE shadow signal stream.

Reads banked Odds API snapshots (the DEC-08 accumulator feed: per-book American prices
incl. Novig, each bookmaker market carrying its own last_update), reconstructs the
leave-Novig-out consensus at the modal line exactly the way the step-b backtest did
(median of per-book 2-way devigged fairs, MAD dispersion), and runs every Novig-quoted
side through the rulings' gate funnel:

    market enabled -> target quoted -> target quote fresh (<= 5m, else SKIPPED_STALE)
    -> anchor n_books >= 4 -> tiered consensus-age cap -> dispersion MAD <= 0.04
    -> thin-anchor guardrail (exactly 4 books: fair range <= 5.0pp, else annex)
    -> price band ask >= 0.25 -> shrunk net EV >= 1.5% -> net EV <= 10% cap
    -> shadow fill simulation under the s5 limit ceiling

Shrinkage is production's own: mlb.execution.kalshi.gates.shrink over a ConsPoint
(h = h0 + h_age * max(0, age - h_age0) + h_disp * disp), with the consensus age taken
at firing (the shadow fires on snapshot arrival, so the backtest fires at age 0, the
backtest's headline convention; per-book staleness is policed by the age-cap gate).

First-sighting semantics: an intent (event, player, stat, line, side) produces at most
one FILLED row, at its first gate-passing evaluation; later passes are REJ_DUP_INTENT.
Every evaluation writes one ledger row, so the funnel is derivable from the ledger.

NO ORDER PATH: this module imports no venue client, no signing, no paper venue.  A
"fill" is a simulation against the banked ask (fill_basis = assumed_at_ask; no depth
is banked anywhere in the feed, the step-b record's stated limitation).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo

from ev.autopilot import ledger as ledger_io
from ev.autopilot.config import MARKETS, SPORT_FEEDS, VENUE, ShadowConfig, load_config
from ev.props import norm_name
from mlb.execution.kalshi.config import ExecConfig
from mlb.execution.kalshi.consensus import ConsPoint
from mlb.execution.kalshi.gates import shrink as production_shrink
from mlb_core.odds.utils import american_to_decimal

_ET = ZoneInfo("America/New_York")
_EXEC_CFG = ExecConfig()          # production shrink constants (h0 / h_age / h_disp)
_FILE_RE = re.compile(r"^props_(\d{2})(\d{2})\.json$")


# ----------------------------------------------------------------------------------------------- records
@dataclass
class CandidateRecord:
    ts: datetime
    sport: str
    stat: str
    system: str
    event_id: str
    matchup: str
    commence: datetime
    player: str
    player_norm: str
    line: float
    p_cons: float                 # median devigged fair P(over), leave-Novig-out
    disp: float                   # MAD of per-book fairs
    fair_range: float             # max - min of per-book fairs
    n_books: int
    book_ages_min: list           # age of each contributing book's quote at ts
    ages_known: bool
    nov_over: float | None        # Novig implied prob (vig-loaded) for over at the modal line
    nov_under: float | None
    nov_age_min: float | None


def _utc(ts) -> datetime:
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _age_min(ts: datetime, last_update, book_last_update) -> float | None:
    for cand in (last_update, book_last_update):
        if cand:
            try:
                return max(0.0, (ts - _utc(cand)).total_seconds() / 60.0)
            except (ValueError, TypeError):
                continue
    return None


def parse_snapshot(payload: list, ts: datetime, cfg: ShadowConfig) -> list:
    """One accumulator snapshot payload -> candidate records at the modal line per
    (stat, player).  Mirrors designB.py's reconstruction, extended with per-book ages."""
    out = []
    for ev in payload:
        try:
            commence = _utc(ev["commence_time"])
        except (KeyError, ValueError, TypeError):
            continue
        event_id = ev.get("canonical_event_id") or ev.get("id")
        if not event_id:
            continue
        matchup = f"{ev.get('away_team', '?')} @ {ev.get('home_team', '?')}"
        quotes: dict = {}          # (book, stat, player_norm) -> {point: [over_price, under_price]}
        ages: dict = {}            # (book, stat) -> age_min of that book's market quote
        display: dict = {}         # (stat, player_norm) -> display name
        for bk in ev.get("bookmakers", []):
            book = str(bk.get("key", "")).lower()
            if not book:
                continue
            for m in bk.get("markets", []):
                mk = m.get("key")
                if mk not in MARKETS:
                    continue
                sport, stat, _system = MARKETS[mk]
                age = _age_min(ts, m.get("last_update"), bk.get("last_update"))
                if age is not None:
                    ages[(book, stat)] = age
                for o in m.get("outcomes", []):
                    name = o.get("name", "")
                    if name.startswith("Over "):
                        side_i, player = 0, o.get("description") or name[5:]
                    elif name.startswith("Under "):
                        side_i, player = 1, o.get("description") or name[6:]
                    else:
                        continue
                    if o.get("price") is None or o.get("point") is None:
                        continue
                    pnorm = norm_name(player)
                    display.setdefault((stat, pnorm), player)
                    cell = quotes.setdefault((book, stat, pnorm), {}).setdefault(
                        round(float(o["point"]), 2), [None, None])
                    if cell[side_i] is None:
                        cell[side_i] = float(o["price"])
        by_sp: dict = {}
        for (book, stat, pnorm), pts in quotes.items():
            by_sp.setdefault((stat, pnorm), {})[book] = pts
        for (stat, pnorm), bookmap in by_sp.items():
            sport, _stat, system = next(v for v in MARKETS.values() if v[1] == stat)
            counts: dict = {}
            for book, pts in bookmap.items():
                if book == VENUE:
                    continue
                for pt, (o, u) in pts.items():
                    if o is not None and u is not None:
                        counts[pt] = counts.get(pt, 0) + 1
            if not counts:
                continue
            modal = max(counts.items(), key=lambda kv: (kv[1], -abs(kv[0])))[0]
            fairs, book_ages, ages_known = [], [], True
            for book, pts in bookmap.items():
                if book == VENUE:
                    continue
                cell = pts.get(modal)
                if cell and cell[0] is not None and cell[1] is not None:
                    io = 1.0 / american_to_decimal(cell[0])
                    iu = 1.0 / american_to_decimal(cell[1])
                    fairs.append(io / (io + iu))
                    a = ages.get((book, stat))
                    if a is None:
                        ages_known = False
                    else:
                        book_ages.append(a)
            if not fairs:
                continue
            med = median(fairs)
            nov = bookmap.get(VENUE, {}).get(modal, [None, None])
            out.append(CandidateRecord(
                ts=ts, sport=sport, stat=stat, system=system, event_id=str(event_id), matchup=matchup,
                commence=commence, player=display.get((stat, pnorm), pnorm), player_norm=pnorm,
                line=modal, p_cons=med, disp=median([abs(x - med) for x in fairs]),
                fair_range=max(fairs) - min(fairs), n_books=len(fairs),
                book_ages_min=book_ages, ages_known=ages_known and len(book_ages) == len(fairs),
                nov_over=(1.0 / american_to_decimal(nov[0])) if nov[0] is not None else None,
                nov_under=(1.0 / american_to_decimal(nov[1])) if nov[1] is not None else None,
                nov_age_min=ages.get((VENUE, stat))))
    return out


# ----------------------------------------------------------------------------------------------- sources
def _snap_ts(day: str, fname: str) -> datetime | None:
    m = _FILE_RE.match(fname)
    if not m:
        return None
    d = datetime.strptime(day, "%Y-%m-%d")
    local = datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=_ET)
    return local.astimezone(timezone.utc)


class DirFeedSource:
    """Local accumulator mirror: base_by_sport[sport] is a directory of {day}/props_{HHMM}.json."""

    def __init__(self, base_by_sport: dict):
        self.base = base_by_sport

    def snapshots(self, sport: str) -> list:
        root = self.base.get(sport)
        if not root or not os.path.isdir(root):
            return []
        out = []
        for day in sorted(os.listdir(root)):
            ddir = os.path.join(root, day)
            if not os.path.isdir(ddir):
                continue
            for fname in sorted(os.listdir(ddir)):
                ts = _snap_ts(day, fname)
                if ts is None:
                    continue
                with open(os.path.join(ddir, fname), encoding="utf-8") as f:
                    out.append((ts, json.load(f)))
        return sorted(out, key=lambda x: x[0])


class StoreFeedSource:
    """The live bank: OddsAccum/{feed}/raw/{day}/props_{HHMM}.json via a shadow_bank store."""

    def __init__(self, store):
        self.store = store

    def snapshots(self, sport: str) -> list:
        prefix = f"OddsAccum/{SPORT_FEEDS[sport]}/raw/"
        out = []
        for key in self.store.list(prefix):
            parts = key[len(prefix):].split("/")
            if len(parts) != 2:
                continue
            ts = _snap_ts(parts[0], parts[1])
            if ts is None:
                continue
            out.append((ts, json.loads(self.store.read(key))))
        return sorted(out, key=lambda x: x[0])


# ----------------------------------------------------------------------------------------------- gates
def _signal_id(rec: CandidateRecord, side: str) -> str:
    day = rec.ts.astimezone(_ET).date().isoformat()
    key = f"{rec.event_id}|{rec.player_norm}|{rec.stat}|{rec.line}|{side}|{day}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _age_bucket(median_age: float | None) -> str:
    if median_age is None:
        return "unknown"
    if median_age <= 15.0:
        return "0-15m"
    if median_age <= 25.0:
        return "15-25m"
    if median_age <= 45.0:
        return "25-45m"
    return ">45m"


@dataclass
class EvalOutcome:
    status: str                   # FILLED | SKIPPED_* | REJ_* | ANNEX_THIN_ANCHOR
    stage: str
    reason: str = ""
    row: dict = field(default_factory=dict)


def evaluate_side(rec: CandidateRecord, side: str, cfg: ShadowConfig) -> EvalOutcome | None:
    """One side of one candidate through the funnel.  None = the target venue does not
    quote this side at the modal line (not an evaluation, mirrors production)."""
    ask = rec.nov_over if side == "yes" else rec.nov_under
    if ask is None:
        return None
    p_side = rec.p_cons if side == "yes" else 1.0 - rec.p_cons
    mins_to_start = (rec.commence - rec.ts).total_seconds() / 60.0
    med_age = median(rec.book_ages_min) if rec.book_ages_min else None
    oldest_age = max(rec.book_ages_min) if rec.book_ages_min else None
    cons = ConsPoint(p=rec.p_cons, n_books=rec.n_books, disp=rec.disp, ts=rec.ts, prev_p=None)
    h = production_shrink(cons, rec.ts, _EXEC_CFG)
    p_hat = p_side - h
    ev_net = (p_hat / ask - 1.0) if ask > 0 else None
    signal_id = _signal_id(rec, side)
    day = rec.ts.astimezone(_ET).date().isoformat()

    gates = {"model_prob": None, "consensus_fair_prob": round(p_side, 6), "p_hat": round(p_hat, 6),
             "shrink_h": round(h, 6), "net_ev_pct": (round(ev_net * 100.0, 4) if ev_net is not None else None),
             "ev_kalshi_form_pct": (round(ev_net * 100.0, 4) if ev_net is not None else None),
             "kelly_fraction": cfg.kelly_fraction, "bankroll_snapshot": cfg.bankroll,
             "n_books": rec.n_books, "disp_mad": round(rec.disp, 6), "fair_range": round(rec.fair_range, 6),
             "median_book_age_min": (round(med_age, 2) if med_age is not None else None),
             "oldest_book_age_min": (round(oldest_age, 2) if oldest_age is not None else None),
             "target_quote_age_min": (round(rec.nov_age_min, 2) if rec.nov_age_min is not None else None),
             "consensus_age_bucket": _age_bucket(med_age), "mins_to_start": round(mins_to_start, 1)}
    row = {"execution_id": hashlib.sha256(f"autopilot-shadow|{signal_id}".encode()).hexdigest()[:32],
           "signal_id": signal_id, "system": rec.system, "league": rec.sport,
           "matchup": rec.matchup, "market_ticker": f"{rec.event_id}:{rec.stat}",
           "selection": f"{rec.player} {'Over' if side == 'yes' else 'Under'} {rec.line}",
           "player": rec.player, "player_norm": rec.player_norm, "stat": rec.stat,
           "event_id": rec.event_id, "line": rec.line, "side": side,
           "gates_snapshot": gates, "signal_price": round(ask, 6), "limit_price_ceiling": None,
           "weighted_fill_price": None, "slippage_pp": None, "requested_usd": None, "filled_usd": None,
           "venue": VENUE, "venue_order_id": None, "client_order_id": f"shadow-{signal_id}",
           "status": None, "simulated": True, "fill_basis": None,
           "quote_observed_at": rec.ts.isoformat(), "submitted_at": rec.ts.isoformat(), "filled_at": None,
           "commence": rec.commence.isoformat(), "game_day": day,
           "closing_fair_prob": None, "novig_close_prob": None, "novig_t15_prob": None,
           "close_state": None, "t15_state": None, "moved_quote": None, "moved_quote_last": None,
           "entry_dislocation_pct": None, "consensus_drift_pp": None, "consensus_drift_ratio_pct": None,
           "consensus_confirm_clv_pct": None, "consensus_confirm_clv_net_pct": None,
           "same_book_clv_pct": None, "same_book_clv_net_pct": None,
           "same_book_clv_last_pct": None, "same_book_clv_last_net_pct": None,
           "result": None, "venue_fee_usd": 0.0, "net_pnl_usd": None}

    def done(status: str, stage: str, reason: str = "") -> EvalOutcome:
        row["status"] = status
        return EvalOutcome(status, stage, reason, row)

    if mins_to_start < cfg.close_buffer_min:
        return done("REJ_GAME_STARTED", "game_state",
                    f"{mins_to_start:.1f}m to start < {cfg.close_buffer_min}m buffer")
    if rec.nov_age_min is None or rec.nov_age_min > cfg.target_quote_max_age_min:
        return done("SKIPPED_STALE", "target_fresh",
                    f"target quote age {rec.nov_age_min} > {cfg.target_quote_max_age_min}m (or unknown)")
    if rec.n_books < cfg.n_books_min:
        return done("REJ_CONS_THIN", "anchor", f"n_books={rec.n_books} < {cfg.n_books_min}")
    late = mins_to_start <= cfg.late_window_min
    med_cap = cfg.age_median_max_min_late if late else cfg.age_median_max_min
    old_cap = cfg.age_oldest_max_min_late if late else cfg.age_oldest_max_min
    if not rec.ages_known or med_age is None or med_age > med_cap or oldest_age > old_cap:
        return done("REJ_CONS_STALE", "consensus_age",
                    f"median age {med_age} (cap {med_cap}), oldest {oldest_age} (cap {old_cap}), known={rec.ages_known}")
    if rec.disp > cfg.disp_max:
        return done("REJ_CONS_WIDE", "dispersion", f"MAD {rec.disp:.4f} > {cfg.disp_max}")
    if rec.n_books == cfg.n_books_min and rec.fair_range > cfg.thin_anchor_max_range:
        return done("ANNEX_THIN_ANCHOR", "thin_anchor",
                    f"exactly {cfg.n_books_min} books and fair range {rec.fair_range:.4f} > {cfg.thin_anchor_max_range}")
    if ask < cfg.ask_min:
        return done("REJ_PRICE_BAND", "price_band", f"ask {ask:.4f} < {cfg.ask_min} (+300 ceiling)")
    if ev_net is None or ev_net < cfg.ev_floor:
        return done("REJ_EV_LOW", "ev_floor", f"net EV {ev_net} < {cfg.ev_floor}")
    if ev_net > cfg.ev_cap:
        return done("REJ_EV_CAP", "ev_cap", f"net EV {ev_net:.4f} > {cfg.ev_cap}")
    ceiling = min(ask + cfg.slippage_cap_pp, p_hat / (1.0 + cfg.ev_floor))
    row["limit_price_ceiling"] = round(ceiling, 6)
    if ask > ceiling:
        return done("SKIPPED_PRICE", "execution", f"ask {ask:.4f} > ceiling {ceiling:.4f}")
    # shadow fill: simulated at the banked ask (no depth banked; assumed at ask)
    kelly_f = max(0.0, (p_hat - ask) / (1.0 - ask)) if ask < 1.0 else 0.0
    stake = min(cfg.bankroll * cfg.kelly_fraction * kelly_f,
                cfg.bankroll * cfg.per_bet_cap_pct, cfg.per_bet_cap_usd)
    stake = max(stake, cfg.min_order_usd) if stake > 0 else cfg.min_order_usd
    row.update(weighted_fill_price=round(ask, 6), slippage_pp=0.0,
               requested_usd=round(stake, 2), filled_usd=round(stake, 2),
               fill_basis="assumed_at_ask", filled_at=rec.ts.isoformat())
    row["gates_snapshot"]["kelly_f_full"] = round(kelly_f, 6)
    return done("FILLED", "execution", "simulated fill at the banked ask")


# ----------------------------------------------------------------------------------------------- runner
def run_stream(source, cfg: ShadowConfig, *, dry_run: bool = False) -> dict:
    """Evaluate every snapshot from the source; returns {"rows", "annex", "funnel", "records"}.
    The kill switch: refuses unless cfg.armed or dry_run (an explicit offline analysis run)."""
    if not cfg.armed and not dry_run:
        raise RuntimeError("AUTOPILOT_SHADOW_ARMED is off: the shadow stream refuses to run "
                           "(pass dry_run=True / --dry-run for an offline analysis run)")
    records: list = []
    for sport in ("nhl", "nba"):
        for ts, payload in source.snapshots(sport):
            records.extend(parse_snapshot(payload, ts, cfg))
    records.sort(key=lambda r: (r.ts, r.event_id, r.stat, r.player_norm))
    rows, annex, funnel = [], [], {}
    seen_filled: set = set()
    for rec in records:
        if not cfg.market_enabled(rec.stat):
            funnel["MARKET_DISABLED"] = funnel.get("MARKET_DISABLED", 0) + 1
            continue
        for side in ("yes", "no"):
            oc = evaluate_side(rec, side, cfg)
            if oc is None:
                continue
            funnel[oc.status] = funnel.get(oc.status, 0) + 1
            if oc.status == "FILLED":
                if oc.row["signal_id"] in seen_filled:
                    oc.row["status"] = "REJ_DUP_INTENT"
                    funnel["REJ_DUP_INTENT"] = funnel.get("REJ_DUP_INTENT", 0) + 1
                    funnel["FILLED"] -= 1
                else:
                    seen_filled.add(oc.row["signal_id"])
            if oc.status == "ANNEX_THIN_ANCHOR":
                annex.append(oc.row)
            rows.append(oc.row)
    return {"rows": rows, "annex": annex, "funnel": funnel, "records": records}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AUT-01 Phase 0 shadow stream (banked snapshots in, JSONL ledger out; "
                                             "shadow only, no order path)")
    ap.add_argument("--source", required=True,
                    help="dir:ROOT (accumulator mirror) | gcs | gcloud (OddsAccum bank via ev.novig.shadow_bank)")
    ap.add_argument("--layout", default="accum", choices=("accum", "flat"),
                    help="dir layout: accum = ROOT/{feed}/raw/{day}/ (default); flat = ROOT/raw_{sport}/{day}/")
    ap.add_argument("--bucket", default=None)
    ap.add_argument("--ledger", required=True, help="output JSONL ledger path (appended)")
    ap.add_argument("--dry-run", action="store_true",
                    help="offline analysis run: bypasses the AUTOPILOT_SHADOW_ARMED kill switch, writes only locally")
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.source.startswith("dir:"):
        root = a.source[4:]
        if a.layout == "flat":
            base = {s: os.path.join(root, f"raw_{s}") for s in ("nhl", "nba")}
        else:
            base = {s: os.path.join(root, SPORT_FEEDS[s], "raw") for s in ("nhl", "nba")}
        source = DirFeedSource(base)
    else:
        from ev.novig import shadow_bank as sb
        source = StoreFeedSource(sb.make_store(a.source, a.bucket))
    res = run_stream(source, cfg, dry_run=a.dry_run)
    from ev.autopilot.closes import attach_closes
    rows = attach_closes(res["rows"], res["records"])
    annex = [r for r in rows if r["status"] == "ANNEX_THIN_ANCHOR"]
    ledger_io.append_rows(a.ledger, rows)
    ledger_io.append_rows(ledger_io.annex_path_for(a.ledger), annex)
    print(json.dumps({"funnel": res["funnel"], "rows": len(rows), "annex": len(annex),
                      "filled": sum(1 for r in rows if r["status"] == "FILLED")}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
