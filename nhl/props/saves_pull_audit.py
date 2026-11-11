"""H1 goalie-pull residual-partition audit for the NHL saves D1 cohort (Argon, Nov 11, 2026 consult).

MEASUREMENT-ONLY. This is a post-hoc residual audit on the shipped D1 saves model's banked
predictions and settled outcomes, built for the Dec 21, 2026 review (n = 400). It is not a
model, not a gate, and not part of serving.

FREEZE (Argon (c)/(d), verbatim in substance): H1 is frozen out of live D1 saves officials
until the n = 400 / Dec 21 judgment completes; this tool is built now but is NOT to be run
against the live cohort before that review; it changes no D1 gate, and if the D1 gate fails
on its own terms on Dec 21, H1 cannot rescue it. The CLI enforces the freeze: any input path
that looks like the live cohort (the paper-track ledger, the served trained/board files, or a
gs:// URI) is refused unless the caller passes the explicit override flag
``--i-understand-this-is-frozen-until-dec-21``. The flag exists so the Dec 21 review itself
can run the audit; it is not a routine bypass.

WHAT IT COMPUTES. Given (a) the D1 saves model's banked predictions, (b) settled outcomes,
and (c) banked NHL play-by-play, partition each settled saves row by PULL-STATE and report:

  (i)  mean signed residual in the pull-state subset vs all other games, where the signed
       residual is ``actual - mu`` and ``mu`` is the closing (last-sighting) prediction --
       the same last-sighting convention as ``nhl.props.paper_track.report_from`` and the
       ``nhl.props.saves_hardening`` history. The shift is pull minus other.
  (ii) tail calibration on the >= 32.5 saves tail: the per-row log loss of the model's
       P(saves > 32.5) against the realised tail event (actual > 32.5), averaged in the
       pull-state subset minus the average in all other games. A positive difference is a
       degradation in the pull-state subset.

PULL-STATE, DEFINED FROM THE SAVES-ROW GOALIE'S PERSPECTIVE (Argon's H1 nitpick). The
quantity of interest is the TRAILING team's 6-on-5 shot pressure against the LEADING goalie
once the trailing team pulls its own goalie. A saves row is therefore in the pull-state iff,
at the 15:00 mark of Period 3, THE GOALIE'S OWN TEAM IS LEADING BY 1 OR 2 GOALS -- i.e. the
opponent is the team trailing by 1-2, the opponent is the team that pulls, and this goalie
is the one who has to face the pulled-goalie flurry. The packet's empty-net shot share
measured the wrong end of the ice (the LEADING team's shots AT an empty net say nothing
about who has to make saves); this tool must not repeat that. In particular a goalie whose
team TRAILS by 1-2 at the mark is NOT in the pull-state: his team pulls its goalie, he may
leave the ice himself, and he faces no 6-on-5 pressure. Score at the mark is derived from
banked PBP goal events at or before 15:00 of Period 3 (shootout goals excluded, matching
``nhl.data.flatten._goals_by_period``). Rows whose game has no usable PBP are UNCLASSIFIED
and excluded from both subsets -- unknown is never silently counted as "other".

PROMOTION BARS (Argon H1 verdict + (e) auto-kill, encoded in ``verdict``): H1 promotes to a
D1 feature only if the mean signed residual shift is >= 0.45 saves in magnitude OR the tail
log-loss degradation is >= 0.0030. Otherwise the audit reports no-effect (auto-kill: mean
signed residual difference < 0.45 saves AND tail log-loss difference < 0.0030).

INPUT FORMATS (paths are always passed as arguments; nothing production is hard-coded):
  * predictions: the served trained payload (``nhl.props.serve_trained`` shape:
    ``{"rows": [{player_id, name, team, opp, date, game_key, stat, mu, alpha}]}``), a bare
    JSON list of such rows, or JSONL of such rows. Only ``stat == "saves"`` rows are used.
    The tail probability comes from, in order: an explicit ``p_over_32_5`` / ``p_tail_32_5``
    field, NegBin(mu, alpha).p_over(32.5) (the repo's ``nhl.props.hierarchical.NegBinDist``),
    or -- when a row's own settled line is exactly 32.5 -- that row's closing ``p_model``.
  * settled: the paper-track settled JSONL (``nhl.props.paper_track.SETTLED_KEY`` shape:
    ``{game_key, player_id, player, stat, line, first: {mu, p_model, ...}, last: {mu,
    p_model, ...}, actual, void, won_over}``), one or more files. Void rows and rows with
    no actual are skipped.
  * pbp: banked raw NHL play-by-play (``NHL/raw/pbp/{game_id}.json.gz`` payloads: NHL API
    shape with ``plays`` / ``awayTeam`` / ``homeTeam`` / ``gameDate``), as files and/or
    directories scanned for ``*.json`` / ``*.json.gz``. The flattened shots grain
    (``nhl.data.flatten.flatten_shots`` rows) is supported at the library level via
    ``score_at_mark_from_shots``.

USAGE (Dec 21 review only; the paths below are placeholders, not production paths):

    python -m nhl.props.saves_pull_audit \
        --predictions <trained-predictions.json> \
        --settled <settled.jsonl> [--settled <settled-2.jsonl>] \
        --pbp <dir-or-file> [--pbp <dir-or-file>] \
        [--out <report.json>] \
        [--i-understand-this-is-frozen-until-dec-21]

Exit codes: 0 = audit ran; 2 = frozen-cohort guard refusal (no input file was opened).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import math
import os
import sys

log = logging.getLogger("nhl.props.saves_pull_audit")

# --- pre-registered constants (Argon H1 verdict + (e); do not tune in code review) -------------------------
PULL_MARK_PERIOD = 3
PULL_MARK_TIME_SEC = 15 * 60          # the 15:00 mark of Period 3
RESIDUAL_SHIFT_BAR = 0.45             # saves; |mean signed residual, pull - other| at/above this promotes
TAIL_LOGLOSS_BAR = 0.0030             # tail log-loss degradation (pull - other) at/above this promotes
TAIL_LINE = 32.5                      # the >= 32.5 saves tail
REVIEW_N = 400                        # the D1 live cohort size judged on REVIEW_DATE
REVIEW_DATE = "2026-12-21"
OVERRIDE_FLAG = "--i-understand-this-is-frozen-until-dec-21"

_EPS_CLIP = (0.005, 0.995)            # the repo's scoring clip (nhl.props.paper_track report)

# Path fragments that identify the LIVE D1 cohort. Pointing the audit at any of these without the
# override flag is refused before any file is opened (see the FREEZE in the module docstring).
LIVE_COHORT_MARKERS = (
    "paper_track/settled",
    "paper_track/snapshots",
    "nhl/paper_track",
    "trained_latest.json",
    "projections_latest.json",
)


class FrozenCohortError(RuntimeError):
    """Raised when the audit is pointed at the live D1 cohort without the freeze override."""


# -----------------------------------------------------------------------------------------------------------
# freeze guard
# -----------------------------------------------------------------------------------------------------------
def is_live_cohort_path(path) -> bool:
    """True when a path string identifies the live D1 cohort (ledger / served files / cloud URI)."""
    p = str(path).replace("\\", "/").lower()
    if p.startswith("gs://"):
        return True
    return any(m in p for m in LIVE_COHORT_MARKERS)


def assert_paths_allowed(paths, override: bool) -> None:
    """Refuse live-cohort inputs unless the freeze override was passed. Checks path STRINGS only:
    on refusal no input file has been opened, read, or executed against."""
    hits = [str(p) for p in paths if is_live_cohort_path(p)]
    if hits and not override:
        raise FrozenCohortError(
            "H1 is frozen out of live D1 saves officials until the n = 400 / Dec 21, 2026 judgment "
            "completes, and this audit must not be run against the live cohort before that review. "
            f"Refusing live-cohort input path(s): {hits}. If this IS the Dec 21 review, re-run with "
            f"{OVERRIDE_FLAG}."
        )
    if hits and override:
        log.warning("FREEZE OVERRIDE %s passed for live-cohort path(s) %s -- this run is the "
                    "Dec 21, 2026 review (n = %d) or it should not be happening.", OVERRIDE_FLAG, hits, REVIEW_N)


# -----------------------------------------------------------------------------------------------------------
# score at the 15:00 mark of Period 3, from banked-format PBP / shots
# -----------------------------------------------------------------------------------------------------------
def _mmss_to_sec(value) -> int | None:
    """'MM:SS' (NHL API timeInPeriod) -> seconds in period. None when unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 1:
            return int(parts[0])
    except ValueError:
        return None
    return None


