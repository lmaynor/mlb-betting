"""nba.minutes_restriction.blowout -- B1 blowout-minutes features inside the Arm S harness.

Source of the build: Argon's game-state consult, Nov 11, 2026 (verdict B1, section (d) scope
ruling, section (e) evidence demand). B1 is a KEEP as a build INSIDE Arm S: projected margin
belongs in the minutes model, nowhere else. Added features, all PREGAME:

  * closing_spread          the case team's closing spread (negative = the case team is
                            favored), consensus across books (see consensus_closing_spreads);
  * abs_closing_spread      |closing_spread|;
  * starter_flag            1.0 when the player started the case game (see the honesty note);
  * abs_spread_x_starter    abs_closing_spread * starter_flag -- the interaction Argon named;
  * team_pull_speed         the case team's shrunk blowout pull-speed (see team_pull_speed):
                            the per-team slope of mean starter minutes on |closing spread|,
                            fit on DEV seasons only and shrunk toward the league slope by
                            team sample size. Coach identity is not in the banked box
                            scores, so TEAM is the grouping -- a stated proxy for the
                            coach effect Argon asked for.

PREGAME ONLY -- Argon's nitpick is part of the spec: the packet's 4.8-minute figure is a
REALIZED-margin effect; conditioning on realized margin would leak the outcome into the
features. Every quantity here is built from the pregame closing spread (BettingPros game-line
partitions, closing/near-tip snapshot) and from box-score history strictly before the test
seasons. Realized margin is never read.

STARTER-FLAG HONESTY NOTE: the harness's features.py deliberately excludes the box score's
`started` flag from Arm S because starting lineups publish ~30 min pre-tip, after Arm S's
decision time (tip - 60 min). Argon's B1 spec names abs(spread) * starter_flag explicitly,
so the flag is included here as the pregame PROJECTED starter flag's measurable stand-in:
in a live deployment the input is the projected starter (rotation reporting), and the box
score's realized starter flag is its backtest proxy (the two agree for the overwhelming
majority of rotation players, and a return case's starter status is the very quantity the
restriction screen reasons about). The caveat is stated, not hidden: this module is an
offline experiment inside the harness, and promoting the interaction to any decision-time
use requires the projected-starter input, not the realized flag.

EVALUATION (Argon section (e), auto-kill): band-mapped MAE -- the harness's minutes MAE
(grade.py's arm_s_mae: |predicted minutes - realized minutes|, the quantity the pre-
registered Arm S bars are stated in) -- on the registered split (fit dev seasons
2021-22..2023-24 only; test 2024-25 + 2025-26), before vs after adding the B1 features,
reported for the full sample and the |spread| < 9.5 / |spread| >= 9.5 cuts, on the
harness's primary population (non-game-2 cases). The build PASSES only if the MAE
improvement is >= 0.15 minutes overall AND >= 0.35 minutes in the |spread| >= 9.5 cut;
below either bar it is AUTO-KILLED. Both arms are graded on the IDENTICAL case set (cases
with a matched closing spread), so the comparison is the features, not the sample.
"""
from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from nba.minutes_restriction import arm_s, features
from nba.minutes_restriction.arm_s import DEV_SEASONS, TEST_SEASONS
from nba.teams import team_code

SPREAD_CUT = 9.5
BAR_OVERALL_MINUTES = 0.15
BAR_HIGH_SPREAD_MINUTES = 0.35
PULL_SPEED_SHRINK_K = 82.0          # team-games; one season of games halves the shrinkage
PULL_SPEED_MIN_GAMES = 20
# BettingPros spellings -> repo tricodes (mirrors nba.game.market_eval.BP_TO_TRICODE).
BP_TO_TRICODE = {"PHO": "PHX", "NOR": "NOP", "UTH": "UTA"}
_FRESH_BEFORE_H, _FRESH_AFTER_MIN = 4.0, 15.0   # market_eval's closing-quote window

B1_FEATURE_COLUMNS = features.FEATURE_COLUMNS + [
    "closing_spread", "abs_closing_spread", "starter_flag", "abs_spread_x_starter",
    "team_pull_speed",
]

PREDICTION_COLUMNS_B1 = arm_s.PREDICTION_COLUMNS + [
    "closing_spread", "abs_closing_spread", "team_pull_speed",
]


