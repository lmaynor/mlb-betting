"""The vectorised drive / play simulator: N coherent joint games per call.

State per simulated game: possession, yardline_100, down, yards-to-go, game clock, both scores. Each iteration of the loop
advances EVERY unfinished game by one action:

    drive start   down 1, yards-to-go min(10, yardline)
    4th down      softmax over {go, field goal, punt} (fitted); field goals are logistic in kick distance
    scrimmage     pass / run from the fitted call model, which depends on down, distance, field position AND the clock x score
                  state -- this is where the score-state response enters; efficiency pools carry a script class too
    pass          sack -> target (QB share, receiver share) -> interception / completion -> yards, all drawn per play, so one
                  pass play moves the QB's attempts / yards / TDs AND his receiver's targets / yards / TDs together
    rush          rusher by carry share, yards from the cell pool
    clock         snap-to-snap seconds from the empirical pool for (outcome, hurry class); end-of-drive gaps by drive end
    drive end     TD (+ extra point / two-point), FG, punt, turnover (+ defensive TD), downs, missed FG; next field position
                  from empirical pools; halftime flips possession to the team that did not receive the opening kickoff

Every stat of every player in the game is a function of the same simulated plays, so correlations (QB yards with receiver
yards, team pass attempts with rush attempts, total with margin) are produced by the mechanics, not imposed. Regulation only
(no overtime). Team strength enters through additive logit shifts (sack / interception / completion / pass rate) and
multipliers (yards per completion, yards per carry, pace) supplied by `nfl.sim.ratings`; with neutral adjustments the
simulator reproduces the league-average game it was fitted on (see `evaluate.league_check`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from nfl.sim import fit as F
from nfl.sim import pbp_prep as PP

STATS = ("pass_attempts", "pass_completions", "pass_yds", "pass_tds", "interceptions", "rush_attempts", "rush_yds",
         "targets", "receptions", "rec_yds", "rush_tds", "rec_tds")
S_ATT, S_CMP, S_PYD, S_PTD, S_INT, S_CAR, S_RYD, S_TGT, S_REC, S_RECYD, S_RTD, S_RECTD = range(12)
TS_PASSPL, TS_RUSHPL, TS_SACK, TS_DRIVES, TS_FIRSTD, TS_TD = range(6)
TS_SCRIPT0 = 6          # 6 + 2 * script_class (+1 for rushes): pass / rush play counts per script class (normal, chasing, milking)
N_TS = 12
MAX_STEPS = 420
FG_RANGE_YL = 55

NEUTRAL_OFF = {"proe": 0.0, "sack": 0.0, "int": 0.0, "cmp": 0.0, "ypcmp": 1.0, "ypr": 1.0, "pace": 1.0}


@dataclass
class PlayerSpec:
    pid: str
    name: str
    pos: str
    att_w: float = 0.0          # relative pass-attempt weight (QBs)
    tgt_w: float = 0.0          # relative target weight
    car_w: float = 0.0          # relative carry weight
    cr_shift: float = 0.0       # logit shift of completion probability when targeted
    ypr_f: float = 1.0          # multiplier on yards of his completions
    ypc_f: float = 1.0          # multiplier on yards of his carries


@dataclass
class TeamSpec:
    name: str
    off: dict = field(default_factory=lambda: dict(NEUTRAL_OFF))     # effect of THIS team's offence
    deff: dict = field(default_factory=lambda: dict(NEUTRAL_OFF))    # effect of THIS team's defence on the opposing offence
    players: list = field(default_factory=list)
    eff: float = 1.0            # extra yards multiplier on this offence (environment anchoring); 1 = none


@dataclass
class SimResult:
    stats: np.ndarray           # (N, 2, K, 12) int16
    pts: np.ndarray             # (N, 2) final score (regulation)
    team: np.ndarray            # (N, 2, 12) team counts
    ends: np.ndarray            # (N, 2, 7) drive endings by type: TD, FG, punt, turnover, downs, missed FG, (unused)
    teams: tuple
    K: int

    def stat(self, side: int, j: int, name: str) -> np.ndarray:
        return self.stats[:, side, j, STATS.index(name)].astype(np.float64)


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-z))


def call_logit(P: "F.SimParams", down, ytg, yl, tb, sb) -> np.ndarray:
    nb = F.N_BASE_CALL
    Xb = F.call_design(down, ytg, yl)
    return Xb @ P.call_w[:nb] + P.call_w[nb + np.asarray(tb) * PP.N_SB + np.asarray(sb)]


def _stack(teams: tuple, K: int, field_: str) -> np.ndarray:
    a = np.zeros((2, K))
    for t, tm in enumerate(teams):
        for j, p in enumerate(tm.players):
            a[t, j] = getattr(p, field_)
    return a


def _cum(w: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """Row-normalised cumulative weights (rows with no weight put everything on slot 0)."""
    w = np.asarray(w, dtype=float).copy()
    z = w.sum(1)
    w[z <= 0, 0] = 1.0
    c = np.cumsum(w, 1)
    c = c / c[:, -1:]
    c[:, -1] = 1.0 + eps
    return c


def simulate(P: "F.SimParams", teams: tuple, n: int = 2000, seed: int = 0, home_first_kick: Optional[np.ndarray] = None,
             pace_scale: Optional[float] = None) -> SimResult:
    """Simulate `n` regulation games between teams[0] and teams[1]."""
    rng = np.random.default_rng(seed)
    K = max(1, max(len(t.players) for t in teams))
    att_cum = _cum(_stack(teams, K, "att_w"))
    tgt_cum = _cum(_stack(teams, K, "tgt_w"))
    car_cum = _cum(_stack(teams, K, "car_w"))
    cr_shift = _stack(teams, K, "cr_shift")
    ypr_f = np.ones((2, K))
    ypc_f = np.ones((2, K))
    for t, tm in enumerate(teams):
        for j, p in enumerate(tm.players):
            ypr_f[t, j], ypc_f[t, j] = p.ypr_f, p.ypc_f
    O = {k: np.array([teams[0].off[k], teams[1].off[k]]) for k in NEUTRAL_OFF}
    D = {k: np.array([teams[0].deff[k], teams[1].deff[k]]) for k in NEUTRAL_OFF}
    EFF = np.array([teams[0].eff, teams[1].eff])
    pace = P.pace_scale if pace_scale is None else pace_scale

    stats = np.zeros((n, 2, K, len(STATS)), dtype=np.int16)
    team = np.zeros((n, 2, N_TS), dtype=np.int16)
    ends = np.zeros((n, 2, 7), dtype=np.int16)
    pts = np.zeros((n, 2), dtype=np.int16)
    ar = np.arange(n)
    off = rng.integers(0, 2, n) if home_first_kick is None else np.asarray(home_first_kick, dtype=int)
    first_recv = off.copy()
    gs = np.full(n, 3600.0)
    down = np.ones(n, dtype=int)
    ytg = np.full(n, 10.0)
    yl = P.kick_pool.draw(np.zeros(n, dtype=np.int64), rng.random(n)).astype(float)
    need_start = np.ones(n, dtype=bool)
    done = np.zeros(n, dtype=bool)
    tdx = P.td_extra
    pf = P.fumb

    def convert(m_idx, scorer, rngu):
        """add the extra point / two-point result for a TD by `scorer` (0/1 array) to the games in m_idx."""
        u1, u2 = rngu
        two = u1 < tdx["p_two_attempt"]
        good = np.where(two, u2 < tdx["p_two_good"], u2 < tdx["p_xp_good"])
        pts[m_idx, scorer] += (6 + np.where(two, 2, 1) * good).astype(np.int16)

    for _step in range(MAX_STEPS):
        act = np.nonzero(~done)[0]
        if len(act) == 0:
            break
        # ---- drive start --------------------------------------------------------------------------------------------
        ns = act[need_start[act]]
        if len(ns):
            down[ns] = 1
            ytg[ns] = np.minimum(10.0, yl[ns])
            need_start[ns] = False
            team[ns, off[ns], TS_DRIVES] += 1
        o = off[act]
        d = 1 - o
        g = gs[act]
        qtr = np.minimum(4, ((3600 - g) // 900).astype(int) + 1)
        hsr = np.where(g > 1800, g - 1800, g)
        sd = (pts[act, o] - pts[act, d]).astype(float)
        tb = PP.time_bucket(qtr, g, hsr)
        sb = PP.score_bucket(sd)
        dn, yt, yd = down[act], ytg[act], yl[act]
        # ---- 4th down decision ---------------------------------------------------------------------------------------
        action = np.zeros(len(act), dtype=int)                       # 0 scrimmage, 1 field goal, 2 punt
        i4 = np.nonzero(dn == 4)[0]
        if len(i4):
            Z = np.column_stack([np.zeros(len(i4)), F.fourth_design(yt[i4], yd[i4], sd[i4], tb[i4]) @ P.fourth_w.T])
            Z -= Z.max(1, keepdims=True)
            E = np.exp(Z)
            E[yd[i4] > FG_RANGE_YL, 1] = 0.0                          # out of field goal range
            E[yd[i4] <= 20, 2] *= 0.02                                # nobody punts from inside the opponent 20
            pr = E / E.sum(1, keepdims=True)
            u = rng.random(len(i4))
            ch = (u[:, None] > np.cumsum(pr, 1)).sum(1)
            action[i4] = np.minimum(ch, 2)
        end = np.zeros(len(act), dtype=bool)                          # drive ends on this action
        etype = np.full(len(act), -1)
        pts_off = np.zeros(len(act), dtype=int)
        pts_def = np.zeros(len(act), dtype=int)
        new_yl = yd.copy()
        new_dn = dn.copy()
        new_yt = yt.copy()
        kind = np.zeros(len(act), dtype=int)                          # clock kind: 0 run 1 complete 2 incomplete 3 sack
        hurry = PP.hurry_class(tb, sb)
        next_off_same = np.zeros(len(act), dtype=bool)                # defensive TD: the original offence gets the ball back
        next_yl_override = np.full(len(act), np.nan)

        # ---- field goals ---------------------------------------------------------------------------------------------
        ifg = np.nonzero(action == 1)[0]
        if len(ifg):
            kd = yd[ifg] + 17.0
            pmake = _sigmoid(F.fg_design(kd) @ P.fg_w)
            made = rng.random(len(ifg)) < pmake
            end[ifg] = True
            etype[ifg] = np.where(made, 1, 5)
            pts_off[ifg] = np.where(made, 3, 0)
            next_yl_override[ifg[~made]] = np.minimum(100.0 - (yd[ifg[~made]] + 7.0), 80.0)
        # ---- punts ---------------------------------------------------------------------------------------------------
        ipu = np.nonzero(action == 2)[0]
        if len(ipu):
            end[ipu] = True
            etype[ipu] = 2
        # ---- scrimmage plays -----------------------------------------------------------------------------------------
        isc = np.nonzero(action == 0)[0]
        if len(isc):
            os_, ds_ = o[isc], d[isc]
            lg = call_logit(P, dn[isc], yt[isc], yd[isc], tb[isc], sb[isc]) + O["proe"][os_] + D["proe"][ds_]
            ispass = rng.random(len(isc)) < _sigmoid(lg)
            script = PP.script_class(tb[isc], sb[isc])
            cell = F.play_cell(yd[isc], yt[isc], script)
            yards = np.zeros(len(isc))
            sims = act[isc]
            # -- pass
            ip = np.nonzero(ispass)[0]
            if len(ip):
                sp = isc[ip]
                oo, dd, cc = os_[ip], ds_[ip], cell[ip]
                sims_p = sims[ip]
                team[sims_p, oo, TS_PASSPL] += 1
                team[sims_p, oo, TS_SCRIPT0 + 2 * script[ip]] += 1
                pp = P.pass_probs[cc]
                p_sack = _sigmoid(_logit(pp[:, 0]) + O["sack"][oo] + D["sack"][dd])
                is_sack = rng.random(len(ip)) < p_sack
                # target / receiver
                has_t = (rng.random(len(ip)) < P.p_target) & ~is_sack
                rj = (rng.random(len(ip))[:, None] > tgt_cum[oo]).sum(1)
                rj = np.minimum(rj, K - 1)
                qj = np.minimum((rng.random(len(ip))[:, None] > att_cum[oo]).sum(1), K - 1)
                p_int = _sigmoid(_logit(pp[:, 1]) + O["int"][oo] + D["int"][dd])
                # the fitted cell frequency is per THROW (throwaways included); a targeted throw completes with P / p_target
                p_cmp = _sigmoid(_logit(np.minimum(pp[:, 2] / P.p_target, 0.97)) + O["cmp"][oo] + D["cmp"][dd]
                                 + np.where(has_t, cr_shift[oo, rj], 0.0))
                p_cmp = np.where(has_t, np.minimum(p_cmp, 0.985 - p_int), 0.0)           # p_cmp above is P(complete | targeted)
                u3 = rng.random(len(ip))
                is_int = ~is_sack & (u3 < p_int)
                is_cmp = ~is_sack & ~is_int & (u3 < p_int + p_cmp) & has_t
                # yards
                ycmp = P.cmp_pool.draw(cc, rng.random(len(ip)))
                mult = O["ypcmp"][oo] * D["ypcmp"][dd] * EFF[oo] * np.where(has_t, ypr_f[oo, rj], 1.0)
                ycmp = np.where(ycmp > 0, np.rint(ycmp * mult), ycmp)
                ysack = P.sack_pool.draw(np.zeros(len(ip), dtype=np.int64), rng.random(len(ip)))
                y = np.where(is_cmp, ycmp, np.where(is_sack, ysack, 0.0))
                y = np.minimum(y, yd[isc][ip])
                td = is_cmp & (y >= yd[isc][ip])
                # fumbles (completions, sacks)
                fum = ((is_cmp & (rng.random(len(ip)) < pf["cmp"])) | (is_sack & (rng.random(len(ip)) < pf["sack"]))) & ~td
                # stats
                att = ~is_sack
                stats[sims_p[att], oo[att], qj[att], S_ATT] += 1
                stats[sims_p[is_cmp], oo[is_cmp], qj[is_cmp], S_CMP] += 1
                stats[sims_p[is_cmp], oo[is_cmp], qj[is_cmp], S_PYD] += y[is_cmp].astype(np.int16)
                stats[sims_p[is_int], oo[is_int], qj[is_int], S_INT] += 1
                stats[sims_p[td], oo[td], qj[td], S_PTD] += 1
                tg = has_t & ~is_sack
                stats[sims_p[tg], oo[tg], rj[tg], S_TGT] += 1
                stats[sims_p[is_cmp], oo[is_cmp], rj[is_cmp], S_REC] += 1
                stats[sims_p[is_cmp], oo[is_cmp], rj[is_cmp], S_RECYD] += y[is_cmp].astype(np.int16)
                stats[sims_p[td], oo[td], rj[td], S_RECTD] += 1
                team[sims_p[is_sack], oo[is_sack], TS_SACK] += 1
                kind[sp] = np.where(is_sack, 3, np.where(is_cmp, 1, 2))
                yards[ip] = y
                # drive-ending outcomes of the pass play
                pick6 = is_int & (rng.random(len(ip)) < pf["pick6"])
                lost = is_int | fum
                fum_td = fum & (rng.random(len(ip)) < pf["fumb_td"])
                dtd = pick6 | fum_td
                end[sp] = td | lost
                etype[sp] = np.where(td, 0, np.where(lost, 3, -1))
                pts_off[sp] = np.where(td, 6, 0)
                pts_def[sp] = np.where(dtd, 6, 0)
                next_off_same[sp] = dtd
            # -- rush
            ir = np.nonzero(~ispass)[0]
            if len(ir):
                sr = isc[ir]
                oo, dd, cc = os_[ir], ds_[ir], cell[ir]
                sims_r = sims[ir]
                team[sims_r, oo, TS_RUSHPL] += 1
                team[sims_r, oo, TS_SCRIPT0 + 1 + 2 * script[ir]] += 1
                cj = np.minimum((rng.random(len(ir))[:, None] > car_cum[oo]).sum(1), K - 1)
                yr = P.rush_pool.draw(cc, rng.random(len(ir)))
                mult = O["ypr"][oo] * D["ypr"][dd] * EFF[oo] * ypc_f[oo, cj]
                yr = np.where(yr > 0, np.rint(yr * mult), yr)
                yr = np.minimum(yr, yd[isc][ir])
                td = yr >= yd[isc][ir]
                fum = (rng.random(len(ir)) < pf["rush"]) & ~td
                fum_td = fum & (rng.random(len(ir)) < pf["fumb_td"])
                stats[sims_r, oo, cj, S_CAR] += 1
                stats[sims_r, oo, cj, S_RYD] += yr.astype(np.int16)
                stats[sims_r[td], oo[td], cj[td], S_RTD] += 1
                kind[sr] = 0
                yards[ir] = yr
                end[sr] = td | fum
                etype[sr] = np.where(td, 0, np.where(fum, 3, -1))
                pts_off[sr] = np.where(td, 6, 0)
                pts_def[sr] = np.where(fum_td, 6, 0)
                next_off_same[sr] = fum_td
            # -- downs and field position for plays that did not end the drive
            cont = isc[~end[isc]]
            yy = yards[~end[isc]]
            nyl = yd[cont] - yy
            nyl = np.minimum(nyl, 99.0)
            first = yy >= yt[cont]
            ndn = np.where(first, 1, dn[cont] + 1)
            nyt = np.where(first, np.minimum(10.0, nyl), yt[cont] - yy)
            team[act[cont[first]], o[cont[first]], TS_FIRSTD] += 1
            lost_downs = ndn >= 5
            new_yl[cont] = nyl
            new_dn[cont] = ndn
            new_yt[cont] = nyt
            ld = cont[lost_downs]
            end[ld] = True
            etype[ld] = 4
            # spot of a turnover on downs / fumble / interception for the next-start formula
            moved = isc[end[isc] & (etype[isc] == 3)]
            new_yl[moved] = np.minimum(np.maximum(yd[moved] - yards[np.searchsorted(isc, moved)], 1.0), 99.0)
        # ---- score the drive end and decide the next possession ----------------------------------------------------------
        sims_a = act
        # touchdowns by the offence: extra point / two-point
        itd = np.nonzero(end & (etype == 0))[0]
        if len(itd):
            convert(sims_a[itd], o[itd], (rng.random(len(itd)), rng.random(len(itd))))
            team[sims_a[itd], o[itd], TS_TD] += 1
        ife = np.nonzero(end & (etype == 1))[0]
        if len(ife):
            pts[sims_a[ife], o[ife]] += 3
        idt = np.nonzero(end & (pts_def > 0))[0]
        if len(idt):
            convert(sims_a[idt], d[idt], (rng.random(len(idt)), rng.random(len(idt))))
        # next possession
        n_off = np.where(next_off_same, o, d)
        start_cell = np.zeros(len(act), dtype=np.int64)
        gi = np.nonzero(end)[0]
        if len(gi):
            et = etype[gi]
            ends[act[gi], o[gi], et] += 1
            nyl_e = np.full(len(gi), np.nan)
            kick = np.isin(et, (0, 1, 6)) | next_off_same[gi]
            nyl_e[kick] = P.kick_pool.draw(np.zeros(int(kick.sum()), dtype=np.int64), rng.random(int(kick.sum())))
            sp_ = ~kick & np.isin(et, (2, 3))
            cellp = (et[sp_] - 2) * 10 + PP.yl_bucket10(yd[gi][sp_])
            nyl_e[sp_] = P.start_pool.draw(cellp.astype(np.int64), rng.random(int(sp_.sum())))
            dn_ = ~kick & (et == 4)
            nyl_e[dn_] = np.clip(100.0 - new_yl[gi][dn_], 1.0, 99.0)
            mf = ~kick & (et == 5)
            nyl_e[mf] = np.clip(next_yl_override[gi][mf], 1.0, 99.0)
            nyl_e = np.where(np.isnan(nyl_e), 75.0, nyl_e)
            off[act[gi]] = n_off[gi]
            yl[act[gi]] = np.clip(nyl_e, 1.0, 99.0)
            need_start[act[gi]] = True
        keep = np.nonzero(~end)[0]
        down[act[keep]] = new_dn[keep]
        ytg[act[keep]] = new_yt[keep]
        yl[act[keep]] = new_yl[keep]
        # ---- clock ---------------------------------------------------------------------------------------------------
        el = np.zeros(len(act))
        ik = np.nonzero(~end & (action == 0))[0]
        if len(ik):
            cellc = (kind[ik] * 3 + hurry[ik]).astype(np.int64)
            el[ik] = P.el_pool.draw(cellc, rng.random(len(ik))) * O["pace"][o[ik]] * D["pace"][d[ik]]
        if len(gi):
            cellg = (np.clip(etype[gi], 0, 6) * 3 + hurry[gi]).astype(np.int64)
            el[gi] = P.gap_pool.draw(cellg, rng.random(len(gi))) * O["pace"][o[gi]] * D["pace"][d[gi]]
        el = np.maximum(el * pace, 0.0)
        g_new = g - el
        crossed = (g > 1800) & (g_new <= 1800)
        over = g_new <= 0
        gs[act] = np.where(crossed, 1800.0, np.where(over, 0.0, g_new))
        done[act[over]] = True
        # halftime: the team that did not receive the opening kick gets the ball; the half-ending drive is over
        ch = np.nonzero(crossed & ~over)[0]
        if len(ch):
            s = act[ch]
            off[s] = 1 - first_recv[s]
            yl[s] = P.kick_pool.draw(np.zeros(len(s), dtype=np.int64), rng.random(len(s)))
            need_start[s] = True
    done[:] = True
    pts_f = pts.copy()
    return SimResult(stats=stats, pts=pts_f, team=team, ends=ends, teams=teams, K=K)
