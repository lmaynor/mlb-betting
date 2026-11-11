"""
BestBook derivative capture (Workstream B, derivative-archive-plan-2026-11-11.md): the sibling parser
ev.bestbook_game_lines.derivative_quotes (period markets + team totals, archive schema) and the RAW-FIRST
OddsAccum archiving in ev.bestbook_events. Row shapes follow the ones probed live 2026-11-01 and the ignored
families in tests/test_bestbook_game_lines.py: family is '{game}|token|...', `line` is the AWAY handicap on
spread rows, rung labels carry a REAL minus sign, quotes are per-venue yes/no percents + native strings.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from ev import bestbook_events as bbe
from ev import bestbook_game_lines as bgl

MINUS = chr(0x2212)
GAME = "nba:2026-11-02-LAC-GSW"
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=5)).isoformat()


def pct(american):
    p = 100.0 / (american + 100.0) if american > 0 else -american / (-american + 100.0)
    return round(p * 100.0, 4)


def q(venue, yes, no):
    out = {"venue": venue}
    if yes is not None:
        out.update(yes=pct(yes), yesNative=f"{yes:+d}".replace("-", MINUS))
    if no is not None:
        out.update(no=pct(no), noNative=f"{no:+d}".replace("-", MINUS))
    return out


def row(rid, family, rung, line, quotes, rung_team=None, section="lines", team=None):
    return {"id": f"match:{GAME}|{rid}", "venue": "match", "game": GAME, "gameTeams": ["LAC", "GSW"], "section": section,
            "family": f"{GAME}|{family}", "rung": rung, "line": line, "rungTeam": rung_team, "team": team, "quotes": quotes}


def by(quotes, market, period, book, team_scope="game", line="any"):
    hit = [r for r in quotes if r["market"] == market and r["period"] == period and r["book"] == book
           and r["team_scope"] == team_scope and (line == "any" or r["line"] == line)]
    assert len(hit) == 1, (market, period, book, team_scope, line, hit)
    return hit[0]


def deriv_rows():
    return [
        # 1H total 110.5 (the row shape the full-game parser's fixtures already carry as an ignored family)
        row("total|1h|110.5", "total|1h", "Over 110.5", 110.5, [q("fd", -110, -110), q("dk", -112, -108)], section="periods:1h"),
        # 1H ML, both orientations: LAC (away) +120 / GSW (home) -140 at fanduel
        row("ml|1h|LAC", "ml|1h", "LAC wins", 0, [q("fd", 120, -140)], rung_team="LAC"),
        row("ml|1h|GSW", "ml|1h", "GSW wins", 0, [q("fd", -140, 120)], rung_team="GSW"),
        # Q1 spread: 'GSW -1.5' home lays -> home handicap -1.5
        row("spread|1q|GSW|1.5", "spread|1q", f"GSW {MINUS}1.5", 1.5, [q("fd", -115, -105)], rung_team="GSW"),
        # Q2 total (standalone quarter)
        row("total|2q|55.5", "total|2q", "Over 55.5", 55.5, [q("dk", -108, -112)], section="periods:2q"),
        # Full-game team total for GSW + a 1H team total for LAC
        row("teamtotal|GSW|110.5", "teamtotal|GSW", "Over 110.5", 110.5, [q("fd", -115, -105)]),
        row("teamtotal|1h|LAC|52.5", "teamtotal|LAC|1h", "Over 52.5", 52.5, [q("dk", -110, -110)], team="LAC"),
        # Full-game rows: the derivative parser must NOT claim them (game_line_quotes owns them)
        row("ml|LAC", "ml", "LAC wins", 0, [q("fd", 114, -134)], rung_team="LAC"),
        row("total|full|220.5", "total|full", "Over 220.5", 220.5, [q("fd", -110, -110)]),
    ]


def test_period_scopes_are_stored_explicitly():
    quotes = bgl.derivative_quotes(deriv_rows())
    assert by(quotes, "totals", "h1", "fanduel", line=110.5)["period_scope"] == "cumulative"    # 1H = Q1+Q2
    assert by(quotes, "spreads", "q1", "fanduel", line=-1.5)["period_scope"] == "standalone"   # Q1 only
    assert by(quotes, "totals", "q2", "draftkings", line=55.5)["period_scope"] == "standalone"
    tt = by(quotes, "team_totals", "full", "fanduel", team_scope="GSW")
    assert tt["period_scope"] == "standalone" and tt["line"] == 110.5
    tt1h = by(quotes, "team_totals", "h1", "draftkings", team_scope="LAC")
    assert tt1h["period_scope"] == "cumulative"


def test_price_conventions_and_home_handicap():
    quotes = bgl.derivative_quotes(deriv_rows())
    ml = by(quotes, "h2h", "h1", "fanduel")
    assert (ml["price_a"], ml["price_b"]) == (120, -140) and ml["line"] is None and ml["n_outcomes"] == 2
    sp = by(quotes, "spreads", "q1", "fanduel")
    assert (sp["price_a"], sp["price_b"]) == (-105, -115)          # yes = home covers = b; no = away = a
    tot = by(quotes, "totals", "h1", "draftkings", line=110.5)
    assert (tot["price_a"], tot["price_b"]) == (-112, -108)        # a = Over, b = Under
    tt = by(quotes, "team_totals", "full", "fanduel", team_scope="GSW")
    assert (tt["price_a"], tt["price_b"]) == (-115, -105)


def test_raw_family_is_stored_verbatim_and_full_game_rows_are_not_claimed():
    quotes = bgl.derivative_quotes(deriv_rows())
    assert by(quotes, "totals", "h1", "fanduel", line=110.5)["raw_family"] == f"{GAME}|total|1h"
    assert by(quotes, "team_totals", "full", "fanduel", team_scope="GSW")["raw_family"] == f"{GAME}|teamtotal|GSW"
    assert all(r["source"] == "bestbook" for r in quotes)
    assert not [r for r in quotes if r["period"] == "full" and r["market"] in ("h2h", "totals", "spreads")
                and r["team_scope"] == "game"]                     # full-game 2-way rows stay with game_line_quotes


def test_three_way_ml_is_never_merged_with_two_way():
    rows = [
        row("pwin3|rt|LAC", "pwin3|rt", "LAC wins", None, [q("fd", 300, -400)], rung_team="LAC"),
        row("pwin3|rt|draw", "pwin3|rt", "Draw", None, [q("fd", 260, -320)]),
        row("pwin3|rt|GSW", "pwin3|rt", "GSW wins", None, [q("fd", -150, 130)], rung_team="GSW"),
        row("ml|1h|LAC", "ml|1h", "LAC wins", 0, [q("fd", 120, -140)], rung_team="LAC"),
    ]
    quotes = bgl.derivative_quotes(rows)
    three = [r for r in quotes if r["n_outcomes"] == 3]
    assert len(three) == 1
    assert (three[0]["price_a"], three[0]["price_draw"], three[0]["price_b"]) == (300, 260, -150)
    assert three[0]["market"] == "h2h" and three[0]["raw_family"] == f"{GAME}|pwin3|rt"
    two = [r for r in quotes if r["n_outcomes"] == 2]
    assert len(two) == 1 and two[0]["price_draw"] is None and two[0]["period"] == "h1"


def test_exchange_placeholders_dropped_and_dfs_skipped_and_unknown_counted():
    rows = [
        row("total|1h|110.5", "total|1h", "Over 110.5", 110.5,
            [{"venue": "novig", "yes": 1.0, "no": 99.0, "yesNative": "1c", "noNative": "99c"}, q("pp", -110, -110)],
            section="periods:1h"),
        row("winsby|LAC|10", "winsby|LAC", "LAC wins by 10+", None, [q("fd", 500, -700)], rung_team="LAC"),
        row("race|50|LAC", "race|LAC", "Race to 50", None, [q("fd", -110, -110)], rung_team="LAC"),
    ]
    stats: dict = {}
    quotes = bgl.derivative_quotes(rows, stats)
    assert quotes == []                                            # placeholder exchange sides + DFS venue yield nothing
    assert stats["unknown_family"] == 2                            # wins-by + race families: skipped and COUNTED, never guessed
    assert stats.get("derivative_rows", 0) == 1


# ---- crawl capture + raw-first archive -------------------------------------------------------------------------------------

def _slate():
    return [{"key": GAME, "sport": "nba", "away": "LAC", "home": "GSW", "start": START.replace("+00:00", "Z")}]


def test_crawl_captures_raw_rows_before_parsing_and_banks_them():
    dg: list = []
    events, counts = bbe.build_events_from_snapshots("nba", _slate(), {GAME: {"rows": deriv_rows()}}, NOW, derivative_games=dg)
    assert counts["derivative_games"] == 1 and counts["derivative_quotes"] > 0 and counts["derivative_unknown_family"] == 0
    assert dg[0]["raw_rows"], "the raw lines section must be captured even though parsing also ran"
    assert {r["family"] for r in dg[0]["raw_rows"]} >= {f"{GAME}|total|1h", f"{GAME}|teamtotal|GSW"}

    result = {"sport": "nba", "fetched_at": NOW.isoformat(), "window": {"days": 7}, "events": events,
              "game_lines": [], "derivative_games": dg, "counts": counts}
    written: dict[str, bytes] = {}
    order: list[str] = []
    def fake_write(data, key):
        written[key] = data
        order.append(key)
    with patch("mlb_core.storage.write_bytes", side_effect=fake_write):
        pointer = bbe.write_derivative_archive(result)
    raw_key = "OddsAccum/basketball_nba/raw/2026-11-02/derivatives_1300.json"
    csv_key = "OddsAccum/basketball_nba/2026-11-02/derivatives_1300.csv"
    assert order == [raw_key, csv_key, "OddsAccum/basketball_nba/latest_derivatives.json"]   # raw FIRST
    raw = json.loads(written[raw_key])
    assert raw["games"][0]["rows"] == dg[0]["raw_rows"]                                       # unmodified raw rows
    csv_text = written[csv_key].decode()
    assert "team_totals" in csv_text and "cumulative" in csv_text and f"{GAME}|total|1h" in csv_text
    assert pointer["raw_key"] == raw_key and pointer["quotes"] == counts["derivative_quotes"]


def test_archive_is_written_even_when_parsing_yields_no_derivative_rows():
    rows = [row("winsby|LAC|10", "winsby|LAC", "LAC wins by 10+", None, [q("fd", 500, -700)], rung_team="LAC")]
    dg: list = []
    bbe.build_events_from_snapshots("nba", _slate(), {GAME: {"rows": rows}}, NOW, derivative_games=dg)
    assert dg[0]["quotes"] == [] and dg[0]["unknown_family"] == 1 and dg[0]["raw_rows"] == rows
    result = {"sport": "nba", "fetched_at": NOW.isoformat(), "events": [], "game_lines": [], "derivative_games": dg, "counts": {}}
    written: dict[str, bytes] = {}
    with patch("mlb_core.storage.write_bytes", side_effect=lambda b, k: written.__setitem__(k, b)):
        pointer = bbe.write_derivative_archive(result)
    assert pointer["quotes"] == 0
    assert json.loads(written[pointer["raw_key"]])["games"][0]["rows"] == rows               # raw banked despite zero rows parsed
    assert written[pointer["csv_key"]].decode().count("\n") == 1                              # header only


def test_a_derivative_parser_failure_keeps_the_raw_rows_and_the_crawl(monkeypatch):
    dg: list = []
    monkeypatch.setattr(bbe.bgl, "derivative_quotes", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    events, counts = bbe.build_events_from_snapshots("nba", _slate(), {GAME: {"rows": deriv_rows()}}, NOW, derivative_games=dg)
    assert dg[0]["raw_rows"] and dg[0]["quotes"] == [] and counts["derivative_quotes"] == 0


def test_write_latest_archives_before_the_latest_pointer_and_hides_raw_from_props_file():
    dg: list = []
    bbe.build_events_from_snapshots("nba", _slate(), {GAME: {"rows": deriv_rows()}}, NOW, derivative_games=dg)
    result = {"sport": "nba", "fetched_at": NOW.isoformat(), "window": {"days": 7}, "events": [], "game_lines": [],
              "derivative_games": dg, "counts": {}}
    order: list[str] = []
    written: dict[str, bytes] = {}
    def fake_write(data, key):
        written[key] = data
        order.append(key)
    with patch("mlb_core.storage.write_bytes", side_effect=fake_write):
        bbe.write_latest(result)
    assert order[0].endswith("derivatives_1300.json") and order[-1] == "Odds/bestbook_live/nba/latest.json"
    assert "derivative_games" not in json.loads(written["Odds/bestbook_live/nba/latest.json"])


def test_snapshot_class_bands():
    assert bbe.snapshot_class(3) == "close"
    assert bbe.snapshot_class(15) == "t15"
    assert bbe.snapshot_class(60) == "t60"
    assert bbe.snapshot_class(300) == "intra"
    assert bbe.snapshot_class(-10) == "intra"
    assert bbe.snapshot_class(None) == "intra"
    rows = bbe.derivative_archive_rows(
        [{"bb_key": GAME, "commence_time": START, "quotes": [
            {"market": "totals", "period": "h1", "book": "fanduel"}]}], "nba", NOW.isoformat())
    assert rows[0]["mins_to_kickoff"] == 300.0 and rows[0]["sport"] == "basketball_nba" and rows[0]["event_id"] == GAME
