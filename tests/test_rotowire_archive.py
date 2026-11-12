"""nba.data.rotowire_archive + scripts/pull_rotowire_prose: offline tests only.

ZERO live fetches at test time: fake sessions serve fixture files. Since the 2026-11-11
fetcher/parser fix, the page/AJAX fixtures are VERBATIM REAL CAPTURES, not hand-built HTML
(the hand-built set hid both live defects: it had no void elements and no page cursor):
  * rotowire_player_page.html -- a byte-verbatim slice of the banked Banchero player page
    (NBA/news/rotowire_archive/raw/5700/page.html): the 5 archive blocks, the "Read More"
    button carrying data-last-update-time="2026-10-21 19:24:13.360", and the 1 rumors block.
  * rotowire_ajax_page1.json / rotowire_ajax_page2.json -- the probe-5 payloads: two real
    cursor-threaded archive responses, 100 updates each.
  * rotowire_ajax_error.json -- the server's genuine 49-byte rejection of a cursorless call.
Superseded / contradictory updates surviving banking raw is gated live on the banked corpus
(Kawhi Leonard's Jan 2025 window), and in this suite by the dedupe test below.
"""
import importlib.util
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from nba.data import rotowire_archive as rw

FIXTURES = Path(__file__).parent / "fixtures"
KAWHI = rw.PlayerRef(player="Kawhi Leonard", rotowire_id=3195, slug="kawhi-leonard")
BANCHERO = rw.PlayerRef(player="Paolo Banchero", rotowire_id=5700, slug="paolo-banchero")
PAGE_CURSOR = "2026-10-21 19:24:13.360"  # the fixture page's data-last-update-time, verbatim
FETCHED = "2026-11-11T12:00:00+00:00"


def _page_html() -> str:
    return (FIXTURES / "rotowire_player_page.html").read_text()


