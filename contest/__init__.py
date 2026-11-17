"""contest -- paper-money betting contest engine (beezy.fyi Bankroll Challenge).

Sport-agnostic shared infra, NOT a sport pillar. Play money only: nothing here moves real money or places a real order.
Design and open decisions: docs/designs/bowl-bankroll-contest-scope.md.

    odds      American odds <-> decimal, payout rounding (integer cents, half-up)
    rules     ContestRules: stake limits, parlay / teaser limits, teaser payout tables (PLACEHOLDERS until the real rulebook)
    slips     Leg / Slip, validation (limits, closing buffer, quote staleness, price changes) and pricing
    grading   leg grading from a final score, bet settlement (straight / parlay / teaser, pushes and voids)
    schema    DDL (Postgres in prod, SQLite in tests)
    store     DB operations: join, place_bet, settle_game, void_game, adjust, reconcile, leaderboard, visible_bets

Money is always INTEGER CENTS. Times are ISO-8601 UTC strings in the DB and aware datetimes in code. The server clock is the only
clock: nothing here trusts a client timestamp.
"""
