"""
patch_e10_line_movement.py -- E10: line movement feature integration.

Patches:
  1. bet_tracker.py  -- add morning_odds + line_move_pct columns to schema
  2. capture_closing_lines.py -- compute line_move_pct when writing closing line
  3. run_k.py  -- pass morning_odds to log_bet for K/OUTS bets
  4. run_nrfi.py -- pass morning_odds to log_bet
  5. run_hr.py -- pass morning_odds to log_bet
  6. run_f5.py -- pass morning_odds to log_bet

Apply from Cloud Shell:
  cp ~/Downloads/line_movement.py ~/mlb-betting/mlb_core/odds/
  python3 ~/mlb-betting/patch_e10_line_movement.py

Then add to each retrain script's feature list (after confirming signal):
  "morning_line_move_pct" in HALFINN_FEATURES / K_FEATURES / etc.
  (This requires historical morning odds to be in the feature CSV,
   which means running the morning snapshot and storing it. The column
   will be NaN for all historical rows -- XGBoost handles NaN natively.
   After 200+ live bets with morning_odds populated, retrain with it.)
"""
import os
import subprocess
import sys

base = os.path.expanduser("~/mlb-betting")
errors = []


def patch(label, path, old, new):
    with open(path) as f:
        src = f.read()
    if old not in src:
        errors.append(f"ANCHOR NOT FOUND: {label} in {os.path.basename(path)}")
        print(f"  SKIP (anchor not found): {label}")
        return False
    src = src.replace(old, new, 1)
    with open(path, "w") as f:
        f.write(src)
    r = subprocess.run(["python3", "-m", "py_compile", path],
                       capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"  {'OK' if ok else 'SYNTAX ERROR'}: {label}")
    if not ok:
        errors.append(f"SYNTAX ERROR: {label}: {r.stderr}")
    return ok


# ── 1. bet_tracker.py: schema + migration + log_bet ──────────────────────────
print("E10.1: bet_tracker.py...")
bt = f"{base}/mlb_core/tracking/bet_tracker.py"

patch(
    "morning_odds schema column",
    bt,
    old="closing_odds REAL,\n\tclosing_prob REAL,\n\tclv_pct REAL\n)",
    new="closing_odds REAL,\n\tclosing_prob REAL,\n\tclv_pct REAL,\n\tmorning_odds INTEGER,\n\tline_move_pct REAL\n)",
)

patch(
    "morning_odds migration SQL constants",
    bt,
    old='# Migration: add CLV columns (T08, 2026-05-19).',
    new='''# Migration: add CLV columns (T08, 2026-05-19).
# Migration: add line movement columns (E10, 2026-05-21).
_MIGRATE_MORNING_ODDS_SQL = "ALTER TABLE bets ADD COLUMN morning_odds INTEGER"
_MIGRATE_LINE_MOVE_SQL    = "ALTER TABLE bets ADD COLUMN line_move_pct REAL"''',
)

patch(
    "morning_odds migration execute",
    bt,
    old='        for clv_sql in (_MIGRATE_CLOSING_ODDS_SQL, _MIGRATE_CLOSING_PROB_SQL, _MIGRATE_CLV_PCT_SQL):',
    new='        for clv_sql in (_MIGRATE_CLOSING_ODDS_SQL, _MIGRATE_CLOSING_PROB_SQL, _MIGRATE_CLV_PCT_SQL,\n'
        '                       _MIGRATE_MORNING_ODDS_SQL, _MIGRATE_LINE_MOVE_SQL):',
)

patch(
    "morning_odds log_bet parameter",
    bt,
    old='    book: str = None,\n) -> int:',
    new='    book: str = None,\n    morning_odds: int = None,\n) -> int:',
)

patch(
    "morning_odds INSERT columns",
    bt,
    old='            bet_type, model_prob, market_prob, edge, kelly_pct,\n'
        '                    odds, stake, kelly_triggered, paper, notes, created_at, lambda_k, proj_k, book)\n'
        '            VALUES\n'
        '                (:system, :game_date, :game_pk, :player, :away_team, :home_team,\n'
        '            :bet_type, :model_prob, :market_prob, :edge, :kelly_pct,\n'
        '                    :odds, :stake, :kelly_triggered, :paper, :notes, :created_at, :lambda_k, :proj_k, :book)',
    new='            bet_type, model_prob, market_prob, edge, kelly_pct,\n'
        '                    odds, stake, kelly_triggered, paper, notes, created_at, lambda_k, proj_k, book, morning_odds)\n'
        '            VALUES\n'
        '                (:system, :game_date, :game_pk, :player, :away_team, :home_team,\n'
        '            :bet_type, :model_prob, :market_prob, :edge, :kelly_pct,\n'
        '                    :odds, :stake, :kelly_triggered, :paper, :notes, :created_at, :lambda_k, :proj_k, :book, :morning_odds)',
)