def _at_or_before_mark(period, period_type, time_sec) -> bool:
    """A goal counts toward the mark score iff it happened in Period 1-2, or in Period 3 at or
    before 15:00. Shootout goals never count (they are not game play and are excluded from the
    recomputed score in nhl.data.flatten, the same convention used here)."""
    if period_type == "SO":
        return False
    try:
        period = int(period)
    except (TypeError, ValueError):
        return False
    if period < PULL_MARK_PERIOD:
        return True
    if period > PULL_MARK_PERIOD:
        return False
    return time_sec is not None and int(time_sec) <= PULL_MARK_TIME_SEC


def score_at_mark_from_pbp(pbp: dict) -> tuple | None:
    """(away_goals, home_goals) at the 15:00 mark of Period 3 from a raw NHL API PBP payload
    (the banked NHL/raw/pbp/{game_id}.json.gz shape), counting `goal` plays by eventOwnerTeamId.
    None when the payload carries no team ids (the score cannot be attributed)."""
    if not pbp:
        return None
    away_id = (pbp.get("awayTeam") or {}).get("id")
    home_id = (pbp.get("homeTeam") or {}).get("id")
    if away_id is None or home_id is None:
        return None
    away = home = 0
    for play in pbp.get("plays", []) or []:
        if play.get("typeDescKey") != "goal":
            continue
        pd_ = play.get("periodDescriptor") or {}
        if not _at_or_before_mark(pd_.get("number"), pd_.get("periodType"),
                                  _mmss_to_sec(play.get("timeInPeriod"))):
            continue
        owner = (play.get("details") or {}).get("eventOwnerTeamId")
        if owner == away_id:
            away += 1
        elif owner == home_id:
            home += 1
    return away, home


