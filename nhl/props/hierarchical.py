"""nhl.props.hierarchical -- coherent NHL prop-projection primitives for the daily board (ev.daily_projections).

The board used to project goals, assists and points as three independent processes and saves from the goalie's own
trailing average. This module supplies the pieces that tie the markets together. Everything here is PURE (numpy / scipy,
no I/O, no clock) and every function takes rows that the caller has already cut to games strictly before the game date
(`before`), so leakage is a property of the call site, enforced by tests/test_nhl_props_hierarchical.py.

  goals       mu = 0.55 * trailing goals + 0.45 * mu_sog * EB shooting %   (shooting % shrunk to a position prior,
              K = 85 shots over the last 30 games: a 50% heater regresses to the shot volume times a sane percentage)
  assists     EWM / TOI-rate estimate (half-life 10: assist counts are the noisiest of the three)
  points      mu_points = mu_goals + mu_assists EXACTLY (one point per goal at most, so the markets cannot disagree);
              the tails come from a NegBin whose spread is the league's own points dispersion
  pp_points   power-play goals + assists (nhl.data.powerplay), shrunk to a position prior: the counts are sparse
  blocked     trailing blocks per TOI x the opponent's shot-generation pace
  saves       EB save % x expected shots against (team shots against blended with the opponent's shot pace) x the
              share of the game a starter is in net, priced with a two-regime mixture (full game / early hook) whose
              TOTAL spread is the league's measured goalie-saves cv (floored at 0.24), so the hook only reshapes the
              left tail instead of inventing extra variance
  sog         unchanged estimator + a damped share of the shots / assists of a top-line teammate who is off the roster

NOT modelled, because the data does not exist in this repo: power-play / even-strength TOI splits (the boxscore has only
total TOI), shot attempts (Corsi / Fenwick) per player in the live path, confirmed starting goalies, injuries (the
roster layer only knows who is on or off a roster, not who is hurt). The constants below that come from the design spec
rather than from a fit are labelled as such; the replay evidence is docs/analysis/nhl_hierarchical_engine_2026-11-01.md.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

# ----------------------------------------------------------------------------------------------------- constants
SH_PRIOR_K = 85.0                       # shots of evidence behind the positional shooting-% prior (spec)
SH_PRIOR = {"F": 0.112, "D": 0.055}     # prior shooting % for forwards / defensemen (spec)
SH_WINDOW = 30                          # games behind the shooting-% estimate
GOALS_EWM_WEIGHT = 0.55                 # weight of the trailing goals estimate vs the shots x finishing estimate (spec)
HALFLIFE_ASSISTS = 10.0                 # games (spec)

PACE_WINDOW = 15                        # team games behind a shot-pace estimate
PACE_CLIP = (0.85, 1.15)
PACE_SHRINK_K = 30.0                    # team-games of evidence behind a pace ratio: the factor is worth ~0 on blocks, so heavily shrunk

VACATE_DAMP = 0.50                      # share of a missing player's production handed to teammates (spec)
VACATE_TOI_MIN = {"F": 16.0, "D": 18.5}  # a top-6 forward / top-4 defenseman (spec): average TOI over the last 10 games
VACATE_RECIPIENTS = {"F": 9, "D": 4}
VACATE_WINDOW = 10

SV_PRIOR_K = 400.0                      # shots faced behind the league save % prior (spec)
SV_PRIOR = 0.903                        # fallback league save %: the live prior is the trailing league rate (GameLogs.league_sv)
SV_WINDOW = 30                          # appearances behind a goalie's save %
CV_FLOOR = 0.24                         # floor on the total coefficient of variation of a goalie's saves (spec)
# Measured on the 2021-2023 regular seasons / playoffs (the replay's selection seasons; docs/analysis/
# nhl_hierarchical_engine_2026-11-01.md). The spec's illustrative figures were 0.965 / 8% / 16 saves / sd 4.5.
START_SHARE = 0.970                     # a starter faces this share of his team's shots against (2021-25: 0.967-0.976)
P_HOOK = 0.066                          # P(a starter plays < 55 minutes): 7.2% / 6.3% / 6.2% in 2021 / 22 / 23
HOOK_MEAN, HOOK_SD = 13.8, 7.3          # saves of such a starter (pulled, injured, late pulls are all in it)

# Power-play points per game of rotation-level skaters (average TOI >= 14), measured on 2022-2023.
PP_PRIOR = {"F": 0.180, "D": 0.077}
PP_PRIOR_K = 6.0                        # games of evidence behind the PP prior (chosen on 2023: log score best of 6 / 12 / 24 / 48)


# ---------------------------------------------------------------------------------------------------- row helpers
def before(rows: list, as_of: str | None) -> list:
    """Rows of games strictly before `as_of` (YYYY-MM-DD). A row with no `_date` cannot be placed in time and is kept."""
    if not as_of:
        return rows
    return [r for r in rows if r.get("_date") is None or str(r.get("_date"))[:10] < as_of]


def pos_group(position) -> str:
    """'D' for defensemen, 'F' for every other skater (C / L / R / W / unknown)."""
    return "D" if str(position or "").strip().upper().startswith("D") else "F"


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def col_values(rows: list, col: str) -> list:
    """The finite numeric values of `col` over rows (None rows dropped), chronological."""
    return [v for v in (_num(r.get(col)) for r in rows) if v is not None]


def ewm(vals, halflife: float) -> float | None:
    n = len(vals)
    if n == 0:
        return None
    w = 0.5 ** ((n - 1 - np.arange(n)) / max(halflife, 1e-6))
    return float(np.sum(w * np.asarray(vals, dtype=float)) / np.sum(w))


def shrink(rate: float, n_eff: float, prior: float, k: float) -> float:
    """Empirical-Bayes shrinkage of a per-game rate backed by `n_eff` games toward `prior` with `k` games of evidence."""
    return (n_eff * rate + k * prior) / (n_eff + k)


# --------------------------------------------------------------------------------------------- skater components
def eb_shooting_pct(goals: float, sog: float, position, k: float = SH_PRIOR_K) -> float:
    """(goals + k * prior) / (sog + k): the player's shooting % shrunk to the forward / defense prior."""
    prior = SH_PRIOR[pos_group(position)]
    return (goals + k * prior) / (sog + k)


