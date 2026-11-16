"""DEC-09 -- grader for the DEC-08 first-scorer classifier (first basket / first TD).

DEC-08 banked the market (prices raw-first at T-120/T-30/T-5, outcomes derived back to
2019 NBA / 2018 NFL) but never built the grading machinery. This module is that build.
It joins banked T-30 boards to settled outcomes, constructs the four pre-registered
grading arms, and judges them against the pre-registered bars.

Pre-registered design (first-basket-classifier-spike-2026-11-11.md, unchanged except the
one Argon amendment below):
  arms:  (a) book-only fair (de-vig over the grading book's complete listed set)
         (b) base rates (season-to-date involvement shares, no model)
         (c) Decision 1 raw (Google Decisions API, model gemini-decision-1; ONE `choice`
             call per game over the book's listed outcome set, input = pre-tip features)
         (d) encompassing blend of (c) into (a), weight fit on the chronological first
             40% of graded games, applied untouched to the remaining 60%
  bars at n >= 300 priced+settled (interim read at n = 150, no promotion before 300):
         1. Decision 1 log loss beats base rates by >= 0.02, paired day-cluster 95% CI
            excluding 0 (a fail kills the family; no blend result rescues it)
         2. blend log loss beats book-only by >= 0.010 on the held-out 60%, CI excl. 0
         3. staking the classifier's top choice at the best banked decision-time price
            returns ROI >= +3% over the graded sample

ARGON AMENDMENT (RUL-20261116-profitable-markets-paths, Q2 Rank 3 -- ADOPTED): the
book-only fair uses parameter-free POWER de-vig (method="log": fair_i = q_i**k with k
solved so the fairs sum to 1, the house convention in mlb_core.odds.utils.devig_three_way)
instead of proportional de-vig, because the high-hold longshot skew in these one-sided
lists creates phantom +EV under proportional. All standard errors are game-day clustered
(ev.novig.clv_gate.cluster_mean_ci). The proportional book arm is still computed so the
memo can report the amendment's measured effect.

DECISIONS THE SPIKE LEFT OPEN (made here, flagged in the DEC-09 memo -- none silent):
  D1 grading book = the priced book with the largest listed set at T-30 (tie: book key
     ascending). "The book" is singular in the spike; several books can clear the gate.
  D2 NBA settlement variant: primary = first FIELD GOAL (first_fg_*); the first_point_*
     (FT-inclusive) variant is computed for the book/base arms as a labeled sensitivity.
     The outcomes job stores both precisely because book definitions differ.
  D3 log-loss arms grade only games whose realized scorer is IN the grading book's
     listed set (the spike defines the listed set as the game's outcome set). The
     coverage rate (scorer listed / settled) is reported as its own funnel line; the
     staking arm grades ALL priced+settled games (an unlisted scorer is simply a loss).
  D4 base rates: current-season scored-first counts strictly before the game's ET date,
     add-0.5 smoothing over the listed set (an unsmoothed zero would make log loss
     infinite the first time a cold player scores first).
  D5 blend form = linear pool w * p_clf + (1 - w) * p_book (the spike says "encompassing
     blend" without a formula); w on a 0.01 grid minimizing fit-window log loss.
  D6 classifier v1 input = starters (actual, from banked box scores) + outcomes-derived
     involvement counts/shares + team first-scorer rates. The spike also lists PBP-derived
     first-shot-attempt share and tip-win / first-possession rates; those are OMITTED in
     v1 because the hoopR PBP mirror is stale (banked through Nov 10, 2026) -- a flagged
     spec gap, to close when the mirror refreshes.
  D7 NFL market keys: ParlayAPI returns the first-TD market under book-specific keys
     (player_first_td, player_first_touchdown, player_first_touchdown_scorer, ...) while
     nba.config.FIRST_SCORER_MARKETS names only player_1st_td, so the DEC-08 accumulator's
     summary layer reports 0 priced NFL games. The RAW payloads hold the boards (raw-first
     archiving did its job); this grader reads the alias set in MARKET_ALIASES from raw.
     Fixing the accumulator config is a DEC-08 production change, NOT made here.
  D8 classifier probabilities are floored at 1e-4 for log loss, and are banked in the run
     artifact and reused by event_id on later runs (the protocol is decision-time; a
     re-call weeks later must not rewrite history).
  D9 (recovery-run addition, Nov 16) NFL short keys collide for distinct players across
     a game's priced listed sets (A.J. Brown / Amon-Ra St. Brown; Travis / Trevor Etienne --
     both pairs observed on the Nov 15, 2026 slate). Under a collided key no arm can
     attribute probabilities, best prices, or the realized scorer to a person, so the
     game is ungradeable: counted in the funnel as key_collision and excluded from the
     LL and staking arms (fail-closed, cf. the namesake_history drop in the NBA joiner).
     Residual limitation, disclosed not fixed: base-rate history counts are keyed the
     same way, so a collided key's counts are the union of both players' counts, and an
     outcomes-side short form is attributed to the one listed namesake when only one is
     listed (the outcomes table's player_id would be needed to do better).

Fail-closed throughout: a game that cannot be joined, parsed, or priced is counted in the
funnel under its reason and excluded -- never imputed, never invented.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
SNAPSHOT = "T-30"                    # the pre-registered decision-time snapshot
MIN_OUTCOMES = 8                     # completeness gate (nba.config.FIRST_SCORER_MIN_OUTCOMES)
SMOOTHING_ALPHA = 0.5                # D4
PROB_FLOOR = 1e-4                    # D8
BLEND_FIT_FRACTION = 0.4             # pre-registered 40/60 split
BLEND_MIN_FIT = 10                   # below this the blend is reported not-fit (diagnostic tier only)
BAR_D1_GAIN = 0.02                   # Decision 1 vs base rates, log-loss gain
BAR_BLEND_GAIN = 0.010               # blend vs book-only, log-loss gain (held-out)
BAR_STAKING_ROI = 0.03               # top-choice staking ROI
FULL_GRADE_N = 300
INTERIM_N = 150

SPORT_NBA = "basketball_nba"
SPORT_NFL = "americanfootball_nfl"
MARKET_NBA = "player_first_basket"
# D7: every key under which a book's game-level first-TD-scorer board has been observed.
MARKET_ALIASES = {
    SPORT_NBA: {MARKET_NBA},
    SPORT_NFL: {"player_1st_td", "player_first_td", "player_first_touchdown",
                "player_first_touchdown_scorer"},
}

# ParlayAPI full team name -> the abbreviation the outcomes tables carry
# (hoopR/ESPN for NBA, nflverse for NFL).
NBA_TEAM_ABBREV = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GS", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO", "New York Knicks": "NY",
    "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SA", "Toronto Raptors": "TOR", "Utah Jazz": "UTAH",
    "Washington Wizards": "WAS",
}
NFL_TEAM_ABBREV = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}
TEAM_ABBREV = {SPORT_NBA: NBA_TEAM_ABBREV, SPORT_NFL: NFL_TEAM_ABBREV}

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


# ---------------------------------------------------------------------------
# small pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def implied_prob(american) -> float:
    """American odds -> vig-inclusive implied probability."""
    a = float(american)
    return 100.0 / (a + 100.0) if a > 0 else abs(a) / (abs(a) + 100.0)


def american_to_decimal(american) -> float:
    a = float(american)
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def devig_n(implied: list[float], method: str = "power") -> list[float]:
    """N-outcome de-vig for a one-sided Yes list. `implied` are vig-inclusive probs.

    proportional: fair_i = q_i / sum(q).
    power (method="log", the Argon amendment): fair_i = q_i**k with k >= 1 solved by
    bisection so the fairs sum to 1 -- the mlb_core.odds.utils.devig_three_way power
    convention generalized to n outcomes. Shades longshots harder than proportional.
    """
    qs = [float(q) for q in implied]
    if any(q <= 0.0 or q >= 1.0 for q in qs):
        raise ValueError(f"devig_n: implied probs must be in (0, 1), got {qs}")
    s = sum(qs)
    if method == "proportional":
        return [q / s for q in qs]
    if method != "power":
        raise ValueError(f"devig_n: unknown method {method!r}")
    if s <= 1.0 + 1e-12:
        return [q / s for q in qs]
    lo, hi = 0.5, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(q ** mid for q in qs) > 1.0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    raw = [q ** k for q in qs]
    tot = sum(raw)
    return [v / tot for v in raw]


def normalize_player(name: str) -> str:
    """Canonical player key: lowercase, punctuation stripped, suffix tokens dropped."""
    toks = re.sub(r"[^a-z0-9 ]", " ", str(name).lower()).split()
    toks = [t for t in toks if t not in _SUFFIXES]
    return " ".join(toks)


def nfl_short_key(name: str) -> tuple[str, str]:
    """nflverse PBP name key: (first initial, last name token) -- outcomes carry 'F.Lastname'."""
    toks = normalize_player(name).split()
    if not toks:
        return ("", "")
    return (toks[0][0], toks[-1])


def et_date(commence_time: str) -> str:
    """ET calendar date (YYYY-MM-DD) of an ISO commence_time -- the outcomes game_date basis."""
    dt = datetime.fromisoformat(str(commence_time).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date().isoformat()


def season_of(sport: str, date_iso: str) -> int:
    """Season label for a game date, in each outcomes table's own convention.

    NBA outcomes use the hoopR ending year (2026-27 season = 2027); NFL uses the
    season's starting year (2026 season includes Jan/Feb 2027 playoffs)."""
    y, m, _ = (int(x) for x in date_iso.split("-"))
    if sport == SPORT_NBA:
        return y + 1 if m >= 7 else y
    return y if m >= 3 else y - 1