def score_at_mark_from_shots(rows) -> tuple | None:
    """(away_goals, home_goals) at the mark from flattened shot rows of ONE game
    (nhl.data.flatten.flatten_shots shape: period / period_type / time_sec / event / is_home,
    where `goal` is one of the shot events). None when no row carries `is_home`."""
    rows = list(rows or [])
    if not rows or all(r.get("is_home") is None for r in rows):
        return None
    away = home = 0
    for r in rows:
        if r.get("event") != "goal":
            continue
        if not _at_or_before_mark(r.get("period"), r.get("period_type"), r.get("time_sec")):
            continue
        if r.get("is_home"):
            home += 1
        else:
            away += 1
    return away, home


def game_key_from_pbp(pbp: dict) -> str | None:
    """The repo game_key ('nhl:{date}:{AWAY}@{HOME}', nhl.props.serve_trained) for a PBP payload."""
    if not pbp:
        return None
    date = str(pbp.get("gameDate") or "")[:10]
    away = (pbp.get("awayTeam") or {}).get("abbrev")
    home = (pbp.get("homeTeam") or {}).get("abbrev")
    if not date or not away or not home:
        return None
    return f"nhl:{date}:{away}@{home}"


def pull_state(goalie_team_goals: int, opp_goals: int) -> bool:
    """THE partition, from the saves-row goalie's perspective: True iff the goalie's own team
    LEADS by 1 or 2 goals at the mark -- the state in which the trailing opponent pulls its
    goalie and this goalie faces the 6-on-5 flurry. A goalie whose team trails by 1-2 is NOT
    in the pull-state (see the module docstring; that is the packet's wrong-end error)."""
    return 1 <= (int(goalie_team_goals) - int(opp_goals)) <= 2


