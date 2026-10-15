"""Offline tests for mlb_core.odds.bettingpros_loader (synthetic long tables; no storage, no network)."""
import numpy as np
import pandas as pd
import pytest

from mlb_core.odds import bettingpros_loader as L
from mlb_core.odds import bettingpros_props as bpp
from mlb_core.odds.utils import american_to_implied_prob, devig_two_way

SCHED = "2025-12-11 00:30:00"          # 2025-12-10 19:30 ET


def q(book_id, over, under, line=1.5, player="Connor McDavid", pid=100, event=1, market="shots", date="2025-12-10",
      kind=None, name=None, updated="2025-12-10 23:00:00", team="EDM", scheduled=SCHED):
    """Two long rows (over + under) for one book at one line. `over`/`under` None = that side absent."""
    out = []
    for side, px in (("over", over), ("under", under)):
        if px is None:
            continue
        out.append({"date": date, "sport": "NHL", "market": market, "market_id": 1, "event_id": event,
                    "scheduled": scheduled, "away": "EDM", "home": "CHI", "player_id": pid, "player": player,
                    "team": team, "line": line, "side": side, "book_id": book_id,
                    "book": name or bpp.BOOK_NAMES.get(book_id, f"book_{book_id}"),
                    "book_kind": kind or bpp.book_kind(book_id), "odds": px, "is_open": False, "updated": updated})
    return out


def frame(*groups):
    rows = [r for g in groups for r in g]
    return pd.DataFrame(rows)


def fair(o, u):
    return devig_two_way(american_to_implied_prob(o), american_to_implied_prob(u))[0]


BOOKS = [(10, -120, 100), (12, -110, -110), (13, -130, 105), (19, -115, -105)]       # FD, DK, Caesars, MGM


def basic():
    return frame(*[q(b, o, u) for b, o, u in BOOKS])


def test_excludes_kalshi_dfs_consensus_open_sharp():
    extra = (q(68, -105, -105) + q(37, -120, -120) + q(0, -110, -110) + q(2, -125, 105)
             + q(10, -300, 250, name="Kalshi")                       # Kalshi spelled in the NAME on another id
             + [dict(r, is_open=True, book_kind="open") for r in q(10, -400, 300)])
    df = frame(basic().to_dict("records"), extra)
    out = L.consensus_from_long(df, min_books=3)
    assert len(out) == 1
    assert out.iloc[0]["n_books"] == 4                               # only the four real books counted
    want = np.median([fair(o, u) for _, o, u in BOOKS])
    assert out.iloc[0]["p_over_cons"] == pytest.approx(want)


def test_one_sided_book_is_skipped():
    df = frame(basic().to_dict("records"), q(18, -125, None), q(24, None, 110))
    out = L.consensus_from_long(df, min_books=3)
    assert out.iloc[0]["n_books"] == 4


def test_two_sided_requires_same_line():
    # book 18 quotes the over at 1.5 and the under at 2.5: never pairs
    df = frame(basic().to_dict("records"), q(18, -125, None, line=1.5), q(18, None, 110, line=2.5))
    out = L.consensus_from_long(df, min_books=3)
    assert out.iloc[0]["n_books"] == 4 and out.iloc[0]["line"] == 1.5


def test_median_of_devigged_probs_not_of_prices():
    out = L.consensus_from_long(basic(), min_books=3).iloc[0]
    fs = sorted(fair(o, u) for _, o, u in BOOKS)
    assert out["p_over_cons"] == pytest.approx((fs[1] + fs[2]) / 2)
    med = np.median(fs)
    assert out["disp"] == pytest.approx(np.median(np.abs(np.array(fs) - med)))


def test_modal_line_most_books_wins():
    df = frame(*[q(b, o, u, line=1.5) for b, o, u in BOOKS[:3]],
               *[q(b, o, u, line=2.5) for b, o, u in BOOKS])          # 2.5 has 4 two-sided books, 1.5 has 3
    out = L.consensus_from_long(df, min_books=3)
    assert len(out) == 1 and out.iloc[0]["line"] == 2.5 and out.iloc[0]["n_books"] == 4


def test_modal_line_tie_breaks_on_closest_to_even_then_lower_line():
    # equal book counts: 1.5 is a 65% over line, 2.5 is ~50/50 -> 2.5 wins
    lopsided = [(10, -180, 150), (12, -175, 145), (13, -190, 160)]
    even = [(10, -110, -110), (12, -108, -112), (13, -112, -108)]
    df = frame(*[q(b, o, u, line=1.5) for b, o, u in lopsided], *[q(b, o, u, line=2.5) for b, o, u in even])
    assert L.consensus_from_long(df, 3).iloc[0]["line"] == 2.5
    # identical probabilities at both lines -> the LOWER line
    df2 = frame(*[q(b, o, u, line=3.5) for b, o, u in even], *[q(b, o, u, line=2.5) for b, o, u in even])
    assert L.consensus_from_long(df2, 3).iloc[0]["line"] == 2.5


