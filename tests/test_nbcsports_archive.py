"""nba.data.nbcsports_archive + scripts/pull_nbcsports_prose: offline tests only.

ZERO live fetches at test time: fake sessions serve fixture files. The fixtures are
VERBATIM REAL CAPTURES (the failure class this lane already hit twice with hand-built
HTML): byte-verbatim excerpts of live NBC Sports pages captured 2026-11-11 --
Melton pages 1, 2 and 30 and Embiid page 1, each holding its 10 real PlayerNewsPost
blocks. Expectations below are the captures' actual contents.
"""
import importlib.util
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from nba.data import nbcsports_archive as nb

FIXTURES = Path(__file__).parent / "fixtures"
MELTON = nb.PlayerRef(player="De'Anthony Melton", nbc_id=10744, slug="deanthony-melton")
EMBIID = nb.PlayerRef(player="Joel Embiid", nbc_id=10488, slug="joel-embiid")
FETCHED = "2026-11-11T12:00:00+00:00"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Serves queued pages for any news URL in order (page 1 first), then repeats the
    last one -- which is exactly the site's past-the-end clamp behavior. Records calls."""

    def __init__(self, pages):
        self.calls = []
        self._pages = list(pages)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        if len(self._pages) > 1:
            return FakeResponse(text=self._pages.pop(0))
        return FakeResponse(text=self._pages[0])


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
# (a) real-capture parsing: fields, subject vs mention, timestamps
# ---------------------------------------------------------------------------

def test_parse_melton_page1_fields_and_subject_marks():
    items = nb.parse_items_html(_fixture("nbcsports_melton_p1.html"), MELTON, FETCHED)
    assert len(items) == 10                            # the page's 10 real blocks
    first = items[0]
    assert first.is_subject and first.subject_nbc_id == 10744
    assert first.subject_player == "De'Anthony Melton"
    assert first.published_date == "2026-11-12"
    assert first.published_ts == datetime(2026, 11, 12, 5, 21, 14, 90000,
                                          tzinfo=timezone.utc).timestamp()
    assert first.item_type == "Recap" and first.author == "Raphielle Johnson"
    assert first.source_line is None                   # staff recaps carry no Source line
    assert "much to write home about" in first.body
    assert first.migration_stratum is False
    # a mention item: about a teammate, tagged onto Melton's page, kept verbatim
    mention = items[1]
    assert not mention.is_subject
    # NBC's own header names this block "Gary Payton" (no suffix) while its slug/id
    # carry "gary-payton-ii"/10479 -- the id is the identity; the name is display text.
    assert mention.subject_player == "Gary Payton" and mention.subject_nbc_id == 10479
    assert mention.subject_slug == "gary-payton-ii"
    assert mention.source_line == "NBA Injury Report" and mention.item_type == "Injury"
    assert "frees up additional minutes for De’Anthony Melton" in mention.body
    assert sum(1 for i in items if i.is_subject) == 5


def test_parse_melton_page2_subject_count_matches_verified_read():
    # Luc's live read of this exact page (2026-11-11): only 2 of 10 items are Melton's.
    items = nb.parse_items_html(_fixture("nbcsports_melton_p2.html"), MELTON, FETCHED)
    assert len(items) == 10
    assert sum(1 for i in items if i.is_subject) == 2
    sources = {i.source_line for i in items if i.source_line}
    assert "Shams Charania" in sources and "NBA Lineups" in sources


def test_parse_deep_page_and_seed_page():
    deep = nb.parse_items_html(_fixture("nbcsports_melton_p30.html"), MELTON, FETCHED)
    assert len(deep) == 10
    assert deep[0].published_date == "2022-11-13" and deep[-1].published_date == "2022-11-01"
    assert sum(1 for i in deep if i.is_subject) == 7
    embiid = nb.parse_items_html(_fixture("nbcsports_embiid_p1.html"), EMBIID, FETCHED)
    assert len(embiid) == 10 and all(i.is_subject for i in embiid)


def test_display_datetime_tz_parsing():
    # the site's rendered form: "Oct 21, 2026, 7:40 PM CDT" (Luc's verified read)
    assert nb.parse_display_datetime("Oct 21, 2026, 7:40 PM CDT") == \
        datetime(2026, 10, 22, 0, 40, tzinfo=timezone.utc).timestamp()
    assert nb.parse_display_datetime("Jan 4, 2025, 6:05 PM EST") == \
        datetime(2025, 1, 4, 23, 5, tzinfo=timezone.utc).timestamp()
    assert nb.parse_display_datetime("not a date") is None


