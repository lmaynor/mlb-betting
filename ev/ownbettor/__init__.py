"""
ev.ownbettor -- AUT-09: the OWN-ACCOUNT automated bettor (Luc's own venue accounts,
his own API keys, his own money).  NOT the subscriber autopilot (AUT-01): no third-party
custody, no subscriber features, no shared pipeline changes.

The complete execution path lives here:

    AUT-01 signal (shadow-ledger row, status FILLED)
      -> stake sizing (Kelly vs the configured bankroll; computed for EVERY signal)
      -> rule gate (net EV floor 1.5%, odds ceiling +300, limit orders only)
      -> risk gate (kill switch, daily bet/spend caps, daily loss stop, error breaker)
      -> venue adapter (Kalshi first; Novig / ProphetX own-account follow the same seam)
      -> audit log (append-only JSONL: every signal, decision, intent, result, settlement)

SAFETY POSTURE (v1, per Luc's Nov 16, 2026 direction "build it now, capped hard and
ready to switch on later"):
  * Dry-run / paper mode is the default and the only mode exercised by this build.
  * Real-money placement sits behind a hard switch (OWNBETTOR_LIVE_ARMED) that ships
    OFF.  Turning it on is Luc's separate later decision (his AUT-02/03/04 gates plus
    the Dec 1 AUT-01 scoring) and is NOT part of this build.
  * Even armed, the live path goes through mlb.execution.kalshi.client, which refuses
    production in code today (ProductionNotEnabled) -- the switch is real but unloaded
    until Luc provisions production access through the secure flow.
"""
