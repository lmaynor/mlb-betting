"""
Tests for mlb_core.alerts.ladder_alerts.
Plain test functions, no external fixtures beyond local helpers.
Must not import sqlalchemy or modules pulling it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import mlb_core.alerts.ladder_alerts as la


# -- Local test fixtures & helpers ----------------------------------------------------------------------------

def make_book_bar(book: str, over: int | None = None, under: int | None = None, ts: str | None = "2026-11-20T12:00:00Z", **kw) -> dict:
    bar = {
        "book": book,
        "over": over,
        "under": under,
        "ts": ts,
        "over_cap": False,
        "under_cap": False,
        "stale_opener": False,
    }
    bar.update(kw)
    return bar


def make_row(
    stat: str = "receptions",
    line: float = 4.5,
    player: str = "Travis Kelce",
    player_norm: str = "travis kelce",
    game_key: str = "nfl:2026-11-20:KC@BUF",
    away: str = "KC",
    home: str = "BUF",
    commence: str = "2026-11-20T18:00:00Z",
    best_over: dict | None = None,
    best_under: dict | None = None,
    books: list[dict] | None = None,
    rung_vig: float | None = 0.045,
    modal_open: float | None = 4.5,
    modal_point: float | None = 5.5,
    **kw,
) -> dict:
    """Build a board row fixture in the ev/odds_board.py output shape."""
    if books is None:
        books = [
            make_book_bar("draftkings", over=600, under=-900),
            make_book_bar("betmgm", over=550, under=-800),
            make_book_bar("fanduel", over=500, under=-750),
            make_book_bar("caesars", over=450, under=-650),
            make_book_bar("bet365", over=475, under=-700),
        ]

    if best_over is None and best_under is None:
        best_over = {
            "book": "draftkings",
            "american": 600,
            "p_cons_loo": 0.16,
            "disp_loo": 0.02,
            "n_ref_loo": 4,
        }

    row = {
        "game_key": game_key,
        "commence": commence,
        "away": away,
        "home": home,
        "player": player,
        "player_norm": player_norm,
        "stat": stat,
        "line": line,
        "books": books,
        "best_over": best_over,
        "best_under": best_under,
        "n_ref": 5,
        "disp": 0.02,
        "p_cons": 0.16,
        "rung_vig": rung_vig,
        "modal_open": modal_open,
        "modal_point": modal_point,
    }
    row.update(kw)
    return row


# -- Test 1: Fully Qualifying Count Rung ----------------------------------------------------------------------

def test_fully_qualifying_count_rung():
    """
    A fully qualifying count rung produces exactly one embed containing:
    - '[FORWARD SHADOW · UNGRADED]'
    - 'Consensus Fair'
    - 'Gate floor (+5.0%): +557'
    - 'Shadow EV @ Best'
    - 'Ref books: 4 (2-sided)'
    - 'Rung Vig: 4.5%'
    - Top books ordered best price first
    """
    r = make_row(
        stat="receptions",
        line=4.5,
        player="Travis Kelce",
        best_over={
            "book": "draftkings",
            "american": 600,
            "p_cons_loo": 0.16,
            "disp_loo": 0.02,
            "n_ref_loo": 4,
        },
        rung_vig=0.045,
        modal_open=4.5,
        modal_point=5.5,
    )

    embeds, overflow = la.plan_ladder_pings([r])
    assert len(embeds) == 1
    assert overflow is None

    embed = embeds[0]
    desc = embed["description"]

    # Verify governance tag
    assert "[FORWARD SHADOW · UNGRADED] LADDER" in desc

    # Main anchor line
    assert "Main: Open 4.5 → Now 5.5" in desc

    # Top books line ordered best price first (DraftKings +600, BetMGM +550, FanDuel +500)
    assert "Top books: DraftKings +600 · BetMGM +550 · FanDuel +500" in desc

    # Consensus fair line (p=0.16 -> +525, 16.0%)
    assert "Consensus Fair +525 · 16.0%" in desc

    # Gate floor line (e=0.05, p=0.16 -> +557)
    assert "Gate floor (+5.0%): +557" in desc

    # Shadow EV line (0.16 * 7.0 - 1 = +12.0%)
    assert "Shadow EV @ Best: +12.0%" in desc

    # Ref books line with disp and rung vig
    assert "Ref books: 4 (2-sided) · Disp: 0.020 · Rung Vig: 4.5%" in desc

    # Title: Count category + over 4.5 -> "5+"
    assert embed["title"] == "Travis Kelce — 5+ (Receptions)"
    assert embed["color"] == 0x57F287
    assert embed["url"] == la.EDGE_URL
    assert "KC @ BUF" in embed["footer"]["text"]


# -- Test 2: Novig Behavior -----------------------------------------------------------------------------------

def test_novig_behavior():
    """
    Novig checks:
    1. best_over.book = 'novig' -> no ping at all.
    2. best_over is a sportsbook, but novig has the best bar price:
       novig appears nowhere in embed text, top-books starts with best sportsbook.
    """
    # Case 1: best_over is Novig
    r1 = make_row(
        best_over={
            "book": "novig",
            "american": 600,
            "p_cons_loo": 0.16,
            "disp_loo": 0.02,
            "n_ref_loo": 4,
        },
        books=[
            make_book_bar("novig", over=600, under=-900),
            make_book_bar("draftkings", over=500, under=-750),
            make_book_bar("betmgm", over=450, under=-650),
            make_book_bar("fanduel", over=400, under=-600),
            make_book_bar("caesars", over=350, under=-500),
        ],
    )
    embeds1, overflow1 = la.plan_ladder_pings([r1])
    assert len(embeds1) == 0
    assert overflow1 is None

    # Case 2: best_over is DraftKings (+600), but Novig has a higher bar (+700)
    r2 = make_row(
        best_over={
            "book": "draftkings",
            "american": 600,
            "p_cons_loo": 0.16,
            "disp_loo": 0.02,
            "n_ref_loo": 4,
        },
        books=[
            make_book_bar("novig", over=700, under=-1000),
            make_book_bar("draftkings", over=600, under=-900),
            make_book_bar("betmgm", over=550, under=-800),
            make_book_bar("fanduel", over=500, under=-750),
            make_book_bar("caesars", over=450, under=-650),
        ],
    )
    embeds2, overflow2 = la.plan_ladder_pings([r2])
    assert len(embeds2) == 1
    assert overflow2 is None

    embed2 = embeds2[0]
    full_text = f"{embed2['title']} {embed2['description']} {embed2['footer']['text']}".lower()

    # Novig appears nowhere in the embed text
    assert "novig" not in full_text

    # Top-books line starts with the best sportsbook
    assert "Top books: DraftKings +600 · BetMGM +550 · FanDuel +500" in embed2["description"]


# -- Test 3: Gate Failures Suppress Ping -----------------------------------------------------------------------

def test_gate_failures_suppress_ping():
    """Verify each gate failure independently suppresses qualification."""
    # 1. n_ref_loo = 3 (needs >= 4)
    r_nref = make_row(best_over={
        "book": "draftkings",
        "american": 600,
        "p_cons_loo": 0.16,
        "disp_loo": 0.02,
        "n_ref_loo": 3,
    })
    assert la.plan_ladder_pings([r_nref]) == ([], None)

    # 2. disp_loo = 0.08 (needs <= 0.06)
    r_disp = make_row(best_over={
        "book": "draftkings",
        "american": 600,
        "p_cons_loo": 0.16,
        "disp_loo": 0.08,
        "n_ref_loo": 4,
    })
    assert la.plan_ladder_pings([r_disp]) == ([], None)

    # 3. Net EV below floor (price +105, p_cons_loo 0.45 count -> EV = 0.45 * 2.05 - 1 = -7.75% < 5.0%)
    r_ev = make_row(best_over={
        "book": "draftkings",
        "american": 105,
        "p_cons_loo": 0.45,
        "disp_loo": 0.02,
        "n_ref_loo": 4,
    }, books=[
        make_book_bar("draftkings", over=105, under=-125),
        make_book_bar("betmgm", over=100, under=-120),
        make_book_bar("fanduel", over=100, under=-120),
        make_book_bar("caesars", over=100, under=-120),
        make_book_bar("bet365", over=100, under=-120),
    ])
    assert la.plan_ladder_pings([r_ev]) == ([], None)

    # 4. stale_opener is True on best book's bar
    r_stale = make_row(books=[
        make_book_bar("draftkings", over=600, under=-900, stale_opener=True),
        make_book_bar("betmgm", over=550, under=-800),
        make_book_bar("fanduel", over=500, under=-750),
        make_book_bar("caesars", over=450, under=-650),
        make_book_bar("bet365", over=475, under=-700),
    ])
    assert la.plan_ladder_pings([r_stale]) == ([], None)

    # 5. Best book ts is 45 min older than the freshest two-sided ts
    r_old_ts = make_row(books=[
        make_book_bar("draftkings", over=600, under=-900, ts="2026-11-20T11:15:00Z"),
        make_book_bar("betmgm", over=550, under=-800, ts="2026-11-20T12:00:00Z"),
        make_book_bar("fanduel", over=500, under=-750, ts="2026-11-20T12:00:00Z"),
        make_book_bar("caesars", over=450, under=-650, ts="2026-11-20T12:00:00Z"),
        make_book_bar("bet365", over=475, under=-700, ts="2026-11-20T12:00:00Z"),
    ])
    assert la.plan_ladder_pings([r_old_ts]) == ([], None)

    # 6. Best book bar is one-sided (under is None)
    r_one_sided = make_row(books=[
        make_book_bar("draftkings", over=600, under=None),
        make_book_bar("betmgm", over=550, under=-800),
        make_book_bar("fanduel", over=500, under=-750),
        make_book_bar("caesars", over=450, under=-650),
        make_book_bar("bet365", over=475, under=-700),
    ])
    assert la.plan_ladder_pings([r_one_sided]) == ([], None)

    # 7. Placeholder cap price on best_over (>= 10000 or <= -5000)
    r_cap_hi = make_row(best_over={
        "book": "draftkings",
        "american": 10000,
        "p_cons_loo": 0.16,
        "disp_loo": 0.02,
        "n_ref_loo": 4,
    })
    assert la.plan_ladder_pings([r_cap_hi]) == ([], None)

    r_cap_lo = make_row(best_over={
        "book": "draftkings",
        "american": -5000,
        "p_cons_loo": 0.16,
        "disp_loo": 0.02,
        "n_ref_loo": 4,
    })
    assert la.plan_ladder_pings([r_cap_lo]) == ([], None)


# -- Test 4: Siblings Formatting ------------------------------------------------------------------------------

def test_siblings_formatting():
    """
    A ladder with 4 qualifying rungs produces 1 embed:
    primary + 2 inline siblings + '+1 more in /edge'.
    """
    # Create 4 rows for the same player, stat, game with different lines
    # EV: row 1 (line 4.5) has 20%, row 2 (5.5) has 15%, row 3 (6.5) has 12%, row 4 (7.5) has 8%
    rows = [
        make_row(
            line=4.5,
            best_over={"book": "draftkings", "american": 600, "p_cons_loo": 0.1714, "disp_loo": 0.02, "n_ref_loo": 4},  # EV ~20%
        ),
        make_row(
            line=5.5,
            best_over={"book": "draftkings", "american": 700, "p_cons_loo": 0.1438, "disp_loo": 0.02, "n_ref_loo": 4},  # EV ~15%
        ),
        make_row(
            line=6.5,
            best_over={"book": "draftkings", "american": 800, "p_cons_loo": 0.1244, "disp_loo": 0.02, "n_ref_loo": 4},  # EV ~12%
        ),
        make_row(
            line=7.5,
            best_over={"book": "draftkings", "american": 900, "p_cons_loo": 0.108, "disp_loo": 0.02, "n_ref_loo": 4},   # EV ~8%
        ),
    ]

    embeds, overflow = la.plan_ladder_pings(rows)
    assert len(embeds) == 1
    assert overflow is None

    embed = embeds[0]
    desc = embed["description"]

    # Primary is line 4.5 (label "5+")
    assert embed["title"] == "Travis Kelce — 5+ (Receptions)"

    # Sibling line must exist and mention 6+ and 7+, with +1 more in /edge
    assert "Other qualifying rungs:" in desc
    assert "6+ (+700, +15.0% shadow EV)" in desc
    assert "7+ (+800, +12.0% shadow EV)" in desc
    assert "+1 more in /edge" in desc


# -- Test 5: Window Cap and Overflow Digest -------------------------------------------------------------------

def test_window_cap_and_overflow():
    """
    5 qualifying ladders:
    Plan returns 3 top embeds + 1 overflow digest whose title counts 2 more.
    Ordering by EV descending verified by player names.
    """
    players = [
        ("Player A", "player a", 0.1857),  # EV ~30%
        ("Player B", "player b", 0.1786),  # EV ~25%
        ("Player C", "player c", 0.1714),  # EV ~20%
        ("Player D", "player d", 0.1643),  # EV ~15%
        ("Player E", "player e", 0.1571),  # EV ~10%
    ]

    all_rows = []
    for name, norm, p_fair in players:
        row = make_row(
            player=name,
            player_norm=norm,
            best_over={"book": "draftkings", "american": 600, "p_cons_loo": p_fair, "disp_loo": 0.02, "n_ref_loo": 4},
        )
        all_rows.append(row)

    embeds, overflow = la.plan_ladder_pings(all_rows)

    assert len(embeds) == 3
    assert overflow is not None

    # Verify ordering of top 3 embeds
    assert "Player A" in embeds[0]["title"]
    assert "Player B" in embeds[1]["title"]
    assert "Player C" in embeds[2]["title"]

    # Overflow digest checks
    assert overflow["title"] == "More ladder dislocations (2 more)"
    assert overflow["color"] == 0x57F287
    assert overflow["url"] == la.EDGE_URL
    assert "[FORWARD SHADOW · UNGRADED] LADDER" in overflow["description"]
    assert "Player D — 5+:" in overflow["description"]
    assert "Player E — 5+:" in overflow["description"]
    assert overflow["footer"]["text"] == "Full ladders in /edge"


# -- Test 6: Gate Floor American Unit Checks ------------------------------------------------------------------

def test_gate_floor_american_unit_checks():
    """
    Unit checks:
    - (0.16, 'count') == 557
    - (0.60, 'yardage') == -141
    - None for a placeholder-cap result
    - None for p <= 0
    - None for p >= 1
    - None for milestone or invalid category
    """
    # Required unit checks
    assert la.gate_floor_american(0.16, "count") == 557
    assert la.gate_floor_american(0.60, "yardage") == -141

    # Placeholder cap result (e.g. p = 0.005 -> d = 1.05 / 0.005 = 210 -> A = 20900 >= 10000 -> None)
    assert la.gate_floor_american(0.005, "count") is None

    # p <= 0
    assert la.gate_floor_american(0, "count") is None
    assert la.gate_floor_american(-0.2, "yardage") is None

    # p >= 1
    assert la.gate_floor_american(1.0, "yardage") is None
    assert la.gate_floor_american(1.5, "count") is None

    # invalid p or None
    assert la.gate_floor_american(None, "yardage") is None

    # milestone / invalid category
    assert la.gate_floor_american(0.16, "milestone") is None
    assert la.gate_floor_american(0.16, "other") is None


# -- Test 7: Banned Content Discipline ------------------------------------------------------------------------

def test_banned_content():
    """
    Embed text must NEVER contain standalone words:
    'pick', 'play', 'recommended', 'mispriced', 'verified edge', 'official', 'Kelly', 'units',
    or the phrase 'Playable to'.
    'player' must not false-positive on 'play'.
    'Playable to' is absent while 'Gate floor' is present.
    """
    r = make_row(player="Player One", modal_open=4.5, modal_point=5.5)
    embed = la.build_ladder_embed(r)

    full_text = f"{embed['title']}\n{embed['description']}\n{embed['footer']['text']}"

    # Banned standalone words pattern
    banned_words = [
        "pick",
        "play",
        "recommended",
        "mispriced",
        "verified edge",
        "official",
        "kelly",
        "units",
    ]
    for w in banned_words:
        pattern = rf"\b{re.escape(w)}\b"
        match = re.search(pattern, full_text, re.IGNORECASE)
        assert match is None, f"Found banned word '{w}' in embed text: {match.group(0)}"

    # Phrase check
    assert "playable to" not in full_text.lower()

    # Required allowed phrase
    assert "gate floor" in full_text.lower()

    # Ensure "player" is present and didn't trigger "play"
    assert "Player One" in embed["title"]


# -- Test 8: Milestone and Stat Scope -------------------------------------------------------------------------

def test_milestone_and_stat_scope():
    """
    Milestone and out-of-scope stats NEVER ping:
    - anytime_td row -> no ping
    - points (NBA stat) row -> no ping
    """
    # anytime_td (NFL milestone)
    r_anytime = make_row(
        stat="anytime_td",
        line=0.5,
        best_over={"book": "draftkings", "american": 600, "p_cons_loo": 0.16, "disp_loo": 0.02, "n_ref_loo": 4},
    )
    assert la.plan_ladder_pings([r_anytime]) == ([], None)

    # points (NBA stat)
    r_points = make_row(
        stat="points",
        line=25.5,
        best_over={"book": "draftkings", "american": 600, "p_cons_loo": 0.16, "disp_loo": 0.02, "n_ref_loo": 4},
    )
    assert la.plan_ladder_pings([r_points]) == ([], None)


# -- Test 9: Yardage Stat Formatting & Unchanged Modal Anchor -------------------------------------------------

def test_yardage_formatting_and_unchanged_anchor():
    """
    Verify yardage line formatting (65.0 -> '65', 64.5 -> '64.5'),
    title 'Over 65', and unchanged anchor 'Main: 65 · Unchanged'.
    """
    r = make_row(
        stat="rec_yds",
        line=65.0,
        player="Justin Jefferson",
        player_norm="justin jefferson",
        best_over={"book": "draftkings", "american": 150, "p_cons_loo": 0.45, "disp_loo": 0.02, "n_ref_loo": 4},  # EV = 0.45 * 2.5 - 1 = +12.5% >= 2.5%
        books=[
            make_book_bar("draftkings", over=150, under=-180),
            make_book_bar("betmgm", over=140, under=-170),
            make_book_bar("fanduel", over=135, under=-165),
            make_book_bar("caesars", over=130, under=-160),
            make_book_bar("bet365", over=125, under=-155),
        ],
        modal_open=65.0,
        modal_point=65.0,
    )

    embeds, overflow = la.plan_ladder_pings([r])
    assert len(embeds) == 1
    assert overflow is None

    embed = embeds[0]
    assert embed["title"] == "Justin Jefferson — Over 65 (Rec Yds)"
    assert "Main: 65 · Unchanged" in embed["description"]
    assert "Gate floor (+2.5%):" in embed["description"]


# -- Test 10: Under Side Qualification & Delivery Path --------------------------------------------------------

def test_under_side_qualification_and_delivery(monkeypatch):
    """
    Verify under side qualification:
    - Title has 'Under 64.5'
    - post_ladder_pings bot delivery path and webhook fallback path.
    """
    r = make_row(
        stat="pass_yds",
        line=64.5,
        player="Josh Allen",
        player_norm="josh allen",
        best_over=None,
        best_under={"book": "draftkings", "american": 150, "p_cons_loo": 0.55, "disp_loo": 0.02, "n_ref_loo": 4},
        books=[
            make_book_bar("draftkings", over=-180, under=150),
            make_book_bar("betmgm", over=-170, under=140),
            make_book_bar("fanduel", over=-165, under=135),
            make_book_bar("caesars", over=-160, under=130),
            make_book_bar("bet365", over=-155, under=125),
        ],
        modal_open=64.5,
        modal_point=64.5,
    )

    embeds, _ = la.plan_ladder_pings([r])
    assert len(embeds) == 1
    assert "Under 64.5" in embeds[0]["title"]

    # Delivery test: Nothing qualifies -> returns False
    assert la.post_ladder_pings([]) is False

    # Delivery test: Bot path
    bot_calls = []
    monkeypatch.setattr(la, "bot_configured", lambda: True)
    monkeypatch.setattr(la, "post_via_bot", lambda kind, content, embs, sport=None: bot_calls.append((kind, sport, len(embs))) or True)
    assert la.post_ladder_pings([r], sport="nfl") is True
    assert len(bot_calls) == 1
    assert bot_calls[0] == ("card", "nfl", 1)

    # Delivery test: Webhook fallback
    monkeypatch.setattr(la, "bot_configured", lambda: False)
    monkeypatch.setattr(la, "webhook_for", lambda kind, sport=None: "https://discord.test/hook")

    class FakeResponse:
        status_code = 204

    webhook_posts = []

    def fake_post(url, json=None, timeout=None):
        webhook_posts.append((url, json))
        return FakeResponse()

    with patch("requests.post", side_effect=fake_post):
        assert la.post_ladder_pings([r], sport="nfl") is True
        assert len(webhook_posts) == 1
        assert webhook_posts[0][0] == "https://discord.test/hook"
        assert len(webhook_posts[0][1]["embeds"]) == 1

    # Delivery test: No destination configured -> returns False
    monkeypatch.setattr(la, "webhook_for", lambda kind, sport=None: None)
    assert la.post_ladder_pings([r], sport="nfl") is False