def shooting_pct_from_rows(rows: list, position=None) -> float | None:
    """EB shooting % over the last SH_WINDOW rows that carry both goals and sog; None when none do."""
    g = s = 0.0
    n = 0
    for r in rows[-SH_WINDOW:]:
        gv, sv = _num(r.get("goals")), _num(r.get("sog"))
        if gv is None or sv is None:
            continue
        g, s, n = g + gv, s + sv, n + 1
    if n == 0:
        return None
    return eb_shooting_pct(g, s, position if position is not None else rows[-1].get("position"))


def goals_mu(trailing_goals: float, mu_sog: float, sh_eb: float, w: float = GOALS_EWM_WEIGHT) -> float:
    """Goals before the opponent factor: w * trailing goals + (1 - w) * mu_sog * EB shooting %."""
    return w * trailing_goals + (1.0 - w) * mu_sog * sh_eb


def points_mu(mu_goals: float, mu_assists: float) -> float:
    """The conservation law: a skater's expected points are exactly his expected goals plus assists."""
    return mu_goals + mu_assists


def pp_prior_rate(rate: float, n_eff: float, position) -> float:
    return shrink(rate, n_eff, PP_PRIOR[pos_group(position)], PP_PRIOR_K)


# ------------------------------------------------------------------------------------------- team shot pace
def team_pace(team_rows: dict, as_of: str | None = None, window: int = PACE_WINDOW, canon=lambda x: x) -> dict:
    """{team: {sog_for, sog_against, goals_against, pim, sa_ewm, n}} over each team's last `window` games before `as_of`,
    plus the league means under the key '_league'. `team_rows` = {team_abbr: [team-game dicts with sog, shots_against,
    goals_against, pim, _date]} chronological (cockpit_form `idx["team"]`). Teams with no usable games are absent."""
    out: dict = {}
    for ab, rows in (team_rows or {}).items():
        rs = before(rows, as_of)[-window:]
        sf, sa = col_values(rs, "sog"), col_values(rs, "shots_against")
        if not sf or not sa:
            continue
        ga, pim = col_values(rs, "goals_against"), col_values(rs, "pim")
        all_sa = col_values(before(rows, as_of)[-60:], "shots_against")
        key = canon(ab)
        if key is None:
            continue
        out[key] = {"sog_for": float(np.mean(sf)), "sog_against": float(np.mean(sa)),
                    "goals_against": float(np.mean(ga)) if ga else None,
                    "pim": float(np.mean(pim)) if pim else None,
                    "sa_ewm": ewm(all_sa, 8.0), "n": min(len(sf), len(sa))}
    if out:
        out["_league"] = {k: float(np.mean([v[k] for v in out.values() if v.get(k) is not None]))
                          for k in ("sog_for", "sog_against")}
    return out


