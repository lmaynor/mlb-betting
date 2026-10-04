"""ev -- cross-sport (NFL / NBA / NHL) EV sweep and Kalshi execution.

Design: docs/designs/multisport_ev_sweep.md. Sport-specific facts live in ev.sports adapters;
everything else is sport-agnostic. Reuses the demo-verified executor in mlb.execution.kalshi.
"""
