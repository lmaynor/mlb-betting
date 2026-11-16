"""
ev.ownbettor.signals -- the bettor's view of the AUT-01 signal stream.

Input rows are AUT-01 shadow-ledger rows (ev.autopilot.stream output, banked as JSONL).
A row is an actionable signal iff its status is FILLED -- i.e. it already passed the
upstream funnel (market enabled, fresh target quote, >= 4-book anchor, dispersion,
thin-anchor guardrail, shrunk net EV inside [1.5%, 10%], simulated fill at the ask).
Every other row is an upstream reject and is not a signal to this package.

Prices and probabilities are in SIDE terms, exactly as the shadow stamps them:
signal_price is the target ask for the side being bought, gates_snapshot.p_hat is the
shrunk model probability of that side, and limit_price_ceiling is the highest price
the upstream design allows paying for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ev.autopilot import ledger as autopilot_ledger

ACTIONABLE_STATUS = "FILLED"


@dataclass(frozen=True)
class Signal:
    signal_id: str
    system: str
    league: str
    stat: str
    player: str
    event_id: str
    matchup: str
    line: float
    side: str                    # 'yes' (buy the over side) | 'no' (buy the under side)
    p_hat: float                 # shrunk model prob of `side`, side terms
    signal_price: float          # target ask at firing, side terms (probability units)
    limit_price_ceiling: float   # never pay above this
    net_ev_pct: float | None     # upstream net EV (percent), Kalshi form when stamped
    game_day: str                # ET day
    commence: str
    raw: dict                    # the full upstream row, for the audit trail

    @property
    def selection(self) -> str:
        return f"{self.player} {'Over' if self.side == 'yes' else 'Under'} {self.line}"


def from_autopilot_row(row: dict) -> Signal | None:
    """One AUT-01 ledger row -> Signal, or None when the row is not an actionable signal."""
    if row.get("status") != ACTIONABLE_STATUS:
        return None
    gates = row.get("gates_snapshot") or {}
    p_hat = gates.get("p_hat")
    price = row.get("signal_price")
    ceiling = row.get("limit_price_ceiling")
    if p_hat is None or price is None or ceiling is None:
        return None
    ev_pct = gates.get("ev_kalshi_form_pct")
    if ev_pct is None:
        ev_pct = gates.get("net_ev_pct")
    return Signal(
        signal_id=str(row["signal_id"]), system=str(row.get("system") or ""),
        league=str(row.get("league") or ""), stat=str(row.get("stat") or ""),
        player=str(row.get("player") or ""), event_id=str(row.get("event_id") or ""),
        matchup=str(row.get("matchup") or ""), line=float(row.get("line") or 0.0),
        side=str(row.get("side") or ""), p_hat=float(p_hat), signal_price=float(price),
        limit_price_ceiling=float(ceiling),
        net_ev_pct=(float(ev_pct) if ev_pct is not None else None),
        game_day=str(row.get("game_day") or ""), commence=str(row.get("commence") or ""),
        raw=row)


def load_signal_rows(path: str) -> list:
    """All rows of an AUT-01 JSONL ledger (raises on a corrupt line, like the source)."""
    return autopilot_ledger.read_rows(path)


def signals_from_rows(rows: list) -> list:
    out = []
    for r in rows:
        s = from_autopilot_row(r)
        if s is not None:
            out.append(s)
    return out