def log_loss(prob: float) -> float:
    """Log loss of the realized outcome at `prob` (floored, D8)."""
    return -math.log(max(float(prob), PROB_FLOOR))


# ---------------------------------------------------------------------------
# banked boards (raw payloads) -> per-game decision-time boards
# ---------------------------------------------------------------------------

def extract_board(payload: dict, sport: str) -> dict[str, list[dict]]:
    """One raw ParlayAPI event payload -> {book: [{player, price}, ...]} for the sport's
    first-scorer market (alias-aware, D7). Mirrors nba.odds.first_scorer.flatten_micro_market's
    junk filters but keeps every book's full set and accepts the observed alias keys."""
    from nba.odds.parlay_extract import split_team_tail
    aliases = MARKET_ALIASES[sport]
    out: dict[str, list[dict]] = {}
    for book in payload.get("bookmakers", []) or []:
        bkey = book.get("key", "")
        for market in book.get("markets", []) or []:
            if market.get("key") not in aliases:
                continue
            seen: dict[str, dict] = {}
            for o in market.get("outcomes", []) or []:
                if (o.get("name") or "").strip().lower() not in ("yes", "") and o.get("description") is None:
                    continue
                # Books (notably Bovada) suffix a team tail ("Amon-Ra St. Brown (DET)");
                # strip it exactly as the DEC-08 accumulator does, or name matching breaks.
                player, _hint = split_team_tail(o.get("description") or o.get("name") or "")
                if not player or "{" in player or "}" in player or " @ " in player:
                    continue
                if o.get("price") is None:
                    continue
                seen.setdefault(normalize_player(player),
                                {"player": player, "price": int(o["price"])})
            if seen:
                out.setdefault(bkey, []).extend(seen.values())
    return out