# ---------------------------------------------------------------------------
# (b) fetcher termination: clamp, all-seen overlap, window stop, empty
# ---------------------------------------------------------------------------

def test_clamp_termination_on_repeated_page():
    # FakeSession repeats the last page forever (the site's clamp): page1, page2, page2...
    session = FakeSession([_fixture("nbcsports_melton_p1.html"),
                           _fixture("nbcsports_melton_p2.html")])
    pull = nb.fetch_player_archive(session, MELTON, sleeper=lambda s: None)
    assert pull["stopped"] == "clamped"
    assert pull["requests"] == 3                       # p1, p2, and the clamped repeat
    assert len(pull["raw_pages"]) == 2                 # the clamped page is NOT re-banked
    assert len(pull["records"]) == 20


def test_window_stop_pages_until_cutoff_passed():
    session = FakeSession([_fixture("nbcsports_melton_p1.html"),
                           _fixture("nbcsports_melton_p2.html"),
                           _fixture("nbcsports_melton_p30.html")])
    pull = nb.fetch_player_archive(session, MELTON, oldest_date=date(2026, 8, 1),
                                   sleeper=lambda s: None)
    # p1 items are Oct-Nov 2026; p2's oldest NEW item is 2026-07-01 < 2026-08-01 -> stop.
    assert pull["stopped"] == "passed_oldest_window"
    assert pull["requests"] == 2
    assert len(pull["records"]) == 20


def test_empty_first_page_raises():
    session = FakeSession(["<html><body>no blocks here</body></html>"])
    with pytest.raises(RuntimeError, match="no items"):
        nb.fetch_player_archive(session, MELTON, sleeper=lambda s: None)


def test_non_200_raises():
    class Boom:
        def get(self, url, params=None, headers=None, timeout=None):
            return FakeResponse(text="", status_code=403)
    with pytest.raises(RuntimeError, match="HTTP 403"):
        nb.fetch_player_archive(Boom(), MELTON, sleeper=lambda s: None)


def test_user_agent_header_on_every_request():
    session = FakeSession([_fixture("nbcsports_melton_p1.html")])
    nb.fetch_player_archive(session, MELTON, sleeper=lambda s: None)
    assert session.calls
    for call in session.calls:
        assert call["headers"] == {"User-Agent": nb.USER_AGENT}


# ---------------------------------------------------------------------------
# (c) streaming three-level dedupe
# ---------------------------------------------------------------------------

def _mini_block(subj_slug, subj_id, subj_name, ts_iso, headline, body="body text"):
    first, _, last = subj_name.partition(" ")
    return (f'<div class="PlayerNewsPost"><div class="PlayerNewsPost-name-container">'
            f'<a href="https://www.nbcsports.com/nba/{subj_slug}/{subj_id}">'
            f'<span class="PlayerNewsPost-firstName">{first}</span> '
            f'<span class="PlayerNewsPost-lastName">{last}</span></a></div>'
            f'<h3 class="PlayerNewsPost-headline">{headline}</h3>'
            f'<div class="PlayerNewsPost-analysis">{body}</div>'
            f'<div class="PlayerNewsPost-date" data-date="{ts_iso}"></div></div>')


def _page(*blocks):
    return "<html>" + "".join(blocks) + "</html>"


SHARED = _mini_block("teammate-one", 111, "Teammate One", "2026-10-05T00:00:00Z",
                     "Teammate One is out Monday")


def test_cross_player_item_banked_once_second_appearance_is_mention():
    ledger = nb.CorpusLedger()
    a = nb.PlayerRef(player="Player A", nbc_id=1001, slug="player-a")
    b = nb.PlayerRef(player="Player B", nbc_id=1002, slug="player-b")
    own_a = _mini_block("player-a", 1001, "Player A", "2026-10-06T00:00:00Z", "Player A returns")
    own_b = _mini_block("player-b", 1002, "Player B", "2026-10-04T00:00:00Z", "Player B returns")
    pull_a = nb.fetch_player_archive(FakeSession([_page(own_a, SHARED)]), a,
                                     sleeper=lambda s: None, ledger=ledger)
    assert len(pull_a["records"]) == 2 and pull_a["mentions"] == []
    pull_b = nb.fetch_player_archive(FakeSession([_page(own_b, SHARED)]), b,
                                     sleeper=lambda s: None, ledger=ledger)
    # the shared teammate blurb is NOT banked a second time: it is a mention reference
    assert [r.headline for r in pull_b["records"]] == ["Player B returns"]
    assert len(pull_b["mentions"]) == 1
    m = pull_b["mentions"][0]
    assert m["headline"] == "Teammate One is out Monday" and m["banked_under_nbc_id"] == 1001
    assert ledger.level3_mentions == 1 and len(ledger.seen) == 3


