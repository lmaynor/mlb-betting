"""Tests for the Action Network props backfill stack (BNK-11).

Offline: fixtures are trimmed captures of LIVE payloads (assists main board
2026-11-15; rebounds milestone 10+ board 2025-01-15, from the BNK-10 spike),
and the HTTP client is faked, so nothing touches the network or a bucket.
The disabled-by-default gate is tripwired like the first-scorer stack's:
with either gate missing, zero calls and zero writes may happen.
"""
import importlib.util
import json
from pathlib import Path

from nba.odds import action_network as AN

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "bank_action_network", ROOT / "scripts" / "bank_action_network.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture(name):
    return json.loads((FIX / name).read_text())


# ---------------------------------------------------------------------------
# Market tables: the milestone key sets are pinned to the alt boards' own
# site config (extracted 2026-11-16); a silent key change must fail here.
# ---------------------------------------------------------------------------

def test_market_tables_complete():
    assert len(AN.MARKET_TYPES) == 7
    assert len(AN.MILESTONE_SLUGS) == 49
    assert len(AN.ALL_MARKET_TYPES) == 56


def test_milestone_keys_match_site_config():
    cases = {
        ("points", 5): "core_bet_type_201_694_player_points_milestones_5_or_more",
        ("points", 50): "core_bet_type_201_1143_player_points_milestones_50_or_more",
        ("rebounds", 2): "core_bet_type_202_1243_player_rebounds_milestones_2_or_more",
        ("rebounds", 10): "core_bet_type_202_741_player_rebounds_milestones_10_or_more",
        ("rebounds", 17): "core_bet_type_202_1138_player_rebounds_milestones_17_or_more",
        ("rebounds", 18): "core_bet_type_202_1273_player_rebounds_milestones_18_or_more",
        ("rebounds", 20): "core_bet_type_202_1242_player_rebounds_milestones_20_or_more",
        ("assists", 2): "core_bet_type_203_747_player_assists_milestones_2_or_more",
        ("assists", 16): "core_bet_type_203_759_player_assists_milestones_16_or_more",
        ("threes", 1): "core_bet_type_204_760_player_threes_made_milestones_1_or_more",
        ("threes", 6): "core_bet_type_204_1052_player_threes_made_milestones_6_or_more",
        ("threes", 10): "core_bet_type_204_1058_player_threes_made_milestones_10_or_more",
    }
    for (stat, n), expected in cases.items():
        assert AN.milestone_type(stat, n) == expected
        assert AN.ALL_MARKET_TYPES[AN.milestone_slug(stat, n)] == expected


def test_build_url_single_type_with_all_books():
    url = AN.build_url("nba", "20261115", AN.MARKET_TYPES["assists"])
    assert "bookIds=15,30,68,69,75,123,79" in url
    assert "customPickTypes=core_bet_type_26_assists" in url
    assert url.endswith("date=20261115")
    assert url.count("customPickTypes=") == 1     # multi-type calls 400 (BNK-10)


def test_other_league_tables_pinned():
    nhl = AN.LEAGUE_MARKET_TYPES["nhl"]
    assert nhl["saves"] == "core_bet_type_38_goaltender_saves"       # priority surface
    assert nhl["shots_on_goal"] == "core_bet_type_31_shots_on_goal"  # priority surface
    assert nhl["shots_on_goal_milestone_8plus"] == \
        "core_bet_type_205_772_player_shots_on_goal_milestones_8_or_more"
    nfl = AN.LEAGUE_MARKET_TYPES["nfl"]
    assert nfl["passing_yards"] == "core_bet_type_9_passing_yards"
    assert nfl["anytime_touchdown_scorer"] == "core_bet_type_62_anytime_touchdown_scorer"
    assert nfl["first_touchdown_scorer"] == "core_bet_type_56_first_touchdown_scorer"
    assert nfl["passing_yards_milestone_300plus"] == \
        "core_bet_type_525_1010_player_passing_yards_milestones_300_or_more"


def test_one_sided_flags():
    assert AN.is_one_sided("nba", "rebounds_milestone_10plus")
    assert not AN.is_one_sided("nba", "points")
    assert AN.is_one_sided("nhl", "anytime_goal_scorer")       # scorer market: Yes list
    assert AN.milestone_threshold("anytime_goal_scorer") is None
    assert not AN.is_one_sided("nhl", "saves")
    assert AN.is_one_sided("nfl", "to_score_2_or_more_touchdowns")


def test_normalize_gamelines_sample():
    payload = _fixture("action_network_gamelines_sample.json")
    rows = AN.normalize_gamelines(payload, date_iso="2026-11-14",
                                  scraped_at="2026-11-16T07:00:00+00:00")
    assert len(rows) == 42
    assert {r["market_type"] for r in rows} >= {"total", "moneyline"}
    assert all(r["event_id"] == rows[0]["event_id"] for r in rows)
    assert {r["book_name"] for r in rows} >= {"consensus", "open", "draftkings"}
    assert all(r["odds"] is not None for r in rows)


# ---------------------------------------------------------------------------
# Normalizer against live captures
# ---------------------------------------------------------------------------