def assemble_games(storage, sport: str) -> tuple[list[dict], dict]:
    """All banked events for `sport` with their T-30 board state. Returns (games, funnel).

    A game dict: event_id, commence_time, date_et, home_team, away_team, boards
    ({book: rows} at T-30 or None), priced_books, grading_book, listed (grading book's
    rows), best_price ({player_key: (decimal, american, book)} across priced books)."""
    keys = storage.list_keys(f"FirstScorer/{sport}/raw/")
    by_event: dict[str, dict[str, str]] = {}
    for k in keys:
        fname = k.rsplit("/", 1)[-1]
        event_id, _, snap = fname[:-5].rpartition("_") if fname.endswith(".json") else (fname, "", "")
        if event_id and snap:
            by_event.setdefault(event_id, {})[snap] = k
    funnel = {"banked_events": len(by_event), "with_t30": 0, "priced": 0, "key_collision": 0}
    games = []
    for event_id, snaps in sorted(by_event.items()):
        if SNAPSHOT not in snaps:
            games.append({"event_id": event_id, "boards": None, "drop_reason": "no_t30_snapshot"})
            continue
        funnel["with_t30"] += 1
        payload = json.loads(storage.read_bytes(snaps[SNAPSHOT]))
        boards = extract_board(payload, sport)
        priced_books = {b: rows for b, rows in boards.items() if len(rows) >= MIN_OUTCOMES}
        game = {
            "event_id": event_id,
            "commence_time": payload.get("commence_time") or "",
            "date_et": et_date(payload.get("commence_time") or ""),
            "home_team": payload.get("home_team"), "away_team": payload.get("away_team"),
            "boards": boards, "priced_books": sorted(priced_books),
        }
        if not priced_books:
            game["drop_reason"] = "no_priced_book"
            games.append(game)
            continue
        funnel["priced"] += 1
        # D1: grading book = largest listed set; tie-break book key ascending.
        grading = sorted(priced_books, key=lambda b: (-len(priced_books[b]), b))[0]
        # D9 check runs over the union of every priced book's set: the staking arm's
        # best price spans books, so a namesake listed only at another book corrupts
        # attribution just as one inside the grading set does.
        collisions = listed_key_collisions(
            [r for rows in priced_books.values() for r in rows], sport)
        if collisions:
            # D9: ambiguous identity inside the grading set -- ungradeable, fail closed.
            game["drop_reason"] = "key_collision"
            game["key_collisions"] = collisions
            funnel["key_collision"] += 1
            games.append(game)
            continue
        game["grading_book"] = grading
        game["listed"] = priced_books[grading]
        best: dict[str, tuple[float, int, str]] = {}
        for b in game["priced_books"]:
            for row in priced_books[b]:
                pk = player_key(sport, row["player"])
                dec = american_to_decimal(row["price"])
                if pk not in best or dec > best[pk][0]:
                    best[pk] = (dec, row["price"], b)
        game["best_price"] = best
        games.append(game)
    return games, funnel