def test_best_odds_and_novig_columns():
    df = frame(basic().to_dict("records"), q(60, -102, -118))        # Novig two-sided
    out = L.consensus_from_long(df, min_books=3).iloc[0]
    assert out["n_books"] == 5
    assert out["best_over_odds"] == -102                               # -102 is the best of -120/-110/-130/-115/-102
    assert out["best_under_odds"] == 105                               # Caesars +105
    assert out["novig_over_odds"] == -102 and out["novig_under_odds"] == -118


def test_novig_absent_is_nan_and_novig_one_sided_still_reported_but_not_counted():
    out = L.consensus_from_long(basic(), 3).iloc[0]
    assert np.isnan(out["novig_over_odds"]) and np.isnan(out["novig_under_odds"])
    df = frame(basic().to_dict("records"), q(60, -105, None))
    o2 = L.consensus_from_long(df, 3).iloc[0]
    assert o2["n_books"] == 4 and o2["novig_over_odds"] == -105 and np.isnan(o2["novig_under_odds"])


def test_min_books_gating():
    df = frame(*[q(b, o, u) for b, o, u in BOOKS[:2]])
    assert len(L.consensus_from_long(df, min_books=3)) == 0
    assert len(L.consensus_from_long(df, min_books=2)) == 1


def test_empty_and_all_filtered_inputs():
    out = L.consensus_from_long(pd.DataFrame(columns=bpp.COLUMNS))
    assert list(out.columns) == L.OUTPUT_COLUMNS and len(out) == 0
    assert len(L.consensus_from_long(frame(q(68, -110, -110)))) == 0          # only Kalshi
    assert list(L.consensus_from_long(None).columns) == L.OUTPUT_COLUMNS


def test_player_norm_handles_suffix_and_accents():
    df = pd.concat([frame(*[q(b, o, u, player="Jos\u00e9 Alvarado Jr.") for b, o, u in BOOKS]),
                    frame(*[q(b, o, u, player="Ja'Marr Chase", pid=7) for b, o, u in BOOKS])])
    out = L.consensus_from_long(df, 3)
    assert set(out["player_norm"]) == {"jose alvarado", "jamarr chase"}
    assert all(ord(ch) < 128 for ch in "".join(out["player_norm"]))


def test_one_row_per_event_player_stat_and_stat_label_mapping():
    df = frame(*[q(b, o, u, market="shots", player="A B", pid=1) for b, o, u in BOOKS],
               *[q(b, o, u, market="points", player="A B", pid=1, line=0.5) for b, o, u in BOOKS],
               *[q(b, o, u, market="shots", player="C D", pid=2) for b, o, u in BOOKS])
    out = L.consensus_from_long(df, 3, sport="NHL")
    assert sorted(zip(out["player_norm"], out["stat"])) == [("a b", "points"), ("a b", "sog"), ("c d", "sog")]
    assert len(out) == 3


def test_game_date_is_eastern_and_pregame_flag():
    out = L.consensus_from_long(basic(), 3).iloc[0]
    assert out["game_date"] == "2025-12-10"                            # 00:30 UTC on the 11th is the 10th in ET
    assert out["pregame"] == True                                      # noqa: E712  (updated 23:00 ET-eve < 00:30 UTC start)


def test_pregame_only_drops_post_start_quotes():
    late = frame(*[q(b, o, u, updated="2025-12-11 01:00:00") for b, o, u in BOOKS[:2]],
                 *[q(b, o, u, updated="2025-12-10 22:00:00") for b, o, u in BOOKS[2:]])
    assert L.consensus_from_long(late, 2).iloc[0]["n_books"] == 4
    out = L.consensus_from_long(late, 2, pregame_only=True)
    assert out.iloc[0]["n_books"] == 2 and out.iloc[0]["pregame"] == True    # noqa: E712


def test_duplicate_quotes_latest_updated_wins():
    old = q(10, -300, 250, updated="2025-12-10 10:00:00")
    df = frame(basic().to_dict("records"), old)
    out = L.consensus_from_long(df, 3).iloc[0]
    assert out["n_books"] == 4 and out["p_over_cons"] == pytest.approx(
        np.median([fair(o, u) for _, o, u in BOOKS]))


def test_build_market_consensus_reads_partitions(monkeypatch):
    calls = []

    def fake_read(sport, market, start, end, prefix):
        calls.append((sport, market, start, end))
        return basic() if market == "shots" else pd.DataFrame(columns=bpp.COLUMNS)
    monkeypatch.setattr(bpp, "read_props", fake_read)
    out = L.build_market_consensus("nhl", "2025-12-01", "2025-12-31", markets=["sog", "points"], min_books=3)
    assert {c[1] for c in calls} == {"shots", "points"}
    assert len(out) == 1 and out.iloc[0]["stat"] == "sog" and out.iloc[0]["sport"] == "NHL"
    assert L.coverage(out)["books_hist"] == {4: 1}
    assert len(L.build_market_consensus("nhl", markets=["assists"])) == 0
    with pytest.raises(ValueError):
        L.build_market_consensus("mlb")
