"""NHL SOG live scorer: real-fixture shape handling, synthetic flag/guard behaviour, stale refusal, no side effects."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ev import sweep_props_alerts as sw
from mlb_core.data.player_names import PlayerResolver
from nhl.config import team_abbrev
from nhl.props import features as F
from nhl.props import score, train
from nhl.props.gamelog import GAMELOG_COLS

FIX = Path(__file__).parent / "fixtures" / "odds" / "nhl_props_sample.json"
NOW = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
OVI, CAUF = 8471214, 8481540
ROSTER = [{"player_id": OVI, "full_name": "Alex Ovechkin", "team": "WSH"},
          {"player_id": CAUF, "full_name": "Cole Caufield", "team": "MTL"}]
INFO = {OVI: {"team": "WSH", "position": "L"}, CAUF: {"team": "MTL", "position": "R"}}


def _gamelog(last_date="2026-10-12"):
    """WSH / MTL / BOS / TOR, 8 skaters each, ~70 game days up to last_date; Ovechkin and Caufield among them."""
    rng = np.random.default_rng(11)
    teams = ["WSH", "MTL", "BOS", "TOR"]
    ids = {t: ([OVI] if t == "WSH" else [CAUF] if t == "MTL" else []) for t in teams}
    nxt = 9000000
    for t in teams:
        while len(ids[t]) < 8:
            ids[t].append(nxt)
            nxt += 1
    end = pd.Timestamp(last_date)
    rows, gid = [], 2026020000
    pair = [(("WSH", "MTL"), ("BOS", "TOR")), (("WSH", "BOS"), ("MTL", "TOR")), (("WSH", "TOR"), ("MTL", "BOS"))]
    for d in range(70):
        date = (end - pd.Timedelta(days=2 * (69 - d))).strftime("%Y-%m-%d")
        for home, away in pair[d % 3]:
            gid += 1
            for team, opp, ih in ((home, away, 1), (away, home, 0)):
                for k, pid in enumerate(ids[team]):
                    rows.append({"player_id": pid, "game_id": gid, "date": date, "team": team, "opp": opp, "is_home": ih,
                                 "position": "D" if k < 2 else "L", "toi_sec": float(rng.integers(700, 1300)),
                                 "sog": float(rng.poisson(1.2 + 0.4 * (k >= 2))), "attempts": float(rng.poisson(2.8)),
                                 "pp_attempts": float(rng.poisson(0.4)), "goals": 0, "assists": 0})
    return pd.DataFrame(rows, columns=GAMELOG_COLS)


@pytest.fixture(scope="module")
def world():
    gl = _gamelog()
    art = train.fit(F.build_features(gl), cutoff=None)
    return gl, art


def _prices(p_over, vig=1.04):
    def amer(p):
        p = min(max(p, 0.02), 0.98)
        return int(round(-100 * p / (1 - p))) if p >= 0.5 else int(round(100 * (1 - p) / p))
    return amer(p_over * vig), amer((1 - p_over) * vig)


def _event(p_fair, n_books=5, start_in_min=180, line=1.5, books=None):
    o, u = _prices(p_fair)
    def mk(name):
        return [{"name": f"Over {name}", "description": name, "price": o, "point": line},
                {"name": f"Under {name}", "description": name, "price": u, "point": line}]
    names = ["Alex Ovechkin", "Alexander Ovechkin", "Alex Ovechkin (WSH)", "Alex Ovechkin", "Alex Ovechkin"]
    books = books or ["betmgm", "draftkings", "bovada", "caesars", "pinnacle"][:n_books]
    start = (NOW + timedelta(minutes=start_in_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "ev1", "commence_time": start, "home_team": "Washington Capitals", "away_team": "Montreal Canadiens",
            "bookmakers": [{"key": b, "markets": [{"key": "player_shots_on_goal", "outcomes": mk(names[i % 5])}]}
                           for i, b in enumerate(books)]}


def _score(world, ev, gl=None, info=INFO, **kw):
    g, art = world
    return score.score_events([ev], NOW, art, g if gl is None else gl, info, PlayerResolver(ROSTER), {"WSH", "MTL"},
                              team_abbrev)


def _model_p_at(world, line):
    rows, _ = _score(world, _event(0.5, line=line))
    return rows[0]["p_model_over"]


def _model_p(world):
    return _model_p_at(world, 1.5)


def test_edge_vs_consensus_flags_and_guards(world):
    pm = _model_p(world)
    rows, st = _score(world, _event(pm - 0.07))                              # market 7pp BELOW the model -> bet OVER
    r = rows[0]
    assert r["flag"] and r["reason"] == "FLAG" and r["side"] == "OVER" and r["edge"] == pytest.approx(0.07, abs=0.012)
    assert r["n_books"] == 5 and r["ev_model"] > 0 and r["log_only"] is True and r["validated"] is False
    assert st["players_scored"] == 1 and st["unresolved_quotes"] == 0

    rows, _ = _score(world, _event(pm + 0.07))                               # market ABOVE the model -> UNDER
    assert rows[0]["side"] == "UNDER" and rows[0]["flag"]

    longshot = _score(world, _event(_model_p_at(world, 2.5) - 0.07, line=2.5))[0][0]       # +EV% explodes at a longshot price
    assert longshot["reason"] == "EV_TOO_HIGH" and not longshot["flag"]
    assert _score(world, _event(pm - 0.20))[0][0]["reason"] == "EDGE_TOO_HIGH"          # winner's-curse cap
    assert not _score(world, _event(pm - 0.20))[0][0]["flag"]
    assert _score(world, _event(pm - 0.02))[0][0]["reason"] == "SMALL_EDGE"
    assert _score(world, _event(pm - 0.07, n_books=2))[0][0]["reason"] == "THIN_BOOKS"
    assert _score(world, _event(pm - 0.07, start_in_min=10))[0][0]["reason"] == "NEAR_START"


def test_dfs_books_never_form_the_consensus(world):
    pm = _model_p(world)
    ev = _event(pm - 0.07, books=["betmgm", "draftkings", "underdog", "sleeper", "prizepicks"])
    r = _score(world, ev)[0][0]
    assert r["n_books"] == 2 and r["reason"] == "THIN_BOOKS"                  # only the two real books count


def test_real_payload_shape_is_handled_without_flags(world):
    """tests/fixtures/odds/nhl_props_sample.json: SOG arrives as a DFS two-sided line + one-sided 'Yes' ladders only."""
    ev = json.load(open(FIX))
    ev["commence_time"] = (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, st = _score(world, ev)
    assert not any(r["flag"] for r in rows)                                  # no two-sided real book -> never a flag
    assert all(r["reason"] == "NO_CONSENSUS" for r in rows) and rows            # scored as evidence only (Yes ladders)
    assert {r["player"] for r in rows} <= {"Alex Ovechkin", "Cole Caufield", "Chris Kreider"}
    assert all(r["n_books"] == 0 and r["cons_p_over"] is None for r in rows)


def test_stale_game_log_is_refused(world):
    g, _ = world
    stale = g[g["date"] < "2026-10-01"]                                       # history stops 13 days before the event
    rows, st = _score(world, _event(0.5), gl=stale)
    assert rows == [] and st["stale_log"] == 1


def test_player_whose_team_is_not_in_the_event_is_skipped(world):
    rows, st = _score(world, _event(0.5), info={OVI: {"team": "BOS", "position": "L"}, CAUF: INFO[CAUF]})
    assert rows == [] and st["team_mismatch"] >= 1


def test_player_without_enough_history_is_not_scored(world):
    g, _ = world
    thin = g[~((g.player_id == OVI) & (g.date < "2026-10-08"))]               # only ~2 prior games remain
    rows, st = _score(world, _event(0.5), gl=thin)
    assert rows == [] and st["no_history"] == 1


# ----------------------------------------------------------------------------- run_once: refusal + no side effects
class Mem:
    def __init__(self):
        self.b = {}

    def exists(self, k):
        return k in self.b

    def read_bytes(self, k):
        return self.b[k]

    def write_bytes(self, d, k):
        self.b[k] = d

    def read_csv(self, k, **kw):
        import io
        return pd.read_csv(io.BytesIO(self.b[k]))


@pytest.fixture
def mem(monkeypatch):
    m = Mem()
    from mlb_core import storage
    for n in ("exists", "read_bytes", "write_bytes", "read_csv"):
        monkeypatch.setattr(storage, n, getattr(m, n))
    # a scorer must never post: any Discord call fails the test loudly
    monkeypatch.setattr(sw, "post_discord", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scorer posted to Discord")))
    return m


def _bank(mem, ts, events):
    from nba.config import oddsaccum_latest_kind_key, oddsaccum_raw_key
    mem.b[oddsaccum_latest_kind_key("icehockey_nhl", "props")] = json.dumps(
        {"timestamp": ts.isoformat(), "date": "2026-10-14", "hhmm": "1730", "events_priced": len(events)}).encode()
    mem.b[oddsaccum_raw_key("icehockey_nhl", "2026-10-14", "props", "1730")] = json.dumps(events).encode()


def test_run_once_refuses_a_stale_snapshot_and_a_missing_pointer(mem, world, tmp_path):
    _, art = world
    train.save(art, str(tmp_path / "m"))
    assert score.run_once(str(tmp_path / "m"), NOW, out_dir=str(tmp_path / "o"))["status"] == "skipped"     # no pointer
    _bank(mem, NOW - timedelta(minutes=120), [_event(0.5)])
    res = score.run_once(str(tmp_path / "m"), NOW, out_dir=str(tmp_path / "o"))
    assert res["status"] == "skipped" and "old" in res["reason"]
    assert not (tmp_path / "o").exists()


def test_run_once_is_local_by_default_and_writes_no_bets_or_gcs(mem, world, tmp_path, monkeypatch):
    g, art = world
    train.save(art, str(tmp_path / "m"))
    pm = _model_p(world)
    _bank(mem, NOW - timedelta(minutes=5), [_event(pm - 0.07)])
    from nhl.props import score as sc
    monkeypatch.setattr(sc, "load_player_info", lambda: INFO)
    monkeypatch.setattr(sw, "load_resolver", lambda spec: (PlayerResolver(ROSTER), {"WSH", "MTL"}))
    before = set(mem.b)
    res = sc.run_once(str(tmp_path / "m"), NOW, out_dir=str(tmp_path / "o"), gamelog_loader=lambda: g)
    assert res["status"] == "ok" and res["flagged"] == 1 and res["log_only"] is True and res["validated"] is False
    csvs = list((tmp_path / "o").rglob("model_rows_*.csv"))
    assert len(csvs) == 1 and pd.read_csv(csvs[0])["flag"].sum() == 1
    assert set(mem.b) == before                                                # nothing written to (fake) GCS without --gcs
    sc.run_once(str(tmp_path / "m"), NOW, out_dir=None, gcs=True, gamelog_loader=lambda: g)
    assert any(k.startswith("EVSweep/nhl_props_model/") for k in mem.b)


def test_module_has_no_bets_table_or_discord_dependency():
    src = Path(score.__file__).read_text()
    for banned in ("bet_tracker", "insert_bet", "requests.post", "post_discord(", "DISCORD_WEBHOOK", "settle_bets"):
        assert banned not in src.replace("never posts to Discord", ""), banned