# ---------------------------------------------------------------------------
# outcomes: load, join, base rates
# ---------------------------------------------------------------------------

def load_outcomes(storage, sport: str):
    """All banked outcome seasons for the sport as one DataFrame (empty frame if none)."""
    import pandas as pd
    prefix = (f"FirstScorer/outcomes/basketball_nba/first_basket_" if sport == SPORT_NBA
              else f"FirstScorer/outcomes/americanfootball_nfl/first_td_")
    frames = []
    for k in sorted(storage.list_keys("FirstScorer/outcomes/")):
        if k.startswith(prefix) and k.endswith(".parquet"):
            frames.append(storage.read_parquet(k))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def join_outcome(game: dict, outcomes, sport: str):
    """The outcomes row for a game (match on ET date + team abbrevs), or None."""
    if outcomes is None or len(outcomes) == 0:
        return None
    amap = TEAM_ABBREV[sport]
    home, away = amap.get(game.get("home_team")), amap.get(game.get("away_team"))
    if home is None or away is None:
        return None
    rows = outcomes[(outcomes["game_date"].astype(str).str[:10] == game["date_et"])
                    & (outcomes["home_team"] == home) & (outcomes["away_team"] == away)]
    if len(rows) == 0:
        return None
    return rows.iloc[0]


def scorer_key(sport: str, outcome_row, variant: str = "fg") -> str | None:
    """Normalized realized-scorer key from an outcomes row (NBA variant per D2)."""
    if outcome_row is None:
        return None
    if sport == SPORT_NFL:
        name = outcome_row.get("first_td_player")
        return " ".join(nfl_short_key(name)) if isinstance(name, str) and name else None
    col = "first_fg_player" if variant == "fg" else "first_point_player"
    name = outcome_row.get(col)
    return normalize_player(name) if isinstance(name, str) and name else None


