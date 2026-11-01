"""scripts.harvest_sgp_quotes: link validation, provenance of written rows, fit gating (offline, no browser)."""

from __future__ import annotations

import csv
import json
from datetime import datetime

from scripts import harvest_sgp_quotes as h


def _cand(link, legs=2, book="fanduel"):
    legs_l = [{"label": f"leg{i}", "book_american": 200 + 10 * i} for i in range(legs)]
    return {"player": "P", "book": book, "archetype": "nba_reb_ast_10_10", "legs": legs_l,
            "deeplink": link, "est_sgp_american": 900, "ev_pct": 5.0, "game_key": "nba:2026-10-30:A@B"}


def _run(tmp_path, monkeypatch, cands, answers):
    opened = []
    monkeypatch.setattr(h.webbrowser, "open", lambda u: opened.append(u))
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(it))
    cpath = tmp_path / "cands.json"
    cpath.write_text(json.dumps({"candidates": cands}))
    csv_path = tmp_path / "quotes.csv"
    h.harvest_browser_links(str(cpath), str(csv_path), str(tmp_path / "params.json"), limit=10)
    rows = list(csv.DictReader(open(csv_path))) if csv_path.exists() else []
    return opened, rows


def test_non_https_or_foreign_links_are_never_opened(tmp_path, monkeypatch):
    cands = [_cand("http://evil.com/?fanduel.com"), _cand("https://evil.com/x"), _cand("javascript:alert(1)"),
             _cand("file:///etc/passwd"), _cand("http://sportsbook.fanduel.com/x")]
    opened, rows = _run(tmp_path, monkeypatch, cands, [])
    assert opened == [] and rows == []


def test_valid_link_opens_and_row_is_provenance_tagged(tmp_path, monkeypatch):
    opened, rows = _run(tmp_path, monkeypatch, [_cand("https://sportsbook.fanduel.com/event/1")], ["+950"])
    assert opened == ["https://sportsbook.fanduel.com/event/1"]
    assert len(rows) == 1
    assert rows[0]["source"] == "harvest" and rows[0]["sgp_odds"] == "950"
    datetime.fromisoformat(rows[0]["timestamp"])  # real timestamp


def test_three_leg_row_keeps_leg3_so_the_fitter_can_skip_it(tmp_path, monkeypatch):
    _, rows = _run(tmp_path, monkeypatch, [_cand("https://sportsbook.fanduel.com/event/1", legs=3)], ["+1200"])
    assert rows[0]["leg3_odds"] == "220"


def test_fit_only_with_no_real_quotes_writes_nothing(tmp_path):
    csv_path = tmp_path / "q.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=h.CSV_COLUMNS)
        w.writeheader()
        for i in range(3):  # far fewer than the minimum, and one placeholder source
            w.writerow({"book": "fanduel", "archetype": "nba_reb_ast_10_10", "leg1_odds": 300 + i, "leg2_odds": 350,
                        "sgp_odds": 1400, "timestamp": "2026-10-30T00:00:00Z", "source": "initial_seed"})
    params = tmp_path / "params.json"
    assert h.run_fit_only(str(csv_path), str(params)) == 1
    assert not params.exists()