def _tri(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().upper()
    return BP_TO_TRICODE.get(s) or team_code(s) or None


# -----------------------------------------------------------------------------------------------
# Closing spread consensus (pregame only)

def consensus_closing_spreads(quotes: pd.DataFrame, *, min_books: int = 3) -> pd.DataFrame:
    """BettingPros game-line spread quotes (per-book rows) -> one closing spread per game.

    Mirrors nba.game.market_eval's closing rule: non-open rows from real books only
    (book_kind == "book"; the Consensus pseudo-book is excluded), the book's quote must
    have been updated inside the closing window [tip - 4h, tip + 15min] (the partitions
    store one near-tip snapshot per book; this keeps the near-tip quote and drops stale
    ones), the game line is the MODAL line across books (ties: smallest |line|), and at
    least `min_books` books must quote that modal line.

    `line` in the partitions is signed from the quoting side's perspective, so a book's
    home line is its home-side row's line (or the negation of its away-side row's).

    -> frame [home, away, game_day, closing_spread_home, n_books]: home/away are repo
    tricodes, game_day is the partition's ET game date (YYYY-MM-DD), and
    closing_spread_home is the home team's closing spread (negative = home favored).
    Games without a qualifying consensus are ABSENT (fail closed), never imputed.
    """
    cols = ["home", "away", "game_day", "closing_spread_home", "n_books"]
    if quotes is None or not len(quotes):
        return pd.DataFrame(columns=cols)
    q = quotes.copy()
    q = q[(q["is_open"] == False) & (q["book_kind"] == "book")]  # noqa: E712
    if not len(q):
        return pd.DataFrame(columns=cols)
    tip = pd.to_datetime(q["scheduled"])
    upd = pd.to_datetime(q["updated"])
    q = q[(upd >= tip - pd.Timedelta(hours=_FRESH_BEFORE_H))
          & (upd <= tip + pd.Timedelta(minutes=_FRESH_AFTER_MIN))]
    if not len(q):
        return pd.DataFrame(columns=cols)
    q = q.assign(_home_line=np.where(q["side"] == "home", q["line"], -q["line"]))
    rows = []
    for (_market, event_id), g in q.groupby(["market", "event_id"]):
        home, away = _tri(g["home"].iloc[0]), _tri(g["away"].iloc[0])
        if not home or not away:
            continue
        book_lines = g.groupby("book")["_home_line"].last()
        counts = book_lines.value_counts()
        top = counts[counts == counts.max()]
        line = float(min(top.index, key=abs))
        n_books = int(counts.loc[line])
        if n_books < min_books:
            continue
        rows.append({"home": home, "away": away,
                     "game_day": str(g["date"].iloc[0])[:10],
                     "closing_spread_home": line, "n_books": n_books})
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["game_day", "home", "away"]).reset_index(drop=True)


def attach_closing_spread(cases: pd.DataFrame, spreads: pd.DataFrame) -> pd.DataFrame:
    """Case frame + consensus spreads -> cases with closing_spread signed from the CASE
    TEAM's perspective (negative = the case team is favored), abs_closing_spread, and
    spread_n_books. The game-day match tolerates a +/- 1 day offset (mirrors
    market_eval._find: a frame's stored date can be the UTC day). Unmatched cases keep
    NaN -- callers decide the evaluation set; nothing is imputed here."""
    out = cases.copy()
    out["closing_spread"] = np.nan
    out["abs_closing_spread"] = np.nan
    out["spread_n_books"] = np.nan
    if spreads is None or not len(spreads) or not len(out):
        return out
    lookup = {(r.home, r.away, r.game_day): (r.closing_spread_home, r.n_books)
              for r in spreads.itertuples()}
    for idx, case in out.iterrows():
        team, opp = case["team"], case.get("opponent")
        if not team or opp is None or (isinstance(opp, float) and pd.isna(opp)):
            continue
        day0 = pd.Timestamp(case["game_date"])
        for off in (0, 1, -1):
            day = (day0 + pd.Timedelta(days=off)).date().isoformat()
            hit = lookup.get((team, opp, day)) or lookup.get((opp, team, day))
            if hit is None:
                continue
            home_line, n_books = hit
            # The lookup is keyed (home, away): if the case team is the key's home, the
            # stored line is already from its perspective; else negate.
            signed = home_line if (team, opp, day) in lookup else -home_line
            out.at[idx, "closing_spread"] = signed
            out.at[idx, "abs_closing_spread"] = abs(signed)
            out.at[idx, "spread_n_books"] = n_books
            break
    return out


# -----------------------------------------------------------------------------------------------
# Team pull-speed random effect (dev seasons only, shrunk)