def base_rates(game: dict, outcomes, sport: str, variant: str = "fg") -> dict[str, float]:
    """Arm (b): season-to-date scored-first shares over the grading book's listed set.

    Counts are the game's own season (outcomes-table convention), game_date strictly
    before the game's ET date; add-alpha smoothing over the listed set (D4). Player keys
    are the sport's match keys (normalized full name NBA, nflverse short key NFL)."""
    season = season_of(sport, game["date_et"])
    hist = outcomes[(outcomes["season"] == season)
                    & (outcomes["game_date"].astype(str).str[:10] < game["date_et"])]
    counts: dict[str, int] = {}
    if sport == SPORT_NFL:
        for name in hist["first_td_player"].dropna():
            counts[" ".join(nfl_short_key(name))] = counts.get(" ".join(nfl_short_key(name)), 0) + 1
    else:
        col = "first_fg_player" if variant == "fg" else "first_point_player"
        for name in hist[col].dropna():
            counts[normalize_player(name)] = counts.get(normalize_player(name), 0) + 1
    listed_keys = [player_key(sport, r["player"]) for r in game["listed"]]
    weights = {pk: counts.get(pk, 0) + SMOOTHING_ALPHA for pk in listed_keys}
    tot = sum(weights.values())
    return {pk: w / tot for pk, w in weights.items()}


def player_key(sport: str, name: str) -> str:
    """The match key for a listed player name, per sport."""
    return " ".join(nfl_short_key(name)) if sport == SPORT_NFL else normalize_player(name)


def listed_key_collisions(listed: list[dict], sport: str) -> dict[str, list[str]]:
    """{player_key: [distinct normalized full names]} for keys claimed by 2+ distinct
    players in one game's listed set (D9). Distinctness is judged on the letter-only
    concatenation of the normalized name, so punctuation variants of ONE person across
    books ("D.J. Moore" / "DJ Moore") do not false-positive, while genuinely different
    people sharing a short key ("A.J. Brown" / "Amon-Ra St. Brown") do."""
    by_key: dict[str, dict[str, str]] = {}
    for r in listed:
        norm = normalize_player(r["player"])
        by_key.setdefault(player_key(sport, r["player"]), {})[re.sub(r"[^a-z]", "", norm)] = norm
    return {k: sorted(v.values()) for k, v in by_key.items() if len(v) > 1}


def book_fairs(game: dict, sport: str, method: str = "power") -> dict[str, float]:
    """Arm (a): de-vigged fairs over the grading book's listed set (Argon amendment:
    power by default; proportional kept for the amendment-effect comparison)."""
    implied = [implied_prob(r["price"]) for r in game["listed"]]
    fairs = devig_n(implied, method)
    return {player_key(sport, r["player"]): f
            for r, f in zip(game["listed"], fairs)}


# ---------------------------------------------------------------------------
# classifier (arm c): Decision 1 via the Decisions API
# ---------------------------------------------------------------------------

def involvement_lines(game: dict, outcomes, sport: str, starters: dict | None = None) -> list[str]:
    """Per-listed-player pre-tip feature lines for the classifier input (D6).

    starters: {player_key: bool} actual-starter flags from banked box scores (NBA only;
    None when unavailable). Counts are current-season, strictly before the game date."""
    season = season_of(sport, game["date_et"])
    hist = outcomes[(outcomes["season"] == season)
                    & (outcomes["game_date"].astype(str).str[:10] < game["date_et"])]
    if sport == SPORT_NFL:
        scorer_col, team_col = "first_td_player", "first_td_team"
        keys = [" ".join(nfl_short_key(n)) for n in hist[scorer_col].dropna()]
    else:
        scorer_col, team_col = "first_fg_player", "first_fg_team"
        keys = [normalize_player(n) for n in hist[scorer_col].dropna()]
    from collections import Counter
    pcount = Counter(keys)
    tcount = Counter(hist[team_col].dropna())
    team_games = Counter()
    for _, r in hist.iterrows():
        team_games[r["home_team"]] += 1
        team_games[r["away_team"]] += 1
    out = []
    for row in game["listed"]:
        pk = player_key(sport, row["player"])
        parts = [f"{row['player']}: {pcount.get(pk, 0)} first scores this season before today"]
        if starters is not None:
            parts.append("starter" if starters.get(pk) else "not a confirmed starter")
        out.append("; ".join(parts))
    rates = {}
    for full, ab in TEAM_ABBREV[sport].items():
        if full in (game.get("home_team"), game.get("away_team")):
            g = team_games.get(ab, 0)
            rates[full] = (tcount.get(ab, 0) / g) if g else 0.0
    return out, rates