def classify_row(row: dict, score_at_mark: tuple | None) -> bool | None:
    """Pull-state for one saves row given its game's (away, home) score at the mark. The row's
    team is matched against the game_key's AWAY@HOME abbrevs (predictions carry team/opp;
    settled rows are joined to predictions first -- see `join_rows`). None = unclassified."""
    if score_at_mark is None:
        return None
    gk = str(row.get("game_key") or "")
    team = row.get("team")
    if not team or "@" not in gk:
        return None
    away_abbr, home_abbr = gk.rsplit(":", 1)[-1].split("@")
    away_goals, home_goals = score_at_mark
    if str(team) == home_abbr:
        return pull_state(home_goals, away_goals)
    if str(team) == away_abbr:
        return pull_state(away_goals, home_goals)
    return None


# -----------------------------------------------------------------------------------------------------------
# tail probability: P(saves > 32.5) under the model's NegBin(mu, alpha)
# -----------------------------------------------------------------------------------------------------------
def negbin_p_over(mu: float, alpha: float, line: float = TAIL_LINE) -> float:
    """P(X > floor(line)) for NegBin(mu, alpha) (var = mu + alpha*mu^2; alpha <= 0 is Poisson).
    Uses the repo's nhl.props.hierarchical.NegBinDist when importable; otherwise the identical
    scipy computation inline, so the audit stays runnable outside the serving package."""
    try:
        from nhl.props.hierarchical import NegBinDist
        return float(NegBinDist(float(mu), float(alpha)).p_over(float(line)))
    except Exception:  # noqa: BLE001 -- standalone fallback, same math as NegBinDist._frozen
        from scipy.stats import nbinom, poisson
        mu = max(float(mu), 1e-9)
        alpha = max(float(alpha), 0.0)
        if alpha <= 0:
            return float(1.0 - poisson(mu).cdf(math.floor(line)))
        n = 1.0 / alpha
        return float(1.0 - nbinom(n, n / (n + mu)).cdf(math.floor(line)))


def _tail_prob(joined: dict) -> float | None:
    """The model's P(saves > 32.5) for one joined row, by the documented fallback chain."""
    for k in ("p_over_32_5", "p_tail_32_5", "p_tail"):
        if joined.get(k) is not None:
            return float(joined[k])
    mu, alpha = joined.get("mu"), joined.get("alpha")
    if mu is not None and alpha is not None:
        try:
            return negbin_p_over(float(mu), float(alpha))
        except Exception:  # noqa: BLE001 -- no scipy and no repo dist: the tail is unknowable for this row
            return None
    if joined.get("line") is not None and float(joined["line"]) == TAIL_LINE and joined.get("p_model") is not None:
        return float(joined["p_model"])
    return None


# -----------------------------------------------------------------------------------------------------------
# loading + joining (formats: see the module docstring)
# -----------------------------------------------------------------------------------------------------------
def _read_json_payload(path: str):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path: str) -> list:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def load_predictions(path: str) -> dict:
    """Banked trained predictions -> {(game_key, player_id): row}, saves rows only. Accepts the
    served payload ({rows: [...]}), a bare list, or JSONL."""
    if str(path).endswith((".jsonl", ".jsonl.gz")):
        rows = _read_jsonl(path)
    else:
        payload = _read_json_payload(path)
        rows = payload.get("rows", []) if isinstance(payload, dict) else list(payload)
    out = {}
    for r in rows:
        if r.get("stat") not in (None, "saves"):
            continue
        if r.get("game_key") is None or r.get("player_id") is None:
            continue
        out[(r["game_key"], int(r["player_id"]))] = r
    return out