def pace_factor(team_sog: float | None, league_sog: float | None, n: int = PACE_WINDOW,
                k: float = PACE_SHRINK_K, clip: tuple = PACE_CLIP) -> float:
    """Opponent shot-generation factor = team shots per game / league shots per game, shrunk toward 1 by n / (n + k)
    and clipped to [0.85, 1.15]. 1.0 when either input is missing."""
    if team_sog is None or league_sog is None or league_sog <= 0:
        return 1.0
    ratio = 1.0 + (team_sog / league_sog - 1.0) * (n / (n + k))
    return float(min(max(ratio, clip[0]), clip[1]))


# ------------------------------------------------------------------------------------ absent-teammate opportunity
def vacated_opportunity(team_players: list, on_roster, extra_absent=(), as_of: str | None = None) -> dict:
    """Production a team's recent regulars leave behind tonight, handed (damped) to the teammates who replace them.

    `team_players` = [(player_norm, position, rows)] for one team (rows chronological, any date; cut here to < as_of).
    `on_roster(player_norm) -> bool | None`: True the roster carries him for this team, False it does not (traded,
    released, sent down), None unknown (a partial roster pull proves nothing: nobody is treated as absent then).
    `extra_absent` = names to treat as absent whatever the roster says (the hook for a future injury feed).

    A player counts as absent when he averaged >= 16.0 TOI (forward) / 18.5 (defenseman) over the team's last 10 games
    and is off the roster. His average shots / assists / TOI over the games he played, times the share of those 10 games
    he played (his teammates' trailing numbers already contain the games he was in), times 0.5, is split among the same
    position group's active top-9 forwards / top-4 defensemen in proportion to TOI. Returns
    {player_norm: {"sog": x, "assists": y, "toi": z}} for the recipients only ({} when nobody is absent)."""
    cut = [(nm, pos_group(pos), before(rows, as_of)) for nm, pos, rows in team_players]
    games: dict = {}
    for _, _, rs in cut:
        for r in rs:
            gid, d = r.get("game_id"), str(r.get("_date") or "")[:10]
            if gid is not None and d:
                games[gid] = d
    last = [g for g, _ in sorted(games.items(), key=lambda kv: kv[1])[-VACATE_WINDOW:]]
    if len(last) < 5:
        return {}
    last_set = set(last)
    stats = []
    for nm, grp, rs in cut:
        played = [r for r in rs if r.get("game_id") in last_set and (_num(r.get("toi_min")) or 0.0) > 0]
        if not played:
            continue
        stats.append({"name": nm, "grp": grp, "f": len(played) / len(last),
                      "in_last": any(r.get("game_id") == last[-1] for r in played),
                      "toi": float(np.mean(col_values(played, "toi_min"))),
                      "sog": float(np.mean(col_values(played, "sog") or [0.0])),
                      "ast": float(np.mean(col_values(played, "assists") or [0.0]))})
    absent_names = set(extra_absent)
    vacated = {"F": [0.0, 0.0, 0.0], "D": [0.0, 0.0, 0.0]}
    active = {"F": [], "D": []}
    for s in stats:
        state = None if s["name"] in absent_names else on_roster(s["name"])
        # a player who dressed in the team's latest game is evidently active, whatever a name-matched roster says
        is_absent = s["name"] in absent_names or (state is False and not s["in_last"])
        if is_absent:
            if s["toi"] >= VACATE_TOI_MIN[s["grp"]]:
                v = vacated[s["grp"]]
                v[0] += VACATE_DAMP * s["f"] * s["sog"]
                v[1] += VACATE_DAMP * s["f"] * s["ast"]
                v[2] += VACATE_DAMP * s["f"] * s["toi"]
        elif s["f"] >= 0.5 and (state is True or (state is False and s["in_last"])):
            active[s["grp"]].append(s)
    out: dict = {}
    for grp in ("F", "D"):
        if not any(vacated[grp]) or not active[grp]:
            continue
        top = sorted(active[grp], key=lambda s: -s["toi"])[:VACATE_RECIPIENTS[grp]]
        tot = sum(s["toi"] for s in top)
        for s in top:
            share = s["toi"] / tot
            out[s["name"]] = {"sog": vacated[grp][0] * share, "assists": vacated[grp][1] * share,
                              "toi": vacated[grp][2] * share}
    return out