def build_classifier_input(game: dict, outcomes, sport: str, starters: dict | None = None) -> str:
    feat_lines, rates = involvement_lines(game, outcomes, sport, starters)
    market_label = "first touchdown scorer" if sport == SPORT_NFL else "first field goal scorer"
    lines = [
        f"Game: {game.get('away_team')} at {game.get('home_team')}, "
        f"commence {game.get('commence_time')} (ET date {game['date_et']}).",
        f"Market: the game's {market_label}, chosen from the listed players only.",
    ]
    for full, rate in rates.items():
        lines.append(f"{full} scored first in {rate:.0%} of its games this season before today.")
    lines.append("Listed players with season-to-date involvement (all figures computed "
                 "strictly before this game):")
    lines.extend(f"- {l}" for l in feat_lines)
    return "\n".join(lines)


def classifier_question(game: dict, sport: str) -> dict:
    market_label = "first touchdown scorer" if sport == SPORT_NFL else "first field goal scorer"
    criteria = {}
    for row in game["listed"]:
        criteria[row["player"]] = f"listed at {row['price']:+d} by the book"
    return {"first_scorer": {
        "type": "choice",
        "instructions": (f"From the input's listed players only, give the probability "
                         f"distribution over who will be the game's {market_label}. "
                         f"The options are exactly the listed players."),
        "criteria": criteria,
    }}


def call_classifier(client, game: dict, outcomes, sport: str, starters: dict | None = None):
    """One Decisions call for the game -> {player_key: prob} over the listed set, or
    None on abstention / unusable response (fail closed; the game is counted, not filled)."""
    from ev.decisions_client import DecisionsClient
    input_text = build_classifier_input(game, outcomes, sport, starters)
    result = client.call(input_text, classifier_question(game, sport))
    if result is None or getattr(result, "abstained", False):
        return None
    answers = getattr(result, "answers", None) or {}
    dist = DecisionsClient.probabilities(answers.get("first_scorer"))
    if not dist:
        return None
    listed = {player_key(sport, r["player"]) for r in game["listed"]}
    out = {}
    for name, p in dist.items():
        pk = player_key(sport, name)
        if pk in listed:
            out[pk] = out.get(pk, 0.0) + float(p)
    if not out:
        return None
    tot = sum(out.values())
    if tot <= 0:
        return None
    # Options the API left at zero stay zero here; the log-loss floor (D8) handles them.
    for pk in listed:
        out.setdefault(pk, 0.0)
    return {pk: v / tot for pk, v in out.items()}


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------

def blend_probs(book: dict[str, float], clf: dict[str, float], w: float) -> dict[str, float]:
    return {pk: w * clf.get(pk, 0.0) + (1.0 - w) * book.get(pk, 0.0) for pk in book}


def fit_blend_weight(records: list[dict]) -> float:
    """Grid-search w in [0, 1] minimizing mean classifier-vs-book blend log loss on the
    fit window (records carry book/clf prob dicts + the realized scorer key)."""
    best_w, best_ll = 0.0, float("inf")
    for i in range(101):
        w = i / 100.0
        ll = sum(log_loss(blend_probs(r["book"], r["clf"], w)[r["scorer"]])
                 for r in records) / len(records)
        if ll < best_ll:
            best_ll, best_w = ll, w
    return best_w


