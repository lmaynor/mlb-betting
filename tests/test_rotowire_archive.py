"""nba.data.rotowire_archive + scripts/pull_rotowire_prose: offline tests only.

Fake sessions and hand-built synthetic fixtures (tests/fixtures/rotowire_*): ZERO live fetches were
used to build this suite. Fixture blurb texts are the short factual items quoted in the 2026-11-11
verification check for Kawhi Leonard's Jan 2025 return window, including the contradictory Jan 3 pair
the real archive carries raw -- both must survive parsing, in served order.
"""
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from nba.data import rotowire_archive as rw

FIXTURES = Path(__file__).parent / "fixtures"
KAWHI = rw.PlayerRef(player="Kawhi Leonard", rotowire_id=3195, slug="kawhi-leonard")
FETCHED = "2026-11-11T12:00:00+00:00"


def _page_html() -> str:
    return (FIXTURES / "rotowire_player_page.html").read_text()


def _ajax(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeResponse:
    def __init__(self, text="", payload=None, status_code=200):
        self.text = text
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Serves the player page, then the queued AJAX payloads in order. Records every call."""

    def __init__(self, ajax_payloads=()):
        self.calls = []
        self._ajax = list(ajax_payloads)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if url == KAWHI.page_url:
            return FakeResponse(text=_page_html())
        if url == rw.AJAX_URL:
            payload = self._ajax.pop(0)
            return FakeResponse(text=json.dumps(payload), payload=payload)
        if url == rw.RSS_URL:
            return FakeResponse(text=(FIXTURES / "rotowire_rss.xml").read_text())
        raise AssertionError(f"unexpected URL {url}")


class FakeStore:
    def __init__(self):
        self.writes = {}

    def write_bytes(self, data, key):
        self.writes[key] = data

    def exists(self, key):
        return key in self.writes

    def read_bytes(self, key):
        return self.writes[key]


# ---------------------------------------------------------------------------
# (a) fragment parser
# ---------------------------------------------------------------------------

def test_parse_player_page_fragment_fields_and_order():
    records = rw.parse_updates_html(_page_html(), KAWHI, "page", FETCHED)
    assert len(records) == 3
    first = records[0]
    assert first.headline == "Remains on minutes limit Monday"
    assert first.published_date == "2025-01-13"
    assert first.published_ts == 1736773200.0          # cursor-precise ts kept alongside the displayed date
    assert first.injury_tag == "Knee"
    assert "minutes restriction" in first.body
    assert first.player == "Kawhi Leonard" and first.rotowire_id == 3195
    assert first.source == "page" and first.fetched_at == FETCHED
    # the gated ANALYSIS upsell is not news prose and must not leak into any body
    assert all("Subscribe now" not in r.body for r in records)
    assert records[1].injury_tag is None


def test_parse_retains_contradictory_pair_in_served_order():
    records = rw.parse_updates_html(_page_html(), KAWHI, "page", FETCHED)
    pair = [r.headline for r in records if r.published_date == "2025-01-03"]
    # superseded / wrong updates are retained raw, never reconciled away
    assert pair == ["Questionable, but likely to play Saturday", "Ruled out for Saturday"]


def test_parse_ajax_fragment_same_shape():
    payload = _ajax("rotowire_ajax_page1.json")
    records = rw.parse_updates_html(payload["updatesHTML"], KAWHI, "ajax", FETCHED)
    assert [r.headline for r in records] == ["Questionable, but likely to play Saturday", "Ruled out for Saturday"]
    assert all(r.source == "ajax" for r in records)
    assert records[1].injury_tag == "Knee"


# ---------------------------------------------------------------------------
# (b) cursor paging + (h) UA header and pacing
# ---------------------------------------------------------------------------

def test_cursor_paging_threads_cursor_and_stops_at_no_more():
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json")])
    pull = rw.fetch_player_archive(session, KAWHI, sleeper=lambda s: None)
    assert pull["stopped"] == "no_more_updates"
    assert pull["requests"] == 3                       # player page + 2 AJAX pages
    ajax_calls = [c for c in session.calls if c["url"] == rw.AJAX_URL]
    assert "lastUpdateTime" not in ajax_calls[0]["params"]
    assert ajax_calls[1]["params"]["lastUpdateTime"] == 1735862400   # page 1's newLastUpdateTime threaded
    assert ajax_calls[0]["params"]["numUpdates"] == 100
    headlines = [r.headline for r in pull["records"]]
    assert "Assigned to G League" in headlines        # oldest page reached


def test_cursor_paging_stops_when_cursor_passes_oldest_window():
    old_page = {"updatesHTML": _ajax("rotowire_ajax_page2.json")["updatesHTML"],
                "newLastUpdateTime": 1734652800,      # Dec 20, 2024
                "noMoreUpdates": False}
    session = FakeSession([old_page, _ajax("rotowire_ajax_page2.json")])
    pull = rw.fetch_player_archive(session, KAWHI, oldest_date=date(2025, 1, 1), sleeper=lambda s: None)
    assert pull["stopped"] == "passed_oldest_window"
    assert pull["requests"] == 2                       # page + 1 AJAX; the second AJAX is never requested


def test_user_agent_header_on_every_request():
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json")])
    rw.fetch_player_archive(session, KAWHI, sleeper=lambda s: None)
    assert session.calls
    for call in session.calls:
        # Generic desktop browser UA only; no identifying headers of any kind.
        assert call["headers"] == {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            )
        }


def test_pacing_sleeps_at_least_configured_delay_between_requests():
    waits = []
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json")])
    rw.fetch_player_archive(session, KAWHI, delay=3.0, sleeper=waits.append)
    assert len(waits) == 2                             # between the 3 requests, never before the first
    assert all(w >= 3.0 for w in waits)


# ---------------------------------------------------------------------------
# (c) dedupe
# ---------------------------------------------------------------------------

def test_dedupe_refetch_adds_zero_new_records():
    once = rw.parse_updates_html(_page_html(), KAWHI, "page", FETCHED)
    twice = rw.dedupe_records(once + rw.parse_updates_html(_page_html(), KAWHI, "page", FETCHED))
    assert len(twice) == len(once) == 3
    # a different headline at the same ts is a different update, not a dupe
    other = rw.BlurbRecord(player="Kawhi Leonard", rotowire_id=3195, published_date="2025-01-13",
                           published_ts=1736773200.0, headline="Different headline", body="x",
                           injury_tag=None, source="page", fetched_at=FETCHED)
    assert len(rw.dedupe_records(once + [other])) == 4


# ---------------------------------------------------------------------------
# (d) window slicing
# ---------------------------------------------------------------------------

def _rec(day, headline):
    d = date.fromisoformat(day)
    return rw.BlurbRecord(player="Kawhi Leonard", rotowire_id=3195, published_date=day,
                          published_ts=datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp(),
                          headline=headline, body="x", injury_tag=None, source="page", fetched_at=FETCHED)


def test_window_slicing_bounds_are_inclusive():
    records = [_rec("2024-12-24", "too early"), _rec("2024-12-25", "return minus 10d"),
               _rec("2025-01-03", "mid window"), _rec("2025-01-04", "return game day"),
               _rec("2025-01-05", "after return")]
    window = rw.slice_window(records, "2025-01-04")
    assert [r.headline for r in window] == ["return minus 10d", "mid window", "return game day"]


# ---------------------------------------------------------------------------
# (e) RSS parsing
# ---------------------------------------------------------------------------

def test_rss_items_map_to_shared_record_shape():
    records = rw.parse_rss((FIXTURES / "rotowire_rss.xml").read_text(), FETCHED)
    assert len(records) == 2
    k = records[0]
    assert k.player == "Kawhi Leonard"
    assert k.headline == "Remains on minutes limit Monday"
    assert k.rotowire_id == 3195                       # recovered from the player-page link pattern
    assert k.published_date == "2025-01-13"
    assert k.published_ts == datetime(2025, 1, 13, 18, 30, tzinfo=timezone.utc).timestamp()
    assert "minutes restriction" in k.body
    assert k.source == "rss" and k.guid == "rotowire-nba-20250113-3195-1"
    assert records[1].player == "Joel Embiid" and records[1].rotowire_id == 3572


# ---------------------------------------------------------------------------
# (f) case-set builder
# ---------------------------------------------------------------------------

def _box_frame():
    rows = []

    def add(pid, name, team, dates, minutes, dnp_from=None, reason=""):
        for i, d in enumerate(dates):
            dnp = dnp_from is not None and i >= dnp_from[0] and i < dnp_from[1]
            rows.append({"athlete_id": pid, "athlete_display_name": name, "team_abbreviation": team,
                         "game_date": d, "game_id": pid * 1000 + i, "season": 2025,
                         "minutes": 0.0 if dnp else minutes[i] if isinstance(minutes, list) else minutes,
                         "did_not_play": bool(dnp), "reason": reason if dnp else ""})

    days = [f"2025-01-{d:02d}" for d in (1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25)]
    # Star: 8 played @30, 4 missed (knee), returns @19 -> a default-population case
    add(1, "Star Player", "LAC", days, [30] * 8 + [0] * 4 + [19], dnp_from=(8, 12), reason="Left Knee Sprain")
    # Fringe: baseline 12 < 18 -> excluded at default
    add(2, "Fringe Player", "LAC", days, [12] * 8 + [0] * 4 + [10], dnp_from=(8, 12), reason="Ankle")
    # Short spell: 2 missed -> excluded at default, included wide
    add(3, "Short Spell", "LAC", days, [28] * 8 + [0] * 2 + [25] * 3, dnp_from=(8, 10), reason="Calf")
    # Coach's-decision spell: not an injury return -> excluded at both widths
    add(4, "Healthy Scratch", "LAC", days, [30] * 8 + [0] * 4 + [30], dnp_from=(8, 12), reason="Coach's Decision")
    return pd.DataFrame(rows)


def test_case_set_builder_default_population():
    cases = rw.build_case_set(_box_frame())
    assert [c.player for c in cases] == ["Star Player"]
    c = cases[0]
    assert c.missed == 4 and c.baseline_minutes == 30.0 and c.return_minutes == 19.0
    assert c.return_date == "2025-01-25" and c.spell_start == "2025-01-17"
    assert c.window_start == "2025-01-15"              # return - 10 days
    assert rw.distinct_players(cases) == ["Star Player"]


def test_case_set_builder_wide_population():
    cases = rw.build_case_set(_box_frame(), min_missed=rw.WIDE_MIN_MISSED, min_baseline=rw.WIDE_MIN_BASELINE)
    assert sorted(c.player for c in cases) == ["Short Spell", "Star Player"]


# ---------------------------------------------------------------------------
# (g) disabled by default (module + runner)
# ---------------------------------------------------------------------------

def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "pull_rotowire_prose", Path(__file__).parents[1] / "scripts" / "pull_rotowire_prose.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_historic_pull_disabled_makes_no_requests_and_no_writes():
    session = FakeSession([_ajax("rotowire_ajax_page1.json")])
    store = FakeStore()
    cases = rw.build_case_set(_box_frame())
    res = rw.run_historic_pull([KAWHI], cases, session=session, execute=False, env={}, store=store)
    assert res["status"] == "disabled"
    assert session.calls == [] and store.writes == {}
    assert res["plan"]["players"] == 1 and res["plan"]["estimated_requests"] == 3


def test_run_historic_pull_execute_without_env_flag_still_refuses():
    session = FakeSession([_ajax("rotowire_ajax_page1.json")])
    store = FakeStore()
    res = rw.run_historic_pull([KAWHI], [], session=session, execute=True, env={}, store=store)
    assert res["status"] == "disabled"
    assert session.calls == [] and store.writes == {}


def test_runner_no_execute_is_a_zero_call_dry_run(capsys):
    runner = _runner_module()
    session, store = FakeSession(), FakeStore()
    rc = runner.main([], session=session, env={}, store=store)
    assert rc == 0
    assert session.calls == [] and store.writes == {}
    assert json.loads(capsys.readouterr().out)["status"] == "disabled"


def test_runner_execute_without_env_flag_refuses(capsys):
    runner = _runner_module()
    session, store = FakeSession(), FakeStore()
    rc = runner.main(["--execute"], session=session, env={}, store=store)
    assert rc == 2
    assert session.calls == [] and store.writes == {}


def test_runner_rss_without_execute_is_disabled(capsys):
    runner = _runner_module()
    session, store = FakeSession(), FakeStore()
    rc = runner.main(["--rss"], session=session, env={}, store=store)
    assert rc == 0
    assert session.calls == [] and store.writes == {}


# ---------------------------------------------------------------------------
# Enabled path (fake session + fake store only): banking layout + sorted corpus
# ---------------------------------------------------------------------------

def test_enabled_pull_banks_raw_then_parsed_sorted():
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json")])
    store = FakeStore()
    cases = rw.build_case_set(_box_frame())
    star = rw.PlayerRef(player="Star Player", rotowire_id=3195, slug="kawhi-leonard")
    res = rw.run_historic_pull([star], cases, session=session, execute=True,
                               env={rw.ENABLE_ENV: "1"}, store=store, sleeper=lambda s: None)
    assert res["status"] == "done"
    keys = set(store.writes)
    assert f"{rw.PREFIX}/raw/3195/page.html" in keys
    assert f"{rw.PREFIX}/raw/3195/ajax_0001.json" in keys
    assert f"{rw.PREFIX}/parsed/3195.jsonl" in keys
    corpus = [json.loads(line) for line in store.writes[f"{rw.PREFIX}/parsed/3195.jsonl"].decode().splitlines()]
    ts = [r["published_ts"] for r in corpus]
    assert ts == sorted(ts)                            # parsed output sorted by timestamp
    assert {r["source"] for r in corpus} <= {"page", "ajax"}


def test_enabled_rss_snapshot_banks_dated_files():
    session = FakeSession()
    store = FakeStore()
    res = rw.run_rss_snapshot(session=session, execute=True, env={rw.ENABLE_ENV: "1"}, store=store,
                              now=datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc))
    assert res["status"] == "done" and res["records"] == 2
    assert any(k.endswith(".xml") and "/rss/2026-11-11/" in k for k in store.writes)


# ---------------------------------------------------------------------------
# Player map / resolver helpers
# ---------------------------------------------------------------------------

def test_player_map_and_url_helpers():
    players = rw.load_player_map([{"player": "Joel Embiid", "rotowire_id": "3572"},
                                  {"player": "No Id", "rotowire_id": ""}])
    assert players == [rw.PlayerRef("Joel Embiid", 3572, "joel-embiid")]
    assert rw.parse_player_url("https://www.rotowire.com/basketball/player/kawhi-leonard-3195") == ("kawhi-leonard", 3195)
    assert rw.slugify("Paolo Banchero") == "paolo-banchero"


def test_resolver_uses_injectable_session_and_player_url_pattern():
    class SearchSession:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append(url)
            return FakeResponse(text='<a href="/basketball/player/paolo-banchero-5700">Paolo Banchero</a>')

    s = SearchSession()
    ref = rw.resolve_player(s, "Paolo Banchero")
    assert ref == rw.PlayerRef("Paolo Banchero", 5700, "paolo-banchero")
    assert s.calls == [rw.SEARCH_URL]
