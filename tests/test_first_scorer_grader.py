"""
tests/test_first_scorer_grader.py -- DEC-09 first-scorer grader math.

Covers the pure machinery: de-vig (proportional + the Argon-amended power method),
name/date/season keys, base-rate construction (strictly-before, smoothed), blend
fitting, staking arithmetic, and the bar/verdict logic at the accrual and interim
tiers. Imports the module by file path (house pattern, cf. test_devig.py) so the
suite runs without the package __init__ chain.
"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

_spec = importlib.util.spec_from_file_location(
    "first_scorer_grader",
    str(Path(__file__).resolve().parents[1] / "ev" / "first_scorer_grader.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


class TestPrices:
    def test_implied(self):
        assert G.implied_prob(800) == pytest.approx(1 / 9)
        assert G.implied_prob(-200) == pytest.approx(2 / 3)
        assert G.american_to_decimal(800) == pytest.approx(9.0)
        assert G.american_to_decimal(-200) == pytest.approx(1.5)


class TestDevig:
    IMPLIED = [0.30, 0.22, 0.16, 0.12, 0.09, 0.07, 0.05, 0.04, 0.03, 0.02]  # sums to 1.10

    def test_proportional_sums_to_one(self):
        fairs = G.devig_n(self.IMPLIED, "proportional")
        assert sum(fairs) == pytest.approx(1.0)
        assert fairs[0] == pytest.approx(0.30 / 1.10)

    def test_power_sums_to_one_and_shades_longshots(self):
        power = G.devig_n(self.IMPLIED, "power")
        prop = G.devig_n(self.IMPLIED, "proportional")
        assert sum(power) == pytest.approx(1.0)
        # power (k > 1) pushes more overround onto the longshot tail than proportional
        assert power[-1] < prop[-1]
        assert power[0] > prop[0]

    def test_power_no_overround_is_identity(self):
        qs = [0.5, 0.3, 0.2]
        assert G.devig_n(qs, "power") == pytest.approx(qs)

    def test_rejects_bad_probs(self):
        with pytest.raises(ValueError):
            G.devig_n([0.5, 1.2], "power")
        with pytest.raises(ValueError):
            G.devig_n([0.5, 0.6], "shin")


class TestKeys:
    def test_normalize_player(self):
        assert G.normalize_player("P.J. Washington Jr.") == "p j washington"
        assert G.normalize_player("Luka Doncic") == "luka doncic"

    def test_nfl_short_key_matches_outcome_form(self):
        assert " ".join(G.nfl_short_key("Fernando Mendoza")) == "f mendoza"
        assert " ".join(G.nfl_short_key("F.Mendoza")) == "f mendoza"
        assert " ".join(G.nfl_short_key("Deebo Samuel")) == " ".join(G.nfl_short_key("D.Samuel"))

    def test_et_date(self):
        # 2026-11-15T00:00Z is Nov 14 in ET -- the outcomes game_date basis
        assert G.et_date("2026-11-15T00:00:00Z") == "2026-11-14"
        assert G.et_date("2026-11-15T18:00:00Z") == "2026-11-15"

    def test_season_of(self):
        assert G.season_of(G.SPORT_NBA, "2026-11-14") == 2027
        assert G.season_of(G.SPORT_NBA, "2027-04-10") == 2027
        assert G.season_of(G.SPORT_NFL, "2026-11-15") == 2026
        assert G.season_of(G.SPORT_NFL, "2027-01-10") == 2026


class TestKeyCollisions:
    def test_brown_collision_detected(self):
        listed = [{"player": "A.J. Brown"}, {"player": "Amon-Ra St. Brown"},
                  {"player": "Jahmyr Gibbs"}]
        coll = G.listed_key_collisions(listed, G.SPORT_NFL)
        assert set(coll) == {"a brown"}
        assert coll["a brown"] == ["a j brown", "amon ra st brown"]

    def test_etienne_brothers_detected(self):
        listed = [{"player": "Travis Etienne Jr."}, {"player": "Trevor Etienne"}]
        assert set(G.listed_key_collisions(listed, G.SPORT_NFL)) == {"t etienne"}

    def test_clean_set_and_same_person_variants_pass(self):
        listed = [{"player": "Nico Collins"}, {"player": "Nick Chubb"}]
        assert G.listed_key_collisions(listed, G.SPORT_NFL) == {}
        # one person under two spellings is not a collision of distinct players
        listed = [{"player": "Luka Doncic"}, {"player": "Luka Doncic"}]
        assert G.listed_key_collisions(listed, G.SPORT_NBA) == {}
        # punctuation variants of one name across books ("D.J." vs "DJ") must not trip
        listed = [{"player": "D.J. Moore"}, {"player": "DJ Moore"},
                  {"player": "Breece Hall"}]
        assert G.listed_key_collisions(listed, G.SPORT_NFL) == {}


def _outcomes(rows):
    return pd.DataFrame(rows)


class TestBaseRates:
    def test_strictly_before_smoothed_current_season(self):
        outcomes = _outcomes([
            {"season": 2027, "game_date": "2026-11-01", "first_fg_player": "Alpha One",
             "first_fg_team": "DEN", "home_team": "DEN", "away_team": "NO"},
            {"season": 2027, "game_date": "2026-11-02", "first_fg_player": "Beta Two",
             "first_fg_team": "NO", "home_team": "DEN", "away_team": "NO"},
            # same-day game must NOT count (strictly before)
            {"season": 2027, "game_date": "2026-11-03", "first_fg_player": "Alpha One",
             "first_fg_team": "DEN", "home_team": "DEN", "away_team": "NO"},
            # prior season must NOT count
            {"season": 2026, "game_date": "2026-04-01", "first_fg_player": "Gamma Three",
             "first_fg_team": "DEN", "home_team": "DEN", "away_team": "NO"},
        ])
        game = {"date_et": "2026-11-03", "listed": [
            {"player": "Alpha One", "price": 500}, {"player": "Beta Two", "price": 600},
            {"player": "Gamma Three", "price": 700}]}
        rates = G.base_rates(game, outcomes, G.SPORT_NBA, "fg")
        # counts: Alpha 1, Beta 1, Gamma 0 -> +0.5 smoothing over total 3.5
        assert rates["alpha one"] == pytest.approx(1.5 / 3.5)
        assert rates["beta two"] == pytest.approx(1.5 / 3.5)
        assert rates["gamma three"] == pytest.approx(0.5 / 3.5)
        assert sum(rates.values()) == pytest.approx(1.0)


def _record(date, scorer, book_p, base_p, clf_p, top=None, top_dec=5.0):
    keys = ["a", "b", "c"]
    dist = lambda p: {k: v for k, v in zip(keys, p)}  # noqa: E731
    return {"date_et": date, "commence_time": f"{date}T00:00:00Z", "scorer": scorer,
            "book": dist(book_p), "book_prop": dist(book_p), "base": dist(base_p),
            "clf": dist(clf_p), "top_choice": top or scorer, "top_decimal": top_dec}


class TestGrade:
    def test_accrual_tier_bars_unjudged(self):
        recs = [_record(f"2026-11-{d:02d}", "a", [0.5, 0.3, 0.2], [0.4, 0.3, 0.3],
                        [0.8, 0.1, 0.1]) for d in (10, 11, 12)]
        out = G.grade(recs, G.SPORT_NBA)
        assert out["tier"] == "accrual"
        assert out["bars"] == {"bar1_d1_beats_base": None, "bar2_blend_beats_book": None,
                               "bar3_staking_roi": None}
        assert out["mean_ll"]["clf"] < out["mean_ll"]["base"]
        assert out["staking"]["roi"] == pytest.approx(4.0)  # always hits at decimal 5.0

    def test_staking_unlisted_scorer_is_a_loss(self):
        recs = [_record("2026-11-10", "a", [0.5, 0.3, 0.2], [0.4, 0.3, 0.3], [0.8, 0.1, 0.1])]
        stake = [dict(recs[0], scorer="zzz unlisted")]
        out = G.grade(recs, G.SPORT_NBA, staking_records=stake)
        assert out["staking"]["n"] == 1 and out["staking"]["roi"] == pytest.approx(-1.0)

    def test_interim_tier_bar1_passes_on_consistent_gain(self):
        recs = []
        for day in range(1, 31):  # 30 day-clusters x 5 games = 150
            clf_p = [0.85, 0.08, 0.07] if day % 2 else [0.75, 0.13, 0.12]
            for _ in range(5):
                recs.append(_record(f"2026-10-{day:02d}", "a", [0.5, 0.3, 0.2],
                                    [0.34, 0.33, 0.33], clf_p))
        out = G.grade(recs, G.SPORT_NBA)
        assert out["tier"] == "interim"
        assert out["bars"]["bar1_d1_beats_base"] is True
        assert out["bars"]["bar3_staking_roi"] is True
        assert out["blend"]["fit"] is True

    def test_fit_blend_weight_recovers_classifier(self):
        recs = [_record("2026-11-10", "a", [0.34, 0.33, 0.33], [0.34, 0.33, 0.33],
                        [0.97, 0.02, 0.01]) for _ in range(5)]
        assert G.fit_blend_weight(recs) == pytest.approx(1.0)


class TestExtractBoard:
    def test_alias_keys_and_junk_filter(self):
        payload = {"bookmakers": [
            {"key": "underdog", "markets": [{"key": "player_first_td", "outcomes": [
                {"name": "Yes", "description": "Bam Knight", "price": 785},
                {"name": "Yes", "description": "Blake Corum", "price": 509},
                {"name": "Yes", "description": "{bad}", "price": 100}]}]},
            {"key": "fliff", "markets": [{"key": "player_first_touchdown", "outcomes": [
                {"name": "Yes", "description": "Blake Corum", "price": 545}]}]},
            {"key": "other", "markets": [{"key": "player_points", "outcomes": [
                {"name": "Yes", "description": "Someone", "price": 100}]}]},
        ]}
        boards = G.extract_board(payload, G.SPORT_NFL)
        assert [r["player"] for r in boards["underdog"]] == ["Bam Knight", "Blake Corum"]
        assert boards["fliff"][0]["price"] == 545
        assert "other" not in boards

    def test_bovada_team_tail_stripped(self):
        payload = {"bookmakers": [{"key": "bovada", "markets": [
            {"key": "player_first_touchdown_scorer", "outcomes": [
                {"name": "Yes", "description": "Amon-Ra St. Brown (DET)", "price": 700},
                {"name": "Yes", "description": "A.J. Brown (NE)", "price": 900}]}]}]}
        boards = G.extract_board(payload, G.SPORT_NFL)
        names = [r["player"] for r in boards["bovada"]]
        assert "Amon-Ra St. Brown" in names
        assert " ".join(G.nfl_short_key(names[0])) == " ".join(G.nfl_short_key("A.St. Brown"))

    def test_nba_board(self):
        payload = {"bookmakers": [{"key": "hardrock", "markets": [
            {"key": "player_first_basket", "outcomes": [
                {"name": "Yes", "description": "Jamal Murray", "price": 400}]}]}]}
        boards = G.extract_board(payload, G.SPORT_NBA)
        assert boards["hardrock"][0]["player"] == "Jamal Murray"