patch(
    "morning_odds params dict",
    bt,
    old='"book": book,\n            },',
    new='"book": book,\n                "morning_odds": morning_odds,\n            },',
)

# ── 2. capture_closing_lines.py: compute line_move_pct ───────────────────────
print("E10.2: capture_closing_lines.py...")
ccl = f"{base}/runners/capture_closing_lines.py"

patch(
    "line_move_pct computation",
    ccl,
    old='            tracker.write_closing_line(bid, c_odds, complement_odds=comp_odds)\n'
        '            captured += 1',
    new='            tracker.write_closing_line(bid, c_odds, complement_odds=comp_odds)\n'
        '            # E10: compute line_move_pct from morning_odds\n'
        '            try:\n'
        '                from mlb_core.odds.line_movement import compute_line_move_pct\n'
        '                from sqlalchemy import text as _lmt\n'
        '                with tracker.engine.connect() as _lmc:\n'
        '                    _morn = _lmc.execute(\n'
        '                        _lmt("SELECT morning_odds FROM bets WHERE id=:id"),\n'
        '                        {"id": bid}\n'
        '                    ).fetchone()\n'
        '                if _morn and _morn[0] is not None:\n'
        '                    _lm = compute_line_move_pct(_morn[0], c_odds)\n'
        '                    if _lm is not None:\n'
        '                        with tracker.engine.begin() as _lmw:\n'
        '                            _lmw.execute(\n'
        '                                _lmt("UPDATE bets SET line_move_pct=:lm WHERE id=:id"),\n'
        '                                {"lm": _lm, "id": bid}\n'
        '                            )\n'
        '            except Exception as _lme:\n'
        '                logger.warning(f"line_move_pct failed for bet_id={bid}: {_lme}")\n'
        '            captured += 1',
)

# ── 3. run_k.py: load morning odds and pass to log_bet ───────────────────────
print("E10.3: run_k.py morning_odds...")
rk = f"{base}/runners/run_k.py"

# Load morning odds at start of _build_predictions
patch(
    "K load morning odds",
    rk,
    old='    # E04: load OUTS trained model (falls back to Normal proxy if not found)\n'
        '    _outs_booster, _outs_features, _outs_feat_means, _outs_nb_alpha, _outs_cal = _load_outs_model(cfg)',
    new='    # E04: load OUTS trained model (falls back to Normal proxy if not found)\n'
        '    _outs_booster, _outs_features, _outs_feat_means, _outs_nb_alpha, _outs_cal = _load_outs_model(cfg)\n\n'
        '    # E10: load morning snapshot for line movement signal\n'
        '    from mlb_core.odds.line_movement import load_morning_odds\n'
        '    _morning_odds = load_morning_odds(run_date) if run_date else {}',
)

# Pass morning_odds for K bets
patch(
    "K morning_odds in K results.append",
    rk,
    old='                        "market": "K",\n'
        '                        "bookmaker": k_info.get("bookmaker"),\n'
        '                    })',
    new='                        "market": "K",\n'
        '                        "bookmaker": k_info.get("bookmaker"),\n'
        '                        "morning_odds": _morning_odds.get(\n'
        '                            f"pitching_strikeouts-{row[\'_pitcher_id\']}-game-ou-{side.lower()}"\n'
        '                        ),\n'
        '                    })',
)

# Pass morning_odds for OUTS bets
patch(
    "K morning_odds in OUTS results.append",
    rk,
    old='                        "market": "OUTS",\n'
        '                        "bookmaker": outs_info.get("bookmaker"),\n'
        '                    })',
    new='                        "market": "OUTS",\n'
        '                        "bookmaker": outs_info.get("bookmaker"),\n'
        '                        "morning_odds": _morning_odds.get(\n'
        '                            f"pitching_outs-{row[\'_pitcher_id\']}-game-ou-{side.lower()}"\n'
        '                        ),\n'
        '                    })',
)

# Pass morning_odds in log_bet call
patch(
    "K morning_odds in log_bet",
    rk,
    old='            book = row.get("bookmaker"),\n'
        '        )',
    new='            book         = row.get("bookmaker"),\n'
        '            morning_odds = row.get("morning_odds"),\n'
        '        )',
)