def test_normalize_main_board_sample():
    payload = _fixture("action_network_assists_sample.json")
    rows = AN.normalize_payload(payload, date_iso="2026-11-15", market_slug="assists",
                                pick_type=AN.MARKET_TYPES["assists"],
                                scraped_at="2026-11-16T07:00:00+00:00")
    n_outcomes = sum(len(((b or {}).get("event") or {}).get(AN.MARKET_TYPES["assists"]) or [])
                     for b in payload["markets"].values())
    assert len(rows) == n_outcomes == 60
    assert all(r["player_name"] for r in rows)            # 100% name resolution
    assert all(r["team_abbr"] for r in rows)
    assert all(r["line"] is not None and r["odds"] is not None for r in rows)
    assert {r["side"] for r in rows} <= {"over", "under"}
    assert not any(r["is_milestone"] for r in rows)
    assert {r["book_name"] for r in rows} >= {"consensus", "open", "draftkings"}
    assert all(r["market"] == "assists" for r in rows)
    # AN model outputs must NOT leak into the price panel
    assert not any("edge" in r or "projection" in r for r in rows)


def test_normalize_milestone_sample_is_one_sided():
    payload = _fixture("action_network_milestone_sample.json")
    pick = "core_bet_type_202_741_player_rebounds_milestones_10_or_more"
    rows = AN.normalize_payload(payload, date_iso="2025-01-15",
                                market_slug="rebounds_milestone_10plus",
                                pick_type=pick, scraped_at="2026-11-16T07:00:00+00:00")
    assert len(rows) == 40
    assert all(r["is_milestone"] for r in rows)
    assert all(r["threshold"] == 10.0 and r["line"] == 10.0 for r in rows)
    assert {r["side"] for r in rows} == {"noside"}        # one-sided Yes prices
    assert all(r["player_name"] for r in rows)


def test_normalize_empty_payload():
    payload = {"games": [], "markets": {}, "players": []}
    assert AN.payload_games(payload) == []
    assert AN.normalize_payload(payload, date_iso="2026-07-04", market_slug="points",
                                pick_type=AN.MARKET_TYPES["points"],
                                scraped_at="x") == []


# ---------------------------------------------------------------------------
# Pacer + fetch policy (owner's adjust-on-throttle rule)
# ---------------------------------------------------------------------------

class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_pacer_steps_down_on_throttle_and_recovers():
    pacer = AN.AdaptivePacer(shard="t", clock=_FakeClock(), wall=lambda: "now")
    assert pacer.interval == 1.0
    pacer.on_throttle("http_429")
    assert pacer.interval > 1.0
    assert pacer.adjustments and pacer.adjustments[-1]["trigger"] == "http_429"
    stepped = pacer.interval
    for _ in range(AN.AdaptivePacer.STEP_UP_AFTER):
        pacer.on_success(0.5)
    assert pacer.interval < stepped                       # eases back, never below base
    assert pacer.interval >= AN.AdaptivePacer.BASE_INTERVAL


def test_pacer_latency_counts_as_throttle():
    pacer = AN.AdaptivePacer(shard="t", clock=_FakeClock(), wall=lambda: "now")
    pacer.on_success(6.0)
    assert pacer.interval > 1.0


class _FakeCall:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        return AN.FetchResult(status=status, body=b"{}", latency_s=0.2)


def _policy(call, state=None):
    pacer = AN.AdaptivePacer(shard="t", clock=_FakeClock(), wall=lambda: "now")
    return AN.fetch_with_policy(call, pacer, state if state is not None else {},
                                sleep=lambda s: None, rand=lambda: 0.5)


def test_policy_403_wall_hard_stops():
    out = _policy(_FakeCall([403, 403, 403]))
    assert out.hard_stop and out.attempts == 3


def test_policy_400_gives_up_without_retry():
    call = _FakeCall([400, 200])
    out = _policy(call)
    assert out.gave_up and call.calls == 1


def test_policy_429_backs_off_then_succeeds():
    out = _policy(_FakeCall([429, 200]))
    assert not out.gave_up and not out.hard_stop and out.attempts == 2
    assert out.result.status == 200


def test_policy_5xx_exhausts_to_gave_up():
    out = _policy(_FakeCall([500, 502, 503, 500]))
    assert out.gave_up and out.attempts == 4


# ---------------------------------------------------------------------------
# Runner gate + market resolution (tripwire: plan mode touches nothing)
# ---------------------------------------------------------------------------

def test_runner_gate_requires_both():
    mod = _load_script()
    assert mod.execution_allowed(False, {mod.ENABLE_ENV: "1"})[0] is False
    assert mod.execution_allowed(True, {})[0] is False
    assert mod.execution_allowed(True, {mod.ENABLE_ENV: "1"})[0] is True


def test_runner_plan_mode_is_inert(capsys):
    mod = _load_script()
    rc = mod.main(["--shard", "t", "--start", "20261109", "--end", "20261110"], environ={})
    assert rc == 0
    assert "PLAN ONLY" in capsys.readouterr().out


def test_resolve_markets():
    mod = _load_script()
    assert len(mod.resolve_markets("nba", "all")) == 57        # gamelines + 56 prop types
    assert mod.resolve_markets("nba", "all")[0] == "gamelines"
    assert len(mod.resolve_markets("nba", "main")) == 8        # gamelines + 7
    assert len(mod.resolve_markets("nba", "milestones")) == 49
    assert mod.resolve_markets("nba", "points,rebounds") == ["gamelines", "points", "rebounds"]
    # the verified manifest narrows the candidate table
    narrowed = mod.resolve_markets("nhl", "all", manifest_markets=["saves", "gamelines"])
    assert narrowed == ["gamelines", "saves"]
