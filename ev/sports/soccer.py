"""Soccer adapters (EPL + UCL). Club codes are the canonical Reep-register codes (soccer.data.reep_register),
which are also the codes Kalshi tickers use for these clubs.

Kalshi soccer facts read from Kalshi's own pages 2026-11-03: EPL match contracts exist as three linked binary
markets (home win / away win / TIE), plus exact-score and totals-style contracts and futures series
(KXUCL, KXLALIGA, ...). The GAME/TOTAL series suffix grammar below is the repo's standard adapter grammar
(prefix + GAME / TOTAL); it is re-verified against live markets by scripts/probe_soccer_odds.py before the
soccer legs are enabled in the RFQ book, and the strict mapper (ev.kalshi_map) fails closed on any wording it
does not recognize. SPREAD is disabled: no Kalshi soccer margin grammar has been verified, and an unverified
spread mapping is a wrong bet, not a missing one.
"""
from ev.sports.base import SportAdapter
from soccer.data.reep_register import all_codes, league_codes


def _adapter(prefix: str, codes) -> SportAdapter:
    return SportAdapter(
        sport="soccer", kalshi_prefix=prefix, league_phrase="Soccer", unit="goals",
        codes=frozenset(codes),
        # A draw is its own contract (TIE), never a $0.50 split: the home/away contracts resolve NO on a draw.
        tie_policy="none",
        disabled={"spread": "Kalshi soccer margin grammar unverified (2026-11-03); 1X2 and totals only"},
    )


SOCCER_EPL = _adapter("KXEPL", league_codes("epl"))
SOCCER_UCL = _adapter("KXUCL", league_codes("ucl"))

# The registered adapter for sport "soccer": EPL prefix, the union code table (either league's clubs resolve).
SOCCER = _adapter("KXEPL", all_codes())

LEAGUE_ADAPTERS = {"epl": SOCCER_EPL, "ucl": SOCCER_UCL}