# --------------------------------------------------------------------------------------------------- goalie saves
def eb_save_pct(rows: list, k: float = SV_PRIOR_K, prior: float = SV_PRIOR, window: int = SV_WINDOW) -> float:
    """(saves + k * prior) / (shots against + k) over the goalie's last `window` appearances (rows with both numbers)."""
    sv = sa = 0.0
    for r in rows[-window:]:
        s, a = _num(r.get("saves")), _num(r.get("shots_against"))
        if s is None or a is None:
            continue
        sv, sa = sv + s, sa + a
    return (sv + k * prior) / (sa + k)


def expected_shots_against(team_sa: float | None, opp_sog_for: float | None, own_sa: float | None) -> float | None:
    """Shots the goalie's team faces tonight: the mean of the team's trailing shots against and the opponent's trailing
    shots for. With either team term missing: the other one; with both missing: the goalie's own trailing shots faced."""
    have = [x for x in (team_sa, opp_sog_for) if x is not None]
    if have:
        return float(np.mean(have))
    return own_sa


class SavesMixture:
    """Two-regime distribution of a starter's saves: a full game, Normal(mu_full, sd_full), with probability 1 - p, and an
    early hook, Normal(hook_mean, hook_sd), with probability p. `fit` solves mu_full / sd_full so that the mixture's mean is
    exactly `mu` and its standard deviation is `cv * mu` (the league's measured spread): the hook then shapes the left
    tail without adding variance. Lines are x.5, so P(over L) = 1 - CDF(L) is already continuity-corrected."""

    def __init__(self, mu_full: float, sd_full: float, p: float = P_HOOK, hook_mean: float = HOOK_MEAN,
                 hook_sd: float = HOOK_SD):
        self.mu_full, self.sd_full, self.p = mu_full, max(sd_full, 1e-6), p
        self.hook_mean, self.hook_sd = hook_mean, hook_sd

    @classmethod
    def fit(cls, mu: float, cv: float, p: float = P_HOOK, hook_mean: float = HOOK_MEAN,
            hook_sd: float = HOOK_SD) -> "SavesMixture":
        cv = max(cv, CV_FLOOR)
        mu_full = (mu - p * hook_mean) / (1.0 - p)
        between = p * (1.0 - p) * (mu_full - hook_mean) ** 2
        var_full = (cv * mu) ** 2 - p * hook_sd ** 2 - between
        var_full = var_full / (1.0 - p)
        floor = (0.10 * mu_full) ** 2                       # a full game is never less noisy than shots x save %
        return cls(mu_full, math.sqrt(max(var_full, floor)), p, hook_mean, hook_sd)

    @property
    def mean(self) -> float:
        return (1.0 - self.p) * self.mu_full + self.p * self.hook_mean

    def cdf(self, x: float) -> float:
        return float((1.0 - self.p) * norm.cdf(x, self.mu_full, self.sd_full)
                     + self.p * norm.cdf(x, self.hook_mean, self.hook_sd))

    def p_over(self, line: float) -> float:
        return 1.0 - self.cdf(line)

    def ppf(self, q: float) -> float:
        lo, hi = 0.0, self.mu_full + 8.0 * self.sd_full
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self.cdf(mid) < q:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)