def team_pull_speed(box: pd.DataFrame, spreads: pd.DataFrame, *,
                    dev_seasons=DEV_SEASONS, shrink_k: float = PULL_SPEED_SHRINK_K,
                    min_games: int = PULL_SPEED_MIN_GAMES) -> tuple[dict, pd.DataFrame]:
    """Shrunk per-team blowout pull-speed from banked box scores + closing spreads.

    Definition: for each team-game in the DEV seasons (season_start in dev_seasons --
    test-season box scores are never read, so the effect cannot leak the test period),
    mean starter minutes (starters = box rows with starter True who played). The team's
    raw pull-speed is the OLS slope of mean starter minutes on |closing spread| across
    its team-games (minutes per point of spread; negative = starters sit sooner as the
    spread grows). The league slope is the pooled OLS across all team-games. Each team's
    effect is shrunk toward the league slope by sample size:

        shrunk = (n * raw + K * league) / (n + K),   K = shrink_k team-games.

    A team with fewer than min_games matched team-games gets the league slope exactly.
    Coach identity is not in the banked data: TEAM is the grouping (the stated proxy).

    -> ({team_tricode: shrunk_slope}, diagnostics frame with per-team n / raw / shrunk /
    the league slope).
    """
    diag_cols = ["team", "n_games", "raw_slope", "shrunk_slope", "league_slope"]
    empty = ({}, pd.DataFrame(columns=diag_cols))
    if box is None or not len(box) or spreads is None or not len(spreads):
        return empty
    df = box.copy()
    if "season_type" in df.columns:
        types = pd.to_numeric(df["season_type"], errors="coerce")
        if types.isin([1, 2, 3, 5]).any():
            df = df[types.isin([2, 5])]
    if "season" in df.columns:                       # hoopR season = year the season ends
        df = df[(pd.to_numeric(df["season"], errors="coerce") - 1).isin(list(dev_seasons))]
    if not len(df):
        return empty
    df["_team"] = [team_code(t) or str(t).strip().upper() for t in df["team_abbreviation"]]
    df["_minutes"] = pd.to_numeric(df["minutes"], errors="coerce")
    df["_starter"] = df["starter"].fillna(False).astype(bool) if "starter" in df.columns else False
    df["_played"] = ~df["did_not_play"].fillna(False).astype(bool) \
        if "did_not_play" in df.columns else df["_minutes"].fillna(0) > 0
    starters = df[df["_starter"] & df["_played"] & df["_minutes"].notna()]
    tg = (starters.groupby(["game_id", "_team"], as_index=False)
          .agg(mean_starter_minutes=("_minutes", "mean"),
               game_date=("game_date", "first"),
               home_away=("home_away", "first")))
    # Opponent per team-game from the box itself (both teams' rows share the game_id).
    teams_by_game = df.drop_duplicates(["game_id", "_team"]).groupby("game_id")["_team"].apply(list)
    tg["_opp"] = [next((t for t in teams_by_game.get(gid, []) if t != team), None)
                  for gid, team in zip(tg["game_id"], tg["_team"])]
    lookup = {(r.home, r.away, r.game_day): r.closing_spread_home for r in spreads.itertuples()}

    def abs_spread(row):
        if row["_opp"] is None:
            return np.nan
        day0 = pd.Timestamp(row["game_date"])
        for off in (0, 1, -1):
            day = (day0 + pd.Timedelta(days=off)).date().isoformat()
            if (row["_team"], row["_opp"], day) in lookup:
                return abs(lookup[(row["_team"], row["_opp"], day)])
            if (row["_opp"], row["_team"], day) in lookup:
                return abs(lookup[(row["_opp"], row["_team"], day)])
        return np.nan

    tg["abs_spread"] = tg.apply(abs_spread, axis=1)
    tg = tg[tg["abs_spread"].notna()]
    if not len(tg):
        return empty

    def _slope(x: np.ndarray, y: np.ndarray) -> float:
        if len(x) < 2 or np.var(x) == 0:
            return 0.0
        return float(np.polyfit(x, y, 1)[0])

    league = _slope(tg["abs_spread"].to_numpy(float), tg["mean_starter_minutes"].to_numpy(float))
    effects: dict = {}
    diag_rows = []
    for team, g in tg.groupby("_team"):
        n = int(len(g))
        raw = _slope(g["abs_spread"].to_numpy(float), g["mean_starter_minutes"].to_numpy(float))
        shrunk = league if n < min_games else (n * raw + shrink_k * league) / (n + shrink_k)
        effects[team] = float(shrunk)
        diag_rows.append({"team": team, "n_games": n, "raw_slope": float(raw),
                          "shrunk_slope": float(shrunk), "league_slope": float(league)})
    diag = pd.DataFrame(diag_rows, columns=diag_cols).sort_values("team").reset_index(drop=True)
    return effects, diag


