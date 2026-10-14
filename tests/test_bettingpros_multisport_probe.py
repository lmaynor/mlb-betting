"""scripts/bettingpros_multisport_probe.py pure logic: market resolution + per-offer book coverage (synthetic offers)."""
import importlib.util
import os

SPEC = importlib.util.spec_from_file_location(
    "bp_probe", os.path.join(os.path.dirname(__file__), "..", "scripts", "bettingpros_multisport_probe.py"))
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

NAMES = {2: "Pinnacle", 10: "FanDuel", 12: "DraftKings", 13: "Caesars", 38: "ProphetX", 60: "Novig", 37: "PrizePicks"}


def offer(player, over, under):
    """over/under: {book_id: (line, cost)}; book id 0 is the BettingPros consensus row."""
    def sel(side, d):
        return {"selection": side.lower(), "label": side,
                "books": [{"id": b, "lines": [{"line": ln, "cost": c}]} for b, (ln, c) in d.items()]}
    return {"participants": [{"name": player}], "selections": [sel("Over", over), sel("Under", under)]}


def test_resolve_markets_uses_catalog_slugs_not_hardcoded_ids():
    catalog = [{"id": 105, "slug": "receiving-yards", "category": "player-props", "period": "game"},
               {"id": 75, "slug": "most-receiving-yards", "category": "player-props", "period": "game"},
               {"id": 999, "slug": "passing-yards", "category": "player-props", "period": "1st-half"},   # wrong period
               {"id": 1, "slug": "moneyline", "category": "game-odds", "period": "game"}]
    found, all_props = probe.resolve_markets(catalog, "NFL")
    assert found == {"rec_yds": {"market_id": 105, "slug": "receiving-yards"}}                # only the real match
    assert "most-receiving-yards" in all_props and "moneyline" not in all_props
    assert probe.resolve_markets(catalog, "XXX") == ({}, ["most-receiving-yards", "receiving-yards"])


def test_coverage_counts_two_sided_books_at_the_modal_line_and_ignores_consensus():
    o = offer("A", over={0: (50.5, -110), 10: (50.5, -115), 12: (50.5, -110), 13: (49.5, -120), 37: (50.5, -118)},
              under={0: (50.5, -110), 10: (50.5, -105), 12: (50.5, -110), 13: (49.5, 100)})
    c = probe.offer_coverage(o)
    assert c["books_any"] == {10, 12, 13, 37}                                                 # consensus (0) excluded
    assert c["modal_line"] == 50.5 and c["n_modal_lines"] == 2
    assert c["books_modal_two_sided"] == {10, 12}          # 13 is two-sided but at another line; 37 is over-only


def test_summarize_reports_watchlist_books_and_pct_with_three_plus_books():
    good = offer("A", over={10: (5.5, -110), 12: (5.5, -110), 60: (5.5, -105)},
                 under={10: (5.5, -110), 12: (5.5, -110), 60: (5.5, -115)})
    thin = offer("B", over={10: (5.5, -110)}, under={10: (5.5, -110)})
    s = probe.summarize([good, thin], NAMES)
    assert s["offers"] == 2 and s["players"] == 2
    assert s["pct_offers_ge3_two_sided"] == 50.0 and s["books_modal_two_sided"]["max"] == 3
    assert s["watch"]["novig"] == {"offers": 1, "pct": 50.0, "two_sided_modal": 1}
    assert s["watch"]["pinnacle"]["offers"] == 0                                              # absent book reports 0, not KeyError
    assert s["book_presence_pct"]["FanDuel"] == 100.0


def test_summarize_handles_no_offers():
    s = probe.summarize([], NAMES)
    assert s["offers"] == 0 and s["pct_offers_ge3_two_sided"] is None and s["book_presence_pct"] == {}