def _ajax(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _terminal_page() -> dict:
    """A minimal end-of-archive control payload (no updatesHTML of its own): the documented
    genuine end condition, noMoreUpdates, echoing page 2's cursor."""
    return {"updatesHTML": "", "newLastUpdateTime": "2024-02-14 08:48:03.647",
            "noMoreUpdates": True}


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
        if url in (BANCHERO.page_url, KAWHI.page_url):
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

def test_parse_real_page_blocks_are_distinct_records():
    # The genuine page slice carries 6 news-update blocks (5 archive + 1 rumors). With the
    # shipped depth counting, the blocks' void <img> elements nested them all into ONE
    # concatenated record; each block must now parse as its own record, in served order.
    records = rw.parse_updates_html(_page_html(), BANCHERO, "page", FETCHED)
    assert [r.headline for r in records] == [
        "Exits Friday's loss early", "Doubtful to return Friday", "Returns to Friday's game",
        "Goes to locker room Friday", "Dominant performance in loss Wednesday",
        "Not ready for contact"]
    assert [r.published_date for r in records] == [
        "2026-10-23", "2026-10-23", "2026-10-23", "2026-10-23", "2026-10-21", "2025-12-01"]
    first = records[0]
    assert first.injury_tag == "Ankle"                 # the __inj segment, not body prose
    assert first.body.startswith("Banchero didn't check back in")
    assert records[2].injury_tag is None
    assert first.player == "Paolo Banchero" and first.rotowire_id == 5700
    assert first.source == "page" and first.fetched_at == FETCHED
    # Real markup carries no per-blurb timestamp attribute: ts falls back to the displayed
    # date at 00:00 UTC so dedupe keys stay stable.
    assert first.published_ts == datetime(2026, 10, 23, tzinfo=timezone.utc).timestamp()
    # Boundaries: no record's body may contain another record's headline (concatenation),
    # container chrome (position / team) is not prose, and the gated ANALYSIS upsell
    # ("Subscribe now...") must not leak into any body.
    for r in records:
        assert "Subscribe now" not in r.body
        assert not r.body.startswith(("F ", "Orlando Magic"))
        for other in records:
            if other is not r:
                assert other.headline not in r.body


def test_parse_real_ajax_fragment_100_distinct_records():
    payload = _ajax("rotowire_ajax_page1.json")
    records = rw.parse_updates_html(payload["updatesHTML"], BANCHERO, "ajax", FETCHED)
    assert len(records) == 100                         # numUpdates=100, every block its own record
    assert all(r.source == "ajax" for r in records)
    assert records[0].headline == "Another 24-point effort"
    assert records[0].published_date == "2026-10-16"
    assert records[-1].headline == "Lifts Magic to victory"
    assert records[-1].published_date == "2025-04-25"
    dates = [r.published_date for r in records]
    assert dates == sorted(dates, reverse=True)        # served order is newest first, preserved
    # repeated headlines at different dates are distinct updates, all retained (97 distinct
    # of 100): parsing never reconciles or drops a superseded update
    assert len({r.headline for r in records}) == 97
    assert all("Subscribe now" not in r.body for r in records)


# ---------------------------------------------------------------------------
# (b) cursor paging + (h) UA header and pacing
# ---------------------------------------------------------------------------

def test_cursor_seeded_from_page_attribute_and_threaded():
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json"),
                           _terminal_page()])
    pull = rw.fetch_player_archive(session, BANCHERO, sleeper=lambda s: None)
    assert pull["stopped"] == "no_more_updates"
    assert pull["requests"] == 4                       # player page + 3 AJAX pages
    ajax_calls = [c for c in session.calls if c["url"] == rw.AJAX_URL]
    # the first call is seeded verbatim from the page's data-last-update-time attribute --
    # a cursorless call is what the live server rejects (rotowire_ajax_error.json)
    assert ajax_calls[0]["params"]["lastUpdateTime"] == PAGE_CURSOR
    assert ajax_calls[0]["params"]["numUpdates"] == 100
    # each response's newLastUpdateTime (a datetime STRING on the live route) threads verbatim
    assert ajax_calls[1]["params"]["lastUpdateTime"] == "2025-04-25 22:57:42.090"
    assert ajax_calls[2]["params"]["lastUpdateTime"] == "2024-02-14 08:48:03.647"
    assert len(pull["records"]) == 206                 # 6 page + 100 + 100, deduped
    headlines = [r.headline for r in pull["records"]]
    assert "Lifts Magic to victory" in headlines       # oldest real page reached


def test_cursor_seed_reads_the_page_attribute():
    assert rw._page_cursor(_page_html()) == PAGE_CURSOR
    assert rw._page_cursor("<html>no button here</html>") is None


def test_cursor_paging_stops_when_cursor_passes_oldest_window():
    # real string cursors: page 1 lands 2025-04-25 (inside), page 2 lands 2024-02-14 (past)
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json"),
                           _ajax("rotowire_ajax_page2.json")])
    pull = rw.fetch_player_archive(session, BANCHERO, oldest_date=date(2024, 6, 1),
                                   sleeper=lambda s: None)
    assert pull["stopped"] == "passed_oldest_window"
    assert pull["requests"] == 3                       # page + 2 AJAX; the third is never requested


def test_cursor_paging_oldest_window_with_epoch_cursor():
    old_page = {"updatesHTML": "", "newLastUpdateTime": 1734652800,   # Dec 20, 2024, epoch form
                "noMoreUpdates": False}
    session = FakeSession([old_page, old_page])
    pull = rw.fetch_player_archive(session, BANCHERO, oldest_date=date(2025, 1, 1),
                                   sleeper=lambda s: None)
    assert pull["stopped"] == "passed_oldest_window"
    assert pull["requests"] == 2                       # page + 1 AJAX


def test_error_payload_raises_never_reads_as_end_of_archive():
    # the genuine server rejection of a cursorless call: a failed pass, loudly raised --
    # the shipped code banked it as a clean "no_more_updates" finish with a truncated corpus
    session = FakeSession([_ajax("rotowire_ajax_error.json")])
    with pytest.raises(RuntimeError, match="error payload"):
        rw.fetch_player_archive(session, BANCHERO, sleeper=lambda s: None)