def test_within_player_repeat_is_level2_dupe_not_mention():
    ledger = nb.CorpusLedger()
    a = nb.PlayerRef(player="Player A", nbc_id=1001, slug="player-a")
    own = _mini_block("player-a", 1001, "Player A", "2026-10-06T00:00:00Z", "Player A returns")
    older = _mini_block("player-a", 1001, "Player A", "2026-10-01T00:00:00Z", "Player A sits")
    # page 2 repeats `own` (page-boundary overlap) plus one older item: not all-seen,
    # so paging continues; the repeat is dropped as a level-2 dupe before banking.
    session = FakeSession([_page(own), _page(own, older), _page(older)])
    pull = nb.fetch_player_archive(session, a, sleeper=lambda s: None, ledger=ledger)
    assert [r.headline for r in pull["records"]] == ["Player A returns", "Player A sits"]
    # both repeats (page 2's overlap of `own`, page 3's repeat of `older`) are
    # level-2 dupes, dropped before banking; page 3 is then all-seen: level-1 stop.
    assert pull["level2_dupes"] == 2 and pull["mentions"] == []
    assert pull["stopped"] == "all_seen_overlap"


def test_all_seen_page_stops_paging():
    ledger = nb.CorpusLedger()
    a = nb.PlayerRef(player="Player A", nbc_id=1001, slug="player-a")
    b = nb.PlayerRef(player="Player B", nbc_id=1002, slug="player-b")
    own_b = _mini_block("player-b", 1002, "Player B", "2026-10-04T00:00:00Z", "Player B returns")
    nb.fetch_player_archive(FakeSession([_page(SHARED)]), a, sleeper=lambda s: None, ledger=ledger)
    # B's first page has one new item + the shared one; B's second page is entirely
    # already-seen (shifted, not identical) -> all_seen_overlap stop, mentions kept.
    shifted = _mini_block("teammate-one", 111, "Teammate One", "2026-10-03T00:00:00Z",
                          "Teammate One upgraded to questionable")
    nb.fetch_player_archive(FakeSession([_page(shifted)]), a, sleeper=lambda s: None, ledger=ledger)
    session = FakeSession([_page(own_b, SHARED), _page(shifted)])
    pull = nb.fetch_player_archive(session, b, sleeper=lambda s: None, ledger=ledger)
    assert pull["stopped"] == "all_seen_overlap"
    assert pull["requests"] == 2
    assert len(pull["raw_pages"]) == 1                 # the all-seen page is not re-banked
    assert ledger.level1_stops >= 1


def test_dedupe_and_window_slicing():
    items = nb.parse_items_html(_fixture("nbcsports_melton_p1.html"), MELTON, FETCHED)
    assert len(nb.dedupe_records(items + items)) == len(items) == 10
    window = nb.slice_window(items, "2026-11-10")      # items in [Oct 31, Nov 10], subject only
    assert window and all(i.is_subject for i in window)
    assert all("2026-10-31" <= i.published_date <= "2026-11-10" for i in window)
    everything = nb.slice_window(items, "2026-11-10", subject_only=False)
    assert len(everything) > len(window)               # mentions join when asked


def test_ledger_json_round_trip():
    led = nb.CorpusLedger()
    led.seen[(1735689600.0, "Headline", 111)] = 1001
    led.player_seen[1001] = {(1735689600.0, "Headline", 111)}
    led.level3_mentions = 4
    back = nb.CorpusLedger.from_jsonable(json.loads(json.dumps(led.to_jsonable())))
    assert back.seen == led.seen and back.player_seen == led.player_seen
    assert back.level3_mentions == 4


# ---------------------------------------------------------------------------
# (d) guarded runs + banking layout
# ---------------------------------------------------------------------------

def _cases():
    return [{"player": "De'Anthony Melton", "return_date": "2022-01-06",
             "window_start": "2021-12-27"},
            {"player": "Joel Embiid", "return_date": "2024-04-02",
             "window_start": "2024-03-23"}]