# -----------------------------------------------------------------------------------------------
# B1 feature matrix

def build_features_b1(cases: pd.DataFrame, pull_speed: Mapping[str, float], *,
                      league_pull_speed: float = 0.0) -> pd.DataFrame:
    """Case frame (spread-attached) -> the Arm S matrix plus the five B1 columns.

    Raises ValueError when any case lacks a closing spread: an unmatched case must be
    excluded by the caller, never silently coded as a pick'em."""
    base = features.build_features(cases)
    if cases is None or not len(cases):
        return pd.DataFrame(columns=B1_FEATURE_COLUMNS, dtype=float)
    spread = pd.to_numeric(cases["closing_spread"], errors="coerce")
    if spread.isna().any():
        raise ValueError("build_features_b1: cases without a closing spread must be "
                         "excluded before features are built (no imputation)")
    out = base.copy()
    out["closing_spread"] = spread.astype(float)
    out["abs_closing_spread"] = spread.abs().astype(float)
    starter = cases["started"].fillna(False).astype(bool).astype(float) \
        if "started" in cases.columns else pd.Series(0.0, index=cases.index)
    out["starter_flag"] = starter
    out["abs_spread_x_starter"] = out["abs_closing_spread"] * starter
    out["team_pull_speed"] = cases["team"].map(
        lambda t: float(pull_speed.get(t, league_pull_speed))).astype(float)
    return out[B1_FEATURE_COLUMNS].astype(float)


# -----------------------------------------------------------------------------------------------
# The B1 model: Arm S's protocol, extended matrix

class ArmSB1Model:
    """Ridge on realized_ratio over build_features_b1 -- same alpha, scaling, clip, and
    mean-ratio fallback as arm_s.ArmSModel; only the feature matrix changes."""

    def __init__(self, alpha: float = arm_s.RIDGE_ALPHA):
        self.alpha = float(alpha)
        self._pipeline = None
        self._mean_ratio: float | None = None
        self.train_seasons_: list = []
        self.n_train_: int = 0
        self.used_fallback_: bool = False
        self.pull_speed_: dict = {}
        self.league_pull_speed_: float = 0.0

    def fit(self, cases: pd.DataFrame, pull_speed: Mapping[str, float], *,
            league_pull_speed: float = 0.0) -> "ArmSB1Model":
        if cases is None or not len(cases):
            raise ValueError("ArmSB1Model.fit: no training cases")
        y = pd.to_numeric(cases["realized_ratio"], errors="coerce").to_numpy(dtype=float)
        ok = np.isfinite(y)
        if ok.sum() == 0:
            raise ValueError("ArmSB1Model.fit: no finite realized_ratio in training cases")
        self.train_seasons_ = sorted(int(s) for s in pd.unique(cases["season_start"]))
        self.n_train_ = int(ok.sum())
        self._mean_ratio = float(np.mean(y[ok]))
        self.pull_speed_ = dict(pull_speed)
        self.league_pull_speed_ = float(league_pull_speed)
        if self.n_train_ < arm_s.MIN_TRAIN_ROWS:
            self._pipeline = None
            self.used_fallback_ = True
            return self
        from sklearn.linear_model import Ridge
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        X = build_features_b1(cases, self.pull_speed_,
                              league_pull_speed=self.league_pull_speed_).to_numpy(dtype=float)[ok]
        self._pipeline = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha))
        self._pipeline.fit(X, y[ok])
        self.used_fallback_ = False
        return self

    def predict_ratio(self, cases: pd.DataFrame) -> np.ndarray:
        if self._mean_ratio is None:
            raise RuntimeError("ArmSB1Model.predict_ratio called before fit")
        if cases is None or not len(cases):
            return np.array([], dtype=float)
        if self._pipeline is None:
            raw = np.full(len(cases), self._mean_ratio, dtype=float)
        else:
            raw = self._pipeline.predict(build_features_b1(
                cases, self.pull_speed_,
                league_pull_speed=self.league_pull_speed_).to_numpy(dtype=float))
        return np.clip(np.asarray(raw, dtype=float), arm_s.RATIO_CLIP[0], arm_s.RATIO_CLIP[1])


