#!/usr/bin/env bash
# setup_parlay_schedules.sh -- provision the full ParlayAPI accumulator fleet
# (jobs + Cloud Scheduler crons), sized to the $5 / 20,000-credit-per-month tier.
#
# Wraps deploy/setup_parlay_accumulator.sh once per (sport, kind, schedule).
#
# CREDIT BUDGET (20,000/mo). Billing: props = 1 credit per (event x market);
# game lines = 1 credit per market for the whole slate; slate discovery = 1.
#   MLB props (6 markets x ~15 games + 1) ~= 91 cr/snapshot x 5/day ~= 13,600/mo
#   MLB game lines (3 markets, whole slate) =   3 cr/snapshot x 8/day ~=    720/mo
#   NBA jobs: STAGED (no scheduler) -- offseason, ~0 credits until October.
#   Active total ~14,300/mo -- safe headroom under 20,000.
#
# Prereqs: image rebuilt with current nba/ + the 6-market config
#   (./deploy/deploy_service.sh) and secret `parlay-api-key` present.
#
# Usage:
#   PROJECT_ID=concrete-crow-445205-m4 bash ./deploy/setup_parlay_schedules.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
HERE="$(cd "$(dirname "$0")" && pwd)"

acc() {  # sport kind schedule
  PROJECT_ID="$PROJECT_ID" SPORT="$1" KIND="$2" SCHEDULE="$3" \
    bash "$HERE/setup_parlay_accumulator.sh"
}

echo "=== ParlayAPI accumulator fleet ($PROJECT_ID) ==="

# --- MLB -- RETIRED (credit unification) -----------------------------------
# The live snapshot job (ODDS_PRIMARY=parlay; mlb-snapshot-* schedulers calling
# /snapshot-odds 4x/day) is now the single ParlayAPI consumer: it pulls props +
# game lines, writes the SGO-shaped snapshot AND banks the OddsAccum artifacts.
# Running these standalone accumulators too would double-spend credits
# (4x/day snapshot + 5x/day accumulator ~= 24k/mo > 20k). Jobs stay PROVISIONED
# (schedule "") so they can be invoked manually if ever needed.
acc baseball_mlb props      ""
acc baseball_mlb game_lines ""

# --- NBA (offseason -- stage jobs, NO scheduler until ~October) -------------
acc basketball_nba props      ""
acc basketball_nba game_lines ""

echo ""
echo "Active: parlay-accum-mlb-props (5x/day), parlay-accum-mlb-game-lines (every 3h)."
echo "Staged (no schedule yet): parlay-accum-nba-props, parlay-accum-nba-game-lines."
echo ""
echo "In October, schedule NBA, e.g.:"
echo "  PROJECT_ID=$PROJECT_ID SPORT=basketball_nba KIND=props SCHEDULE='0 23,0,1,2,3 * * *' bash deploy/setup_parlay_accumulator.sh"