def load_settled(paths) -> list:
    """Paper-track settled JSONL files -> settled saves rows (void / unplayed rows dropped)."""
    out = []
    for path in paths:
        for r in _read_jsonl(path):
            if r.get("stat") != "saves" or r.get("void") or r.get("actual") is None:
                continue
            out.append(r)
    return out


def load_pbp_scores(paths) -> dict:
    """Banked PBP files/dirs -> {game_key: (away_goals, home_goals) at the mark}. Files that are
    not readable PBP payloads are skipped with a log line, never guessed at."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                files.extend(os.path.join(root, n) for n in sorted(names)
                             if n.endswith((".json", ".json.gz")))
        else:
            files.append(p)
    scores = {}
    for f in files:
        try:
            pbp = _read_json_payload(f)
        except Exception as exc:  # noqa: BLE001
            log.info("skipping unreadable PBP file %s: %s", f, exc)
            continue
        gk, score = game_key_from_pbp(pbp), score_at_mark_from_pbp(pbp)
        if gk is not None and score is not None:
            scores[gk] = score
    return scores


def join_rows(settled: list, predictions: dict) -> list:
    """Settled rows joined to their banked prediction on (game_key, player_id). The closing
    (last-sighting) mu / p_model win over the prediction file's, matching the repo's closing
    convention; the prediction file supplies alpha and team when the settled row lacks them."""
    out = []
    for s in settled:
        pred = predictions.get((s.get("game_key"), int(s["player_id"]))) if s.get("player_id") is not None else None
        last = s.get("last") or {}
        mu = last.get("mu")
        if mu is None and pred is not None:
            mu = pred.get("mu")
        if mu is None:
            continue                                    # no prediction to take a residual against
        joined = {
            "game_key": s.get("game_key"), "player_id": s.get("player_id"), "player": s.get("player"),
            "team": (pred or {}).get("team") or s.get("team"), "opp": (pred or {}).get("opp") or s.get("opp"),
            "line": s.get("line"), "mu": float(mu), "actual": float(s["actual"]),
            "alpha": (pred or {}).get("alpha", s.get("alpha")),
            "p_model": last.get("p_model"),
        }
        for k in ("p_over_32_5", "p_tail_32_5", "p_tail"):
            if pred is not None and pred.get(k) is not None:
                joined[k] = pred[k]
            elif s.get(k) is not None:
                joined[k] = s[k]
        joined["residual"] = joined["actual"] - joined["mu"]     # signed: actual - predicted saves
        joined["p_tail"] = _tail_prob(joined)
        out.append(joined)
    return out


# -----------------------------------------------------------------------------------------------------------
# the audit + verdict
# -----------------------------------------------------------------------------------------------------------
def _logloss(y: float, p: float) -> float:
    lo, hi = _EPS_CLIP
    p = min(max(float(p), lo), hi)
    return -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))


def verdict(residual_shift, tail_logloss_diff) -> dict:
    """Argon's H1 promotion bars, encoded: promotes to a D1 feature only if the mean signed
    residual shift is >= 0.45 saves in magnitude OR the tail log-loss degradation is
    >= 0.0030; otherwise no-effect (the (e) auto-kill: both below their bars)."""
    resid_hit = residual_shift is not None and abs(float(residual_shift)) >= RESIDUAL_SHIFT_BAR
    tail_hit = tail_logloss_diff is not None and float(tail_logloss_diff) >= TAIL_LOGLOSS_BAR
    promotes = bool(resid_hit or tail_hit)
    return {
        "verdict": "PROMOTE_TO_D1_FEATURE" if promotes else "NO_EFFECT",
        "promotes": promotes,
        "residual_shift_bar": RESIDUAL_SHIFT_BAR,
        "residual_shift_hit": bool(resid_hit),
        "tail_logloss_bar": TAIL_LOGLOSS_BAR,
        "tail_logloss_hit": bool(tail_hit),
        "rule": ("promotes iff |mean signed residual shift| >= 0.45 saves OR tail log-loss "
                 "degradation >= 0.0030; otherwise no-effect (Argon H1 + (e) auto-kill)"),
    }


def audit(joined: list, scores: dict) -> dict:
    """Partition joined saves rows by pull-state and compute the two H1 quantities + verdict."""
    pull, other, unclassified = [], [], 0
    for row in joined:
        state = classify_row(row, scores.get(row.get("game_key")))
        if state is None:
            unclassified += 1
        elif state:
            pull.append(row)
        else:
            other.append(row)

    def _mean(rows, key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return float(sum(vals) / len(vals)) if vals else None

    def _tail_ll(rows):
        vals = [_logloss(1.0 if r["actual"] > TAIL_LINE else 0.0, r["p_tail"])
                for r in rows if r.get("p_tail") is not None]
        return (float(sum(vals) / len(vals)) if vals else None), len(vals)

    mean_pull, mean_other = _mean(pull, "residual"), _mean(other, "residual")
    shift = None if mean_pull is None or mean_other is None else mean_pull - mean_other
    ll_pull, n_tail_pull = _tail_ll(pull)
    ll_other, n_tail_other = _tail_ll(other)
    tail_diff = None if ll_pull is None or ll_other is None else ll_pull - ll_other
    return {
        "audit": "H1 goalie-pull residual partition (Argon Nov 11, 2026; measurement-only)",
        "review": {"n_expected": REVIEW_N, "date": REVIEW_DATE},
        "pull_state_definition": ("the saves-row goalie's team LEADS by 1-2 goals at the 15:00 "
                                  "mark of Period 3 (the trailing opponent pulls its goalie and "
                                  "this goalie faces the 6-on-5 pressure)"),
        "n_joined": len(joined),
        "n_pull": len(pull),
        "n_other": len(other),
        "n_unclassified_no_pbp": unclassified,
        "mean_signed_residual_pull": mean_pull,
        "mean_signed_residual_other": mean_other,
        "mean_signed_residual_shift": shift,
        "tail_line": TAIL_LINE,
        "tail_logloss_pull": ll_pull,
        "tail_logloss_other": ll_other,
        "tail_logloss_diff": tail_diff,
        "n_tail_pull": n_tail_pull,
        "n_tail_other": n_tail_other,
        **verdict(shift, tail_diff),
    }


def run(predictions_path: str, settled_paths, pbp_paths, override: bool = False) -> dict:
    """Guard first (path strings only), then load, join, and audit. The guard runs before any
    input file is opened."""
    assert_paths_allowed([predictions_path, *settled_paths, *pbp_paths], override)
    predictions = load_predictions(predictions_path)
    settled = load_settled(settled_paths)
    scores = load_pbp_scores(pbp_paths)
    return audit(join_rows(settled, predictions), scores)


# -----------------------------------------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="H1 goalie-pull residual-partition audit for the NHL saves D1 cohort "
                    "(measurement-only; frozen until the Dec 21, 2026 / n = 400 review -- see the "
                    "module docstring).")
    ap.add_argument("--predictions", required=True,
                    help="banked D1 saves predictions (served trained payload JSON, list, or JSONL)")
    ap.add_argument("--settled", required=True, action="append",
                    help="settled outcomes JSONL (paper-track settled shape); repeatable")
    ap.add_argument("--pbp", required=True, action="append",
                    help="banked NHL PBP file (.json/.json.gz) or directory of them; repeatable")
    ap.add_argument("--out", default=None, help="also write the JSON report here")
    ap.add_argument(OVERRIDE_FLAG, action="store_true", dest="freeze_override",
                    help="acknowledge the freeze: H1 is frozen out of live D1 saves officials until "
                         "the n = 400 / Dec 21, 2026 judgment completes. Required only when an "
                         "input path is the live cohort. For the Dec 21 review, not routine use.")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        report = run(a.predictions, a.settled, a.pbp, override=a.freeze_override)
    except FrozenCohortError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(report, indent=1)
    print(text)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