def test_run_disabled_makes_no_requests_and_no_writes():
    session = FakeSession([_fixture("nbcsports_melton_p1.html")])
    store = FakeStore()
    res = nb.run_historic_pull([MELTON], _cases(), session=session, execute=False,
                               env={}, store=store)
    assert res["status"] == "disabled"
    assert session.calls == [] and store.writes == {}
    assert res["plan"]["players"] == 1 and res["plan"]["cases"] == 2


def test_run_execute_without_env_flag_still_refuses():
    session = FakeSession([_fixture("nbcsports_melton_p1.html")])
    store = FakeStore()
    res = nb.run_historic_pull([MELTON], _cases(), session=session, execute=True,
                               env={}, store=store)
    assert res["status"] == "disabled"
    assert session.calls == [] and store.writes == {}


def test_enabled_pull_banks_raw_parsed_and_mentions():
    ledger = nb.CorpusLedger()
    store = FakeStore()
    session = FakeSession([_fixture("nbcsports_melton_p1.html"),
                           _fixture("nbcsports_melton_p2.html")])
    res = nb.run_historic_pull([MELTON], _cases(), session=session, execute=True,
                               env={nb.ENABLE_ENV: "1"}, store=store,
                               sleeper=lambda s: None, ledger=ledger)
    assert res["status"] == "done"
    keys = set(store.writes)
    assert f"{nb.PREFIX}/raw/10744/page_0001.html" in keys
    assert f"{nb.PREFIX}/raw/10744/page_0002.html" in keys
    assert f"{nb.PREFIX}/parsed/10744.jsonl" in keys
    corpus = [json.loads(l) for l in store.writes[f"{nb.PREFIX}/parsed/10744.jsonl"].decode().splitlines()]
    assert len(corpus) == 20
    ts = [r["published_ts"] for r in corpus]
    assert ts == sorted(ts)                            # parsed output sorted by timestamp
    assert all("corpus_key" in r for r in corpus)
    loaded = nb.load_parsed_corpus(10744, store=store)
    assert len(loaded) == 20


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "pull_nbcsports_prose", Path(__file__).parents[1] / "scripts" / "pull_nbcsports_prose.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runner_no_execute_is_a_zero_call_dry_run(capsys):
    runner = _runner_module()
    session, store = FakeSession([_fixture("nbcsports_melton_p1.html")]), FakeStore()
    rc = runner.main([], session=session, env={}, store=store)
    assert rc == 0
    assert session.calls == [] and store.writes == {}
    assert json.loads(capsys.readouterr().out)["status"] == "disabled"


def test_runner_execute_without_env_flag_refuses(capsys):
    runner = _runner_module()
    session, store = FakeSession([_fixture("nbcsports_melton_p1.html")]), FakeStore()
    rc = runner.main(["--execute"], session=session, env={}, store=store)
    assert rc == 2
    assert session.calls == [] and store.writes == {}


# ---------------------------------------------------------------------------
# (e) resolver (fake session; live verification was the seed gate)
# ---------------------------------------------------------------------------

class SearchSession:
    def __init__(self, text, status_code=200):
        self.calls = []
        self._text = text
        self._status = status_code

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {})})
        return FakeResponse(text=self._text, status_code=self._status)


def test_resolver_exact_match_and_request_shape():
    html = '<a href="https://www.nbcsports.com/nba/paolo-banchero/11269">Paolo Banchero</a>'
    s = SearchSession(html)
    ref = nb.resolve_player(s, "Paolo Banchero")
    assert ref == nb.PlayerRef("Paolo Banchero", 11269, "paolo-banchero")
    assert s.calls[0]["url"] == nb.SEARCH_URL and s.calls[0]["params"] == {"q": "Paolo Banchero"}
    assert s.calls[0]["headers"] == nb.HEADERS


def test_resolver_apostrophe_slug_skeleton():
    # NBC drops the apostrophe outright: "De'Anthony Melton" -> "deanthony-melton".
    html = '<a href="https://www.nbcsports.com/nba/deanthony-melton/10744">x</a>'
    assert nb.resolve_player(SearchSession(html), "De'Anthony Melton") == MELTON


def test_resolver_ambiguity_and_absence_return_none():
    two = ('<a href="https://www.nbcsports.com/nba/chris-smith/111">a</a>'
           '<a href="https://www.nbcsports.com/nba/chris-smith/222">b</a>')
    assert nb.resolve_player(SearchSession(two), "Chris Smith") is None
    assert nb.resolve_player(SearchSession("<html>none</html>"), "Nobody Here") is None


def test_resolver_non_200_fails_loudly():
    with pytest.raises(RuntimeError):
        nb.resolve_player(SearchSession("", status_code=429), "Joel Embiid")
