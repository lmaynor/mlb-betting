"""As-of inputs of the simulator: team rating deltas and player usage shares.

Both are computed only from games strictly BEFORE the game being simulated (ordered by season, week), never from its
outcome. The roster of a simulated game (which skill players are active) is an explicit input -- the same information as the
pre-game inactive list; usage within it comes from history.

Team ratings (offence and defence, per metric): exponentially weighted (half-life `HALF_LIFE_GAMES` team games, carried over
the off-season at `OFFSEASON_CARRY`) numerator / denominator sums per team, shrunk toward the AS-OF league rate with a
pseudo-count k estimated empirically from the between-team variance of team-season rates (method of moments, training
seasons only):

    sack, interception, completion   logit scale (shift = logit(team) - logit(fitted league))
    yards per completion, per carry, seconds per play   ratio scale
    pass-rate-over-expected          Newton step on the call model's residuals

Player usage: EWM target / carry / attempt shares among the ACTIVE skill players, shrunk toward positional priors for players
with little history, plus shrunk catch-rate and yards-per-reception / per-carry factors, each re-centred so the team-level
numbers keep coming from the team ratings and the players only redistribute them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from nfl.props import env_adjust as EA
from nfl.sim import engine as E
from nfl.sim import fit as F

HALF_LIFE_GAMES = 8.0
OFFSEASON_CARRY = 0.7
PLAYER_HALF_LIFE = 6.0
ROLE_TARGETS = 4.0
ROLE_CARRIES = 8.0
QB_DECAY = 0.5 ** (1.0 / 3.0)
QB_FLOOR = 0.5 ** 3 * 0.9        # on the CUBED scale: a QB under ~0.97 x half the leader's per-game attempts is a backup
LOGIT_METRICS = ("sack", "int", "cmp")
RATIO_METRICS = ("ypcmp", "ypr", "pace")
METRICS = LOGIT_METRICS + RATIO_METRICS + ("proe",)
K_BOUNDS = (10.0, 20000.0)


def _lg(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


# -- team-game table ---------------------------------------------------------------------------------------------------
def team_game_table(plays: pd.DataFrame, P: "F.SimParams") -> tuple:
    """(off, def) frames indexed by (game_id, team) with `<metric>_n` / `<metric>_d` columns plus season / week."""
    pl = plays.copy()
    ph = F.sigmoid(E.call_logit(P, pl["down"], pl["ytg"], pl["yl"], pl["tb"], pl["sb"]))
    isp = pl["is_pass"].to_numpy() == 1
    sack = pl["sack"].to_numpy() == 1
    thr = isp & ~sack
    cmpl = pl["complete"].to_numpy() == 1
    pace_ok = (pl["hurry"].to_numpy() == 0) & pl["elapsed"].notna().to_numpy() & (pl["last_in_drive"].to_numpy() == False)   # noqa: E712
    el = pl["elapsed"].fillna(0.0).to_numpy()
    yds = pl["yards"].to_numpy()
    cols = {
        "sack_n": (isp & sack).astype(float), "sack_d": isp.astype(float),
        "int_n": (thr & (pl["intc"].to_numpy() == 1)).astype(float), "int_d": thr.astype(float),
        "cmp_n": (thr & cmpl).astype(float), "cmp_d": thr.astype(float),
        "ypcmp_n": np.where(isp & cmpl, yds, 0.0), "ypcmp_d": (isp & cmpl).astype(float),
        "ypr_n": np.where(~isp, yds, 0.0), "ypr_d": (~isp).astype(float),
        "pace_n": np.where(pace_ok, el, 0.0), "pace_d": pace_ok.astype(float),
        "proe_n": pl["is_pass"].to_numpy() - ph, "proe_d": ph * (1 - ph),
    }
    base = pl[["game_id", "season", "week"]].copy()
    for k, v in cols.items():
        base[k] = v
    out = []
    for side in ("posteam", "defteam"):
        b = base.copy()
        b["team"] = pl[side].map(EA.canon)
        g = b.groupby(["game_id", "team"], sort=False).agg(
            season=("season", "first"), week=("week", "first"), **{k: (k, "sum") for k in cols}).reset_index()
        out.append(g)
    return out[0], out[1]


def estimate_k(tab: pd.DataFrame, league: dict, plays: pd.DataFrame) -> dict:
    """Pseudo-counts by method of moments from team-season rates (see module docstring)."""
    ts = tab.groupby(["team", "season"]).sum(numeric_only=True)
    ks = {}
    pas = plays[plays["is_pass"] == 1]
    unit_var = {"ypcmp": float(pas.loc[pas["complete"] == 1, "yards"].var()), "ypr": float(plays.loc[plays["is_pass"] == 0, "yards"].var()),
                "pace": float(plays.loc[(plays["hurry"] == 0) & plays["elapsed"].notna(), "elapsed"].var())}
    for m in METRICS:
        n, d = ts[f"{m}_n"].to_numpy(), ts[f"{m}_d"].to_numpy()
        ok = d > 50
        if m == "proe":
            r = n[ok] / d[ok]
            tau2 = max(float(np.var(r, ddof=1) - np.mean(1.0 / d[ok])), 1e-5)
            ks[m] = 1.0 / tau2
            continue
        r = n[ok] / d[ok]
        if m in LOGIT_METRICS:
            p = league[m]
            sv = p * (1 - p)
            tau2 = max(float(np.var(r, ddof=1) - np.mean(sv / d[ok])), 1e-9)
            ks[m] = sv / tau2
        else:
            sv = unit_var[m]
            tau2 = max(float(np.var(r, ddof=1) - np.mean(sv / d[ok])), 1e-9)
            ks[m] = sv / tau2
    return {m: float(min(max(v, K_BOUNDS[0]), K_BOUNDS[1])) for m, v in ks.items()}


# -- as-of EWM states --------------------------------------------------------------------------------------------------
def _states(tab: pd.DataFrame) -> dict:
    """{(game_id, team): {metric_n / metric_d: decayed sum BEFORE the game}} with per-game decay and off-season carry."""
    lam = 0.5 ** (1.0 / HALF_LIFE_GAMES)
    cols = [c for c in tab.columns if c.endswith("_n") or c.endswith("_d")]
    out = {}
    for team, g in tab.sort_values(["season", "week"], kind="stable").groupby("team", sort=False):
        acc = {c: 0.0 for c in cols}
        last_season = None
        for r in g.itertuples(index=False):
            d = r._asdict()
            if last_season is not None and d["season"] != last_season:
                acc = {c: v * OFFSEASON_CARRY for c, v in acc.items()}
            last_season = d["season"]
            out[(d["game_id"], team)] = dict(acc)
            acc = {c: acc[c] * lam + float(d[c]) for c in cols}
    return out


def _league_states(tab: pd.DataFrame) -> dict:
    """{(season, week): decayed league sums before that week} (one decay step per week, off-season carry)."""
    lam = 0.5 ** (1.0 / HALF_LIFE_GAMES)
    cols = [c for c in tab.columns if c.endswith("_n") or c.endswith("_d")]
    wk = tab.groupby(["season", "week"])[cols].sum().sort_index()
    acc = {c: 0.0 for c in cols}
    last_season = None
    out = {}
    for (s, w), r in wk.iterrows():
        if last_season is not None and s != last_season:
            acc = {c: v * OFFSEASON_CARRY for c, v in acc.items()}
        last_season = s
        out[(int(s), int(w))] = dict(acc)
        acc = {c: acc[c] * lam + float(r[c]) for c in cols}
    return out


def league_targets(plays: pd.DataFrame, games: pd.DataFrame) -> dict:
    """{(season, week): (points, plays) per team-game of the league as-of that week} -- the same decay / off-season carry as
    the team states, from games strictly before the week. The simulator is calibrated to these each week so a league that
    scores more (or runs faster) than the training seasons is tracked from results already played."""
    lam = 0.5 ** (1.0 / HALF_LIFE_GAMES)
    gw = games.assign(pts=games["home_score_reg"] + games["away_score_reg"]).groupby(["season", "week"]).agg(
        pts=("pts", "sum"), ng=("game_id", "count"))
    pw = plays.groupby(["season", "week"]).size().rename("plays")
    tab = gw.join(pw, how="inner").sort_index()
    acc = {"pts": 0.0, "tg": 0.0, "plays": 0.0}
    last = None
    out = {}
    for (s, w), r in tab.iterrows():
        if last is not None and s != last:
            acc = {k: v * OFFSEASON_CARRY for k, v in acc.items()}
        last = s
        if acc["tg"] > 0:
            out[(int(s), int(w))] = (acc["pts"] / acc["tg"], acc["plays"] / acc["tg"])
        acc = {"pts": acc["pts"] * lam + float(r["pts"]), "tg": acc["tg"] * lam + 2.0 * float(r["ng"]),
               "plays": acc["plays"] * lam + float(r["plays"])}
    return out


@dataclass
class TeamRatings:
    P: "F.SimParams"
    off_state: dict
    def_state: dict
    league_state: dict
    k_off: dict
    k_def: dict
    game_week: dict = field(default_factory=dict)       # game_id -> (season, week)

    def league_adjust(self, season: int, week: int) -> dict:
        """The league-average OFFENCE adjustment as-of a week (what an average team gets relative to the fitted league)."""
        L = self.league_state.get((int(season), int(week)))
        off = dict(E.NEUTRAL_OFF)
        if L is None:
            return off
        lg = self.P.league
        for m in LOGIT_METRICS:
            if L[f"{m}_d"] > 0:
                off[m] = _lg(L[f"{m}_n"] / L[f"{m}_d"]) - _lg(lg[m])
        for m in RATIO_METRICS:
            if L[f"{m}_d"] > 0:
                off[m] = (L[f"{m}_n"] / L[f"{m}_d"]) / lg[m]
        off["proe"] = L["proe_n"] / L["proe_d"] if L["proe_d"] > 0 else 0.0
        return off

    def adjustments(self, game_id: str, team: str, season: int, week: int) -> tuple:
        """(off, deff) dicts for `team` going into `game_id` (state strictly before the game)."""
        L = self.league_state.get((int(season), int(week)))
        so = self.off_state.get((game_id, team))
        sd = self.def_state.get((game_id, team))
        if L is None or so is None or sd is None:
            return dict(E.NEUTRAL_OFF), dict(E.NEUTRAL_OFF)
        lg = self.P.league

        def rate(m, state, k):
            n, d = state[f"{m}_n"], state[f"{m}_d"]
            ln, ld = L[f"{m}_n"], L[f"{m}_d"]
            lrate = ln / ld if ld > 0 else lg[m]
            return (n + k * lrate) / (d + k), lrate

        off, deff = dict(E.NEUTRAL_OFF), dict(E.NEUTRAL_OFF)
        for m in LOGIT_METRICS:
            ro, _ = rate(m, so, self.k_off[m])
            rd, lrate = rate(m, sd, self.k_def[m])
            off[m] = _lg(ro) - _lg(lg[m])
            deff[m] = _lg(rd) - _lg(lrate)
        for m in RATIO_METRICS:
            ro, _ = rate(m, so, self.k_off[m])
            rd, lrate = rate(m, sd, self.k_def[m])
            off[m] = ro / lg[m]
            deff[m] = rd / lrate
        dl = L["proe_n"] / L["proe_d"] if L["proe_d"] > 0 else 0.0
        off["proe"] = (so["proe_n"] + self.k_off["proe"] * dl) / (so["proe_d"] + self.k_off["proe"])
        deff["proe"] = (sd["proe_n"] + self.k_def["proe"] * dl) / (sd["proe_d"] + self.k_def["proe"]) - dl
        return off, deff


def build_ratings(plays: pd.DataFrame, P: "F.SimParams", k_seasons: Optional[list] = None) -> TeamRatings:
    """`plays` = every scrimmage play up to and including the evaluated season; `k_seasons` = the seasons the pseudo-counts are
    estimated on (the TRAINING seasons only)."""
    toff, tdef = team_game_table(plays, P)
    kp = plays if k_seasons is None else plays[plays["season"].isin(k_seasons)]
    koff = estimate_k(toff[toff["season"].isin(k_seasons)] if k_seasons else toff, P.league, kp)
    kdef = estimate_k(tdef[tdef["season"].isin(k_seasons)] if k_seasons else tdef, P.league, kp)
    lg_tab = toff                                        # league sums: every play appears once on the offence side
    return TeamRatings(P=P, off_state=_states(toff), def_state=_states(tdef), league_state=_league_states(lg_tab), k_off=koff,
                       k_def=kdef, game_week=dict(zip(toff["game_id"], zip(toff["season"], toff["week"]))))


# -- player usage ------------------------------------------------------------------------------------------------------
SKILL = ("QB", "RB", "FB", "WR", "TE")
POS_GROUP = {"QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE"}


class UsageStore:
    """Per-player game logs (player_stats shaped like engine_eval.load_player_logs) with as-of EWM usage."""

    COLS = ("targets", "receptions", "receiving_yards", "carries", "rushing_yards", "attempts")

    def __init__(self, logs: pd.DataFrame, prior_before: int):
        df = logs[logs["position"].isin(SKILL)].copy()
        for c in self.COLS:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df = df.sort_values("_sort", kind="stable")
        self.by_pid = {pid: (g["_sort"].to_numpy(), g[list(self.COLS)].to_numpy(dtype=float))
                       for pid, g in df.groupby("player_id", sort=False)}
        self.pos_of = df.groupby("player_id")["position"].last().to_dict()
        # positional priors from first appearances before `prior_before` (training data only)
        d = df[df["_sort"] < prior_before].copy()
        d["app"] = d.groupby("player_id").cumcount()
        first = d[d["app"] < 3]
        self.prior = {}
        for pos, g in first.groupby(first["position"].map(POS_GROUP)):
            self.prior[pos] = {c: float(g[c].mean()) for c in self.COLS}
        allp = d.groupby(d["position"].map(POS_GROUP))
        tot_tg = float(d["targets"].sum())
        self.league_cr = float(d["receptions"].sum() / max(tot_tg, 1.0))
        self.league_ypr = float(d["receiving_yards"].sum() / max(d["receptions"].sum(), 1.0))
        self.league_ypc = float(d["rushing_yards"].sum() / max(d["carries"].sum(), 1.0))
        self.pos_cr, self.pos_ypr, self.pos_ypc = {}, {}, {}
        for pos, g in allp:
            self.pos_cr[pos] = float(g["receptions"].sum() / max(g["targets"].sum(), 1.0))
            self.pos_ypr[pos] = float(g["receiving_yards"].sum() / max(g["receptions"].sum(), 1.0))
            self.pos_ypc[pos] = float(g["rushing_yards"].sum() / max(g["carries"].sum(), 1.0))

    def asof(self, pid: str, t: int) -> Optional[np.ndarray]:
        h = self.by_pid.get(pid)
        if h is None:
            return None
        n = int(np.searchsorted(h[0], t, side="left"))
        return h[1][:n] if n else None


def build_players(rows: pd.DataFrame, store: UsageStore, t: int, window: Optional[list] = None, n0: float = 0.5,
                  k_cr: float = 25.0, k_y: float = 20.0, newcomer_scale: float = 0.5) -> list:
    """PlayerSpecs for one team-game from the ACTIVE skill players' rows (id / name / position only are read).

    Usage is per TEAM GAME, availability-adjusted: over the team's last games (most recent first, `window` =
    [(sort_key, {player ids unavailable that week})]) a player contributes his stat line when he has one, a ZERO when he was
    healthy but did not record anything, and nothing when he was reported Out / Doubtful that week. Backups who only
    appear in garbage time therefore carry a small share instead of a per-appearance average. Without a window the
    player's own appearances are used (tests)."""
    lam = 0.5 ** (1.0 / PLAYER_HALF_LIFE)
    ix = {c: i for i, c in enumerate(store.COLS)}
    out = []
    for r in rows.itertuples(index=False):
        pos = POS_GROUP.get(r.position)
        if pos is None:
            continue
        pri = store.prior.get(pos, {c: 0.0 for c in store.COLS})
        h = store.asof(r.player_id, t)
        # appearances only: catch rate / yards per reception / per carry (ratios do not need the zeros)
        if h is None:
            app = np.zeros(len(store.COLS))
        else:
            wa = lam ** np.arange(len(h))[::-1]
            app = (wa[:, None] * h).sum(0)
        if window is None:
            sums = app
            wsum = 0.0 if h is None else float(wa.sum())
        else:
            sums, wsum = np.zeros(len(store.COLS)), 0.0
            hs = store.by_pid.get(r.player_id)
            for age, (sort_j, unavail) in enumerate(window):
                if r.player_id in unavail:
                    continue
                w = lam ** age
                wsum += w
                if hs is not None:
                    i = int(np.searchsorted(hs[0], sort_j, side="left"))
                    if i < len(hs[0]) and hs[0][i] == sort_j:
                        sums = sums + w * hs[1][i]
        scale = newcomer_scale
        tg = (sums[ix["targets"]] + n0 * scale * pri["targets"]) / (wsum + n0)
        ca = (sums[ix["carries"]] + n0 * scale * pri["carries"]) / (wsum + n0)
        at = (sums[ix["attempts"]] + n0 * scale * (pri["attempts"] if pos == "QB" else 0.0)) / (wsum + n0)
        if window is not None:
            # a ROLE-HOLDER (per appearance >= ROLE_TARGETS targets / ROLE_CARRIES carries over the team's recent games) who is in the
            # as-of active set is credited at his per-appearance usage: zero-filling the games a starter missed to injury would
            # otherwise erase him on return. Appearances older than the window do not count (a QB who last started years ago is not
            # a starter now).
            hs = store.by_pid.get(r.player_id)
            tgw, caw, atw, w_in, w_qb = 0.0, 0.0, 0.0, 0.0, 0.0
            qsum = 0.0
            qw_all = 0.0
            if hs is not None:
                for age, (sort_j, unavail) in enumerate(window):
                    if r.player_id in unavail:
                        continue
                    wq_ = QB_DECAY ** age
                    qw_all += wq_
                    i = int(np.searchsorted(hs[0], sort_j, side="left"))
                    if i < len(hs[0]) and hs[0][i] == sort_j:
                        w = lam ** age
                        w_in += w
                        tgw += w * hs[1][i][ix["targets"]]
                        caw += w * hs[1][i][ix["carries"]]
                        qsum += wq_ * hs[1][i][ix["attempts"]]
            if w_in > 0:
                tg_app, ca_app = tgw / w_in, caw / w_in
                if tg_app >= ROLE_TARGETS:
                    tg = tg_app
                if ca_app >= ROLE_CARRIES:
                    ca = ca_app
                if pos == "QB":
                    at = float((qsum / max(qw_all, 1e-9)) ** 3)       # the QB role is exclusive: cube the recent per-game attempts
            elif pos == "QB":
                at = 1.0                                                                  # no recent appearance: a nominal weight
        rec_s, tg_s = app[ix["receptions"]], app[ix["targets"]]
        cr = (rec_s + k_cr * store.pos_cr.get(pos, store.league_cr)) / (tg_s + k_cr)
        ypr = (app[ix["receiving_yards"]] + k_y * store.pos_ypr.get(pos, store.league_ypr)) / (rec_s + k_y)
        ypc = (app[ix["rushing_yards"]] + k_y * 2.0 * store.pos_ypc.get(pos, store.league_ypc)) / (app[ix["carries"]] + k_y * 2.0)
        out.append(E.PlayerSpec(pid=r.player_id, name=getattr(r, "player_display_name", ""), pos=pos,
                                att_w=float(at) if pos == "QB" else 0.0, tgt_w=float(max(tg, 0.0)) if pos != "QB" or tg > 0 else 0.0,
                                car_w=float(max(ca, 0.0)),
                                cr_shift=_lg(cr) - _lg(store.league_cr), ypr_f=float(ypr / store.league_ypr),
                                ypc_f=float(ypc / store.league_ypc)))
    if not out:
        return out
    # the QB role is (nearly) exclusive: a quarterback whose recent attempt rate is under QB_FLOOR of the leader's takes none
    qmax = max([p.att_w for p in out if p.pos == "QB"], default=0.0)
    for p in out:
        if p.pos == "QB" and qmax > 0 and p.att_w < QB_FLOOR * qmax:
            p.att_w = 0.0
    # re-centre the player effects on their usage weights so the team-level rates stay with the team ratings
    tw = np.array([p.tgt_w for p in out])
    if tw.sum() > 0:
        m_cr = float(np.sum(tw * np.array([p.cr_shift for p in out])) / tw.sum())
        m_yp = float(np.sum(tw * np.array([p.ypr_f for p in out])) / tw.sum())
        for p in out:
            p.cr_shift -= m_cr
            p.ypr_f /= m_yp
    cw = np.array([p.car_w for p in out])
    if cw.sum() > 0:
        m_yc = float(np.sum(cw * np.array([p.ypc_f for p in out])) / cw.sum())
        for p in out:
            p.ypc_f /= m_yc
    return out