def grade(records: list[dict], sport: str, staking_records: list[dict] | None = None) -> dict:
    """Judge graded records against the pre-registered bars.

    Each record: date_et, scorer (key), book / book_prop / base / clf prob dicts,
    top_choice (key), top_decimal (best banked price for the top choice).
    `records` are the log-loss-graded games (realized scorer in the listed set, D3);
    `staking_records` are ALL priced+settled games (default: same as records) -- an
    unlisted realized scorer is simply a lost stake there.
    Returns per-arm log losses, paired day-clustered gains with CIs, staking ROI, the
    verdict tier (full / interim / accrual) and per-bar pass flags (None when the tier
    does not judge the bar)."""
    from ev.novig.clv_gate import cluster_mean_ci
    n = len(records)
    tier = "full" if n >= FULL_GRADE_N else ("interim" if n >= INTERIM_N else "accrual")
    out = {"sport": sport, "n_graded": n, "tier": tier}
    if n == 0:
        srecs = staking_records or []
        profits = [(r["top_decimal"] - 1.0) if r["top_choice"] == r["scorer"] else -1.0
                   for r in srecs]
        out["staking"] = {"n": len(srecs),
                          "roi": (sum(profits) / len(profits)) if profits else None,
                          "hits": sum(1 for r in srecs if r["top_choice"] == r["scorer"])}
        out["bars"] = {"bar1_d1_beats_base": None, "bar2_blend_beats_book": None,
                       "bar3_staking_roi": None}
        return out
    dates = [r["date_et"] for r in records]
    ll = {arm: [log_loss(r[arm][r["scorer"]]) for r in records]
          for arm in ("book", "book_prop", "base", "clf")}
    out["mean_ll"] = {arm: sum(v) / n for arm, v in ll.items()}
    gains = {
        "d1_vs_base": [b - c for b, c in zip(ll["base"], ll["clf"])],
        "book_power_vs_prop": [p - w for p, w in zip(ll["book_prop"], ll["book"])],
    }
    out["gains"] = {k: cluster_mean_ci(v, dates) for k, v in gains.items()}
    # blend: chronological first 40% fit, rest judged (needs both windows non-empty)
    ordered = sorted(records, key=lambda r: (r["date_et"], r.get("commence_time", "")))
    n_fit = int(len(ordered) * BLEND_FIT_FRACTION)
    out["blend"] = {"fit": False}
    if n_fit >= 1 and len(ordered) - n_fit >= 1 and n >= BLEND_MIN_FIT:
        w = fit_blend_weight(ordered[:n_fit])
        held = ordered[n_fit:]
        ll_blend = [log_loss(blend_probs(r["book"], r["clf"], w)[r["scorer"]]) for r in held]
        ll_book_h = [log_loss(r["book"][r["scorer"]]) for r in held]
        gain = [b - x for b, x in zip(ll_book_h, ll_blend)]
        out["blend"] = {"fit": True, "w": w, "n_fit": n_fit, "n_held": len(held),
                        "mean_ll_blend": sum(ll_blend) / len(held),
                        "gain_vs_book": cluster_mean_ci(gain, [r["date_et"] for r in held])}
    # staking: classifier top choice at best banked price, ALL priced+settled games
    srecs = staking_records if staking_records is not None else records
    profits = [(r["top_decimal"] - 1.0) if r["top_choice"] == r["scorer"] else -1.0
               for r in srecs]
    out["staking"] = {"n": len(srecs),
                      "roi": (sum(profits) / len(profits)) if profits else None,
                      "hits": sum(1 for r in srecs if r["top_choice"] == r["scorer"])}
    # bars judged only at interim/full tiers
    judged = tier in ("interim", "full")
    g1 = out["gains"]["d1_vs_base"]
    bar1 = (g1["mean"] is not None and g1["mean"] >= BAR_D1_GAIN
            and g1["ci_lo"] is not None and g1["ci_lo"] > 0.0) if judged else None
    bar2 = None
    if judged and out["blend"].get("fit"):
        gb = out["blend"]["gain_vs_book"]
        bar2 = (gb["mean"] is not None and gb["mean"] >= BAR_BLEND_GAIN
                and gb["ci_lo"] is not None and gb["ci_lo"] > 0.0)
    bar3 = (out["staking"]["roi"] is not None
            and out["staking"]["roi"] >= BAR_STAKING_ROI) if judged else None
    out["bars"] = {"bar1_d1_beats_base": bar1, "bar2_blend_beats_book": bar2,
                   "bar3_staking_roi": bar3}
    return out
