"""nfl.props.history_join -- score the NFL prop models against HISTORICAL BettingPros per-book prop lines.

    cons = mlb_core.odds.bettingpros_loader.build_market_consensus("nfl", ...)      # or read the parquet from
                                                                                     # scripts/join_bettingpros_history.py
    res, _ = nfl.models.walkforward.eval_season(df, stat, season)                    # row frame, index = df index
    joined, cov = join_consensus(res, df, cons, resolver, stat)
    scores = score(joined)

IDENTITY IS FAIL-CLOSED. Each consensus row's player string (+ BettingPros team hint + the game's ET date) is resolved to a
gsis_id by nfl.props.resolve.PlayerResolver -- exact normalised name or reviewed alias only, narrowed by that week's
roster/team evidence; a namesake that is still ambiguous, an unknown name, or two consensus rows that resolve to the same
(player, game date) are DROPPED and COUNTED, never guessed. A BettingPros team spelling the resolver does not know is not
passed as a hint (the name + the game-date roster still decide); a hint that contradicts the only candidate drops the row.

SELECTION SAFETY. (1) The consensus is the LAST quote BettingPros held for the day (close-ish; see bettingpros_loader);
rows whose quotes were last updated after the scheduled start are dropped (`postgame_quote`). (2) The model rows come from
walkforward.eval_season, which trains on seasons < the test season only and scores features built strictly before kickoff
(nfl/features/props_features.py leakage contract); upcoming/unlabelled rows never reach `res`. (3) The model population is
conditional on the player playing offense; a prop on a player with no scored row (inactive, ineligible by trailing usage,
rookie) is counted as `no_model_row`, not imputed.

The model is re-scored AT THE MARKET'S OWN LINE: P(over) from nfl.models.props.p_over with the model's own dispersion.
Integer lines can push: pushes are removed from both sides (the model's P(over) is renormalised to over/(over+under)) and
counted under `push`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mlb_core.odds import model_vs_market as MVM
from nfl.models import props as P
from nfl.props.resolve import RESOLVED, PlayerResolver, team_code

# bettingpros_props.SPORTS['NFL'] stat label -> nfl.models.props.SPECS stat
_STAT_MAP_ALL = {"pass_yds": "pass_yds", "rush_yds": "rush_yds", "rec_yds": "rec_yds", "receptions": "receptions",
                 "pass_tds": "pass_tds", "anytime_td": "anytime_td",
                 "pass_att": "pass_attempts", "pass_cmp": "completions", "rush_att": "rush_attempts"}
STAT_MAP = {k: v for k, v in _STAT_MAP_ALL.items() if v in P.SPECS}      # only stats the models actually define


def _resolve_rows(cons: pd.DataFrame, resolver: PlayerResolver):
    """-> (gsis list aligned to cons, counts of unresolved / ambiguous). One resolve per distinct (player, team, date)."""
    cache: dict = {}
    ids, bad = [], {"unresolved": 0, "ambiguous": 0}
    for name, team, gd in zip(cons["player"], cons["team"], cons["game_date"]):
        k = (name, team, gd)
        if k not in cache:
            hint = team if isinstance(team, str) and team_code(team) else None
            cache[k] = resolver.resolve(str(name), game_date=gd, team=hint)
        r = cache[k]
        if r.status == RESOLVED:
            ids.append(r.gsis_id)
        else:
            ids.append(None)
            bad["ambiguous" if r.status == "ambiguous" else "unresolved"] += 1
    return ids, bad


def join_consensus(res: pd.DataFrame, df: pd.DataFrame, consensus: pd.DataFrame, resolver: PlayerResolver, stat: str,
                   min_books: int = 3):
    """Join one stat's walk-forward result frame to the BettingPros consensus.

    res       walkforward.eval_season output (index = rows of `df`; needs y, mu_model, attrs['dispersion']['model'])
    df        the player-week feature frame (needs player_id (gsis), gameday, game_id)
    returns   (joined frame, coverage dict). `joined` has one row per scored prop: gsis_id, game_id, game_date, line, y,
              over (1/0), mu_model, p_model (at the market line, push-renormalised), p_market (p_over_cons), n_books.
    Coverage reasons (each consensus row counted once, first stage that lost it): low_books, postgame_quote, unresolved,
    ambiguous, duplicate, no_model_row, push."""
    cons, n_in, dropped = MVM.screen_consensus(consensus, {k: v for k, v in STAT_MAP.items() if v == stat}, min_books)
    dropped = dict(dropped, unresolved=0, ambiguous=0, duplicate=0, no_model_row=0, push=0)
    empty = pd.DataFrame(columns=["gsis_id", "game_id", "game_date", "line", "y", "over", "mu_model", "p_model",
                                  "p_market", "n_books"])
    if len(cons) == 0 or res is None or len(res) == 0:
        dropped["no_model_row"] += int(len(cons))
        return empty, MVM.coverage(n_in, 0, dropped)
    ids, bad = _resolve_rows(cons, resolver)
    dropped["unresolved"], dropped["ambiguous"] = bad["unresolved"], bad["ambiguous"]
    cons = cons.assign(gsis_id=ids)
    cons = cons[cons["gsis_id"].notna()]
    dup = cons.duplicated(["gsis_id", "game_date"], keep=False)
    dropped["duplicate"] = int(dup.sum())
    cons = cons[~dup]

    mdl = pd.DataFrame({"gsis_id": df.loc[res.index, "player_id"].to_numpy(),
                        "game_date": df.loc[res.index, "gameday"].astype(str).str[:10].to_numpy(),
                        "game_id": df.loc[res.index, "game_id"].to_numpy(),
                        "y": res["y"].to_numpy(), "mu_model": res["mu_model"].to_numpy()})
    m = cons.merge(mdl, on=["gsis_id", "game_date"], how="left")
    miss = m["mu_model"].isna()
    dropped["no_model_row"] = int(miss.sum())
    m = m[~miss].copy()
    if len(m):
        disp = res.attrs["dispersion"]["model"]
        over, under, push = P.p_over(stat, m["mu_model"].to_numpy(), m["line"].to_numpy(dtype=float), disp)
        y, line = m["y"].to_numpy(dtype=float), m["line"].to_numpy(dtype=float)
        is_push = np.isclose(y, line)
        dropped["push"] = int(is_push.sum())
        m = m[~is_push].copy()
        over, under = over[~is_push], under[~is_push]
        m["p_model"] = over / np.clip(over + under, 1e-9, None)
        m["over"] = (m["y"].to_numpy(dtype=float) > m["line"].to_numpy(dtype=float)).astype(float)
    else:
        m["p_model"], m["over"] = np.nan, np.nan
    m = m.rename(columns={"p_over_cons": "p_market"})
    joined = m[["gsis_id", "game_id", "game_date", "line", "y", "over", "mu_model", "p_model", "p_market",
                "n_books"]].reset_index(drop=True)
    cov = MVM.coverage(n_in, len(joined), dropped, stat=stat)
    return joined, cov


def score(joined: pd.DataFrame) -> dict:
    """Brier / log-loss for model vs market on the joined rows + blend coefficient c (cluster = game)."""
    if len(joined) == 0:
        return MVM.score_model_vs_market([], [], [], [])
    return MVM.score_model_vs_market(joined["over"], joined["p_model"], joined["p_market"], joined["game_id"])