def test_payload_missing_expected_keys_raises():
    session = FakeSession([{"success": True}])
    with pytest.raises(RuntimeError, match="error payload"):
        rw.fetch_player_archive(session, BANCHERO, sleeper=lambda s: None)


def test_user_agent_header_on_every_request():
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json"),
                           _terminal_page()])
    rw.fetch_player_archive(session, BANCHERO, sleeper=lambda s: None)
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
    session = FakeSession([_ajax("rotowire_ajax_page1.json"), _ajax("rotowire_ajax_page2.json"),
                           _terminal_page()])
    rw.fetch_player_archive(session, BANCHERO, delay=3.0, sleeper=waits.append)
    assert len(waits) == 3                             # between the 4 requests, never before the first
    assert all(w >= 3.0 for w in waits)


# ---------------------------------------------------------------------------
# (c) dedupe
# ---------------------------------------------------------------------------

def test_dedupe_refetch_adds_zero_new_records():
    once = rw.parse_updates_html(_page_html(), BANCHERO, "page", FETCHED)
    twice = rw.dedupe_records(once + rw.parse_updates_html(_page_html(), BANCHERO, "page", FETCHED))
    assert len(twice) == len(once) == 6
    # A different headline at the same ts is a different update, not a dupe: this is the
    # contradictory-pair rule (Kawhi's Jan 3, 2025 pair -- "Questionable, but likely to play
    # Saturday" vs "Ruled out for Saturday") -- superseded updates always survive dedupe.
    ts = datetime(2025, 1, 3, tzinfo=timezone.utc).timestamp()
    pair = [rw.BlurbRecord(player="Kawhi Leonard", rotowire_id=3195, published_date="2025-01-03",
                           published_ts=ts, headline=h, body="x", injury_tag=None,
                           source="page", fetched_at=FETCHED)
            for h in ("Questionable, but likely to play Saturday", "Ruled out for Saturday")]
    assert len(rw.dedupe_records(pair)) == 2


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
                         "game_date": d, "game_id": 401000 + i, "season": 2025,
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


def test_case_set_reproduces_validated_population_on_banked_boxscores():
    # The design population was validated 2026-11-11 against the banked hoopR frame by an
    # independent spike script (evidence dir, cases_gap.json): the module builder must
    # reproduce it exactly. Skips where the banked frame / a parquet engine is unavailable
    # (the evidence frame lives outside the repo).
    try:
        import pyarrow  # noqa: F401
    except Exception:
        pytest.skip("no working parquet engine in this environment")
    ev = Path(os.environ.get("ROTOWIRE_EVIDENCE_DIR",
                             "/home/hatch/workspace/beezy/rotowire-fix-evidence"))
    parquet = ev / "boxscores" / "player_boxscores_2022_2026.parquet"
    if not parquet.exists():
        pytest.skip("banked box-score frame not present")
    cases = rw.build_case_set(pd.read_parquet(parquet))
    assert len(cases) == 1318
    assert len(rw.distinct_players(cases)) == 417
    seeds = {n: sorted(c.return_date for c in cases if c.player == n)
             for n in ("Joel Embiid", "Kawhi Leonard", "Paolo Banchero")}
    assert seeds["Kawhi Leonard"] == ["2024-01-01", "2024-04-23", "2025-01-04", "2025-11-23"]
    assert seeds["Paolo Banchero"] == ["2022-11-25", "2025-01-10", "2025-12-05"]
    assert len(seeds["Joel Embiid"]) == 12 and "2024-04-02" in seeds["Joel Embiid"]
    assert sum(1 for c in cases if not c.reasons) == 876   # UNLISTED absences are the largest class


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
    star = rw.PlayerRef(player="Star Player", rotowire_id=5700, slug="paolo-banchero")
    res = rw.run_historic_pull([star], cases, session=session, execute=True,
                               env={rw.ENABLE_ENV: "1"}, store=store, sleeper=lambda s: None)
    assert res["status"] == "done"
    # Star's window starts 2025-01-15: page 1's cursor (2025-04-25) is inside the walk,
    # page 2's (2024-02-14) passes it -- the walk stops there, having banked both pages.
    assert res["players"][0]["stopped"] == "passed_oldest_window"
    assert res["players"][0]["records"] == 206
    keys = set(store.writes)
    assert f"{rw.PREFIX}/raw/5700/page.html" in keys
    assert f"{rw.PREFIX}/raw/5700/ajax_0001.json" in keys
    assert f"{rw.PREFIX}/raw/5700/ajax_0002.json" in keys
    assert f"{rw.PREFIX}/parsed/5700.jsonl" in keys
    corpus = [json.loads(line) for line in store.writes[f"{rw.PREFIX}/parsed/5700.jsonl"].decode().splitlines()]
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


