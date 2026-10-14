# BettingPros multi-sport player-prop coverage probe -- 2026-10-14

Read-only probe (`scripts/bettingpros_multisport_probe.py`, no GCS/DB writes, ~100 requests) of the free BettingPros
`/v3` feed that `mlb_core/odds/bettingpros.py` already uses for MLB. Answers the P0 item in
`docs/designs/multisport_ev_sweep.md` s5 ("probe BettingPros same-day per-book coverage for the three sports").
Run from a laptop (BettingPros is reachable; it was NOT probed from Cloud Run -- the original P0 wording said Cloud Run,
and that egress path is still untested).

To re-run: `PYTHONPATH=. python3 scripts/bettingpros_multisport_probe.py --out probe.json`.

## Market ids (resolved live from `/v3/markets`, category `player-props`, period `game`)

| Sport | stat | market_id | slug |
|---|---|---|---|
| NFL | pass_yds / rush_yds / rec_yds / receptions / pass_tds | 103 / 107 / 105 / 104 / 102 | passing-yards, rushing-yards, receiving-yards, receptions, passing-touchdowns |
| NBA | pts / reb / ast | 156 / 157 / 151 | points, rebounds, assists |
| NHL | goals / points / assists / sog / saves | 318 / 319 / 320 / 321 / 322 | goals, points, assists, shots, saves |

Do not copy the `most-*` slugs (73/74/75/76 etc.): those are "who has the most" specials, not O/U props. The existing
`MARKETS` table in `bettingpros.py` is MLB-only; `fetch_markets/fetch_events/fetch_offers` now take `sport=` (default
`"MLB"`, so MLB callers are unchanged).

## Coverage (probe window 2026-10-14 .. 10-21; "two-sided @ modal" = books quoting BOTH sides at the most common line)

| Sport / stat | offers | books quoting (median) | two-sided @ modal line (median) | offers with >=3 two-sided |
|---|---|---|---|---|
| NFL pass_yds | 8 | 13 | 5 | 88% |
| NFL rush_yds | 23 | 11 | 8 | 96% |
| NFL rec_yds | 41 | 14 | 7 | 90% |
| NFL receptions | 41 | 13 | 11 | 98% |
| NFL pass_tds | 8 | 12 | 11 | 100% |
| NHL goals (anytime: mostly one-sided Yes) | 30 | 10 | 4 | 87% |
| NHL points / assists / sog | 30 each | 5-6 | 4-5 | 80-90% |
| NHL saves | 2 | 2 | 2 | 50% |
| NBA pts / reb / ast (preseason slate) | 12 each | **1 (FanDuel only)** | 1 | 0% |

- **NFL is deep** and usable as a free second consensus feed (5 NFL stats = exactly the ParlayAPI `PARLAY_MARKETS` set).
  Main-line splits are the catch: `pass_yds` (100%), `rec_yds` (93%) and `rush_yds` (87%) have multiple quoted lines per prop, so exact-point consensus will starve
  the same way it does on ParlayAPI (CONTEXT s15.11) -- the pooled adjacent-half-point logic in `ev/props.py` applies.
- **NBA is not usable yet**: it is preseason (regular season starts ~2026-10-21); only FanDuel quotes. Re-run once the
  season starts before drawing any NBA conclusion.
- **NHL is usable** for skater props; goalie saves are thin.
- No evidence same-day is worse than later-week for NFL/NHL (the MLB docstring's "majors populate on settled dates only"
  did not reproduce). Caveat: the NFL probe's nearest game was 2026-10-15 (+1d), so true game-day (+0d) NFL was not observed.

## Sharp / exchange books -- the pasted claim is half right

`/v3/books` lists Pinnacle (id 2), Novig (60), ProphetX (38), Kalshi (68), Polymarket US (75), but the feed only carries
prices for some:

| Book | NFL props (share of offers) | NHL | Notes |
|---|---|---|---|
| **Pinnacle** | **0% on every market, and 0 on NFL moneyline/spread/total too** | 0% | In the catalog but never quotes in this feed. Checked on raw selections, so not a parsing artifact. Cannot be used as a sharp anchor from BettingPros. |
| Novig | 59-75% (NFL), 83% NHL goals | 0-33% elsewhere | Present, two-sided on many props |
| ProphetX | 80-95% (NFL) | 0-23% | Present |
| Kalshi | 29-83% (NFL) | 0% | Present, but it is the venue the sweep trades against -- must be EXCLUDED from any consensus (mirrors `EXCLUDE_BOOKS` in `mlb/execution/kalshi/consensus.py`) |
| Polymarket US | 0-50% | 0% | Sparse |

DFS/pick'em books (PrizePicks, Underdog, Sleeper, Fliff, Betr) are mixed into every prop and are not two-sided prices;
`ev/props.py` already excludes DFS books from consensus -- any BettingPros adapter must do the same.

Also note `mlb_core/odds/bettingpros.py::BOOK_ID_TO_NAME` is a trimmed US-book map: Novig/ProphetX/Kalshi/Pinnacle ids are
DROPPED by `_sel_by_line`, so today's MLB BettingPros rows never contain them. Using them for NFL/NHL needs a new book map,
not just `sport="NFL"`.

## Not done / next
- No NFL/NHL ingest adapter was built -- this PR only measures. Next step would be a BettingPros -> `ev.props.Quote`
  adapter (book map incl. Novig/ProphetX, DFS + Kalshi exclusion) feeding `ev.sweep_nfl_props` as a cross-check feed.
- Re-run on a Sunday game day for true +0d NFL, and after NBA opening night.