# ── 4. run_nrfi.py: load morning odds ────────────────────────────────────────
print("E10.4: run_nrfi.py morning_odds...")
rn = f"{base}/runners/run_nrfi.py"

# Add morning odds load after snapshot freshness check
patch(
    "NRFI load morning odds",
    rn,
    old='    events = sgo.load_snapshot("Odds/sgo/latest.json")',
    new='    # E10: load morning snapshot for line movement signal\n'
        '    from mlb_core.odds.line_movement import load_morning_odds\n'
        '    _morning_odds = load_morning_odds(run_date)\n\n'
        '    events = sgo.load_snapshot("Odds/sgo/latest.json")',
)

# Pass morning_odds in NRFI results.append -- wire into the NRFI/YRFI O/U rows
patch(
    "NRFI morning_odds in results",
    rn,
    old='            "market_prob":  round(fair, 4),',
    new='            "market_prob":  round(fair, 4),\n'
        '            "morning_odds": _morning_odds.get(\n'
        '                f"points-all-1i-ou-{\'over\' if side == \'NRFI\' else \'under\'}"\n'
        '            ),',
)

# Pass morning_odds in NRFI log_bet
patch(
    "NRFI morning_odds in log_bet",
    rn,
    old='                book             = row.get("bookmaker"),\n'
        '            )',
    new='                book             = row.get("bookmaker"),\n'
        '                morning_odds     = row.get("morning_odds"),\n'
        '            )',
)

# ── 5. run_hr.py: load morning odds ──────────────────────────────────────────
print("E10.5: run_hr.py morning_odds...")
rhr = f"{base}/runners/run_hr.py"

patch(
    "HR load morning odds",
    rhr,
    old='    events = sgo.load_snapshot("Odds/sgo/latest.json")',
    new='    # E10: load morning snapshot for line movement signal\n'
        '    from mlb_core.odds.line_movement import load_morning_odds\n'
        '    _morning_odds = load_morning_odds(run_date)\n\n'
        '    events = sgo.load_snapshot("Odds/sgo/latest.json")',
)

# Wire morning_odds into HR log_bet -- HR bets use player MLB ID in odd_id
patch(
    "HR morning_odds in log_bet",
    rhr,
    old='                book         = row.get("bookmaker"),\n'
        '            )',
    new='                book         = row.get("bookmaker"),\n'
        '                morning_odds = _morning_odds.get(\n'
        '                    f"batting_homeRuns-{row.get(\'player_id\', \'\')}-game-yn-yes"\n'
        '                ),\n'
        '            )',
)

# ── 6. run_f5.py: load morning odds ──────────────────────────────────────────
print("E10.6: run_f5.py morning_odds...")
rf5 = f"{base}/runners/run_f5.py"

patch(
    "F5 load morning odds",
    rf5,
    old='    events = sgo.load_snapshot("Odds/sgo/latest.json")',
    new='    # E10: load morning snapshot for line movement signal\n'
        '    from mlb_core.odds.line_movement import load_morning_odds\n'
        '    _morning_odds = load_morning_odds(run_date)\n\n'
        '    events = sgo.load_snapshot("Odds/sgo/latest.json")',
)

patch(
    "F5 morning_odds in log_bet",
    rf5,
    old='                book         = row.get("bookmaker"),\n'
        '            )',
    new='                book         = row.get("bookmaker"),\n'
        '                morning_odds = _morning_odds.get(\n'
        '                    f"points-{row.get(\'side\',\'\').lower()}-1ix5-ml-{row.get(\'side\',\'\').lower()}"\n'
        '                ),\n'
        '            )',
)

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
if errors:
    print(f"COMPLETED WITH {len(errors)} ERRORS:")
    for e in errors:
        print(f"  {e}")
    print("\nThese are likely whitespace mismatches -- read the exact")
    print("anchor lines from the local file before re-running.")
    sys.exit(1)
else:
    print("ALL E10 PATCHES APPLIED")
    print()
    print("Next steps:")
    print("  1. git add -A")
    print("  2. git commit -m 'feat: E10 line movement feature -- schema + capture + runners'")
    print("  3. ./deploy/deploy_service.sh")
    print()
    print("After 200+ bets with morning_odds populated:")
    print("  - Add 'morning_line_move_pct' to HALFINN_FEATURES and K_FEATURES")
    print("  - Retrain all models to pick up the signal")
    print("  - Check feature importance: if near 0, discard")