def walk_forward_b1(cases: pd.DataFrame, box: pd.DataFrame, spreads: pd.DataFrame, *,
                    dev_seasons=DEV_SEASONS, test_seasons=TEST_SEASONS) -> pd.DataFrame:
    """The B1 arm on the registered split. `cases` must already be spread-attached
    (attach_closing_spread); pull-speed effects are derived here from `box` + `spreads`
    restricted to the dev seasons, so no test-season information enters any feature.

    -> predictions frame (PREDICTION_COLUMNS_B1) with attrs fit_seasons / n_train /
    used_fallback / pull_speed_diag. Raises ValueError when there is nothing to fit or
    grade: fail closed, like the base harness."""
    if cases is None or not len(cases):
        raise ValueError("walk_forward_b1: no cases at all")
    matched = cases[pd.to_numeric(cases["closing_spread"], errors="coerce").notna()].copy()
    train = arm_s.dev_fit_frame(matched, dev_seasons)
    if not len(train):
        raise ValueError(f"walk_forward_b1: no dev-season cases to fit (dev={list(dev_seasons)})")
    test = matched[matched["season_start"].isin(list(test_seasons))].copy()
    if not len(test):
        raise ValueError(f"walk_forward_b1: no test-season cases to grade (test={list(test_seasons)})")
    effects, diag = team_pull_speed(box, spreads, dev_seasons=dev_seasons)
    league = float(diag["league_slope"].iloc[0]) if len(diag) else 0.0
    model = ArmSB1Model().fit(train, effects, league_pull_speed=league)
    pred_ratio = model.predict_ratio(test)
    baseline = pd.to_numeric(test["baseline_minutes"], errors="coerce").to_numpy(dtype=float)
    out = pd.DataFrame({
        "case_id": test["case_id"].values,
        "player": test["player"].values,
        "team": test["team"].values,
        "game_date": test["game_date"].values,
        "season_start": test["season_start"].astype(int).values,
        "season_label": test["season_label"].values,
        "case_class": test["case_class"].values,
        "games_missed": test["games_missed"].astype(int).values,
        "missed_band": test["missed_band"].values,
        "is_injury_return": test["is_injury_return"].astype(bool).values,
        "is_game_two": test["is_game_two"].astype(bool).values,
        "baseline_minutes": baseline,
        "realized_minutes": pd.to_numeric(test["realized_minutes"], errors="coerce").to_numpy(dtype=float),
        "realized_ratio": pd.to_numeric(test["realized_ratio"], errors="coerce").to_numpy(dtype=float),
        "naive_minutes": baseline,
        "pred_ratio": pred_ratio,
        "pred_minutes": pred_ratio * baseline,
        "closing_spread": pd.to_numeric(test["closing_spread"], errors="coerce").to_numpy(dtype=float),
        "abs_closing_spread": pd.to_numeric(test["closing_spread"], errors="coerce").abs().to_numpy(dtype=float),
        "team_pull_speed": test["team"].map(lambda t: float(effects.get(t, league))).to_numpy(dtype=float),
    }, columns=PREDICTION_COLUMNS_B1)
    out.attrs["fit_seasons"] = model.train_seasons_
    out.attrs["n_train"] = model.n_train_
    out.attrs["used_fallback"] = model.used_fallback_
    out.attrs["pull_speed_diag"] = diag
    return out.reset_index(drop=True)


# -----------------------------------------------------------------------------------------------
# Grading: before vs after, Argon's auto-kill bars

def _mae(frame: pd.DataFrame, col: str) -> float | None:
    if frame is None or not len(frame):
        return None
    realized = pd.to_numeric(frame["realized_minutes"], errors="coerce").to_numpy(dtype=float)
    pred = pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(realized) & np.isfinite(pred)
    return float(np.mean(np.abs(realized[ok] - pred[ok]))) if ok.any() else None


def _cut_stats(joined: pd.DataFrame) -> dict:
    return {"n": int(len(joined)),
            "before_mae": _mae(joined, "pred_minutes_before"),
            "after_mae": _mae(joined, "pred_minutes_after")}


def _with_improvement(stats: dict) -> dict:
    b, a = stats["before_mae"], stats["after_mae"]
    stats["improvement"] = (b - a) if (b is not None and a is not None) else None
    return stats