class TypeaheadSession:
    """Serves one canned typeahead payload (shape = the live responses probed 2026-11-11)."""

    def __init__(self, payload, status_code=200):
        self.calls = []
        self._payload = payload
        self._status = status_code

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return FakeResponse(text=json.dumps(self._payload), payload=self._payload,
                            status_code=self._status)


def _ta_entry(name, rid, slug=None, team="PHI"):
    return {"rotoPlayerID": str(rid), "link": f"/basketball/player/{slug or rw.slugify(name)}-{rid}",
            "logo": "", "alt": team, "name": name, "span": "C", "text": team}


def test_resolver_typeahead_exact_match_and_request_shape():
    s = TypeaheadSession({"success": True, "players": [_ta_entry("Paolo Banchero", 5700, team="ORL")]})
    ref = rw.resolve_player(s, "Paolo Banchero")
    assert ref == rw.PlayerRef("Paolo Banchero", 5700, "paolo-banchero")
    assert len(s.calls) == 1
    call = s.calls[0]
    assert call["url"] == rw.TYPEAHEAD_URL
    assert call["params"] == {"searchTerm": "Paolo Banchero", "sport": "NBA"}
    assert call["headers"] == rw.HEADERS  # anonymized posture, unchanged by the rework


def test_resolver_picks_exact_slug_out_of_several_results():
    payload = {"success": True, "players": [
        _ta_entry("Jaylen Brown", 3850, team="BOS"), _ta_entry("Jaylen Wells", 7801, team="MEM"),
        _ta_entry("Jalen Brunson", 4155, team="NYK")]}
    s = TypeaheadSession(payload)
    assert rw.resolve_player(s, "Jaylen Brown") == rw.PlayerRef("Jaylen Brown", 3850, "jaylen-brown")
    assert rw.resolve_player(s, "Jaylen Martin") is None  # absent name never falls back to a near hit


def test_resolver_punctuation_slug_matches_live_shape():
    # Live probe 2026-11-11: "P.J. Tucker" -> link slug "pj-tucker" (punctuation dropped,
    # not hyphenated), id 2708. The fixture carries the live slug verbatim.
    entry = _ta_entry("P.J. Tucker", 2708, slug="pj-tucker", team="NYK")
    s = TypeaheadSession({"success": True, "players": [entry]})
    assert rw.resolve_player(s, "P.J. Tucker") == rw.PlayerRef("P.J. Tucker", 2708, "pj-tucker")


def test_resolver_empty_result_and_ambiguity_return_none():
    assert rw.resolve_player(TypeaheadSession({"success": True, "players": []}), "Nobody Here") is None
    dup = {"success": True, "players": [_ta_entry("Chris Smith", 111), _ta_entry("Chris Smith", 222)]}
    assert rw.resolve_player(TypeaheadSession(dup), "Chris Smith") is None


def test_resolver_id_field_disagreeing_with_link_is_not_a_match():
    bad = _ta_entry("Joel Embiid", 3572)
    bad["rotoPlayerID"] = "9999"
    s = TypeaheadSession({"success": True, "players": [bad]})
    assert rw.resolve_player(s, "Joel Embiid") is None


def test_resolver_non_200_fails_loudly():
    s = TypeaheadSession({"success": False}, status_code=429)
    with pytest.raises(RuntimeError):
        rw.resolve_player(s, "Joel Embiid")