def grade_b1(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    """Before (unmodified Arm S) vs after (B1) predictions on the SAME test cases ->
    the B1 grading dict. Primary population = non-game-2 cases (the harness's primary
    population); cuts on |closing_spread| at SPREAD_CUT. Verdict PASS only when both
    auto-kill bars clear; a cut with no cases fails closed (improvement None -> kill)."""
    # Case facts (realized minutes, game-2 flag, |spread|) come from the AFTER frame:
    # the unmodified harness's prediction frame carries no spread columns.
    b = before[["case_id", "pred_minutes"]].rename(
        columns={"pred_minutes": "pred_minutes_before"})
    a = after[["case_id", "realized_minutes", "is_game_two", "abs_closing_spread",
               "pred_minutes"]].rename(columns={"pred_minutes": "pred_minutes_after"})
    joined = a.merge(b, on="case_id", how="inner")
    primary = joined[~joined["is_game_two"].astype(bool)]
    cuts = {
        "full": _with_improvement(_cut_stats(primary)),
        "abs_spread_lt_9_5": _with_improvement(
            _cut_stats(primary[primary["abs_closing_spread"] < SPREAD_CUT])),
        "abs_spread_ge_9_5": _with_improvement(
            _cut_stats(primary[primary["abs_closing_spread"] >= SPREAD_CUT])),
        "full_incl_game_two": _with_improvement(_cut_stats(joined)),
    }
    overall, high = cuts["full"]["improvement"], cuts["abs_spread_ge_9_5"]["improvement"]
    bars = {
        "overall_mae_improvement": {
            "required_minutes": BAR_OVERALL_MINUTES, "observed": overall,
            "pass": bool(overall is not None and overall >= BAR_OVERALL_MINUTES - 1e-9)},
        "high_spread_mae_improvement": {
            "required_minutes": BAR_HIGH_SPREAD_MINUTES, "observed": high,
            "pass": bool(high is not None and high >= BAR_HIGH_SPREAD_MINUTES - 1e-9)},
    }
    return {
        "cuts": cuts,
        "bars": bars,
        "verdict": "PASS" if all(x["pass"] for x in bars.values()) else "AUTO-KILLED",
        "spread_cut": SPREAD_CUT,
        "n_joined": int(len(joined)),
        "fit_seasons": after.attrs.get("fit_seasons"),
        "n_train": after.attrs.get("n_train"),
        "used_fallback": after.attrs.get("used_fallback"),
    }


def _fmt(value, digits=3):
    return "n/a" if value is None or (isinstance(value, float) and not np.isfinite(value)) \
        else f"{value:.{digits}f}"


def render_markdown_b1(report: dict) -> str:
    """B1 grading dict -> markdown sheet (verdict first, Minto order)."""
    bars = report["bars"]
    lines = [
        "# Arm S + B1 blowout-minutes experiment -- grading",
        "",
        f"**Verdict: {report['verdict']}** (Argon B1 auto-kill bars, Nov 11, 2026 consult)",
        "",
        f"Fit seasons (season_start): {report.get('fit_seasons')} "
        f"(n_train={report.get('n_train')}, mean-ratio fallback={report.get('used_fallback')}); "
        f"joined test cases n={report['n_joined']}; primary population = non-game-2 cases.",
        "",
        "## Auto-kill bars",
        "",
        "| Bar | Required improvement | Observed | Result |",
        "|---|---|---|---|",
        f"| Overall band-mapped MAE | >= {bars['overall_mae_improvement']['required_minutes']:.2f} min "
        f"| {_fmt(bars['overall_mae_improvement']['observed'], 3)} min "
        f"| {'PASS' if bars['overall_mae_improvement']['pass'] else 'FAIL'} |",
        f"| |spread| >= {report['spread_cut']} MAE | >= {bars['high_spread_mae_improvement']['required_minutes']:.2f} min "
        f"| {_fmt(bars['high_spread_mae_improvement']['observed'], 3)} min "
        f"| {'PASS' if bars['high_spread_mae_improvement']['pass'] else 'FAIL'} |",
        "",
        "## Before vs after (band-mapped MAE, minutes)",
        "",
        "| Cut | n | Before (Arm S) | After (Arm S + B1) | Improvement |",
        "|---|---|---|---|---|",
    ]
    labels = {"full": "Full sample (primary)", "abs_spread_lt_9_5": "|spread| < 9.5",
              "abs_spread_ge_9_5": "|spread| >= 9.5",
              "full_incl_game_two": "Full incl. game-2 (supplementary)"}
    for key, label in labels.items():
        c = report["cuts"][key]
        lines.append(f"| {label} | {c['n']} | {_fmt(c['before_mae'], 3)} | "
                     f"{_fmt(c['after_mae'], 3)} | {_fmt(c['improvement'], 3)} |")
    lines.append("")
    return "\n".join(lines) + "\n"
